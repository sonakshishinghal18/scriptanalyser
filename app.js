/* ── Config ──────────────────────────────────────────────── */
const API = window.API_URL || '';

/* ── State ───────────────────────────────────────────────── */
const state = {
  channelUrl:     '',
  analysis:       null,
  selectedTopic:  '',
  selectedLength: 'medium',
  script:         null,
};

/* ── Page router ─────────────────────────────────────────── */
function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById(`page-${id}`).classList.add('active');
  window.scrollTo(0, 0);
}

/* ── Helpers ─────────────────────────────────────────────── */
function $(id) { return document.getElementById(id); }

function copyText(text, btn, label = 'Copy') {
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied');
    btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Copied`;
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg> ${label}`;
    }, 2200);
  });
}

async function readSSE(response, onStatus) {
  const reader = response.body.getReader();
  const dec = new TextDecoder();
  let buf = '', result = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });

    // Split on double newline — SSE event boundary
    const events = buf.split('\n\n');
    buf = events.pop(); // keep incomplete last chunk

    for (const event of events) {
      if (!event.trim()) continue;

      let evt = null, dataStr = '';
      for (const line of event.split('\n')) {
        if (line.startsWith('event: ')) evt = line.slice(7).trim();
        if (line.startsWith('data: ')) dataStr = line.slice(6);
      }

      if (evt && dataStr) {
        try {
          const data = JSON.parse(dataStr);
          if (evt === 'status' && onStatus) onStatus(data.message, data.step);
          if (evt === 'complete') result = data;
          if (evt === 'error') throw new Error(data.message || 'Server error');
        } catch (e) {
          if (evt === 'error') throw e;
          console.error('[sse] parse error:', e, 'raw:', dataStr.slice(0, 300));
        }
      }
    }
  }

  if (!result) throw new Error('No result from server');
  return result;
}

/* ═══════════════════════════════════════════════════════════
   LANDING PAGE
═══════════════════════════════════════════════════════════ */
$('landing-form').addEventListener('submit', e => {
  e.preventDefault();
  const url = $('channel-url').value.trim();
  const err = $('url-error');
  err.classList.add('hidden');

  if (!url) { err.textContent = 'Paste a channel URL to continue.'; err.classList.remove('hidden'); return; }
  if (!url.includes('youtube.com') && !url.includes('youtu.be')) {
    err.textContent = 'Please enter a valid YouTube channel URL.';
    err.classList.remove('hidden'); return;
  }

  state.channelUrl = url;
  showPage('analyse');
  runAnalysis();
});

/* ═══════════════════════════════════════════════════════════
   ANALYSE PAGE
═══════════════════════════════════════════════════════════ */
const TOTAL_STEPS = 4;

function setAnalyseStep(step) {
  document.querySelectorAll('.step-item').forEach(el => {
    const n = parseInt(el.dataset.step);
    el.classList.remove('active', 'done');
    if (n < step)  el.classList.add('done');
    if (n === step) el.classList.add('active');
  });
  const pct = Math.min(((step - 0.5) / TOTAL_STEPS) * 100, 100);
  $('progress-fill').style.width = `${pct}%`;
}

async function runAnalysis() {
  setAnalyseStep(1);
  $('analyse-error').classList.add('hidden');

  try {
    const res = await fetch(`${API}/api/analyse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channelUrl: state.channelUrl }),
    });
    if (!res.ok) throw new Error(`Server error ${res.status}`);

    const data = await readSSE(res, (msg, step) => {
      const el = $('analyse-status');
      if (el) el.textContent = msg;
      if (step) setAnalyseStep(step);
    });

    setAnalyseStep(TOTAL_STEPS + 1);
    $('progress-fill').style.width = '100%';

    state.analysis = data.analysis;
    renderTopicsPage(data.analysis);
    showPage('topics');
  } catch (err) {
    $('analyse-error-msg').textContent = err.message;
    $('analyse-error').classList.remove('hidden');
  }
}

$('analyse-back-btn').addEventListener('click', () => showPage('landing'));

/* ═══════════════════════════════════════════════════════════
   TOPICS PAGE
═══════════════════════════════════════════════════════════ */
function renderTopicsPage(a) {
  $('metrics-row').innerHTML = [
    { label: 'Niche',       val: a.niche            },
    { label: 'Tone',        val: a.tone             },
    { label: 'Avg. length', val: a.avg_video_length },
  ].map(m => `
    <div class="metric-cell">
      <div class="metric-lbl">${m.label}</div>
      <div class="metric-val">${m.val || '—'}</div>
    </div>`).join('');

  $('voice-summary').textContent = a.voice_summary || '';

  const tagColors = ['tag-0','tag-1','tag-2','tag-3'];
  $('tag-row').innerHTML = (a.style_tags || [])
    .map((t, i) => `<span class="tag ${tagColors[i % 4]}">${t}</span>`)
    .join('');

  $('topics-list').innerHTML = '';
  (a.topics || []).forEach((t, i) => {
    const btn = document.createElement('button');
    btn.className = 'topic-card';
    btn.dataset.idx = i;
    btn.innerHTML = `
      <div class="topic-title-row">
        <span class="topic-name">${t.title}</span>
        ${t.trending ? '<span class="trending-badge">trending</span>' : ''}
      </div>
      <div class="topic-reason">${t.reason}</div>`;
    btn.addEventListener('click', () => {
      document.querySelectorAll('.topic-card').forEach(c => c.classList.remove('selected'));
      btn.classList.add('selected');
      state.selectedTopic = t.title;
      $('custom-topic').value = '';
      $('topics-error').classList.add('hidden');
    });
    $('topics-list').appendChild(btn);
  });

  document.querySelectorAll('.length-opt').forEach(btn => {
    btn.classList.toggle('selected', btn.dataset.length === state.selectedLength);
  });
}

$('custom-topic').addEventListener('input', function () {
  const v = this.value.trim();
  if (v) {
    document.querySelectorAll('.topic-card').forEach(c => c.classList.remove('selected'));
    state.selectedTopic = v;
  }
});

document.querySelectorAll('.length-opt').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.length-opt').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    state.selectedLength = btn.dataset.length;
  });
});

$('topics-back-btn').addEventListener('click', () => showPage('landing'));

$('generate-btn').addEventListener('click', async () => {
  const topic = $('custom-topic').value.trim() || state.selectedTopic;
  const errEl = $('topics-error');
  errEl.classList.add('hidden');

  if (!topic) { errEl.textContent = 'Choose or enter a topic first.'; errEl.classList.remove('hidden'); return; }

  state.selectedTopic = topic;
  showPage('generating');
  startGeneratingMessages();
  await runGenerate();
});

/* ═══════════════════════════════════════════════════════════
   GENERATING PAGE — rotating messages
═══════════════════════════════════════════════════════════ */
const GEN_MESSAGES = [
  'Reading your videos...',
  'Studying your vocabulary...',
  'Understanding your patterns...',
  'Assessing your tone...',
  'Mapping your energy...',
  'Learning your rhythm...',
  'Analysing your hooks...',
  'Capturing your style...',
  'Writing your script...',
  'Polishing every line...',
  'Almost there...',
];

let _genMsgInterval = null;

function startGeneratingMessages() {
  let i = 0;
  const el = $('gen-status');
  if (!el) return;
  el.style.opacity = '1';
  el.textContent = GEN_MESSAGES[0];

  _genMsgInterval = setInterval(() => {
    i = (i + 1) % GEN_MESSAGES.length;
    const el = $('gen-status');
    if (!el) { stopGeneratingMessages(); return; }
    el.style.opacity = '0';
    setTimeout(() => {
      const el = $('gen-status');
      if (el) { el.textContent = GEN_MESSAGES[i]; el.style.opacity = '1'; }
    }, 300);
  }, 2500);
}

function stopGeneratingMessages() {
  if (_genMsgInterval) {
    clearInterval(_genMsgInterval);
    _genMsgInterval = null;
  }
}

/* ═══════════════════════════════════════════════════════════
   GENERATE PAGE
═══════════════════════════════════════════════════════════ */
async function runGenerate() {
  try {
    const res = await fetch(`${API}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic:    state.selectedTopic,
        length:   state.selectedLength,
        analysis: state.analysis,
      }),
    });
    if (!res.ok) throw new Error(`Server error ${res.status}`);

    const data = await readSSE(res, (msg) => {
      const el = $('gen-status');
      if (el) el.textContent = msg;
    });

    stopGeneratingMessages();
    state.script = data.script;
    renderScriptPage(data.script);
    showPage('script');
  } catch (err) {
    stopGeneratingMessages();
    console.error('[generate] error:', err);
    const el = $('gen-status');
    if (el) el.textContent = `Error: ${err.message}`;
  }
}

/* ═══════════════════════════════════════════════════════════
   SCRIPT PAGE
═══════════════════════════════════════════════════════════ */
const SECTION_ACCENT = {
  'Hook':           '#e8d5b0',
  'Intro':          '#7ab8f5',
  'Main Content':   '#a8e880',
  'Key Takeaways':  '#f5a0c8',
  'Outro & CTA':    '#c9a96e',
};

function renderScriptPage(s) {
  $('script-title').textContent = s.suggested_title || state.selectedTopic;

  const wordCount = (s.sections || []).reduce((n, sec) => n + sec.content.split(/\s+/).filter(Boolean).length, 0);
  $('script-meta').innerHTML = `
    <div class="meta-item"><div class="meta-dot" style="background:var(--accent-2)"></div>${wordCount.toLocaleString()} words</div>
    <div class="meta-item"><div class="meta-dot"></div>${(s.sections || []).length} sections</div>
    ${s.thumbnail_hook ? `<div class="meta-item"><div class="meta-dot"></div>Thumbnail: <span class="thumbnail-hook">&ldquo;${s.thumbnail_hook}&rdquo;</span></div>` : ''}
  `;

  const container = $('script-sections');
  container.innerHTML = '';
  (s.sections || []).forEach((sec, i) => {
    const color = SECTION_ACCENT[sec.name] || 'var(--accent)';
    const isFirst = i === 0;

    const wrap = document.createElement('div');
    wrap.className = `script-section fade-up`;
    wrap.style.animationDelay = `${i * 0.06}s`;

    const toggle = document.createElement('button');
    toggle.className = `section-toggle${isFirst ? ' open' : ''}`;
    toggle.innerHTML = `
      <div class="toggle-left">
        <div class="section-dot" style="background:${color}"></div>
        <span class="section-name">${sec.name}</span>
        <span class="section-lbl">${sec.label || ''}</span>
      </div>
      <svg class="chevron${isFirst ? ' open' : ''}" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"/>
      </svg>`;

    const body = document.createElement('div');
    body.className = `section-body${isFirst ? ' open' : ''}`;
    body.innerHTML = `
      <pre>${sec.content}</pre>
      <button class="copy-section-btn">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
        Copy section
      </button>`;

    toggle.addEventListener('click', () => {
      const open = body.classList.toggle('open');
      toggle.classList.toggle('open', open);
      toggle.querySelector('.chevron').classList.toggle('open', open);
    });

    body.querySelector('.copy-section-btn').addEventListener('click', function () {
      copyText(sec.content, this, 'Copy section');
    });

    wrap.appendChild(toggle);
    wrap.appendChild(body);
    container.appendChild(wrap);
  });
}

$('script-back-btn').addEventListener('click', () => showPage('topics'));

$('copy-all-btn').addEventListener('click', function () {
  if (!state.script) return;
  const full = (state.script.sections || [])
    .map(s => `— ${s.name.toUpperCase()} —\n\n${s.content}`)
    .join('\n\n\n');
  const text = `${state.script.suggested_title}\n\n${full}`;
  copyText(text, this, 'Copy all');
});

$('reset-btn').addEventListener('click',      () => { resetState(); showPage('landing'); });
$('start-over-btn').addEventListener('click', () => { resetState(); showPage('landing'); });

function resetState() {
  state.channelUrl     = '';
  state.analysis       = null;
  state.selectedTopic  = '';
  state.selectedLength = 'medium';
  state.script         = null;
  $('channel-url').value  = '';
  $('custom-topic').value = '';
  $('url-error').classList.add('hidden');
  const analyseStatus = $('analyse-status');
  if (analyseStatus) analyseStatus.textContent = 'Starting analysis...';
  const progressFill = $('progress-fill');
  if (progressFill) progressFill.style.width = '0%';
  document.querySelectorAll('.step-item').forEach(el => el.classList.remove('active','done'));
  stopGeneratingMessages();
}
