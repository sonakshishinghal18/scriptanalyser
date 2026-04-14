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
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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
            video_ids, handle = await get_channel_video_ids(channel_url, max_videos=20)
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

        if len(transcripts) < 3:
            yield sse("error", {"message": "Not enough usable transcripts found. Please try a channel with more videos."})
            return

        yield sse("status", {"message": f"Read {len(transcripts)} transcripts. Analysing voice...", "step": 3})

        transcript_block = "\n\n---\n\n".join(
            f"Video {i+1}:\n{t}" for i, t in enumerate(transcripts)
        )
        context = (
            f"Here are transcripts from {len(transcripts)} of this creator's recent videos. "
            f"These transcripts are your ONLY source of truth — analyse them deeply and exclusively.\n\n"
            f"{transcript_block}"
        )

        yield sse("status", {"message": "Finding trending topics in your niche...", "step": 4})

        system = (
            "You are an expert content strategist who analyses YouTube creators exclusively from their transcripts. "
            "You only use what the creator actually says in their videos — never assumptions, never channel descriptions, never niche stereotypes. "
            "Study their exact vocabulary, sentence length, energy shifts, how they open, how they build arguments, how they close, "
            "their recurring phrases, filler words, humour style, and unique mannerisms. "
            "Always respond with valid JSON only — no markdown, no preamble."
        )

        prompt = f"""{context}

Using ONLY the transcripts above as your source, analyse this creator's voice deeply.
Focus entirely on how they actually speak — their exact words, sentence structures, catchphrases,
energy shifts, how they open videos, how they argue points, how they close.
Do NOT rely on assumptions about their niche, channel name, or anything outside these transcripts.

Suggest 5 video topics that fit their proven content style and audience.

Return ONLY this exact JSON (no markdown fences):
{{
  "niche": "max 4 words — inferred from transcripts only",
  "tone": "single word",
  "avg_video_length": "e.g. 12 min",
  "posting_pattern": "e.g. Weekly",
  "style_tags": ["tag1", "tag2", "tag3", "tag4"],
  "voice_summary": "2-3 sentences describing exactly how this creator speaks based on the transcripts — their energy, vocabulary level, signature habits",
  "writing_guide": "3-4 specific instructions a ghostwriter must follow to sound exactly like this creator, based on transcript evidence",
  "topics": [
    {{ "title": "compelling title", "reason": "one sentence why it fits their proven content style", "trending": true }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their proven content style", "trending": false }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their proven content style", "trending": true }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their proven content style", "trending": false }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their proven content style", "trending": true }}
  ]
}}"""

        try:
            def call_claude_analyse():
                print("[analyse] calling Claude...", file=sys.stderr)
                result = make_client().messages.create(
                    model=MODEL,
                    max_tokens=1500,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                print(f"[analyse] Claude done, stop_reason={result.stop_reason}", file=sys.stderr)
                return result

            message = await asyncio.to_thread(call_claude_analyse)
            raw = "".join(b.text for b in message.content if b.type == "text")
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip()
            analysis = json.loads(clean)
            yield sse("complete", {"analysis": analysis})

        except json.JSONDecodeError:
            yield sse("error", {"message": "Failed to parse analysis. Please try again."})
        except Exception as e:
            yield sse("error", {"message": f"Analysis failed: {str(e)}"})

    return StreamingResponse(generate(), media_type="text/event-stream")


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

        yield sse("status", {"message": "Writing your script..."})

        system = (
            "You are a master ghostwriter for YouTube creators. "
            "You write scripts that sound exactly like the creator — their specific words, rhythm, energy, mannerisms. "
            "You base everything on what you know about how they actually speak from transcript analysis. "
            "Never generic, never corporate. Always valid JSON only, no markdown."
        )

        prompt = f"""Creator profile (derived exclusively from their video transcripts):
- Niche: {a.get("niche")}
- Tone: {a.get("tone")}
- Style tags: {", ".join(a.get("style_tags", []))}
- Voice summary: {a.get("voice_summary")}
- Writing guide: {a.get("writing_guide")}

Write a complete YouTube script on: "{req.topic}"
Target: STRICTLY {target["words"]} total — do NOT exceed this. ({target["duration"]}) — {target["detail"]}
IMPORTANT: Count your words. Stay within the word limit. It is better to be slightly under than over.

Follow the writing guide strictly. Sound EXACTLY like this creator — use their vocabulary,
their sentence rhythm, their energy. No filler phrases. No generic YouTube-speak.

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
            def call_claude_generate():
                print(f"[generate] calling Claude, topic={req.topic}, length={req.length}", file=sys.stderr)
                result = make_client().messages.create(
                    model=MODEL,
                    max_tokens=3000,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                print(f"[generate] Claude done, stop_reason={result.stop_reason}", file=sys.stderr)
                return result

            message = await asyncio.to_thread(call_claude_generate)
            print("[generate] got message back", file=sys.stderr)

            raw = "".join(b.text for b in message.content if b.type == "text")
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip()

            script = json.loads(clean)

            if not script.get("sections"):
                yield sse("error", {"message": "Script was generated but is incomplete. Please try again."})
                return

            yield sse("complete", {"script": script})

        except json.JSONDecodeError as e:
            print(f"[generate] JSON error: {e}", file=sys.stderr)
            yield sse("error", {"message": "Failed to parse script. Please try again."})
        except Exception as e:
            print(f"[generate] exception: {e}", file=sys.stderr)
            yield sse("error", {"message": f"Script generation failed: {str(e)}"})

    return StreamingResponse(stream(), media_type="text/event-stream")
