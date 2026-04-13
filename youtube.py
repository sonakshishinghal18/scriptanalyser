"""
YouTube utilities — scrape channel video IDs and fetch transcripts.
No API key required.
"""

import re
import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound


def extract_handle(url: str) -> str:
    """Pull the channel handle / ID from a YouTube URL."""
    patterns = [
        r"youtube\.com/@([\w.-]+)",
        r"youtube\.com/c/([\w.-]+)",
        r"youtube\.com/user/([\w.-]+)",
        r"youtube\.com/channel/([\w.-]+)",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    # fallback — last path segment
    return url.rstrip("/").split("/")[-1]


async def get_channel_video_ids(channel_url: str, max_videos: int = 10) -> tuple[list[str], str]:
    """
    Scrape up to `max_videos` video IDs from a channel's /videos page.
    Returns (video_ids, handle).
    """
    handle = extract_handle(channel_url)

    candidates = []
    if handle.startswith("UC"):  # channel ID
        candidates.append(f"https://www.youtube.com/channel/{handle}/videos")
    else:
        h = handle.lstrip("@")
        candidates.append(f"https://www.youtube.com/@{h}/videos")
        candidates.append(f"https://www.youtube.com/c/{h}/videos")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for url in candidates:
            try:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    continue
                ids = _parse_video_ids(r.text, max_videos)
                if ids:
                    return ids, handle
            except Exception:
                continue

    raise ValueError(f"Could not retrieve video list for channel: {handle}")


def _parse_video_ids(html: str, limit: int) -> list[str]:
    """Extract video IDs from raw YouTube HTML."""
    seen: list[str] = []
    added: set[str] = set()
    for pattern in [
        r'"videoId":"([a-zA-Z0-9_-]{11})"',
        r'watch\?v=([a-zA-Z0-9_-]{11})',
    ]:
        for m in re.finditer(pattern, html):
            vid = m.group(1)
            if vid not in added:
                added.add(vid)
                seen.append(vid)
            if len(seen) >= limit:
                return seen
    return seen


def fetch_transcript(video_id: str, max_chars: int = 3000) -> str | None:
    """
    Fetch transcript text for a single video.
    Returns None if unavailable.
    """
    try:
        snippets = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
        text = " ".join(s["text"] for s in snippets)
        return text[:max_chars]
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except Exception:
        return None
