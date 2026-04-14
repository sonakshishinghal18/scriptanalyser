"""
ScriptForge — FastAPI backend
All LLM calls use claude-sonnet-4-5.
Frontend (HTML/CSS/JS) is served directly from this same service.
"""

import json
import os
import sys
import asyncio
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
import anthropic

from youtube import get_channel_video_ids, fetch_transcript

load_dotenv()

app = FastAPI(title="ScriptForge API")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-5"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# ── Serve frontend static files ───────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent
if (FRONTEND_DIR / "index.html").exists():

    @app.get("/")
    def serve_index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/app.js")
    def serve_appjs():
        return FileResponse(str(FRONTEND_DIR / "app.js"))

    @app.get("/style.css")
    def serve_css():
        return FileResponse(str(FRONTEND_DIR / "style.css"))

    @app.get("/config.js")
    def serve_config():
        return FileResponse(str(FRONTEND_DIR / "config.js"))


# ── Models ────────────────────────────────────────────────────────────────────

class AnalyseRequest(BaseModel):
    channelUrl: str

class GenerateRequest(BaseModel):
    topic: str
    length: str
    analysis: dict


# ── Helpers ───────────────────────────────────────────────────────────────────

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}


@app.post("/api/analyse")
async def analyse(req: AnalyseRequest):
    async def generate():
        channel_url = req.channelUrl.strip()

        yield sse("status", {"message": "Resolving channel...", "step": 1})
        try:
            video_ids, handle, channel_metadata = await get_channel_video_ids(channel_url, max_videos=20)
            yield sse("status", {"message": f"Found {len(video_ids)} videos. Reading transcripts...", "step": 2})
        except Exception as e:
            yield sse("error", {"message": f"Could not find this channel. Please check the URL and try again. ({e})"})
            return

        yield sse("status", {"message": "Reading transcripts...", "step": 2})

        async def fetch_one(vid):
            text, used_ytdlp = await asyncio.to_thread(fetch_transcript, vid)
            return vid, text, used_ytdlp

        transcripts: list[str] = []
        ytdlp_triggered = False

        for i in range(0, len(video_ids), 5):
            batch = video_ids[i:i+5]
            results = await asyncio.gather(*[fetch_one(vid) for vid in batch])

            for vid, text, used in results:
                if used:
                    ytdlp_triggered = True
                if text and len(text.strip()) > 200:
                    transcripts.append(text)

            yield sse("status", {"message": f"Reading transcripts... ({len(transcripts)} so far)", "step": 2})

            if len(transcripts) >= 10:
                break

        transcripts = transcripts[:10]

        if not transcripts:
            yield sse("error", {"message": "No transcripts found for this channel. Captions appear to be disabled. Please try a channel that has captions enabled — most large creators do."})
            return

        yield sse("status", {"message": f"Read {len(transcripts)} transcripts. Analysing voice...", "step": 3})

        # ── Build channel metadata context ────────────────────────────────
        try:
            sub_count  = int(channel_metadata.get("subscriber_count", 0))
            view_count = int(channel_metadata.get("view_count", 0))
            vid_count  = int(channel_metadata.get("video_count", 1))
            avg_views  = view_count // max(vid_count, 1)
        except (ValueError, ZeroDivisionError):
            sub_count = avg_views = view_count = vid_count = 0

        metadata_context = f"""
Channel context (use for background awareness ONLY — do NOT use for voice/style analysis):
- Channel name: {channel_metadata.get("title", "")}
- Description: {channel_metadata.get("description", "")}
- Subscribers: {sub_count:,}
- Total views: {view_count:,}
- Videos published: {vid_count}
- Avg views per video: {avg_views:,}
- Country: {channel_metadata.get("country", "Unknown")}
- On YouTube since: {channel_metadata.get("joined", "")[:4]}
"""

        transcript_block = "\n\n---\n\n".join(
            f"Video {i+1}:\n{t}" for i, t in enumerate(transcripts)
        )

        context = (
            f"{metadata_context}\n\n"
            f"Here are transcripts from {len(transcripts)} of this creator's recent videos. "
            f"These transcripts are your ONLY source of truth for voice and style — analyse them deeply.\n\n"
            f"{transcript_block}"
        )

        yield sse("status", {"message": "Finding trending topics in your niche...", "step": 4})

        current_date = datetime.now().strftime("%B %d, %Y")

        system = (
            "You are an expert content strategist who analyses YouTube creators exclusively from their transcripts. "
            "You only use what the creator actually says in their videos — never assumptions, never channel descriptions, never niche stereotypes. "
            "Study their exact vocabulary, sentence length, energy shifts, how they open, how they build arguments, how they close, "
            "their recurring phrases, filler words, humour style, and unique mannerisms. "
            "You are also given channel metadata (subscribers, description, country etc.) — use this for CONTEXT ONLY "
            "to inform topic relevance, audience size awareness, and cultural references. Never use it for voice analysis. "
            f"You also have access to web search — use it to find what topics are currently trending in this creator's niche "
            f"in the last 7 days as of {current_date}. Prioritise topics that are gaining momentum right now, not older trends. "
            "Always respond with valid JSON only — no markdown, no preamble."
        )

        prompt = f"""{context}

Using the transcripts above as your ONLY source for voice and style analysis,
and the channel metadata as background context for topic relevance:

1. Analyse how this creator speaks — their exact words, sentence structures, catchphrases, energy, opening style, argument style, closing style.
2. Use the channel description and subscriber count to understand their audience and positioning.
3. Search the web for what topics are currently trending in this creator's niche in the last 7 days as of {current_date}.
4. Suggest 5 video topics that combine their proven content style with current trending topics, appropriate for their audience size ({sub_count:,} subscribers).
5. Extract 3 real verbatim excerpts from the transcripts that best showcase how this creator speaks.

Return ONLY this exact JSON (no markdown fences):
{{
  "niche": "max 4 words — inferred from transcripts and channel description",
  "tone": "single word",
  "avg_video_length": "e.g. 12 min",
  "posting_pattern": "e.g. Weekly",
  "style_tags": ["tag1", "tag2", "tag3", "tag4"],
  "voice_summary": "2-3 sentences describing exactly how this creator speaks based on the transcripts — their energy, vocabulary level, signature habits",
  "writing_guide": {{
    "sentence_length": "describe their typical sentence length and rhythm — e.g. short punchy sentences mixed with longer explanations",
    "opening_style": "exactly how they start videos — their first 2-3 sentences pattern",
    "signature_phrases": ["exact phrase 1 they repeat", "exact phrase 2", "exact phrase 3"],
    "transitions": "exactly how they move between points — words or phrases they use",
    "energy_pattern": "where they speed up, slow down, use emphasis — describe the rhythm",
    "filler_words": ["filler1", "filler2", "filler3"],
    "closing_style": "exactly how they end videos — their sign-off pattern",
    "instructions": "4-5 specific rules a ghostwriter MUST follow to sound exactly like this creator"
  }},
  "voice_examples": [
    "verbatim 2-3 sentence excerpt from transcripts showing how they open or hook",
    "verbatim 2-3 sentence excerpt showing how they explain or argue a point",
    "verbatim 2-3 sentence excerpt showing how they close or transition"
  ],
  "topics": [
    {{ "title": "compelling title", "reason": "one sentence why it fits their style and current trends", "trending": true }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their style and current trends", "trending": false }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their style and current trends", "trending": true }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their style and current trends", "trending": false }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their style and current trends", "trending": true }}
  ]
}}"""

        try:
            def call_claude_analyse():
                print("[analyse] calling Claude with web search...", file=sys.stderr)
                result = make_client().messages.create(
                    model=MODEL,
                    max_tokens=3000,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                )
                print(f"[analyse] Claude done, stop_reason={result.stop_reason}", file=sys.stderr)
                return result

            message = await asyncio.to_thread(call_claude_analyse)

            text_blocks = [b.text for b in message.content if b.type == "text"]
            print(f"[analyse] text blocks count: {len(text_blocks)}", file=sys.stderr)
            raw = text_blocks[-1] if text_blocks else ""
            print(f"[analyse] raw preview: {raw[:200]}", file=sys.stderr)

            clean = raw.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0]
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0]
            clean = clean.strip()

            analysis = json.loads(clean)

            # ── Attach channel metadata to analysis for frontend use ──────
            analysis["channel_metadata"] = {
                "title":            channel_metadata.get("title", ""),
                "subscriber_count": sub_count,
                "video_count":      vid_count,
                "view_count":       view_count,
                "avg_views":        avg_views,
                "country":          channel_metadata.get("country", ""),
                "joined":           channel_metadata.get("joined", "")[:4],
            }

            yield sse("complete", {"analysis": analysis})

        except json.JSONDecodeError:
            yield sse("error", {"message": "Failed to parse analysis. Please try again."})
        except Exception as e:
            yield sse("error", {"message": f"Analysis failed: {str(e)}"})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/generate")
async def generate_script(req: GenerateRequest):
    async def stream():
        length_map = {
            "short":  {"words": "650-750 words",   "duration": "~5 minutes",  "detail": "punchy and tight"},
            "medium": {"words": "1400-1600 words",  "duration": "~10 minutes", "detail": "balanced depth and pace"},
            "long":   {"words": "1800-2000 words",  "duration": "~15 minutes", "detail": "comprehensive with examples and deep dives"},
        }
        target = length_map.get(req.length, length_map["medium"])
        a = req.analysis

        # ── Extract rich voice data from analysis ─────────────────────────
        writing_guide  = a.get("writing_guide", {})
        voice_examples = a.get("voice_examples", [])
        channel_meta   = a.get("channel_metadata", {})

        # Handle both old string format and new dict format
        if isinstance(writing_guide, str):
            writing_guide_text = writing_guide
        else:
            writing_guide_text = f"""
- Sentence length & rhythm: {writing_guide.get("sentence_length", "")}
- Opening style: {writing_guide.get("opening_style", "")}
- Signature phrases to use: {", ".join(writing_guide.get("signature_phrases", []))}
- Transitions: {writing_guide.get("transitions", "")}
- Energy pattern: {writing_guide.get("energy_pattern", "")}
- Filler words to sprinkle in: {", ".join(writing_guide.get("filler_words", []))}
- Closing style: {writing_guide.get("closing_style", "")}
- Rules: {writing_guide.get("instructions", "")}"""

        voice_examples_text = ""
        if voice_examples:
            voice_examples_text = "\n\nHere are REAL examples of how this creator actually speaks — match this style exactly:\n"
            for i, example in enumerate(voice_examples, 1):
                voice_examples_text += f'\nExample {i}: "{example}"'

        # ── Channel metadata context for generate ─────────────────────────
        channel_context = ""
        if channel_meta:
            channel_context = f"""
Channel context (for awareness only):
- Subscribers: {channel_meta.get("subscriber_count", 0):,}
- Avg views per video: {channel_meta.get("avg_views", 0):,}
- Country: {channel_meta.get("country", "")}
"""

        yield sse("status", {"message": "Researching topic facts..."})

        system = (
            "You are a master ghostwriter for YouTube creators. "
            "You write scripts that sound exactly like the creator — their specific words, rhythm, energy, mannerisms. "
            "You have been given real transcript examples of how this creator speaks — study them carefully and mimic every detail. "
            "You also have access to web search — use it to find verified, current facts about the topic before writing. "
            "Never fabricate statistics, dates, events, or claims. Only use real sourced information. "
            "Never generic, never corporate. Always valid JSON only, no markdown."
        )

        prompt = f"""Creator profile (derived exclusively from their video transcripts):
- Niche: {a.get("niche")}
- Tone: {a.get("tone")}
- Style tags: {", ".join(a.get("style_tags", []))}
- Voice summary: {a.get("voice_summary")}
{channel_context}
Writing guide:
{writing_guide_text}
{voice_examples_text}

Before writing the script:
1. Search the web for current, verified facts, statistics, and recent developments about "{req.topic}"
2. Use ONLY real, sourced facts in the script — never fabricate statistics, dates, events, or claims
3. Then write the complete script in this creator's voice using those verified facts

Write a complete YouTube script on: "{req.topic}"
Target: STRICTLY {target["words"]} total — do NOT exceed this. ({target["duration"]}) — {target["detail"]}
IMPORTANT: Count your words. Stay within the word limit. It is better to be slightly under than over.

Follow the writing guide strictly. Use the real voice examples above as your style template.
Sound EXACTLY like this creator — use their vocabulary, their sentence rhythm, their energy,
their signature phrases, their filler words. No generic YouTube-speak. No filler phrases.

Return ONLY this JSON:
{{
  "suggested_title": "best YouTube title for this video",
  "thumbnail_hook": "6-8 word phrase for thumbnail text",
  "sections": [
    {{ "name": "Hook",           "label": "First 30 seconds", "content": "script content — 50-80 words" }},
    {{ "name": "Intro",          "label": "Set the stage",    "content": "script content — 80-120 words" }},
    {{ "name": "Main Content",   "label": "The core",         "content": "script content — longest section" }},
    {{ "name": "Key Takeaways",  "label": "Land it",          "content": "script content — 80-100 words" }},
    {{ "name": "Outro & CTA",    "label": "Close strong",     "content": "script content — 50-80 words" }}
  ]
}}"""

        try:
            # ── Dynamic max_tokens based on script length ─────────────────
            token_map = {
                "short":  3000,
                "medium": 5000,
                "long":   7000,
            }
            max_tokens = token_map.get(req.length, 5000)

            def call_claude_generate():
                print(f"[generate] calling Claude, topic={req.topic}, length={req.length}, max_tokens={max_tokens}", file=sys.stderr)
                result = make_client().messages.create(
                    model=MODEL,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                )
                print(f"[generate] Claude done, stop_reason={result.stop_reason}", file=sys.stderr)
                return result

            message = await asyncio.to_thread(call_claude_generate)
            print("[generate] got message back", file=sys.stderr)

            # ── Check if Claude was cut off before finishing ───────────────
            if message.stop_reason == "max_tokens":
                print(f"[generate] hit max_tokens limit", file=sys.stderr)
                yield sse("error", {"message": "Script was too long to generate. Try a shorter length or simpler topic."})
                return

            # ── Extract last text block (after web search blocks) ─────────
            text_blocks = [b.text for b in message.content if b.type == "text"]
            print(f"[generate] text blocks count: {len(text_blocks)}", file=sys.stderr)
            raw = text_blocks[-1] if text_blocks else ""
            print(f"[generate] raw length={len(raw)}", file=sys.stderr)

            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip()

            print(f"[generate] attempting json.loads", file=sys.stderr)
            script = json.loads(clean)
            print(f"[generate] json parsed OK, sections={len(script.get('sections', []))}", file=sys.stderr)

            if not script.get("sections"):
                yield sse("error", {"message": "Script was generated but is incomplete. Please try again."})
                return

            print(f"[generate] sending complete SSE", file=sys.stderr)
            yield sse("complete", {"script": script})
            print(f"[generate] complete SSE sent", file=sys.stderr)

        except json.JSONDecodeError as e:
            print(f"[generate] JSON error: {e}", file=sys.stderr)
            yield sse("error", {"message": "Failed to parse script. Please try again."})
        except Exception as e:
            print(f"[generate] exception: {e}", file=sys.stderr)
            yield sse("error", {"message": f"Script generation failed: {str(e)}"})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
