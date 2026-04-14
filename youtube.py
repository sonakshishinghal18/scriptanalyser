"""
YouTube utilities — uses YouTube Data API v3 for video IDs,
youtube-transcript-api + ScraperAPI proxy for transcripts.
"""
import re
import os
import sys
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
from googleapiclient.discovery import build

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")


def extract_handle(url: str) -> str:
    patterns = [
        r"youtube\.com/@([\w.-]+)",
        r"youtube\.com/c/([\w.-]+)",
        r"youtube\.com/user/([\w.-]+)",
        r"youtube\.com/channel/(UC[\w-]+)",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return url.rstrip("/").split("/")[-1]


async def get_channel_video_ids(channel_url: str, max_videos: int = 10) -> tuple[list[str], str, dict]:
    handle = extract_handle(channel_url)
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    channel_id = None

    if handle.startswith("UC"):
        channel_id = handle
    else:
        try:
            h = handle.lstrip("@")
            res = youtube.channels().list(
                part="id",
                forHandle=h
            ).execute()
            if res.get("items"):
                channel_id = res["items"][0]["id"]
        except Exception as e:
            print(f"[api] forHandle lookup failed: {e}", file=sys.stderr)

    if not channel_id:
        try:
            res = youtube.search().list(
                part="snippet",
                q=handle,
                type="channel",
                maxResults=1
            ).execute()
            if res.get("items"):
                channel_id = res["items"][0]["snippet"]["channelId"]
        except Exception as e:
            print(f"[api] search fallback failed: {e}", file=sys.stderr)

    if not channel_id:
        raise ValueError(f"Could not resolve channel ID for: {handle}")

    # ── Fetch channel metadata + uploads playlist in one call ─────────────
    ch_res = youtube.channels().list(
        part="contentDetails,snippet,statistics,brandingSettings",
        id=channel_id
    ).execute()

    if not ch_res.get("items"):
        raise ValueError(f"Channel not found: {channel_id}")

    channel_item = ch_res["items"][0]
    snippet = channel_item.get("snippet", {})
    statistics = channel_item.get("statistics", {})

    # ── Build metadata dict ───────────────────────────────────────────────
    channel_metadata = {
        "title":            snippet.get("title", ""),
        "description":      snippet.get("description", ""),
        "country":          snippet.get("country", ""),
        "joined":           snippet.get("publishedAt", "")[:10],  # "2009-11-08"
        "subscriber_count": statistics.get("subscriberCount", ""),
        "video_count":      statistics.get("videoCount", ""),
        "view_count":       statistics.get("viewCount", ""),
    }

    print(f"[api] channel metadata: {channel_metadata['title']} | "
          f"{channel_metadata['subscriber_count']} subs | "
          f"{channel_metadata['video_count']} videos", file=sys.stderr)

    # ── Fetch video IDs from uploads playlist ─────────────────────────────
    uploads_playlist = channel_item["contentDetails"]["relatedPlaylists"]["uploads"]

    pl_res = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_playlist,
        maxResults=max_videos
    ).execute()

    video_ids = [
        item["contentDetails"]["videoId"]
        for item in pl_res.get("items", [])
    ]

    print(f"[api] found {len(video_ids)} videos for channel {handle}", file=sys.stderr)

    return video_ids, handle, channel_metadata


def fetch_transcript(video_id: str, max_chars: int = 3000) -> tuple[str | None, bool]:
    try:
        if SCRAPER_API_KEY:
            proxy_url = f"http://scraperapi:{SCRAPER_API_KEY}@proxy-server.scraperapi.com:8001"
            session = requests.Session()
            session.proxies = {"http": proxy_url, "https": proxy_url}
            session.verify = False
            ytt = YouTubeTranscriptApi(http_client=session)
        else:
            ytt = YouTubeTranscriptApi()

        snippet_list = ytt.fetch(video_id, languages=["en", "hi", "en-US", "en-GB"])
        text = " ".join(s.text for s in snippet_list)

        if text.strip():
            print(f"[transcript] {video_id} OK ({len(text)} chars)", file=sys.stderr)
            return text[:max_chars], False

    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"[transcript] {video_id} no captions: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[transcript] {video_id} error: {e}", file=sys.stderr)

    return None, False
