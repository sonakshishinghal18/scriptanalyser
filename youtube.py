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


def sample_transcript(text: str, max_chars: int = 6000) -> str:
    """
    Sample from beginning, middle, and end of a transcript
    instead of just truncating from the start.

    Distribution:
      - 40% from the beginning  (hook, intro, early style)
      - 35% from the middle     (argument style, explanations)
      - 25% from the end        (closing style, CTA, sign-off)

    Each chunk is taken as a contiguous block so the voice
    reads naturally within each section.

    Explicit section headers are added so the LLM knows these are
    non-contiguous excerpts for voice analysis only — not a continuous
    script to be reproduced or confused during generation.
    """
    total = len(text)

    # If transcript fits entirely, wrap in a single section header and return
    if total <= max_chars:
        return (
            "[TRANSCRIPT EXCERPT — OPENING/FULL]\n"
            f"{text}\n"
            "[END OF EXCERPT]"
        )

    begin_chars  = int(max_chars * 0.40)
    middle_chars = int(max_chars * 0.35)
    end_chars    = max_chars - begin_chars - middle_chars  # remaining ~25%

    # Beginning: first N chars
    beginning = text[:begin_chars]

    # Middle: centred around the midpoint
    mid        = total // 2
    mid_start  = max(begin_chars, mid - middle_chars // 2)
    mid_end    = mid_start + middle_chars
    # Guard against overlap with end section
    if mid_end > total - end_chars:
        mid_end   = total - end_chars
        mid_start = max(begin_chars, mid_end - middle_chars)
    middle = text[mid_start:mid_end]

    # End: last N chars
    end = text[total - end_chars:]

    sampled = (
        "[TRANSCRIPT EXCERPT — OPENING (first ~40% of budget) — "
        "use to analyse: hook style, intro pattern, early energy]\n"
        f"{beginning}\n"
        "[END OF OPENING EXCERPT]\n\n"

        "[TRANSCRIPT EXCERPT — MID-VIDEO (middle ~35% of budget) — "
        "use to analyse: argument style, explanation patterns, transitions, energy mid-video]\n"
        f"{middle}\n"
        "[END OF MID-VIDEO EXCERPT]\n\n"

        "[TRANSCRIPT EXCERPT — CLOSING (final ~25% of budget) — "
        "use to analyse: closing style, sign-off, CTA pattern, final energy]\n"
        f"{end}\n"
        "[END OF CLOSING EXCERPT]"
    )

    print(
        f"[transcript] sampled {begin_chars}+{middle_chars}+{end_chars} chars "
        f"from {total} total",
        file=sys.stderr,
    )
    return sampled


async def get_channel_video_ids(channel_url: str, max_videos: int = 8) -> tuple[list[str], str, dict]:
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
    snippet      = channel_item.get("snippet", {})
    statistics   = channel_item.get("statistics", {})

    # ── Build metadata dict ───────────────────────────────────────────────
    channel_metadata = {
        "title":            snippet.get("title", ""),
        "description":      snippet.get("description", ""),
        "country":          snippet.get("country", ""),
        "joined":           snippet.get("publishedAt", "")[:10],
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


def fetch_transcript(video_id: str, max_chars: int = 6000) -> tuple[str | None, bool]:
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
            # ── Sample across the full transcript instead of head-truncating ──
            sampled = sample_transcript(text, max_chars=max_chars)
            return sampled, False

    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"[transcript] {video_id} no captions: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[transcript] {video_id} error: {e}", file=sys.stderr)

    return None, False
