/* ── CrawlIQ tools-shared.js — shared utility for all standalone tool pages ── */

const API = 'https://bhavani7-seo-project.hf.space';

/* ── Auth ── */
function _token() { return localStorage.getItem('ciq_token') || ''; }
function _hdrs(extra) {
  const h = { 'Content-Type': 'application/json' };
  const t = _token();
  if (t) h['Authorization'] = 'Bearer ' + t;
  return Object.assign(h, extra || {});
}

async function apiFetch(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign(_hdrs(), opts.headers || {});
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    let detail = '';
    try { const d = await res.clone().json(); detail = d.detail || d.message || ''; } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}
async function apiGet(path)       { return apiFetch(path); }
async function apiPost(path, body){ return apiFetch(path, { method:'POST', body: JSON.stringify(body) }); }

/* ── URL persistence ── */
function getLastUrl() { return localStorage.getItem('ciq_last_url') || ''; }
function setLastUrl(url) { if (url) localStorage.setItem('ciq_last_url', url); }

/* ── Backend crawl state ── */
async function getBackendStatus() {
  try { return await apiGet('/crawl-status'); } catch { return null; }
}
async function backendHasData() {
  const st = await getBackendStatus();
  return st && st.done && (st.pages_crawled || 0) > 0;
}

/* ── Toast ── */
function toolToast(msg, type, dur) {
  dur = dur || 3500;
  let t = document.getElementById('ciq-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'ciq-toast';
    t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1c1f2a;border:1px solid #464554;color:#dfe2f1;padding:10px 18px;border-radius:8px;font-size:12px;z-index:9999;pointer-events:none;transition:opacity .3s;font-family:monospace';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.borderColor = type === 'error' ? '#ff6b6b' : type === 'ok' ? '#10B981' : '#464554';
  t.style.opacity = '1';
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.style.opacity = '0', dur);
}

/* ── Cold-start banner (HF wakeup) ── */
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

/* ── Backend health probe (handles HF Space cold start) ── */
async function probeBackend() {
  try {
    const r = await fetch(API + '/healthz', { cache: 'no-store' });
    const ct = r.headers.get('content-type') || '';
    return r.ok && ct.includes('application/json');
  } catch { return false; }
}

const _COLD_MSGS = [
  'Warming up the backend — HuggingFace free tier sleeps after 48 h…',
  'Loading AI models — Groq, Gemini, Claude and OpenAI initializing…',
  'Almost ready — SEO engine is warming up…',
];

/* Polls /healthz every 4 s until it returns JSON. onTick(msg, remaining, elapsed). */
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

/* ── Sidebar "active" link highlight ── */
function highlightSidebarLink() {
  const path = window.location.pathname.split('/').pop();
  document.querySelectorAll('.sb-link').forEach(a => {
    const href = (a.getAttribute('href') || '').split('/').pop();
    a.classList.toggle('active', href === path);
  });
}

/* ── Pre-fill URL input from localStorage ── */
function prefillUrl(inputId, transform) {
  const url = getLastUrl();
  if (!url) return;
  const inp = document.getElementById(inputId);
  if (!inp || inp.value) return;
  inp.value = transform ? transform(url) : url;
}

/* Extract clean domain from URL */
function domainOf(url) {
  try {
    const u = new URL(url.startsWith('http') ? url : 'https://' + url);
    return u.hostname.replace(/^www\./, '');
  } catch { return url; }
}

/* ── Poll crawl status with timeout ── */
async function pollCrawlStatus(onProgress, timeoutMs) {
  timeoutMs = timeoutMs || 180000;
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const iv = setInterval(async () => {
      if (Date.now() - start > timeoutMs) {
        clearInterval(iv);
        reject(new Error('Crawl timeout'));
        return;
      }
      const st = await getBackendStatus().catch(() => null);
      if (!st) return;
      if (onProgress) onProgress(st);
      if (st.done) { clearInterval(iv); resolve(st); }
      else if (st.error) { clearInterval(iv); reject(new Error(st.error)); }
    }, 2500);
  });
}

document.addEventListener('DOMContentLoaded', highlightSidebarLink);
