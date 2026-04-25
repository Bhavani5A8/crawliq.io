/* ── CrawlIQ app.js — full frontend logic ── */

/* ═══════════════════════════════════════════
   STATE
═══════════════════════════════════════════ */
let _allPages       = [];
let _filteredPages  = [];
let _sortKey        = '';
let _sortAsc        = true;
let _selectedUrls   = new Set();
let _crawlTimer     = null;
let _geminiTimer    = null;
let _optTimer       = null;
let _crawlStart     = 0;
let _techPages      = [];
let _techIdx        = 0;
let _optRows        = [];
let _popupPages     = [];
let _popupIdx       = 0;
let _scoreChart     = null;
let _lastCompTaskId = null;
let _allTechPages   = [];
let _aiProvider     = localStorage.getItem('ciq_ai_provider') || 'groq';
let _aiKey          = localStorage.getItem('ciq_ai_key') || '';

/* ═══════════════════════════════════════════
   UTILITY
═══════════════════════════════════════════ */
const el = id => document.getElementById(id);
const show = id => { const e = el(id); if (e) e.style.display = ''; };
const hide = id => { const e = el(id); if (e) e.style.display = 'none'; };

function toast(msg, type = 'info', dur = 3500) {
  let t = el('ciq-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'ciq-toast';
    t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--surf2);border:1px solid var(--border);color:var(--text);padding:10px 18px;border-radius:8px;font-size:12px;z-index:9999;pointer-events:none;transition:opacity .3s';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.borderColor = type === 'error' ? 'var(--red)' : type === 'ok' ? 'var(--green)' : 'var(--border)';
  t.style.opacity = '1';
  clearTimeout(t._tm);
  t._tm = setTimeout(() => { t.style.opacity = '0'; }, dur);
}

function btns(enabled = {}) {
  const map = {
    gemini: 'gemini-btn', ai: 'sel-ai-btn', opt: 'opt-btn',
    popup: 'popup-btn', export: 'export-btn', tseo: 'tseo-btn',
    serp: 'serp-btn', pdf: 'pdf-btn', save: 'save-project-btn',
  };
  Object.entries(map).forEach(([k, id]) => {
    const btn = el(id);
    if (btn) btn.disabled = !enabled[k];
  });
}

async function apiPost(path, body) {
  const res = await safeAuthFetch(`${API}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return safeJson(res);
}

async function apiGet(path) {
  const res = await safeAuthFetch(`${API}${path}`);
  return safeJson(res);
}

/* ═══════════════════════════════════════════
   CRAWL
═══════════════════════════════════════════ */
async function startCrawl() {
  const raw = (el('url-input')?.value || '').trim();
  if (!raw) { toast('Enter a URL first', 'error'); return; }
  let url = raw;
  if (!url.startsWith('http')) url = 'https://' + url;

  clearInterval(_crawlTimer);
  clearInterval(_geminiTimer);
  clearInterval(_optTimer);
  _allPages = []; _filteredPages = []; _selectedUrls.clear();
  btns({});
  el('results-body').innerHTML = '<tr><td colspan="14"><div class="empty-state"><div class="icon">🔍</div><p>Starting crawl…</p></div></td></tr>';
  ['opt-panel','tseo-panel','ibreak','summary','progress-wrap','sel-toolbar'].forEach(id => hide(id));
  ['cbar','progress-wrap'].forEach(id => show(id));
  el('ctxt').textContent = 'Crawling…';
  el('cspin').style.display = '';
  _crawlStart = Date.now();

  localStorage.setItem('ciq_last_url', url);
  try {
    await apiPost('/crawl', { url, max_pages: 100 });
  } catch (e) {
    toast('Crawl failed: ' + e.message, 'error');
    hide('cbar');
    return;
  }

  _crawlTimer = setInterval(_pollCrawl, 2000);
}

async function _pollCrawl() {
  let st;
  try { st = await apiGet('/crawl-status'); } catch (e) {
    if (e?.isColdStart) el('ctxt').textContent = 'Backend waking up — please wait…';
    return;
  }

  const elapsed = Math.round((Date.now() - _crawlStart) / 1000);
  el('prog-elapsed').textContent = elapsed + 's';

  if (st.pages_crawled !== undefined) {
    const total = st.total_pages || st.pages_crawled || 1;
    const pct   = Math.min(100, Math.round((st.pages_crawled / total) * 100));
    el('prog-fill').style.width = pct + '%';
    el('prog-text').textContent = `${st.pages_crawled} / ${total} pages crawled`;
    el('ctxt').textContent = st.current_url
      ? `Crawling ${st.current_url.slice(0, 60)}…`
      : `${st.pages_crawled} pages crawled…`;
  }

  if (st.done || (!st.running && !st.error && st.pages_crawled > 0)) {
    clearInterval(_crawlTimer);
    await _loadResults();
  } else if (st.error) {
    clearInterval(_crawlTimer);
    hide('cbar');
    toast('Crawl error: ' + st.error, 'error');
  }
}

async function _loadResults() {
  let data;
  try { data = await apiGet('/results'); } catch (e) {
    toast('Failed to load results: ' + e.message, 'error');
    hide('cbar');
    return;
  }

  _allPages = data.results || [];
  hide('cbar');
  show('progress-wrap');

  _updateSummaryCards();
  _renderIssueBreakdown();
  renderResults();

  btns({ gemini: true, opt: true, popup: true, export: true, tseo: true, serp: true, pdf: true, save: true });
  show('ibreak');
  show('summary');
  toast(`Crawl complete — ${_allPages.length} pages`, 'ok');
}

/* ═══════════════════════════════════════════
   RESULTS TABLE
═══════════════════════════════════════════ */
function _updateSummaryCards() {
  const pages  = _allPages;
  const total  = pages.length;
  const issues = pages.filter(p => (p.issues || []).length).length;
  const ok     = pages.filter(p => !(p.issues || []).length).length;
  const high   = pages.filter(p => p.priority === 'High').length;
  const med    = pages.filter(p => p.priority === 'Medium').length;

  el('s-total').textContent  = total;
  el('s-issues').textContent = issues;
  el('s-ok').textContent     = ok;
  el('s-high').textContent   = high;
  el('s-high-bar').textContent = high;
  el('s-med').textContent    = med;
  el('s-low').textContent    = pages.filter(p => p.priority === 'Low').length;
  el('s-ok-sub').textContent = `${ok} of ${total} clean`;
  el('s-high-sub').textContent = high ? `${high} high-priority` : '';
  el('s-med-sub').textContent  = med  ? `${med} medium-priority` : '';
}

function _renderIssueBreakdown() {
  const counts = {};
  _allPages.forEach(p => (p.issues || []).forEach(iss => { counts[iss] = (counts[iss] || 0) + 1; }));
  const grid = el('igrid');
  if (!grid) return;
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 12);
  grid.innerHTML = sorted.map(([iss, n]) =>
    `<div class="icard"><span class="icard-n">${n}</span><span class="icard-l">${esc(iss)}</span></div>`
  ).join('');
}

function renderResults() {
  let pages = [..._allPages];

  const fIssues   = el('f-issues')?.checked;
  const fPriority = el('f-priority')?.value || '';
  const fSelected = el('f-selected')?.checked;

  if (fIssues)   pages = pages.filter(p => (p.issues || []).length);
  if (fPriority) pages = pages.filter(p => p.priority === fPriority);
  if (fSelected) pages = pages.filter(p => _selectedUrls.has(p.url));

  if (_sortKey) {
    pages.sort((a, b) => {
      let av = a[_sortKey] ?? '', bv = b[_sortKey] ?? '';
      if (_sortKey === 'issues') { av = (a.issues || []).length; bv = (b.issues || []).length; }
      if (typeof av === 'string') av = av.toLowerCase();
      if (typeof bv === 'string') bv = bv.toLowerCase();
      return _sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }

  _filteredPages = pages;
  el('page-count').textContent = pages.length;

  const tbody = el('results-body');
  if (!pages.length) {
    tbody.innerHTML = '<tr><td colspan="14"><div class="empty-state"><div class="icon">🔍</div><p>No pages match the current filters.</p></div></td></tr>';
    return;
  }

  tbody.innerHTML = pages.map(p => {
    const checked  = _selectedUrls.has(p.url) ? 'checked' : '';
    const issues   = (p.issues || []).join(', ');
    const aiField  = p.gemini_fields ? '✓' : '';
    const priority = p.priority || '';
    const pClass   = priority === 'High' ? 'style="color:var(--red)"' : priority === 'Medium' ? 'style="color:var(--yellow)"' : '';
    const status   = p.status_code || '';
    const sClass   = status >= 400 ? 'style="color:var(--red)"' : status >= 300 ? 'style="color:var(--yellow)"' : '';
    const score    = p.seo_score != null ? p.seo_score : (p.ranking?.total_score != null ? p.ranking.total_score : '—');
    const kws      = (p.keywords_scored || []).slice(0, 2).map(k => k.keyword || k).join(', ');
    const comp     = p.ranking?.competition_label || '';
    return `<tr>
      <td class="check-cell"><input type="checkbox" ${checked} onchange="toggleRow(this,'${esc(p.url)}')"/></td>
      <td title="${esc(p.url)}" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><a href="${esc(p.url)}" target="_blank" style="color:var(--cyan);text-decoration:none">${esc(shortUrl(p.url))}</a></td>
      <td ${sClass}>${status}</td>
      <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p.title||'')}">${esc(p.title||'—')}</td>
      <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p.meta_description||'')}">${p.meta_description ? '✓' : '✗'}</td>
      <td>${p.h1 ? '✓' : '✗'}</td>
      <td style="color:${(p.issues||[]).length ? 'var(--red)' : 'var(--green)'}">${(p.issues||[]).length}</td>
      <td ${pClass}>${priority}</td>
      <td>${score}</td>
      <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(kws)}</td>
      <td>${esc(comp)}</td>
      <td style="color:var(--cyan)">${aiField}</td>
      <td>${_issueStatusBadge(p)}</td>
      <td><button class="btn btn-outline btn-sm" onclick="openPageDetail('${esc(p.url)}')" style="font-size:10px;padding:3px 8px">View</button></td>
    </tr>`;
  }).join('');
}

function _issueStatusBadge(p) {
  const s = p._issue_status || 'open';
  const colors = { open: 'var(--red)', in_progress: 'var(--yellow)', resolved: 'var(--green)' };
  return `<span style="font-size:9px;color:${colors[s]||'var(--dim)'}">${s}</span>`;
}

function shortUrl(url) {
  try { const u = new URL(url); return u.hostname + u.pathname.slice(0, 40); } catch { return url.slice(0, 50); }
}

function esc(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function applyFilters() { renderResults(); }

function sortBy(key) {
  if (_sortKey === key) _sortAsc = !_sortAsc;
  else { _sortKey = key; _sortAsc = true; }
  renderResults();
}

function toggleSelectAll(cb) {
  _filteredPages.forEach(p => {
    if (cb.checked) _selectedUrls.add(p.url);
    else _selectedUrls.delete(p.url);
  });
  renderResults();
  _updateSelToolbar();
}

function toggleRow(cb, url) {
  if (cb.checked) _selectedUrls.add(url);
  else _selectedUrls.delete(url);
  _updateSelToolbar();
}

function _updateSelToolbar() {
  const n = _selectedUrls.size;
  const tb = el('sel-toolbar');
  if (tb) tb.style.display = n ? 'flex' : 'none';
  const sc = el('sel-count');
  if (sc) sc.textContent = n;
  const selBtn = el('sel-ai-btn');
  if (selBtn) { selBtn.style.display = n ? '' : 'none'; selBtn.disabled = !n; }
}

function clearSelection() {
  _selectedUrls.clear();
  renderResults();
  _updateSelToolbar();
}

/* ═══════════════════════════════════════════
   PAGE DETAIL POPUP (inline)
═══════════════════════════════════════════ */
function openPageDetail(url) {
  _popupPages = _filteredPages.length ? _filteredPages : _allPages;
  _popupIdx   = _popupPages.findIndex(p => p.url === url);
  if (_popupIdx < 0) _popupIdx = 0;
  _renderPopup();
  el('pp-overlay').style.display = 'flex';
}

function openPopup() {
  _popupPages = _filteredPages.length ? _filteredPages : _allPages;
  _popupIdx   = 0;
  _renderPopup();
  el('pp-overlay').style.display = 'flex';
}

function closePopup() { hide('pp-overlay'); }

function navPage(dir) {
  _popupIdx = Math.max(0, Math.min(_popupPages.length - 1, _popupIdx + dir));
  _renderPopup();
}

function openExportFromPopup() { openExportModal(); }

function _renderPopup() {
  const p = _popupPages[_popupIdx];
  if (!p) return;
  el('nav-prev').disabled = _popupIdx === 0;
  el('nav-next').disabled = _popupIdx === _popupPages.length - 1;
  el('nav-cur').textContent = _popupIdx + 1;
  el('nav-tot').textContent = _popupPages.length;
  el('pp-url').textContent  = shortUrl(p.url);
  el('pp-url').title        = p.url;
  el('pp-title').textContent = 'Page Audit';
  const gf     = p.gemini_fields || {};
  const score  = p.seo_score ?? (p.ranking?.total_score ?? '—');
  const pColor = p.priority === 'High' ? 'var(--red)' : p.priority === 'Medium' ? 'var(--yellow)' : 'var(--green)';
  el('pp-rank').innerHTML = `
    <div style="display:flex;gap:12px;flex-wrap:wrap;padding:10px;background:var(--surf2);border-radius:8px;margin-bottom:10px">
      ${_mCard('Score', score, 'var(--cyan)')} ${_mCard('Priority', p.priority||'—', pColor)}
      ${_mCard('Status', p.status_code||'—', p.status_code>=400?'var(--red)':'var(--green)')}
      ${_mCard('Issues', (p.issues||[]).length, (p.issues||[]).length?'var(--red)':'var(--green)')}
    </div>`;
  el('pp-kw').innerHTML = (p.keywords_scored||[]).length
    ? `<div style="font-size:10px;color:var(--dim);margin-bottom:4px">KEYWORDS</div><div style="display:flex;gap:6px;flex-wrap:wrap">${(p.keywords_scored||[]).slice(0,5).map(k=>`<span style="background:var(--surf2);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px;font-family:var(--mono)">${esc(k.keyword||k)}</span>`).join('')}</div>` : '';
  el('pp-meta').innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      <a href="${esc(p.url)}" target="_blank" style="color:var(--cyan);font-size:11px;font-family:var(--mono);word-break:break-all">${esc(p.url)}</a>
    </div>`;
  const SEO_FIELDS = [
    ['Title', p.title, !p.title ? 'Missing' : p.title.length > 60 ? 'Too Long' : 'OK', 'Affects CTR and rankings', gf.fix_title || ''],
    ['Meta Description', p.meta_description, !p.meta_description ? 'Missing' : p.meta_description.length > 160 ? 'Too Long' : 'OK', 'Affects click-through rate', gf.fix_meta || ''],
    ['H1', p.h1, !p.h1 ? 'Missing' : 'OK', 'Primary page heading for crawlers', gf.fix_h1 || ''],
    ['Canonical', p.canonical, !p.canonical ? 'Missing' : 'Set', 'Prevents duplicate content', ''],
    ['Word Count', p.word_count, (p.word_count||0) < 300 ? 'Thin Content' : 'OK', 'Content depth signal', ''],
  ];
  el('pp-tbody').innerHTML = SEO_FIELDS.map(([field, val, issue, why, fix]) => `<tr>
    <td style="padding:6px 10px;font-weight:600">${field}</td>
    <td style="padding:6px 10px;font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(String(val||''))}">${esc(String(val||'—'))}</td>
    <td style="padding:6px 10px;color:${issue==='OK'||issue==='Set'?'var(--green)':'var(--red)'}">${esc(issue)}</td>
    <td style="padding:6px 10px;font-size:11px;color:var(--dim)">${esc(why)}</td>
    <td style="padding:6px 10px;font-size:11px"></td>
    <td style="padding:6px 10px;font-size:11px;color:var(--cyan)">${esc(fix)}</td>
    <td style="padding:6px 10px"></td>
    <td style="padding:6px 10px"></td>
  </tr>`).join('');
}

function _mCard(label, val, color) {
  return `<div style="text-align:center;min-width:60px"><div style="font-size:16px;font-weight:700;color:${color}">${esc(String(val))}</div><div style="font-size:9px;color:var(--dim)">${label}</div></div>`;
}

function _pRow(label, val) {
  if (!val && val !== 0) return '';
  return `<div style="display:flex;gap:8px;margin-bottom:4px;font-size:11px"><span style="color:var(--dim);min-width:120px">${label}</span><span style="font-family:var(--mono);word-break:break-all">${esc(String(val))}</span></div>`;
}

/* ═══════════════════════════════════════════
   AI SETUP
═══════════════════════════════════════════ */
function openAiSetup() { el('ai-overlay').style.display = 'flex'; _renderAiCards(); }
function closeAiSetup() { hide('ai-overlay'); }

function aiOverlayClick(e) { if (e.target === el('ai-overlay')) closeAiSetup(); }

function _renderAiCards() {
  document.querySelectorAll('.ai-provider-card').forEach(card => {
    card.classList.toggle('selected', card.dataset.provider === _aiProvider);
  });
  const keyBox = el('ai-key-input');
  if (keyBox) { keyBox.value = _aiKey; keyBox.type = 'password'; }
  const noKey = _aiProvider === 'ollama' || _aiProvider === 'rules';
  const keyGroup  = el('ai-key-group');
  const noKeyNote = el('ai-no-key-notice');
  if (keyGroup)  keyGroup.style.display  = noKey ? 'none' : '';
  if (noKeyNote) noKeyNote.style.display = noKey ? '' : 'none';
  const curEl = el('ai-cur-provider');
  if (curEl) curEl.textContent = _aiProvider.charAt(0).toUpperCase() + _aiProvider.slice(1);
}

function aiOnKeyInput(val) { _aiKey = val; }

function aiSelectProvider(p) {
  _aiProvider = p;
  _renderAiCards();
}

function aiToggleKeyVisible() {
  const inp = el('ai-key-input');
  if (inp) inp.type = inp.type === 'password' ? 'text' : 'password';
}

async function aiTestConnection() {
  const btn = el('ai-test-btn');
  if (btn) btn.textContent = 'Testing…';
  try {
    const d = await apiGet('/gemini-health');
    toast(d.configured ? `${d.provider} connected ✓` : 'Not configured', d.configured ? 'ok' : 'error');
  } catch (e) { toast('Test failed: ' + e.message, 'error'); }
  finally { if (btn) btn.textContent = 'Test Connection'; }
}

async function aiApplyKey() {
  const key = el('ai-key-input')?.value.trim() || '';
  try {
    await apiPost('/set-api-key', { provider: _aiProvider, api_key: key });
    _aiKey = key;
    localStorage.setItem('ciq_ai_provider', _aiProvider);
    localStorage.setItem('ciq_ai_key', key);
    _refreshAiPill();
    closeAiSetup();
    toast('AI provider saved', 'ok');
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

function _refreshAiPill() {
  const pillText = el('gemini-pill');
  if (pillText) pillText.textContent = _aiProvider.charAt(0).toUpperCase() + _aiProvider.slice(1);
}

async function checkGemini() {
  try {
    const d = await apiGet('/gemini-health');
    _aiProvider = d.provider || _aiProvider;
    _refreshAiPill();
    const cfg = d.configured;
    const dot = document.querySelector('.ai-pill-dot');
    if (dot) dot.style.background = cfg ? 'var(--green)' : 'var(--yellow)';
    const pillText = el('gemini-pill');
    if (pillText) pillText.textContent = d.configured ? _aiProvider : 'No AI key';
  } catch { /* non-fatal */ }
}

/* ═══════════════════════════════════════════
   AI ANALYSIS
═══════════════════════════════════════════ */
async function startGeminiAll() {
  try {
    await apiPost('/analyze-gemini', {});
    show('gbar');
    el('gtxt').textContent = 'AI analysing…';
    _geminiTimer = setInterval(_pollGemini, 2500);
  } catch (e) {
    if (e.message.includes('key') || e.message.includes('configured')) openAiSetup();
    else toast('AI error: ' + e.message, 'error');
  }
}

async function startGeminiSelected() {
  if (!_selectedUrls.size) { toast('Select pages first', 'error'); return; }
  try {
    await apiPost('/analyze-selected', { urls: [..._selectedUrls] });
    show('gbar');
    el('gtxt').textContent = 'AI analysing selected…';
    _geminiTimer = setInterval(_pollGemini, 2500);
  } catch (e) {
    if (e.message.includes('key') || e.message.includes('configured')) openAiSetup();
    else toast('AI error: ' + e.message, 'error');
  }
}

async function _pollGemini() {
  let st;
  try { st = await apiGet('/gemini-status'); } catch { return; }
  el('gtxt').textContent = `AI: ${st.processed||0}/${st.total||'?'} pages…`;
  if (st.done || (!st.running && st.processed > 0)) {
    clearInterval(_geminiTimer);
    hide('gbar');
    await _loadResults();
    toast('AI analysis complete', 'ok');
  } else if (st.error) {
    clearInterval(_geminiTimer);
    hide('gbar');
    toast('AI error: ' + st.error, 'error');
  }
}

/* ═══════════════════════════════════════════
   OPTIMIZER
═══════════════════════════════════════════ */
async function startOptimizer() {
  try {
    await apiPost('/optimize', { urls: null });
    el('otxt').textContent = 'Optimizer running…';
    show('obar');
    _optTimer = setInterval(_pollOptimizer, 3000);
  } catch (e) {
    if (e.message.includes('key') || e.message.includes('configured')) openAiSetup();
    else toast('Optimizer error: ' + e.message, 'error');
  }
}

async function _pollOptimizer() {
  let st;
  try { st = await apiGet('/optimize-status'); } catch { return; }
  el('otxt').textContent = `Optimizing: ${st.processed||0}/${st.total||'?'} pages…`;
  if (st.done || (!st.running && (st.processed || 0) > 0)) {
    clearInterval(_optTimer);
    hide('obar');
    const d = await apiGet('/optimize-table');
    _optRows = d.rows || [];
    show('opt-panel');
    renderOptimizerTable();
    toast('Optimizer complete', 'ok');
  } else if (st.error) {
    clearInterval(_optTimer);
    hide('obar');
    toast('Optimizer error: ' + st.error, 'error');
  }
}

function renderOptimizerTable() {
  const field  = el('opt-field-filter')?.value || '';
  const status = el('opt-status-filter')?.value || '';
  const search = (el('opt-search')?.value || '').toLowerCase();
  let rows = _optRows;
  if (field)  rows = rows.filter(r => r.field === field);
  if (status) rows = rows.filter(r => r.status === status);
  if (search) rows = rows.filter(r => (r.url||'').toLowerCase().includes(search) || (r.current_value||'').toLowerCase().includes(search));
  el('opt-count-label').textContent = rows.length + ' rows';
  el('opt-row-count').textContent = rows.length + ' rows';
  el('opt-tbody').innerHTML = rows.slice(0, 200).map(r => `<tr>
    <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:10px" title="${esc(r.url)}">${esc(shortUrl(r.url))}</td>
    <td>${esc(r.field)}</td>
    <td style="color:var(--yellow)">${esc(r.status)}</td>
    <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.current_value)}">${esc(r.current_value||'—')}</td>
    <td style="max-width:220px;color:var(--cyan)" title="${esc(r.optimized_value)}">${esc(r.optimized_value||'—')}</td>
    <td style="font-size:10px;color:var(--dim)">${esc(r.seo_logic||'')}</td>
  </tr>`).join('');
}

function downloadOptimizer() { window.open(`${API}/export-optimizer`, '_blank'); }

/* ═══════════════════════════════════════════
   TECH SEO
═══════════════════════════════════════════ */
async function runTechSEO() {
  el('tseo-txt').textContent = '🔬 Loading Technical SEO…';
  el('tseo-spin').style.display = '';
  show('tseo-bar');
  try {
    const d = await apiGet('/technical-seo');
    _allTechPages = d.results || d.pages || d || [];
    _techPages    = _allTechPages;
    el('tseo-spin').style.display = 'none';
    el('tseo-txt').textContent = `Technical SEO: ${_allTechPages.length} pages`;
    show('tseo-panel');
    _renderTechSummary();
    renderTechSEOTable();
    toast('Technical SEO audit complete', 'ok');
  } catch (e) {
    el('tseo-spin').style.display = 'none';
    hide('tseo-bar');
    toast('Tech SEO error: ' + e.message, 'error');
  }
}

function _renderTechSummary() {
  if (!_techPages.length) return;
  const scores   = _techPages.map(p => p.tech_score || 0);
  const avg      = Math.round(scores.reduce((a,b)=>a+b,0) / scores.length);
  const grade    = avg >= 85 ? 'A' : avg >= 70 ? 'B' : avg >= 55 ? 'C' : avg >= 40 ? 'D' : 'F';
  const indexable = _techPages.filter(p => (p.indexability||'').startsWith('indexable')).length;
  const issues    = _techPages.reduce((n, p) => n + (p.critical_issues||[]).length, 0);
  const https     = _techPages.filter(p => (p.url||'').startsWith('https')).length;

  el('ts-score').textContent = avg;
  el('ts-grade').textContent = grade;
  el('ts-total').textContent = _techPages.length;
  el('ts-indexable').textContent = indexable;
  el('ts-idx-pct').textContent = Math.round(indexable/_techPages.length*100)+'%';
  el('ts-issues').textContent = issues;
  el('ts-https').textContent = https;
  el('ts-https-status').textContent = https === _techPages.length ? 'Full coverage' : `${https}/${_techPages.length}`;
}

function renderTechSEOTable() {
  const idx    = el('tseo-idx-filter')?.value || '';
  const grade  = el('tseo-grade-filter')?.value || '';
  const search = (el('tseo-search')?.value || '').toLowerCase();
  let pages = [..._allTechPages];
  if (idx)    pages = pages.filter(p => (p.indexability||'').includes(idx));
  if (grade)  pages = pages.filter(p => (p.grade||'') === grade);
  if (search) pages = pages.filter(p => (p.url||'').toLowerCase().includes(search));
  el('tseo-count-label').textContent = pages.length + ' pages';
  el('tseo-row-count').textContent   = pages.length + ' pages';
  _techPages = pages; // filtered set for detail modal navigation
  el('tseo-tbody').innerHTML = pages.slice(0, 200).map((p, i) => `<tr>
    <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:10px" title="${esc(p.url)}">${esc(shortUrl(p.url||''))}</td>
    <td>${p.status_code||'—'}</td>
    <td style="font-size:10px;color:${(p.indexability||'').startsWith('indexable')?'var(--green)':'var(--red)'}">${esc(p.indexability||'—')}</td>
    <td>${p.tech_score||'—'}</td>
    <td style="font-weight:700;color:${p.grade==='A'?'var(--green)':p.grade==='B'?'var(--cyan)':p.grade==='C'?'var(--yellow)':'var(--red)'}">${p.grade||'—'}</td>
    <td>${p.title_ok!=null?(p.title_ok?'✓':'✗'):'—'}</td>
    <td>${p.meta_ok!=null?(p.meta_ok?'✓':'✗'):'—'}</td>
    <td>${p.canonical_ok!=null?(p.canonical_ok?'✓':'✗'):'—'}</td>
    <td>${p.h1_ok!=null?(p.h1_ok?'✓':'✗'):'—'}</td>
    <td>${p.content_ok!=null?(p.content_ok?'✓':'✗'):'—'}</td>
    <td>${(p.url||'').startsWith('https')?'✓':'✗'}</td>
    <td style="color:var(--red)">${(p.critical_issues||[]).length||''}</td>
    <td><button class="btn btn-outline btn-sm" onclick="openTechDetail(${i})" style="font-size:10px;padding:3px 8px">Detail</button></td>
  </tr>`).join('');
}

function openTechDetail(idx) {
  _techIdx = idx;
  _renderTechDetail();
  el('tseo-detail-overlay').style.display = 'flex';
}

function closeTechDetail() { hide('tseo-detail-overlay'); }

function navTechDetail(dir) {
  _techIdx = Math.max(0, Math.min(_techPages.length - 1, _techIdx + dir));
  _renderTechDetail();
}

function _renderTechDetail() {
  const p = _techPages[_techIdx];
  if (!p) return;
  el('tseo-prev-btn').disabled = _techIdx === 0;
  el('tseo-next-btn').disabled = _techIdx === _techPages.length - 1;
  el('tseo-detail-cur').textContent = _techIdx + 1;
  el('tseo-detail-tot').textContent = _techPages.length;
  const urlEl = el('tseo-detail-url');
  if (urlEl) { urlEl.textContent = shortUrl(p.url||''); urlEl.title = p.url||''; }
  const scoreEl = el('tseo-detail-score');
  if (scoreEl) {
    const color = (p.tech_score||0) >= 85 ? 'var(--green)' : (p.tech_score||0) >= 70 ? 'var(--cyan)' : (p.tech_score||0) >= 55 ? 'var(--yellow)' : 'var(--red)';
    scoreEl.innerHTML = `<span style="font-size:32px;font-weight:800;color:${color}">${p.tech_score||'—'}</span><span style="font-size:14px;color:var(--dim);margin-left:6px">/ 100</span><span style="font-size:18px;font-weight:700;color:${color};margin-left:12px">${p.grade||'—'}</span>`;
  }
  const grid = el('tseo-component-grid');
  if (!grid) return;
  const checks = [
    ['Title', p.title_ok], ['Meta', p.meta_ok], ['H1', p.h1_ok],
    ['Canonical', p.canonical_ok], ['HTTPS', (p.url||'').startsWith('https')],
    ['Content', p.content_ok], ['Indexable', (p.indexability||'').startsWith('indexable')],
  ];
  grid.innerHTML = checks.map(([label, ok]) => `
    <div style="background:var(--surf2);border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center">
      <div style="font-size:20px;color:${ok?'var(--green)':'var(--red)'}">${ok?'✓':'✗'}</div>
      <div style="font-size:10px;color:var(--dim);margin-top:4px">${label}</div>
    </div>`).join('');
  if ((p.critical_issues||[]).length) {
    grid.insertAdjacentHTML('afterend', `<div style="margin-top:10px;padding:10px;background:rgba(239,68,68,.05);border:1px solid rgba(239,68,68,.2);border-radius:6px">
      <div style="font-size:10px;font-weight:700;color:var(--red);margin-bottom:6px">CRITICAL ISSUES</div>
      <ul style="margin:0 0 0 16px;font-size:11px">${(p.critical_issues||[]).map(i=>`<li style="margin-bottom:3px">${esc(i)}</li>`).join('')}</ul>
    </div>`);
  }
}

function downloadTechSEO() { window.open(`${API}/export-technical-seo`, '_blank'); }

/* ═══════════════════════════════════════════
   EXPORT
═══════════════════════════════════════════ */
function openExportModal() {
  el('eoverlay').style.display = 'flex';
  el('em-pages').textContent  = _allPages.length;
  el('em-issues').textContent = _allPages.filter(p => (p.issues||[]).length).length;
}
function closeExportModal() { hide('eoverlay'); }

function downloadExcel(type) {
  const path = type === 'popup' ? '/export-popup' : '/export';
  window.open(`${API}${path}`, '_blank');
}

async function exportPDF() {
  try {
    const brandName = localStorage.getItem('ciq_brand_name') || '';
    const path = brandName ? '/export-pdf/branded' : '/export-pdf';
    const res  = await safeAuthFetch(`${API}${path}`);
    if (!res.ok) throw new Error('PDF export failed');
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = 'crawliq-report.pdf'; a.click();
    URL.revokeObjectURL(url);
  } catch (e) { toast('PDF error: ' + e.message, 'error'); }
}

/* ═══════════════════════════════════════════
   SETTINGS
═══════════════════════════════════════════ */
async function openSettings() {
  el('settings-modal').style.display = 'flex';
  if (_ciqUser) {
    el('set-name').value = _ciqUser.name || '';
    el('set-alert-email').value = _ciqUser.alert_email || '';
    el('set-drop-threshold').value = _ciqUser.drop_threshold || 5;
    el('set-brand-name').value = localStorage.getItem('ciq_brand_name') || '';
    if (_ciqUser.api_key) el('api-key-display').value = _ciqUser.api_key;
  }
  _loadUsage();
  _loadGscStatus();
}

function closeSettings() { hide('settings-modal'); }

async function _loadUsage() {
  try {
    const d = await apiGet('/user/usage');
    const used  = d.pages_used || 0;
    const limit = d.pages_limit || 200;
    const pct   = limit > 0 ? Math.min(100, Math.round(used/limit*100)) : 0;
    el('usage-count').textContent = `${used} / ${limit < 0 ? '∞' : limit}`;
    el('usage-bar').style.width = pct + '%';
    el('usage-tier').textContent = `Tier: ${d.tier}`;
    const billing = el('billing-actions');
    if (billing) {
      billing.style.display = '';
      if (d.tier === 'free') {
        billing.innerHTML = `<button class="btn btn-green btn-sm" onclick="upgradePlan('pro')">Upgrade to Pro</button>`;
      } else {
        billing.innerHTML = `<button class="btn btn-outline btn-sm" onclick="openBillingPortal()">Manage Billing</button>`;
      }
    }
  } catch { el('usage-tier').textContent = 'Loading…'; }
}

async function saveSettings() {
  const name       = el('set-name')?.value.trim();
  const alertEmail = el('set-alert-email')?.value.trim();
  const threshold  = parseInt(el('set-drop-threshold')?.value || '5', 10);
  const brandName  = el('set-brand-name')?.value.trim();
  if (brandName) localStorage.setItem('ciq_brand_name', brandName);
  try {
    await apiPost('/user/settings', { name, alert_email: alertEmail, drop_threshold: threshold, brand_name: brandName });
    if (_ciqUser) { _ciqUser.name = name; applyAuthState(); }
    toast('Settings saved', 'ok');
    closeSettings();
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

function toggleApiKeyVisibility() {
  const inp = el('api-key-display');
  if (inp) inp.type = inp.type === 'password' ? 'text' : 'password';
}

async function rotateApiKey() {
  if (!confirm('Generate a new API key? Your old key will stop working.')) return;
  try {
    const d = await apiPost('/auth/api-key/rotate', {});
    el('api-key-display').value = d.api_key;
    if (_ciqUser) _ciqUser.api_key = d.api_key;
    toast('API key rotated', 'ok');
  } catch (e) { toast('Rotate failed: ' + e.message, 'error'); }
}

async function uploadLogo(input) {
  const file = input.files?.[0];
  if (!file) return;
  if (file.size > 512 * 1024) { toast('Logo must be under 512 KB', 'error'); return; }
  const fd = new FormData();
  fd.append('file', file);
  const res = await safeAuthFetch(`${API}/user/logo`, { method: 'POST', body: fd });
  if (res.ok) { toast('Logo uploaded', 'ok'); el('logo-status').textContent = 'Logo saved ✓'; }
  else toast('Upload failed', 'error');
}

async function upgradePlan(tier) {
  try {
    const d = await apiPost('/billing/checkout', { tier });
    if (d.checkout_url) window.open(d.checkout_url, '_blank');
  } catch (e) { toast('Billing error: ' + e.message, 'error'); }
}

async function openBillingPortal() {
  try {
    const d = await apiGet('/billing/portal');
    if (d.portal_url) window.open(d.portal_url, '_blank');
  } catch (e) { toast('Portal error: ' + e.message, 'error'); }
}

/* ═══════════════════════════════════════════
   GSC
═══════════════════════════════════════════ */
async function connectGSC() {
  try {
    const d = await apiGet('/gsc/auth-url');
    if (d.auth_url) window.location.href = d.auth_url;
  } catch (e) { toast('GSC error: ' + e.message, 'error'); }
}

async function _loadGscStatus() {
  try {
    const d = await apiGet('/gsc/status');
    if (d.connected) {
      hide('gsc-not-connected');
      show('gsc-connected-panel');
      const sites = await apiGet('/gsc/sites');
      const sel = el('gsc-site-select');
      if (sel && sites.sites) {
        sel.innerHTML = sites.sites.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
        loadGscData();
      }
    }
  } catch { /* GSC not connected */ }
}

async function loadGscData() {
  const site = el('gsc-site-select')?.value;
  if (!site) return;
  try {
    const d = await apiGet(`/gsc/data?site_url=${encodeURIComponent(site)}`);
    const sumEl = el('gsc-summary');
    if (sumEl && d.totals) {
      sumEl.innerHTML = `
        <div style="background:var(--surf2);padding:8px;border-radius:6px;text-align:center"><div style="font-size:14px;font-weight:700">${d.totals.clicks||0}</div><div style="font-size:10px;color:var(--dim)">Clicks</div></div>
        <div style="background:var(--surf2);padding:8px;border-radius:6px;text-align:center"><div style="font-size:14px;font-weight:700">${d.totals.impressions||0}</div><div style="font-size:10px;color:var(--dim)">Impressions</div></div>
        <div style="background:var(--surf2);padding:8px;border-radius:6px;text-align:center"><div style="font-size:14px;font-weight:700">${((d.totals.ctr||0)*100).toFixed(1)}%</div><div style="font-size:10px;color:var(--dim)">CTR</div></div>
        <div style="background:var(--surf2);padding:8px;border-radius:6px;text-align:center"><div style="font-size:14px;font-weight:700">${(d.totals.position||0).toFixed(1)}</div><div style="font-size:10px;color:var(--dim)">Avg Pos</div></div>
      `;
    }
    const kwEl = el('gsc-kw-table');
    if (kwEl && d.keywords) {
      kwEl.innerHTML = `<table style="width:100%;font-size:10px;border-collapse:collapse;font-family:var(--mono)">
        <thead><tr style="color:var(--dim)"><th>Keyword</th><th>Clicks</th><th>Impressions</th><th>CTR</th><th>Pos</th></tr></thead>
        <tbody>${(d.keywords||[]).slice(0,20).map(k=>`<tr style="border-bottom:1px solid var(--border)">
          <td style="padding:4px 6px">${esc(k.keyword)}</td>
          <td style="padding:4px 6px;text-align:center">${k.clicks}</td>
          <td style="padding:4px 6px;text-align:center">${k.impressions}</td>
          <td style="padding:4px 6px;text-align:center">${(k.ctr*100).toFixed(1)}%</td>
          <td style="padding:4px 6px;text-align:center">${k.position?.toFixed(1)}</td>
        </tr>`).join('')}</tbody>
      </table>`;
    }
    if (d.date_range) el('gsc-date-range').textContent = `Data: ${d.date_range}`;
  } catch (e) { toast('GSC data error: ' + e.message, 'error'); }
}

async function disconnectGSC() {
  if (!confirm('Disconnect Google Search Console?')) return;
  try {
    await safeAuthFetch(`${API}/gsc/disconnect`, { method: 'DELETE' });
    show('gsc-not-connected');
    hide('gsc-connected-panel');
    toast('GSC disconnected', 'ok');
  } catch (e) { toast('Disconnect error: ' + e.message, 'error'); }
}

/* ═══════════════════════════════════════════
   PROJECTS
═══════════════════════════════════════════ */
function openProjects() {
  el('projects-modal').style.display = 'flex';
  _loadProjects();
}
function closeProjects() { hide('projects-modal'); }

async function _loadProjects() {
  const list = el('proj-list');
  if (!list) return;
  try {
    const d = await apiGet('/projects');
    const projects = d.projects || [];
    if (!projects.length) {
      list.innerHTML = '<div style="text-align:center;color:var(--dim);font-size:11px;padding:20px">No projects yet. Create one above.</div>';
      return;
    }
    list.innerHTML = projects.map(p => `
      <div style="display:flex;align-items:center;gap:10px;padding:10px;background:var(--surf2);border:1px solid var(--border);border-radius:8px">
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:600;color:var(--text)">${esc(p.name)}</div>
          <div style="font-size:10px;color:var(--dim);font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.url||'')}</div>
          <div style="font-size:10px;color:var(--dim);margin-top:2px">${p.page_count||0} pages · Score: ${p.health_score||'—'} · ${p.last_crawl_at ? new Date(p.last_crawl_at).toLocaleDateString() : 'Never crawled'}</div>
        </div>
        <button class="btn btn-green btn-sm" onclick="loadProject(${p.id})" style="font-size:10px">Load</button>
        <button class="btn btn-outline btn-sm" onclick="deleteProject(${p.id})" style="font-size:10px;color:var(--red);border-color:var(--red)">Delete</button>
      </div>
    `).join('');
  } catch (e) { list.innerHTML = `<div style="color:var(--red);font-size:11px;padding:10px">${e.message}</div>`; }
}

async function createProject() {
  const name = el('new-proj-name')?.value.trim();
  const url  = el('new-proj-url')?.value.trim();
  if (!name) { toast('Enter a project name', 'error'); return; }
  try {
    await apiPost('/projects', { name, url: url || null });
    el('new-proj-name').value = '';
    el('new-proj-url').value = '';
    _loadProjects();
    toast('Project created', 'ok');
  } catch (e) { toast('Create failed: ' + e.message, 'error'); }
}

async function loadProject(id) {
  try {
    const d = await apiGet(`/projects/${id}`);
    window._ciqProject = d;
    closeProjects();
    toast(`Project "${d.name}" loaded`, 'ok');
    const urlInput = el('url-input');
    if (urlInput && d.url) urlInput.value = d.url;
  } catch (e) { toast('Load failed: ' + e.message, 'error'); }
}

async function deleteProject(id) {
  if (!confirm('Delete this project? This cannot be undone.')) return;
  try {
    await safeAuthFetch(`${API}/projects/${id}`, { method: 'DELETE' });
    _loadProjects();
    toast('Project deleted', 'ok');
  } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

async function saveToProject() {
  const proj = window._ciqProject;
  if (!proj) { openProjects(); toast('Load a project first', 'error'); return; }
  try {
    const d = await apiPost(`/projects/${proj.id}/snapshot`, {});
    toast(`Saved snapshot: health ${d.health_score}`, 'ok');
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

async function openScoreHistory() {
  const proj = window._ciqProject;
  if (!proj) { toast('Load a project first', 'error'); return; }
  el('score-history-modal').style.display = 'flex';
  try {
    const d = await apiGet(`/projects/${proj.id}/history`);
    _renderScoreChart(d.history || []);
  } catch (e) { toast('History error: ' + e.message, 'error'); }
}

function _renderScoreChart(history) {
  const tbody = el('score-history-tbody');
  const rows  = [...history].reverse();
  tbody.innerHTML = rows.map(h => `<tr>
    <td style="padding:6px 10px">${new Date(h.crawled_at||h.date||'').toLocaleDateString()}</td>
    <td style="padding:6px 10px;text-align:center">${h.page_count||'—'}</td>
    <td style="padding:6px 10px;text-align:center">${h.issue_count||'—'}</td>
    <td style="padding:6px 10px;text-align:center;font-weight:700;color:var(--cyan)">${h.health_score||'—'}</td>
  </tr>`).join('');

  const canvas = el('score-chart');
  if (!canvas || !window.Chart) return;
  if (_scoreChart) _scoreChart.destroy();
  _scoreChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: rows.map(h => new Date(h.crawled_at||h.date||'').toLocaleDateString()),
      datasets: [{ label: 'Health Score', data: rows.map(h => h.health_score||0), borderColor: '#22D3EE', backgroundColor: 'rgba(34,211,238,.1)', tension: 0.3, pointRadius: 4 }],
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100 } } },
  });
}

async function openCrawlDiff() {
  const proj = window._ciqProject;
  el('diff-modal').style.display = 'flex';
  el('diff-loading').style.display = '';
  hide('diff-content'); hide('diff-no-data');
  if (!proj) { el('diff-loading').textContent = 'Load a project first to compare crawls.'; return; }
  try {
    const d = await apiGet(`/projects/${proj.id}/diff`);
    hide('diff-loading');
    if (d.new_issues?.length || d.fixed_issues?.length) {
      show('diff-content');
      el('diff-new-count').textContent = d.new_issues?.length || 0;
      el('diff-fixed-count').textContent = d.fixed_issues?.length || 0;
      el('diff-new-list').innerHTML = (d.new_issues||[]).slice(0,50).map(i=>`<div style="padding:2px 0;border-bottom:1px solid var(--border)">${esc(i.url||i)}</div>`).join('');
      el('diff-fixed-list').innerHTML = (d.fixed_issues||[]).slice(0,50).map(i=>`<div style="padding:2px 0;border-bottom:1px solid var(--border)">${esc(i.url||i)}</div>`).join('');
      const sumEl = el('diff-summary');
      if (sumEl) sumEl.innerHTML = `<span style="font-size:12px;color:var(--red)">+${d.new_issues?.length||0} new</span> &nbsp;|&nbsp; <span style="font-size:12px;color:var(--green)">-${d.fixed_issues?.length||0} fixed</span>`;
    } else {
      show('diff-no-data');
      el('diff-no-data').textContent = 'No snapshots to compare yet. Save at least 2 snapshots.';
    }
  } catch (e) {
    hide('diff-loading');
    show('diff-no-data');
    el('diff-no-data').textContent = 'Diff error: ' + e.message;
  }
}

/* ═══════════════════════════════════════════
   TEAM
═══════════════════════════════════════════ */
async function openTeam() {
  const proj = window._ciqProject;
  el('team-modal').style.display = 'flex';
  el('team-proj-name').textContent = proj ? proj.name : '(no project loaded)';
  if (proj) _loadTeamMembers(proj.id);
}

async function _loadTeamMembers(projId) {
  const list = el('team-members-list');
  try {
    const d = await apiGet(`/team/members/${projId}`);
    const members = d.members || [];
    list.innerHTML = members.length
      ? members.map(m => `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
          <span style="flex:1">${esc(m.email||m.name||'')}</span>
          <span style="font-size:10px;color:var(--dim)">${esc(m.role||'')}</span>
          <button class="btn btn-outline btn-sm" onclick="removeMember(${projId},'${esc(m.email||'')}','${esc(m.user_id||m.id||'')}')" style="font-size:10px;color:var(--red);border-color:var(--red)">Remove</button>
        </div>`).join('')
      : '<div style="color:var(--dim);padding:8px 0;font-size:11px">No team members yet.</div>';
  } catch (e) { list.innerHTML = `<div style="color:var(--red);font-size:11px">${e.message}</div>`; }
}

async function inviteTeamMember() {
  const proj = window._ciqProject;
  if (!proj) { toast('Load a project first', 'error'); return; }
  const email = el('invite-email')?.value.trim();
  const role  = el('invite-role')?.value || 'viewer';
  if (!email) { toast('Enter an email address', 'error'); return; }
  const msgEl = el('invite-msg');
  try {
    await apiPost('/team/invite', { project_id: proj.id, email, role });
    if (msgEl) { msgEl.textContent = `Invite sent to ${email}`; msgEl.style.color = 'var(--green)'; msgEl.style.display = ''; }
    el('invite-email').value = '';
    _loadTeamMembers(proj.id);
  } catch (e) {
    if (msgEl) { msgEl.textContent = e.message; msgEl.style.color = 'var(--red)'; msgEl.style.display = ''; }
  }
}

async function removeMember(projId, email, userId) {
  if (!confirm(`Remove ${email} from project?`)) return;
  try {
    await safeAuthFetch(`${API}/team/member`, { method: 'DELETE', body: JSON.stringify({ project_id: projId, user_id: userId || undefined, email: email || undefined }) });
    _loadTeamMembers(projId);
    toast('Member removed', 'ok');
  } catch (e) { toast('Remove failed: ' + e.message, 'error'); }
}

/* ═══════════════════════════════════════════
   SERP INTEL
═══════════════════════════════════════════ */
function openSerpPanel() {
  hide('dash-sec'); hide('competitor-sec');
  show('serp-intel-sec');
  serpTab('pos');
  loadVisibility();
}

function closeSerpPanel() {
  hide('serp-intel-sec');
  show('dash-sec'); show('competitor-sec');
}

function serpTab(tab) {
  ['pos','diff','vis'].forEach(t => {
    const btn = el(`serp-tab-${t}`);
    const pane = el(`serp-pane-${t}`);
    if (btn)  btn.classList.toggle('serp-tab-active', t === tab);
    if (pane) pane.style.display = t === tab ? '' : 'none';
  });
}

async function runBulkSerp() {
  const rawKws  = el('serp-keywords')?.value || '';
  const domain  = el('serp-domain')?.value.trim() || '';
  const keywords = rawKws.split('\n').map(k=>k.trim()).filter(Boolean).slice(0, 20);
  if (!keywords.length || !domain) { toast('Enter keywords and domain', 'error'); return; }
  const btn = el('serp-run-btn');
  const bar = el('serp-pos-bar');
  if (btn) btn.disabled = true;
  if (bar) bar.style.display = 'flex';
  hide('serp-pos-results');
  try {
    const d = await apiPost('/serp/bulk-position', { keywords, domain });
    if (bar) bar.style.display = 'none';
    _renderSerpResults(d.results || []);
    toast(`SERP check done: ${d.total} keywords`, 'ok');
  } catch (e) {
    if (bar) bar.style.display = 'none';
    toast('SERP error: ' + e.message, 'error');
  }
  finally { if (btn) btn.disabled = false; }
}

function _renderSerpResults(results) {
  const tbody = el('serp-pos-tbody');
  if (!tbody) return;
  show('serp-pos-results');
  const sumEl = el('serp-pos-summary');
  const top10 = results.filter(r => r.in_top_10).length;
  if (sumEl) sumEl.innerHTML = `
    <div style="background:var(--surf2);padding:6px 12px;border-radius:6px;font-size:11px"><b style="color:var(--green)">${top10}</b> in top 10</div>
    <div style="background:var(--surf2);padding:6px 12px;border-radius:6px;font-size:11px"><b style="color:var(--text)">${results.length}</b> checked</div>
  `;
  tbody.innerHTML = results.map(r => `<tr>
    <td style="padding:6px 10px">${esc(r.keyword)}</td>
    <td style="padding:6px 10px;text-align:center;font-weight:700;color:${r.position&&r.position<=10?'var(--green)':r.position?'var(--yellow)':'var(--red)'}">${r.position||'Not found'}</td>
    <td style="padding:6px 10px;text-align:center">${r.in_top_10?'✓':'—'}</td>
    <td style="padding:6px 10px;text-align:center">${r.in_top_30?'✓':'—'}</td>
  </tr>`).join('');
}

async function runDifficulty() {
  const rawKws  = el('diff-keywords')?.value || '';
  const keywords = rawKws.split('\n').map(k=>k.trim()).filter(Boolean).slice(0,20);
  if (!keywords.length) { toast('Enter at least one keyword', 'error'); return; }
  const btn = el('diff-run-btn');
  const bar = el('diff-bar');
  if (btn) btn.disabled = true;
  if (bar) bar.style.display = 'flex';
  hide('diff-results');
  try {
    const d = await apiPost('/serp/difficulty', { keywords });
    if (bar) bar.style.display = 'none';
    _renderDifficultyResults(d.results || []);
    toast('Difficulty check complete', 'ok');
  } catch (e) {
    if (bar) bar.style.display = 'none';
    toast('Difficulty error: ' + e.message, 'error');
  }
  finally { if (btn) btn.disabled = false; }
}

function _renderDifficultyResults(results) {
  const tbody = el('diff-tbody');
  if (!tbody) return;
  show('diff-results');
  tbody.innerHTML = results.map(r => `<tr>
    <td style="padding:6px 10px">${esc(r.keyword)}</td>
    <td style="padding:6px 10px;text-align:center;font-weight:700;color:${r.difficulty_score>=70?'var(--red)':r.difficulty_score>=40?'var(--yellow)':'var(--green)'}">${r.difficulty_score}</td>
    <td style="padding:6px 10px;text-align:center">${esc(r.difficulty_label||'')}</td>
    <td style="padding:6px 10px;font-size:10px;color:var(--dim)">${(r.top_domains||[]).slice(0,3).join(', ')}</td>
  </tr>`).join('');
}

async function loadVisibility() {
  const sumEl = el('vis-summary');
  const visEl = el('vis-results');
  if (sumEl) sumEl.innerHTML = '<div style="color:var(--dim);font-size:11px">Loading…</div>';
  try {
    const d = await apiGet('/serp/visibility');
    const total = d.total_keywords || 0;
    if (!total) {
      if (sumEl) sumEl.innerHTML = '<div style="color:var(--dim);font-size:11px">Run a bulk position check first to see visibility data.</div>';
      return;
    }
    const pct = Math.round((d.in_top_10||0)/total*100);
    if (sumEl) sumEl.innerHTML = `
      <div style="background:var(--surf2);padding:8px 14px;border-radius:6px;text-align:center"><div style="font-size:18px;font-weight:700;color:white">${total}</div><div style="font-size:10px;color:var(--dim)">Total Keywords</div></div>
      <div style="background:var(--surf2);padding:8px 14px;border-radius:6px;text-align:center"><div style="font-size:18px;font-weight:700;color:var(--green)">${d.in_top_3||0}</div><div style="font-size:10px;color:var(--dim)">Top 3</div></div>
      <div style="background:var(--surf2);padding:8px 14px;border-radius:6px;text-align:center"><div style="font-size:18px;font-weight:700;color:var(--cyan)">${d.in_top_10||0}</div><div style="font-size:10px;color:var(--dim)">Top 10</div></div>
      <div style="background:var(--surf2);padding:8px 14px;border-radius:6px;text-align:center"><div style="font-size:18px;font-weight:700;color:var(--indigo)">${pct}%</div><div style="font-size:10px;color:var(--dim)">Visibility</div></div>
    `;
    if (visEl) {
      show('vis-results');
      el('vis-tbody').innerHTML = (d.keywords||[]).slice(0,30).map(k=>`<tr style="border-bottom:1px solid var(--border)">
        <td style="padding:5px 8px">${esc(k.keyword)}</td>
        <td style="padding:5px 8px;text-align:center;color:${(k.position||99)<=10?'var(--green)':(k.position||99)<=30?'var(--yellow)':'var(--red)'}">${k.position||'—'}</td>
        <td style="padding:5px 8px;font-size:10px;color:var(--dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(k.page||'')}</td>
        <td style="padding:5px 8px;text-align:center">${k.expected_ctr ? (k.expected_ctr*100).toFixed(1)+'%' : '—'}</td>
      </tr>`).join('');
    }
  } catch (e) {
    if (sumEl) sumEl.innerHTML = `<div style="color:var(--red);font-size:11px">${e.message}</div>`;
  }
}

/* ═══════════════════════════════════════════
   MONITOR
═══════════════════════════════════════════ */
function openMonitorPanel() {
  hide('dash-sec'); hide('competitor-sec');
  show('monitor-sec');
  monTab('schedule');
  loadMonitorJobs();
}

function closeMonitorPanel() {
  hide('monitor-sec');
  show('dash-sec'); show('competitor-sec');
}

function monTab(tab) {
  ['schedule','jobs','history'].forEach(t => {
    const btn  = el(`mon-tab-${t}`);
    const pane = el(`mon-pane-${t}`);
    if (btn)  btn.classList.toggle('serp-tab-active', t === tab);
    if (pane) pane.style.display = t === tab ? '' : 'none';
  });
}

async function scheduleMonitor() {
  const domain   = el('mon-domain')?.value.trim();
  const kwRaw    = el('mon-keywords')?.value || '';
  const interval = parseFloat(el('mon-interval')?.value || '24');
  const keywords = kwRaw.split('\n').map(k=>k.trim()).filter(Boolean);
  if (!domain || !keywords.length) { toast('Enter domain and keywords', 'error'); return; }
  try {
    await apiPost('/monitor/schedule', { domain, keywords, interval_hours: interval });
    toast('Monitoring scheduled', 'ok');
    monTab('jobs');
    loadMonitorJobs();
  } catch (e) { toast('Schedule error: ' + e.message, 'error'); }
}

async function loadMonitorJobs() {
  const tbody = el('mon-jobs-tbody');
  if (!tbody) return;
  try {
    const d = await apiGet('/monitor/jobs');
    const jobs = d.jobs || [];
    if (!jobs.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">No monitoring jobs yet.</td></tr>';
      return;
    }
    tbody.innerHTML = jobs.map(j => `<tr>
      <td style="padding:7px 10px;font-family:var(--mono);font-size:11px">${esc(j.domain)}</td>
      <td style="padding:7px 10px;font-size:11px">${(j.keywords||[]).slice(0,3).join(', ')}${(j.keywords||[]).length>3?'…':''}</td>
      <td style="padding:7px 10px;text-align:center">${j.interval_hours||'24'}h</td>
      <td style="padding:7px 10px;text-align:center">${j.run_count||0}</td>
      <td style="padding:7px 10px;text-align:center;color:${j.active?'var(--green)':'var(--dim)'}">${j.active?'Active':'Paused'}</td>
      <td style="padding:7px 10px;font-size:10px;color:var(--dim)">${j.next_run_at ? new Date(j.next_run_at).toLocaleString() : '—'}</td>
      <td style="padding:7px 10px;display:flex;gap:6px">
        ${j.active ? `<button class="btn btn-outline btn-sm" onclick="pauseJob('${j.job_id}')" style="font-size:10px">Pause</button>` : ''}
        <button class="btn btn-outline btn-sm" onclick="deleteJob('${j.job_id}')" style="font-size:10px;color:var(--red);border-color:var(--red)">Delete</button>
      </td>
    </tr>`).join('');
  } catch (e) { toast('Jobs error: ' + e.message, 'error'); }
}

async function pauseJob(jobId) {
  try {
    await safeAuthFetch(`${API}/monitor/job/${jobId}/cancel`, { method: 'PATCH' });
    loadMonitorJobs();
    toast('Job paused', 'ok');
  } catch (e) { toast('Pause failed: ' + e.message, 'error'); }
}

async function deleteJob(jobId) {
  if (!confirm('Delete this monitoring job?')) return;
  try {
    await safeAuthFetch(`${API}/monitor/job/${jobId}`, { method: 'DELETE' });
    loadMonitorJobs();
    toast('Job deleted', 'ok');
  } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

async function loadHistory() {
  const domain  = el('hist-domain')?.value.trim();
  const keyword = el('hist-keyword')?.value.trim();
  if (!domain) { toast('Enter a domain', 'error'); return; }
  const bar = el('hist-bar');
  if (bar) bar.style.display = 'flex';
  hide('hist-results'); hide('hist-empty');
  try {
    const path = `/monitor/history?domain=${encodeURIComponent(domain)}${keyword?'&keyword='+encodeURIComponent(keyword):''}`;
    const d    = await apiGet(path);
    if (bar) bar.style.display = 'none';
    const hist = d.history || [];
    if (!hist.length) { show('hist-empty'); return; }
    show('hist-results');
    el('hist-tbody').innerHTML = hist.map(h => `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:6px 10px">${esc(h.keyword)}</td>
      <td style="padding:6px 10px;text-align:center;color:${h.position<=10?'var(--green)':h.position<=30?'var(--yellow)':'var(--red)'}">${h.position||'—'}</td>
      <td style="padding:6px 10px;text-align:center">${h.in_top_10?'✓':'—'}</td>
      <td style="padding:6px 10px;text-align:center">${h.in_top_30?'✓':'—'}</td>
      <td style="padding:6px 10px;font-size:10px;color:var(--dim)">${h.checked_at ? new Date(h.checked_at).toLocaleString() : '—'}</td>
    </tr>`).join('');
    const latestEl = el('hist-latest');
    if (latestEl) {
      const latest = await apiGet(`/monitor/latest?domain=${encodeURIComponent(domain)}`);
      const kws = latest.keywords || [];
      latestEl.innerHTML = kws.slice(0,6).map(k => `<div style="background:var(--surf2);padding:8px 12px;border-radius:6px;font-size:11px;font-family:var(--mono)">
        <div style="color:var(--dim);font-size:9px;margin-bottom:2px">${esc(k.keyword)}</div>
        <div style="font-weight:700;color:${k.position<=10?'var(--green)':'var(--yellow)'}">#${k.position||'?'}</div>
      </div>`).join('');
    }
  } catch (e) {
    if (bar) bar.style.display = 'none';
    toast('History error: ' + e.message, 'error');
  }
}

/* ═══════════════════════════════════════════
   COMPETITOR ANALYSIS
═══════════════════════════════════════════ */
function addCompetitorRow() {
  const grid = el('comp-input-grid');
  if (!grid) return;
  const n = grid.querySelectorAll('.comp-url-row').length + 1;
  if (n > 6) { toast('Max 5 competitors', 'error'); return; }
  const div = document.createElement('div');
  div.className = 'comp-url-row';
  div.innerHTML = `<span class="comp-url-label">Competitor ${n-1}</span><input class="comp-input" type="text" placeholder="https://competitor${n-1}.com"/>`;
  grid.appendChild(div);
}

async function startCompAnalysis() {
  const target      = el('comp-target')?.value.trim();
  const competitors = [...document.querySelectorAll('.comp-input')].map(i=>i.value.trim()).filter(Boolean);
  if (!target || !competitors.length) { toast('Enter your site and at least one competitor', 'error'); return; }
  const btn = el('comp-analyze-btn');
  if (btn) btn.disabled = true;
  const sbar = el('comp-sbar');
  if (sbar) sbar.style.display = 'flex';
  el('comp-status-txt').textContent = 'Starting competitor analysis…';
  hide('comp-results');
  try {
    const d = await apiPost('/competitor/analyze', { target_url: target, competitor_urls: competitors });
    const taskId = d.task_id;
    if (!taskId) throw new Error('No task ID returned');
    _pollCompetitor(taskId);
  } catch (e) {
    if (sbar) sbar.style.display = 'none';
    if (btn) btn.disabled = false;
    toast('Analysis error: ' + e.message, 'error');
  }
}

async function _pollCompetitor(taskId) {
  const interval = setInterval(async () => {
    try {
      const st = await apiGet(`/competitor/status/${taskId}`);
      el('comp-status-txt').textContent = st.status_msg || `Status: ${st.status}`;
      if (st.status === 'done') {
        clearInterval(interval);
        _lastCompTaskId = taskId;
        const d = await apiGet(`/competitor/results/${taskId}`);
        _renderCompResults(d);
        el('comp-sbar').style.display = 'none';
        el('comp-analyze-btn').disabled = false;
        el('comp-export-btn').disabled = false;
      } else if (st.status === 'error') {
        clearInterval(interval);
        el('comp-sbar').style.display = 'none';
        el('comp-analyze-btn').disabled = false;
        toast('Analysis error: ' + st.error, 'error');
      }
    } catch { clearInterval(interval); }
  }, 3000);
}

function _renderCompResults(d) {
  show('comp-results');
  const sites = d.sites || [];

  // Scores
  el('comp-scores').innerHTML = sites.map(s => {
    const score = s.overall_score || 0;
    const isTarget = s.is_target;
    return `<div class="comp-score-card${isTarget?' target':''}">
      <div style="font-size:10px;color:var(--dim);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.domain||s.url)}</div>
      <div style="font-size:28px;font-weight:800;color:${isTarget?'var(--cyan)':'var(--text)'}">${score}</div>
      <div style="font-size:10px;color:var(--dim)">Overall Score</div>
    </div>`;
  }).join('');

  // Keyword gaps
  const gaps = d.keyword_gaps || [];
  el('comp-gap-count').textContent = `(${gaps.length})`;
  el('comp-gap-tbody').innerHTML = gaps.slice(0, 50).map(g => `<tr>
    <td style="padding:6px 10px">${esc(g.keyword)}</td>
    <td style="padding:6px 10px;color:var(--yellow)">${esc(g.opportunity||'')}</td>
    <td style="padding:6px 10px">${g.competitor_count||0}</td>
    <td style="padding:6px 10px;font-size:10px;color:var(--dim)">${esc((g.found_in||[]).join(', '))}</td>
  </tr>`).join('');

  // Action plan
  const actions = d.action_plan || [];
  el('comp-action-list').innerHTML = actions.map(a => `
    <div class="comp-action-item">
      <div style="font-size:10px;font-weight:700;color:var(--cyan);margin-bottom:2px">${esc(a.action||a.title||'')}</div>
      <div style="font-size:11px;color:var(--text)">${esc(a.description||a.detail||'')}</div>
    </div>`).join('');
}

async function loadCompHistory() {
  const histPanel = el('comp-hist-panel');
  if (!histPanel) return;
  histPanel.style.display = histPanel.style.display === 'none' ? '' : 'none';
  if (histPanel.style.display === 'none') return;
  try {
    const d = await apiGet('/competitor/history');
    const tasks = d.tasks || d.history || [];
    const tbody = el('comp-hist-tbody');
    if (!tbody) return;
    tbody.innerHTML = tasks.length
      ? tasks.map(t => `<tr>
          <td style="padding:7px 10px;font-family:var(--mono);font-size:11px">${esc(t.target_url||t.target||'')}</td>
          <td style="padding:7px 10px;font-size:11px">${(t.competitor_urls||t.competitors||[]).slice(0,2).join(', ')}</td>
          <td style="padding:7px 10px;text-align:center">${t.overall_score||'—'}</td>
          <td style="padding:7px 10px;text-align:center;color:${t.status==='done'?'var(--green)':'var(--yellow)'}">${t.status}</td>
          <td style="padding:7px 10px;font-size:10px;color:var(--dim)">${t.created_at?new Date(t.created_at).toLocaleDateString():'—'}</td>
          <td style="padding:7px 10px"><button class="btn btn-outline btn-sm" onclick="loadCompTask('${t.task_id}')" style="font-size:10px">Load</button></td>
        </tr>`).join('')
      : '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">No history yet.</td></tr>';
  } catch (e) { toast('History error: ' + e.message, 'error'); }
}

async function loadCompTask(taskId) {
  try {
    const d = await apiGet(`/competitor/results/${taskId}`);
    _renderCompResults(d);
    hide('comp-hist-panel');
    toast('Analysis loaded', 'ok');
  } catch (e) { toast('Load error: ' + e.message, 'error'); }
}

function exportCompExcel() { window.open(`${API}/competitor/export/${_lastCompTaskId || ''}`, '_blank'); }

/* ═══════════════════════════════════════════
   KEYWORD GAP
═══════════════════════════════════════════ */
function openKwGap() { el('kwgap-modal').style.display = 'flex'; }

async function runKeywordGap() {
  const yours  = (el('kwgap-yours')?.value || '').split('\n').map(k=>k.trim()).filter(Boolean);
  const theirs = (el('kwgap-theirs')?.value || '').split('\n').map(k=>k.trim()).filter(Boolean);
  if (!yours.length || !theirs.length) { toast('Enter keywords for both sides', 'error'); return; }

  try {
    const d = await apiPost('/keyword-gap', { your_keywords: yours, competitor_keywords: theirs });
    _renderKwGapResults(d);
  } catch (e) { toast('Keyword Gap error: ' + e.message, 'error'); }
}

function _renderKwGapResults(d) {
  show('kwgap-results');
  el('kwgap-summary').innerHTML = `
    <div style="background:var(--surf2);padding:8px 14px;border-radius:6px;font-size:11px"><b style="color:var(--red)">${d.gap_count}</b> gap opportunities</div>
    <div style="background:var(--surf2);padding:8px 14px;border-radius:6px;font-size:11px"><b style="color:var(--green)">${(d.only_you||[]).length}</b> your unique keywords</div>
    <div style="background:var(--surf2);padding:8px 14px;border-radius:6px;font-size:11px"><b style="color:var(--yellow)">${(d.shared||[]).length}</b> shared keywords</div>
  `;
  el('kwgap-only-comp').innerHTML = (d.only_competitor||[]).map(k=>`<div style="padding:3px 0;border-bottom:1px solid var(--border);font-size:11px;font-family:var(--mono)">${esc(k)}</div>`).join('') || '<div style="color:var(--dim);font-size:11px">None</div>';
  el('kwgap-only-you').innerHTML  = (d.only_you||[]).map(k=>`<div style="padding:3px 0;border-bottom:1px solid var(--border);font-size:11px;font-family:var(--mono)">${esc(k)}</div>`).join('') || '<div style="color:var(--dim);font-size:11px">None</div>';
  el('kwgap-shared').innerHTML    = (d.shared||[]).map(k=>`<div style="padding:3px 0;border-bottom:1px solid var(--border);font-size:11px;font-family:var(--mono)">${esc(k)}</div>`).join('') || '<div style="color:var(--dim);font-size:11px">None</div>';
}

/* ═══════════════════════════════════════════
   SITEMAP CRAWL
═══════════════════════════════════════════ */
function openSitemapCrawl() { el('sitemap-modal').style.display = 'flex'; }

async function startSitemapCrawl() {
  const sitemapUrl = el('sitemap-url-input')?.value.trim();
  const maxPages   = parseInt(el('sitemap-max-pages')?.value || '100', 10);
  const errEl      = el('sitemap-error');
  if (errEl) errEl.style.display = 'none';

  if (!sitemapUrl) {
    if (errEl) { errEl.textContent = 'Enter a sitemap URL'; errEl.style.display = ''; }
    return;
  }

  hide('sitemap-modal');
  clearInterval(_crawlTimer);
  _allPages = []; _filteredPages = []; _selectedUrls.clear();
  btns({});
  show('cbar');
  el('ctxt').textContent = 'Fetching sitemap…';
  el('cspin').style.display = '';
  show('progress-wrap');
  _crawlStart = Date.now();

  try {
    const d = await safeAuthFetch(`${API}/sitemap-crawl?sitemap_url=${encodeURIComponent(sitemapUrl)}&max_pages=${maxPages}`, { method: 'POST' });
    const result = await safeJson(d);
    el('ctxt').textContent = `Sitemap found ${result.urls_found} URLs, crawling…`;
    _crawlTimer = setInterval(_pollCrawl, 2000);
  } catch (e) {
    hide('cbar');
    toast('Sitemap crawl error: ' + e.message, 'error');
  }
}
