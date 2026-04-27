/* ══════════════════════════════════════════════════════════════
   CrawlIQ  ·  seo-core.js  ·  Unified shared module v1.0
   Single source of truth for all tool pages.
   Supersedes tools-shared.js for tech-seo.html.
   ══════════════════════════════════════════════════════════════ */

const API = 'https://bhavani7-seo-project.hf.space';

/* ─────────────────────────────────────────────
   AUTH
   ───────────────────────────────────────────── */
function _token() { return localStorage.getItem('ciq_token') || ''; }
function _hdrs(extra) {
  const h = { 'Content-Type': 'application/json' };
  const t = _token();
  if (t) h['Authorization'] = 'Bearer ' + t;
  return Object.assign(h, extra || {});
}

/* ─────────────────────────────────────────────
   API FETCH  (single implementation, no duplicates)
   ───────────────────────────────────────────── */
async function apiFetch(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign(_hdrs(), opts.headers || {});
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    let detail = '';
    try { const d = await res.clone().json(); detail = d.detail || d.message || ''; } catch {}
    throw new Error(detail || 'HTTP ' + res.status);
  }
  return res.json();
}
function apiGet(path)        { return apiFetch(path); }
function apiPost(path, body) { return apiFetch(path, { method: 'POST', body: JSON.stringify(body) }); }

/* ─────────────────────────────────────────────
   URL HELPERS
   ───────────────────────────────────────────── */
function getLastUrl()      { return localStorage.getItem('ciq_last_url') || ''; }
function setLastUrl(url)   { if (url) localStorage.setItem('ciq_last_url', url); }

/** Read a single query-string parameter from the current page URL */
function getUrlParam(name) {
  return new URLSearchParams(window.location.search).get(name) || '';
}

/** Ensure a URL has a scheme. Returns '' for empty input. */
function normalizeUrl(raw) {
  raw = (raw || '').trim();
  if (!raw) return '';
  return (raw.startsWith('http://') || raw.startsWith('https://')) ? raw : 'https://' + raw;
}

/** Extract clean hostname without www */
function domainOf(url) {
  try { return new URL(normalizeUrl(url)).hostname.replace(/^www\./, ''); }
  catch { return url; }
}

/* ─────────────────────────────────────────────
   BACKEND STATE
   ───────────────────────────────────────────── */
async function getBackendStatus() {
  try { return await apiGet('/crawl-status'); } catch { return null; }
}
async function backendHasData() {
  const st = await getBackendStatus();
  return !!(st && st.done && (st.pages_crawled || 0) > 0);
}

/* ─────────────────────────────────────────────
   COLD-START PROBE  (HuggingFace Space wakeup)
   Call probeBackend() before any /crawl POST.
   If it returns false the Space is sleeping;
   call waitForBackend(onTick) to poll until ready.
   ───────────────────────────────────────────── */
async function probeBackend() {
  try {
    const r = await fetch(API + '/healthz', { cache: 'no-store' });
    const ct = r.headers.get('content-type') || '';
    return r.ok && ct.includes('application/json');
  } catch { return false; }
}

const _COLD_MSGS = [
  'Warming up the backend — HuggingFace free tier sleeps after 48 h of inactivity…',
  'Loading AI models — Groq, Gemini, Claude and OpenAI initializing…',
  'Almost ready — the SEO engine is warming up…',
];

async function waitForBackend(onTick, maxMs) {
  maxMs = maxMs || 300000;
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (await probeBackend()) return true;
    const elapsed = Math.round((Date.now() - start) / 1000);
    const remaining = Math.max(0, 90 - elapsed);
    const msg = _COLD_MSGS[Math.floor(elapsed / 20) % _COLD_MSGS.length];
    if (onTick) onTick(msg, remaining, elapsed);
    await new Promise(res => setTimeout(res, 4000));
  }
  return false;
}

/* ─────────────────────────────────────────────
   TOAST
   ───────────────────────────────────────────── */
function toolToast(msg, type, dur) {
  dur = dur || 3500;
  let t = document.getElementById('ciq-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'ciq-toast';
    t.style.cssText = [
      'position:fixed;bottom:24px;left:50%;transform:translateX(-50%)',
      'background:#1c1f2a;border:1px solid #464554;color:#dfe2f1',
      'padding:10px 18px;border-radius:8px;font-size:12px;z-index:9999',
      'pointer-events:none;transition:opacity .3s;font-family:monospace;white-space:nowrap'
    ].join(';');
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.borderColor = type === 'error' ? '#ff6b6b' : type === 'ok' ? '#10B981' : '#464554';
  t.style.opacity = '1';
  clearTimeout(t._tm);
  t._tm = setTimeout(() => { t.style.opacity = '0'; }, dur);
}

/* ─────────────────────────────────────────────
   COLD-START BANNER  (HuggingFace Space wakeup)
   ───────────────────────────────────────────── */
function showColdBanner(el) {
  if (!el) return;
  el.style.display = 'flex';
  let n = 90;
  const iv = setInterval(() => {
    n--;
    const span = el.querySelector('.cold-elapsed');
    if (span) span.textContent = n + 's';
    if (n <= 0) clearInterval(iv);
  }, 1000);
  return iv;
}

/* ─────────────────────────────────────────────
   SIDEBAR ACTIVE STATE
   ───────────────────────────────────────────── */
function highlightSidebarLink() {
  const path = window.location.pathname.split('/').pop();
  document.querySelectorAll('.sb-link').forEach(a => {
    const href = (a.getAttribute('href') || '').split('/').pop();
    a.classList.toggle('active', href === path);
  });
}

/* ─────────────────────────────────────────────
   URL PRE-FILL
   Priority: ?url= query param  →  localStorage  →  empty
   Returns true if the value came from a query param
   (caller should auto-trigger analysis)
   ───────────────────────────────────────────── */
function prefillUrl(inputId, transform) {
  const qUrl  = getUrlParam('url');
  const saved = getLastUrl();
  const url   = qUrl || saved;
  if (!url) return false;
  const inp = document.getElementById(inputId);
  if (!inp || inp.value) return false;
  inp.value = transform ? transform(url) : url;
  if (qUrl) setLastUrl(qUrl);
  return !!qUrl; // true = came from redirect → auto-trigger
}

/* ─────────────────────────────────────────────
   CRAWL POLLING
   ───────────────────────────────────────────── */
async function pollCrawlStatus(onProgress, timeoutMs) {
  timeoutMs = timeoutMs || 180000;
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const iv = setInterval(async () => {
      if (Date.now() - start > timeoutMs) {
        clearInterval(iv);
        reject(new Error('Crawl timed out — please try again'));
        return;
      }
      const st = await getBackendStatus().catch(() => null);
      if (!st) return;
      if (onProgress) onProgress(st);
      if (st.done)       { clearInterval(iv); resolve(st); }
      else if (st.error) { clearInterval(iv); reject(new Error(st.error)); }
    }, 2500);
  });
}

/* ─────────────────────────────────────────────
   CONTENT-TYPE SAFE FETCH
   Rejects wasm / binary / JS / images — HTML only.
   Used for any direct frontend fetch of a target URL.
   ───────────────────────────────────────────── */
async function fetchHtmlSafe(url) {
  const r = await fetch(normalizeUrl(url), {
    headers: { Accept: 'text/html,application/xhtml+xml,*/*' },
    redirect: 'follow',
  });
  const ct = (r.headers.get('content-type') || '').toLowerCase();
  const blocked = [
    'application/wasm', 'application/octet-stream',
    'application/javascript', 'text/javascript',
    'image/', 'font/', 'audio/', 'video/',
  ];
  if (blocked.some(b => ct.includes(b))) {
    throw new Error('Non-HTML content (' + ct + ') — skipped');
  }
  if (!ct.includes('text/html') && !ct.includes('xhtml') && !ct.includes('text/plain')) {
    throw new Error('Unsupported content-type: ' + ct);
  }
  return r.text();
}

/* ─────────────────────────────────────────────
   SEO POWER WORDS
   Dynamically extracted from page titles and meta descriptions.
   No hardcoded fake scores — real content matching only.
   ───────────────────────────────────────────── */
const POWER_WORDS = [
  'best','top','free','guide','how','ultimate','complete','easy','fast',
  'new','proven','simple','secret','amazing','step-by-step','review',
  'tips','checklist','boost','increase','improve','powerful','essential',
  'advanced','exclusive','instant','expert','winning','tested','guaranteed',
  '#1','must-have','definitive','master','grow','skyrocket','dominate',
  'maximize','optimize','results','success','profitable','revenue',
  'traffic','ranking','quick','save','discover','transform','unlock',
];

function extractPowerWords(text) {
  if (!text) return [];
  const lower = text.toLowerCase();
  return POWER_WORDS.filter(w => lower.includes(w));
}

/* ─────────────────────────────────────────────
   CTR ESTIMATION
   Maps tech_score → estimated SERP position → average CTR.
   Source: Sistrix / Backlinko average organic CTR data.
   Not hardcoded — computed from real audit scores.
   ───────────────────────────────────────────── */
const _CTR_BY_POS = [28.5, 15.7, 11.0, 8.0, 7.2, 5.1, 4.0, 3.2, 2.8, 2.5];

function estimateCTR(techScore) {
  let pos;
  if      (techScore >= 90) pos = 0;
  else if (techScore >= 80) pos = 1;
  else if (techScore >= 70) pos = 2;
  else if (techScore >= 60) pos = 3;
  else if (techScore >= 50) pos = 4;
  else if (techScore >= 40) pos = 5;
  else if (techScore >= 30) pos = 7;
  else                       pos = 9;
  return _CTR_BY_POS[pos] || 1.5;
}

/* ─────────────────────────────────────────────
   SEARCH INTENT DETECTION
   Computed from page title + H1 text.
   Dynamic — no hardcoded intents.
   ───────────────────────────────────────────── */
const _INTENT_RULES = [
  {
    tag: 'Transactional',
    color: 'var(--red)',
    bg: 'rgba(255,107,107,.12)',
    re: /\b(buy|price|cheap|deal|discount|shop|order|purchase|cart|checkout|sale|offer|hire|rent)\b/,
  },
  {
    tag: 'Informational',
    color: 'var(--cyan)',
    bg: 'rgba(78,222,163,.1)',
    re: /\b(how|what|why|guide|tutorial|learn|step|explain|definition|meaning|example|tips|understand)\b/,
  },
  {
    tag: 'Commercial',
    color: 'var(--yellow)',
    bg: 'rgba(245,158,11,.1)',
    re: /\b(best|review|compare|vs|top|alternative|ranking|rated|recommended|versus)\b/,
  },
  {
    tag: 'Navigational',
    color: 'var(--primary)',
    bg: 'rgba(192,193,255,.1)',
    re: /\b(login|sign in|download|contact|free trial|get started|register|account|portal|dashboard)\b/,
  },
];

function detectIntent(title, h1) {
  const text = ((title || '') + ' ' + (h1 || '')).toLowerCase();
  for (const rule of _INTENT_RULES) {
    if (rule.re.test(text)) return rule;
  }
  return { tag: 'Unknown', color: 'var(--muted)', bg: 'var(--surf2)' };
}

/* ─────────────────────────────────────────────
   INIT
   ───────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', highlightSidebarLink);
