"""
ScriptForge — FastAPI backend
All LLM calls use claude-sonnet-4-5.
Frontend (HTML/CSS/JS) is served directly from this same service.
"""

import json
import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
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
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

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
        transcripts: list[str] = []
        handle = ""

        yield sse("status", {"message": "Resolving channel...", "step": 1})
        try:
            video_ids, handle = await get_channel_video_ids(channel_url, max_videos=10)
            yield sse("status", {"message": f"Found {len(video_ids)} videos. Reading transcripts...", "step": 2})
        except Exception as e:
            yield sse("status", {"message": f"Could not fetch video list — using channel context. ({e})", "step": 2})
            video_ids = []
            from youtube import extract_handle
            handle = extract_handle(channel_url)

        fetched = 0
        for vid in video_ids:
            text = await asyncio.to_thread(fetch_transcript, vid)
            if text:
                transcripts.append(text)
                fetched += 1
                yield sse("status", {"message": f"Reading transcripts... ({fetched}/{len(video_ids)})", "step": 2})

        yield sse("status", {"message": "Analysing voice, tone, and patterns...", "step": 3})

        if transcripts:
            transcript_block = "\n\n---\n\n".join(
                f"Video {i+1}:\n{t}" for i, t in enumerate(transcripts)
            )
            context = f"Here are transcripts from the creator's last {len(transcripts)} videos:\n\n{transcript_block}"
        else:
            context = (
                f"Channel URL: {channel_url}\n"
                f"Handle: {handle}\n"
                "No transcripts could be retrieved. "
                "Infer the creator's style from their channel identity and niche."
            )

        yield sse("status", {"message": "Finding trending topics in your niche...", "step": 4})

        system = (
            "You are an expert content strategist who analyses YouTube creators deeply. "
            "Study vocabulary, sentence rhythm, energy, how they open videos, how they argue, "
            "their quirks and catchphrases. Always respond with valid JSON only — no markdown, no preamble."
        )

        prompt = f"""{context}

Analyse this creator and suggest 5 video topics.

Return ONLY this exact JSON (no markdown fences):
{{
  "niche": "max 4 words",
  "tone": "single word",
  "avg_video_length": "e.g. 12 min",
  "posting_pattern": "e.g. Weekly",
  "style_tags": ["tag1", "tag2", "tag3", "tag4"],
  "voice_summary": "2-3 sentences on how this creator speaks, their energy, vocabulary, signature habits",
  "writing_guide": "3-4 sentences a ghostwriter must follow to sound exactly like them",
  "topics": [
    {{ "title": "compelling title", "reason": "one sentence why it fits their audience now", "trending": true }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their audience now", "trending": false }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their audience now", "trending": true }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their audience now", "trending": false }},
    {{ "title": "compelling title", "reason": "one sentence why it fits their audience now", "trending": true }}
  ]
}}"""

        client = make_client()
        message = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = "".join(b.text for b in message.content if b.type == "text")
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        analysis = json.loads(clean)

        yield sse("complete", {"analysis": analysis})

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/generate")
async def generate_script(req: GenerateRequest):
    async def stream():
        length_map = {
            "short":  {"words": "650-750 words",   "duration": "~5 minutes",  "detail": "punchy and tight"},
            "medium": {"words": "1400-1600 words",  "duration": "~10 minutes", "detail": "balanced depth and pace"},
            "long":   {"words": "2800-3200 words",  "duration": "~20 minutes", "detail": "comprehensive with examples and deep dives"},
        }
        target = length_map.get(req.length, length_map["medium"])
        a = req.analysis

        yield sse("status", {"message": "Writing your script..."})

        system = (
            "You are a master ghostwriter for YouTube creators. "
            "You write scripts that sound exactly like the creator — their specific words, rhythm, energy, mannerisms. "
            "Never generic, never corporate. Always valid JSON only, no markdown."
        )

        prompt = f"""Creator profile:
- Niche: {a.get("niche")}
- Tone: {a.get("tone")}
- Style tags: {", ".join(a.get("style_tags", []))}
- Voice summary: {a.get("voice_summary")}
- Writing guide: {a.get("writing_guide")}

Write a complete YouTube script on: "{req.topic}"
Target: {target["words"]} ({target["duration"]}) — {target["detail"]}

Follow the writing guide strictly. Sound EXACTLY like this creator. No filler phrases.

Return ONLY this JSON:
{{
  "suggested_title": "best YouTube title for this video",
  "thumbnail_hook": "6-8 word phrase for thumbnail text",
  "sections": [
    {{ "name": "Hook",           "label": "First 30 seconds", "content": "full script — grab attention immediately in creator's voice" }},
    {{ "name": "Intro",          "label": "Set the stage",    "content": "full script content" }},
    {{ "name": "Main Content",   "label": "The core",         "content": "full script — longest section, {target["detail"]}" }},
    {{ "name": "Key Takeaways",  "label": "Land it",          "content": "full script content" }},
    {{ "name": "Outro & CTA",    "label": "Close strong",     "content": "full script — end exactly how this creator ends videos" }}
  ]
}}"""

        client = make_client()
        message = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = "".join(b.text for b in message.content if b.type == "text")
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        script = json.loads(clean)

        yield sse("complete", {"script": script})

    return StreamingResponse(stream(), media_type="text/event-stream")
