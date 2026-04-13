"""
YouTube utilities — uses YouTube Data API v3 for video IDs,
youtube-transcript-api for transcripts.
"""

import re
import os
import sys
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
from googleapiclient.discovery import build


YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")


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


async def get_channel_video_ids(channel_url: str, max_videos: int = 10) -> tuple[list[str], str]:
    handle = extract_handle(channel_url)

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    # Step 1 — resolve channel ID
    channel_id = None

    if handle.startswith("UC"):
        channel_id = handle
    else:
        # Try forHandle lookup (works for @handles)
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
        # Fallback: search for channel
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

    # Step 2 — get uploads playlist ID
    ch_res = youtube.channels().list(
        part="contentDetails",
        id=channel_id
    ).execute()

    if not ch_res.get("items"):
        raise ValueError(f"Channel not found: {channel_id}")

    uploads_playlist = ch_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Step 3 — fetch latest video IDs from uploads playlist
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
    return video_ids, handle


def fetch_transcript(video_id: str, max_chars: int = 3000) -> tuple[str | None, bool]:
    """
    Fetch transcript using youtube-transcript-api.
    Returns (text, used_fallback).
    """
    try:
        ytt = YouTubeTranscriptApi()
        snippet_list = ytt.fetch(video_id, languages=["en", "en-US", "en-GB"])
        text = " ".join(s.get("text", "") for s in snippet_list)
        if text.strip():
            print(f"[transcript] {video_id} OK ({len(text)} chars)", file=sys.stderr)
            return text[:max_chars], False
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"[transcript] {video_id} no captions: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[transcript] {video_id} error: {e}", file=sys.stderr)

    return None, False
