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


def fmt_time(seconds: float) -> str:
    """Convert seconds to MM:SS string for logging."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def sample_transcript_by_time(snippets: list, max_chars: int = 6000) -> str:
    """
    Sample transcript snippets by actual video timestamp, not character position.

    YouTube provides each snippet with a real start time (seconds), so we cut
    by time percentage — accurate regardless of video length or intro style.

    Time distribution:
      - 40% of budget → first 40% of video duration  (hook, intro, early style)
      - 35% of budget → middle 35% of video duration (argument style, transitions)
      - 25% of budget → final 25% of video duration  (closing style, CTA, sign-off)

    IMPORTANT — labels are written explicitly so Claude knows these are
    VOICE ANALYSIS SAMPLES ONLY and must never be used as a script template
    or reproduced in order when generating a new script.
    """
    if not snippets:
        return ""

    # ── Fix 6: scan all snippets for real max end time ────────────────
    # Last snippet's duration is often 0 in auto-generated captions.
    # Taking the max across all snippets gives a reliable total duration.
    total_duration = max(
        s.start + s.duration for s in snippets if s.duration and s.duration > 0
    ) if any(s.duration and s.duration > 0 for s in snippets) else snippets[-1].start

    # ── If very short video (< 2 min), just return everything ────────────
    if total_duration < 120:
        full_text = " ".join(s.text for s in snippets)
        return (
            "⚠ VOICE ANALYSIS SAMPLE ONLY — DO NOT USE AS SCRIPT TEMPLATE ⚠\n"
            "These are excerpts from a real video used ONLY to study how this "
            "creator speaks. Do NOT reproduce or mirror this structure when "
            "writing a new script.\n\n"
            "[SAMPLE A — FULL VIDEO (short video, full transcript shown)]\n"
            f"{full_text[:max_chars]}\n"
            "[END OF SAMPLE A]"
        )

    # ── Time boundaries ───────────────────────────────────────────────────
    begin_end_time  = total_duration * 0.40   # 0%  → 40%
    middle_end_time = total_duration * 0.75   # 40% → 75%
    # end section    = 75% → 100%

    # ── Assign each snippet to its section by timestamp ───────────────────
    begin_snips  = [s for s in snippets if s.start <  begin_end_time]
    middle_snips = [s for s in snippets if begin_end_time  <= s.start < middle_end_time]
    end_snips    = [s for s in snippets if s.start >= middle_end_time]

    begin_text  = " ".join(s.text for s in begin_snips)
    middle_text = " ".join(s.text for s in middle_snips)
    end_text    = " ".join(s.text for s in end_snips)

    # ── Fix 5: cut at word boundary, not mid-character ────────────────
    # Hard slicing at exactly N chars can cut mid-word or mid-sentence,
    # giving Claude a broken fragment at the end of each sample.
    # We find the last space at or before the budget limit instead.
    def word_boundary_cut(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        cut = text.rfind(' ', 0, limit)   # last space before limit
        return text[:cut] if cut != -1 else text[:limit]

    begin_budget  = int(max_chars * 0.40)
    middle_budget = int(max_chars * 0.35)
    end_budget    = max_chars - begin_budget - middle_budget

    begin_text  = word_boundary_cut(begin_text,  begin_budget)
    middle_text = word_boundary_cut(middle_text, middle_budget)
    end_text    = word_boundary_cut(end_text,    end_budget)

    # ── Timestamp labels for transparency ────────────────────────────────
    begin_label  = f"0:00 – {fmt_time(begin_end_time)}"
    middle_label = f"{fmt_time(begin_end_time)} – {fmt_time(middle_end_time)}"
    end_label    = f"{fmt_time(middle_end_time)} – {fmt_time(total_duration)}"

    print(
        f"[transcript] time-based sample: "
        f"begin({begin_label})={len(begin_text)}c  "
        f"mid({middle_label})={len(middle_text)}c  "
        f"end({end_label})={len(end_text)}c  "
        f"total_duration={fmt_time(total_duration)}",
        file=sys.stderr,
    )

    return (
        "⚠ VOICE ANALYSIS SAMPLES ONLY — DO NOT USE AS SCRIPT TEMPLATE ⚠\n"
        "The three samples below are non-contiguous excerpts from a real video.\n"
        "Their ONLY purpose is to help you understand HOW this creator speaks — "
        "their vocabulary, rhythm, energy, and mannerisms.\n"
        "When writing a NEW script, use these purely as a voice reference. "
        "Do NOT reproduce their content, do NOT mirror their structure, "
        "do NOT place Sample A content at the start of the new script just "
        "because it came from the start of this video.\n\n"

        f"[SAMPLE A — OPENING SECTION ({begin_label}) — "
        "STUDY: hook style, how they open, early energy, first impressions]\n"
        f"{begin_text}\n"
        "[END OF SAMPLE A]\n\n"

        f"[SAMPLE B — MID-VIDEO SECTION ({middle_label}) — "
        "STUDY: how they explain points, transitions, argument style, mid-video energy]\n"
        f"{middle_text}\n"
        "[END OF SAMPLE B]\n\n"

        f"[SAMPLE C — CLOSING SECTION ({end_label}) — "
        "STUDY: how they wrap up, sign-off pattern, CTA style, final energy]\n"
        f"{end_text}\n"
        "[END OF SAMPLE C]"
    )




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

        if snippet_list:
            total_duration = snippet_list[-1].start + snippet_list[-1].duration
            print(
                f"[transcript] {video_id} OK — "
                f"{len(snippet_list)} snippets, "
                f"duration={fmt_time(total_duration)}",
                file=sys.stderr,
            )
            # ── Sample by real timestamps, not character position ──────────────────────
            sampled = sample_transcript_by_time(snippet_list, max_chars=max_chars)
            if sampled.strip():
                return sampled, False

    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"[transcript] {video_id} no captions: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[transcript] {video_id} error: {e}", file=sys.stderr)

    return None, False
