/* CrawlIQ crawl.js — crawl engine, SSE, AI analysis | Part of app.js split v1.0.3 */
async function checkGemini() {
  try {
    const d = await (await fetch(`${API}/ai-config`)).json();
    const pill = document.getElementById('gemini-pill');
    if (d.configured) {
      pill.textContent = `✨ ${d.label} · Ready`;
      pill.className   = 'gemini-pill up';
    } else {
      pill.textContent = '⚙ Set API Key ↗';
      pill.className   = 'gemini-pill down';
    }
    // API is reachable — hide the static fallback notice
    const notice = document.getElementById('api-fallback-notice');
    if (notice) notice.style.display = 'none';
  } catch {
    const pill = document.getElementById('gemini-pill');
    pill.textContent = '⚙ Set API Key ↗';
    pill.className   = 'gemini-pill down';
    // API unreachable — keep the fallback notice visible (already shown in HTML)
  }
}

function startCrawlHero() {
  startCrawl(); // startCrawl() reads #url-input and handles validation
}

// ── Cold-start banner helpers (main crawl) ────────────────────────────────────
let _coldStartTimer = null, _coldStartSecs = 0, _coldMsgIdx = 0;
const _COLD_COUNTDOWN = 90;
const _coldMessages = [
  'Warming up the crawler engine — first load takes ~30 seconds',
  'Loading AI models — Groq, Gemini, Claude ready soon',
  'Almost there — preparing your free SEO audit…',
];
function showColdBanner() {
  const b = document.getElementById('cold-banner');
  b.classList.add('show');
  _coldStartSecs = 0; _coldMsgIdx = 0;
  clearInterval(_coldStartTimer);
  document.getElementById('cold-banner-text').textContent = _coldMessages[0];
  document.getElementById('cold-elapsed').textContent = `${_COLD_COUNTDOWN}s`;
  _coldStartTimer = setInterval(() => {
    _coldStartSecs++;
    const remaining = Math.max(0, _COLD_COUNTDOWN - _coldStartSecs);
    const el = document.getElementById('cold-elapsed');
    el.textContent = remaining > 0 ? `${remaining}s` : '…';
    if (_coldStartSecs % 8 === 0) {
      _coldMsgIdx = (_coldMsgIdx + 1) % _coldMessages.length;
      document.getElementById('cold-banner-text').textContent = _coldMessages[_coldMsgIdx];
    }
  }, 1000);
}
function hideColdBanner() {
  document.getElementById('cold-banner').classList.remove('show');
  clearInterval(_coldStartTimer);
  _coldStartSecs = 0;
  document.getElementById('cold-elapsed').textContent = '';
}

async function waitForMainSpace(maxWaitMs = 300000) {
  const start = Date.now(); let elapsed = 0;
  while (elapsed < maxWaitMs) {
    try {
      const r = await fetch(`${API}/healthz`, { cache: 'no-store' });
      const ct = r.headers.get('content-type') || '';
      if (r.ok && ct.includes('application/json')) return true;
    } catch (_) {}
    elapsed = Date.now() - start;
    await new Promise(r => setTimeout(r, 4000));
    elapsed = Date.now() - start;
  }
  return false;
}

async function startCrawl() {
  const url = document.getElementById('url-input').value.trim();
  maxPages = 50;
  if (!url) { alert('Please enter a URL.'); return; }
  allResults = []; selectedUrls = new Set(); optimizerRows = []; techSEOPages = []; techSEOSiteData = null;
  document.getElementById('opt-panel').classList.remove('show');
  document.getElementById('opt-tbody').innerHTML = '<tr><td colspan="6"><div class="opt-empty">Run ⚡ Optimize after crawl to generate the Live Optimization Table.</div></td></tr>';
  document.getElementById('opt-row-count').textContent = '';
  document.getElementById('opt-count-label').textContent = '0 rows';
  document.getElementById('tseo-panel').classList.remove('show');
  document.getElementById('tseo-tbody').innerHTML = '<tr><td colspan="13"><div class="opt-empty">Run 🔬 Tech SEO after crawl to audit all pages.</div></td></tr>';
  document.getElementById('tseo-row-count').textContent = '';
  bar('t', false);
  renderTable([]); hideSummary();
  bar('c', true, 'Checking backend…'); bar('g', false);
  btnSet('crawl-btn', true);
  btns({ gemini:1, popup:1, export:1, opt:1, tseo:1, pdf:1, serp:1 });
  showProgress(true);
  document.getElementById('sel-toolbar').classList.remove('show');

  // Probe health first — handle cold start gracefully
  try {
    const probe = await fetch(`${API}/healthz`, { cache: 'no-store' });
    const ct = probe.headers.get('content-type') || '';
    if (!probe.ok || !ct.includes('application/json')) {
      showColdBanner();
      bar('c', true, 'Waiting for backend to wake up…');
      const ready = await waitForMainSpace();
      hideColdBanner();
      if (!ready) {
        bar('c', false, '✗ Backend did not respond in 5 min. Try again shortly.');
        btnSet('crawl-btn', false);
        btns({ gemini:1, popup:1, export:1 });
        showProgress(false);
        return;
      }
    }
  } catch (_) {
    showColdBanner('Backend is waking up — HuggingFace free tier sleeps after 48h. Usually 30–90 seconds.');
    bar('c', true, 'Waiting for backend to wake up…');
    const ready = await waitForMainSpace();
    hideColdBanner();
    if (!ready) {
      bar('c', false, '✗ Backend did not respond in 5 min. Try again shortly.');
      btnSet('crawl-btn', false);
      btns({ gemini:1, popup:1, export:1 });
      showProgress(false);
      return;
    }
  }

  bar('c', true, 'Starting crawl…');
  try {
    const res = await fetch(`${API}/crawl`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ url, max_pages:maxPages }) });
    if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
    const data = await res.json().catch(()=>({}));
    _currentJobId = data.job_id || null;
    startCrawlPolling();
  } catch (e) {
    bar('c', false, `✗ ${e.message}`);
    btnSet('crawl-btn', false);
    btns({ gemini:1, popup:1, export:1 });
    showProgress(false);
  }
}

function startCrawlPolling() {
  // Try SSE stream first — falls back to polling if endpoint not available
  if (_currentJobId) {
    _closeSse();
    try {
      _sseSource = new EventSource(`${API}/crawl-stream/${_currentJobId}`);
      _sseSource.onmessage = async (e) => {
        try {
          const s = JSON.parse(e.data);
          updateProgress(s);
          const live = await (await fetch(`${API}/results/live`)).json();
          if (live.results && live.results.length > allResults.length) { allResults = live.results; applyFilters(); updateSummary(allResults); }
          if (s.error) { _closeSse(); bar('c', false, `✗ ${s.error}`); btns({crawl:0,gemini:0,popup:0,export:0,opt:0,tseo:0,pdf:1,serp:1}); showProgress(false); }
          if (s.done) {
            _closeSse(); await loadResults();
            const realPages = allResults.filter(r => r.status_code===200||r.status_code==='200');
            const elapsed = s.elapsed_s||0;
            if (!allResults.length) bar('c', false, '⚠ Site unreachable — check the URL');
            else if (!realPages.length) bar('c', false, `⚠ ${allResults.length} pages recorded but none loaded successfully`);
            else {
              const t = s.timeouts>0 ? ` · ${s.timeouts} timeout${s.timeouts!==1?'s':''}` : '';
              const er = s.errors>0 ? ` · ${s.errors} error${s.errors!==1?'s':''}` : '';
              bar('c', false, `✓ ${realPages.length} pages crawled in ${elapsed}s${t}${er}`);
              btns({crawl:0,gemini:0,popup:0,export:0,opt:0,tseo:0,pdf:0,serp:0});
            }
            btnSet('crawl-btn', false); showProgress(false);
          }
        } catch {}
      };
      _sseSource.onerror = () => { _closeSse(); _startCrawlPollingFallback(); };
      return;
    } catch {}
  }
  _startCrawlPollingFallback();
}

function _closeSse() {
  if (_sseSource) { _sseSource.close(); _sseSource = null; }
}

function _startCrawlPollingFallback() {
  if (crawlTimer) clearInterval(crawlTimer);
  crawlTimer = setInterval(async () => {
    try {
      const statusUrl = _currentJobId ? `${API}/crawl-status?job_id=${_currentJobId}` : `${API}/crawl-status`;
      const s = await (await fetch(statusUrl)).json();
      updateProgress(s);
      const live = await (await fetch(`${API}/results/live`)).json();
      if (live.results && live.results.length > allResults.length) { allResults = live.results; applyFilters(); updateSummary(allResults); }
      if (s.error) { clearInterval(crawlTimer); bar('c', false, `✗ ${s.error}`); btns({crawl:0,gemini:0,popup:0,export:0,opt:0,tseo:0,pdf:1,serp:1}); showProgress(false); }
      if (s.done) {
        clearInterval(crawlTimer); await loadResults();
        const realPages = allResults.filter(r => r.status_code===200||r.status_code==='200');
        const elapsed = s.elapsed_s||0;
        if (!allResults.length) bar('c', false, '⚠ Site unreachable — check the URL');
        else if (!realPages.length) bar('c', false, `⚠ ${allResults.length} pages recorded but none loaded successfully`);
        else {
          const t = s.timeouts>0 ? ` · ${s.timeouts} timeout${s.timeouts!==1?'s':''}` : '';
          const e = s.errors>0 ? ` · ${s.errors} error${s.errors!==1?'s':''}` : '';
          bar('c', false, `✓ ${realPages.length} pages crawled in ${elapsed}s${t}${e}`);
          btns({crawl:0,gemini:0,popup:0,export:0,opt:0,tseo:0,pdf:0,serp:0});
        }
        btnSet('crawl-btn', false); showProgress(false);
      }
    } catch {}
  }, 2000);
}

function updateProgress(s) {
  const crawled=s.pages_crawled||0, queued=s.pages_queued||0;
  const total=Math.max(crawled+queued,crawled,1), pct=Math.round((crawled/total)*100);
  document.getElementById('prog-text').textContent=`${crawled} / ${total} pages crawled`;
  document.getElementById('prog-elapsed').textContent=`${s.elapsed_s||0}s`;
  document.getElementById('prog-fill').style.width=`${pct}%`;
  const u=s.current_url||'';
  if(u) bar('c',true,`Crawling: ${u.replace(/^https?:\/\//,'').slice(0,60)}…`);
}

async function startGeminiAll() {
  bar('g',true,'✨ AI analyzing…'); btns({crawl:1,gemini:1,popup:1,export:1});
  try {
    const res=await fetch(`${API}/analyze-gemini`,{method:'POST'});
    if(!res.ok) throw new Error((await res.json()).detail||'Failed');
    startGeminiPolling();
  } catch(e) { bar('g',false,`✗ ${e.message}`); btns({crawl:0,gemini:0,popup:0,export:0}); }
}

async function startGeminiSelected() {
  if(!selectedUrls.size){alert('Select at least one page first.');return;}
  bar('g',true,`✨ AI analyzing ${selectedUrls.size} pages…`); btns({crawl:1,gemini:1,popup:1,export:1});
  try {
    const res=await fetch(`${API}/analyze-selected`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:[...selectedUrls]})});
    if(!res.ok) throw new Error((await res.json()).detail||'Failed');
    startGeminiPolling();
  } catch(e) { bar('g',false,`✗ ${e.message}`); btns({crawl:0,gemini:0,popup:0,export:0}); }
}

function startGeminiPolling() {
  geminiTimer=setInterval(async()=>{
    try {
      const s=await(await fetch(`${API}/gemini-status`)).json();
      const prog=s.total?` (${s.processed}/${s.total})`:'';
      document.getElementById('gtxt').textContent=`✨ AI processing…${prog}`;
      if(s.error){clearInterval(geminiTimer);bar('g',false,`✗ ${s.error}`);btns({crawl:0,gemini:0,popup:0,export:0});}
      if(s.done){clearInterval(geminiTimer);await loadResults();bar('g',false,`✓ AI complete — ${s.processed} pages analysed`);btns({crawl:0,gemini:0,popup:0,export:0});}
    }catch{}
  },2000);
}

