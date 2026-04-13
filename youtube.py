"""
YouTube utilities — scrape channel video IDs and fetch transcripts.
Primary: youtube-transcript-api
Fallback: yt-dlp (handles auto-generated captions, works when transcripts are off)
No API key required.
"""

import re
import httpx
import tempfile
import os
import json
import sys
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound


def extract_handle(url: str) -> str:
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
    return url.rstrip("/").split("/")[-1]


async def get_channel_video_ids(channel_url: str, max_videos: int = 10) -> tuple[list[str], str]:
    handle = extract_handle(channel_url)

    candidates = []
    if handle.startswith("UC"):
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


def fetch_transcript(video_id: str, max_chars: int = 3000) -> tuple[str | None, bool]:
    """
    Try youtube-transcript-api first.
    Fall back to yt-dlp if that fails.
    Returns (text, used_ytdlp) — used_ytdlp is True if fallback was needed.
    """

    # ── Method 1: youtube-transcript-api ──────────────────────
    try:
        snippets = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["en", "en-US", "en-GB"]
        )
        text = " ".join(s["text"] for s in snippets)
        if text.strip():
            return text[:max_chars], False
    except Exception as e1:
        print(f"[transcript-api] {video_id} failed: {e1}", file=sys.stderr)

    # ── Method 2: yt-dlp fallback ─────────────────────────────
    try:
        text = _fetch_via_ytdlp(video_id, max_chars)
        if text:
            return text, True
        else:
            print(f"[yt-dlp] {video_id} returned empty", file=sys.stderr)
    except Exception as e2:
        print(f"[yt-dlp] {video_id} failed: {e2}", file=sys.stderr)

    return None, True


def _fetch_via_ytdlp(video_id: str, max_chars: int = 3000) -> str | None:
    try:
        import yt_dlp

        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["en", "en-US", "en-GB"],
                "subtitlesformat": "json3",
                "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
            }

            url = f"https://www.youtube.com/watch?v={video_id}"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            files = os.listdir(tmpdir)
            print(f"[yt-dlp] {video_id} downloaded files: {files}", file=sys.stderr)

            for fname in files:
                if fname.endswith(".json3"):
                    text = _parse_json3_subtitles(os.path.join(tmpdir, fname))
                    if text:
                        return text[:max_chars]

            for fname in files:
                if fname.endswith(".vtt"):
                    text = _parse_vtt(os.path.join(tmpdir, fname))
                    if text:
                        return text[:max_chars]

    except Exception as e:
        print(f"[yt-dlp] exception for {video_id}: {e}", file=sys.stderr)

    return None


def _parse_json3_subtitles(filepath: str) -> str:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        words = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                t = seg.get("utf8", "").strip()
                if t and t != "\n":
                    words.append(t)
        return " ".join(words).strip()
    except Exception:
        return ""


def _parse_vtt(filepath: str) -> str:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")
        text_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("WEBVTT") or line.startswith("NOTE"):
                continue
            if re.match(r"[\d:.]+ --> [\d:.]+", line):
                continue
            if re.match(r"^\d+$", line):
                continue
            line = re.sub(r"<[^>]+>", "", line)
            if line:
                text_lines.append(line)
        deduped = []
        for line in text_lines:
            if not deduped or line != deduped[-1]:
                deduped.append(line)
        return " ".join(deduped).strip()
    except Exception:
        return ""
