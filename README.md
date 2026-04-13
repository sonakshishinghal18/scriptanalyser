# ScriptForge

Generate video scripts in your unique voice — plain HTML/CSS/JS frontend, Python FastAPI backend.

---

## Stack

| Layer     | Tech                          |
|-----------|-------------------------------|
| Frontend  | Plain HTML + CSS + JS         |
| Backend   | Python 3.11 + FastAPI + Uvicorn |
| LLM       | Anthropic `claude-sonnet-4-5` |
| Transcripts | youtube-transcript-api      |
| Hosting   | Render (two services)         |

---

## Project Structure

```
scriptforge/
├── backend/
│   ├── main.py            FastAPI app — all routes + LLM calls
│   ├── youtube.py         Channel scraping + transcript fetching
│   ├── requirements.txt
│   ├── .env.example
│   └── .gitignore
├── frontend/
│   ├── index.html         Single-page app (all pages in one file)
│   ├── style.css          Full design system
│   ├── app.js             All page logic + SSE client
│   └── config.js          ← Edit this to set your backend URL
├── render.yaml            Render Blueprint (deploys both services)
├── .gitignore
└── README.md
```

---

## Local Development

### Requirements
- Python 3.10+
- An Anthropic API key → https://console.anthropic.com

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY=sk-ant-...

uvicorn main:app --reload --port 8000
# API running at http://localhost:8000
```

### Frontend

Since it's plain HTML/JS, just open with any static file server:

```bash
cd frontend

# Option A — Python (no install needed)
python -m http.server 5500

# Option B — Node (if installed)
npx serve .
```

Open http://localhost:5500 in your browser.

The frontend talks to the backend via `window.API_URL` (set in `config.js`).
For local dev, leave `config.js` as `window.API_URL = ''` and the browser
will use relative URLs — which won't work across ports. Instead, set:

```js
// frontend/config.js  (local dev only)
window.API_URL = 'http://localhost:8000';
```

---

## Deploy to Render

### Step 1 — Push to GitHub

```bash
# In the scriptforge/ root directory:
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/scriptforge.git
git push -u origin main
```

---

### Step 2 — Deploy Backend (Web Service)

1. Go to https://render.com → **New** → **Web Service**
2. Connect your GitHub repo
3. Fill in:

| Field             | Value                                    |
|-------------------|------------------------------------------|
| Name              | `scriptforge-backend`                    |
| Root Directory    | `backend`                                |
| Runtime           | `Python 3`                               |
| Build Command     | `pip install -r requirements.txt`        |
| Start Command     | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

4. Under **Environment Variables**, add:

| Key                  | Value                          |
|----------------------|--------------------------------|
| `ANTHROPIC_API_KEY`  | `sk-ant-...your key...`        |
| `FRONTEND_URL`       | *(leave blank for now)*        |

5. Click **Create Web Service** and wait for it to deploy.
6. Copy the URL shown — it will look like `https://scriptforge-backend.onrender.com`

---

### Step 3 — Configure Frontend

Before deploying the frontend, tell it where the backend lives:

Open `frontend/config.js` and set:

```js
window.API_URL = 'https://scriptforge-backend.onrender.com';
```

Commit and push:

```bash
git add frontend/config.js
git commit -m "set backend URL"
git push
```

---

### Step 4 — Deploy Frontend (Static Site)

1. Go to Render → **New** → **Static Site**
2. Connect the same GitHub repo
3. Fill in:

| Field                | Value                          |
|----------------------|--------------------------------|
| Name                 | `scriptforge-frontend`         |
| Root Directory       | `frontend`                     |
| Build Command        | *(leave blank)*                |
| Publish Directory    | `.`                            |

4. Under **Redirects/Rewrites**, add:
   - Source: `/*`
   - Destination: `/index.html`
   - Action: `Rewrite`

5. Click **Create Static Site** and wait for deploy.
6. Copy the frontend URL, e.g. `https://scriptforge-frontend.onrender.com`

---

### Step 5 — Wire CORS

Go back to your **backend** service on Render:

1. Environment → add/update:

| Key            | Value                                        |
|----------------|----------------------------------------------|
| `FRONTEND_URL` | `https://scriptforge-frontend.onrender.com`  |

2. Click **Save** — Render will redeploy the backend automatically.

---

### Step 6 — Verify

Visit your frontend URL:
- Paste a public YouTube channel (e.g. `https://www.youtube.com/@mkbhd`)
- Watch the 4-step analysis progress
- Pick a topic, choose length, generate your script

---

## Environment Variables

### Backend (`backend/.env`)

```env
ANTHROPIC_API_KEY=sk-ant-...        # Required
FRONTEND_URL=https://...            # Your frontend Render URL (for CORS)
```

---

## Notes

**Cold starts** — Render free tier spins down after inactivity. First request
after sleep can take 30–60 s. Upgrade to a paid instance to avoid this.

**Transcripts** — Not all videos have captions. The backend gracefully falls
back to channel-level inference when transcripts are unavailable.

**Model** — Uses `claude-sonnet-4-5` for both analysis and script generation.
To switch models, change the `MODEL` constant in `backend/main.py`.
