/* CrawlIQ app.js v1.0.2 */
const API = 'https://bhavani7-seo-project.hf.space';
let allResults=[], sortKey='', sortAsc=true, crawlTimer=null, geminiTimer=null;
let selectedUrls=new Set(), popupPages=[], popupIndex=0, maxPages=50;
let optimizerRows=[], optTimer=null;

// Delay HF backend ping — runs 3s after load so it never blocks FCP/LCP
window.addEventListener('load', () => setTimeout(checkGemini, 3000));
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
  } catch {
    const pill = document.getElementById('gemini-pill');
    pill.textContent = '⚙ Set API Key ↗';
    pill.className   = 'gemini-pill down';
  }
}

function startCrawlHero() {
  const raw = (document.getElementById('url-input').value || '').trim();
  if (!raw) { document.getElementById('url-input').focus(); return; }
  const url = raw.startsWith('http') ? raw : 'https://' + raw;
  window.location.href = 'backend/pages/tech-seo.html?url=' + encodeURIComponent(url);
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
    startCrawlPolling();
  } catch (e) {
    bar('c', false, `✗ ${e.message}`);
    btnSet('crawl-btn', false);
    btns({ gemini:1, popup:1, export:1 });
    showProgress(false);
  }
}

function startCrawlPolling() {
  crawlTimer = setInterval(async () => {
    try {
      const s = await (await fetch(`${API}/crawl-status`)).json();
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

async function loadResults() {
  const data=await(await fetch(`${API}/results`)).json();
  allResults=data.results||[];
  updateSummary(allResults); applyFilters();
}

function applyFilters() {
  const io=document.getElementById('f-issues').checked;
  const pr=document.getElementById('f-priority').value;
  const so=document.getElementById('f-selected').checked;
  let rows=[...allResults];
  if(io) rows=rows.filter(r=>r.issues&&r.issues.length);
  if(pr) rows=rows.filter(r=>r.priority===pr);
  if(so) rows=rows.filter(r=>selectedUrls.has(r.url));
  document.getElementById('page-count').textContent=rows.length;
  renderTable(rows);
}

function renderTable(rows) {
  const tb=document.getElementById('results-body');
  if(!rows.length){tb.innerHTML=`<tr><td colspan="14"><div class="empty-state"><div class="icon">🔍</div><p>No results yet. Enter a URL above and crawl.</p></div></td></tr>`;return;}
  tb.innerHTML=rows.map(r=>{
    const h1=Array.isArray(r.h1)?r.h1[0]||'—':r.h1||'—';
    const aiFix=(r.gemini_fields||[]).filter(f=>f.issue!=='OK'&&(f.fix||f.suggestion)).map(f=>f.fix||f.suggestion).join(' · ');
    const hasIss=r.issues&&r.issues.length>0;
    const sel=selectedUrls.has(r.url);
    const score=r.ranking?`<span class="score-pill score-${r.ranking.grade.toLowerCase()}">${r.ranking.score} ${r.ranking.grade}</span>`:'—';
    // Issue status dropdown — reads from _issueStatusCache
    const statusKey = r.url+'|'+(r.issues&&r.issues[0]||'');
    const savedStatus = window._issueStatusCache?.[statusKey] || 'open';
    const statusDrop = hasIss
      ? `<select class="issue-status-sel ${savedStatus}" title="Track this issue"
           onchange="updateIssueStatus('${escJ(r.url)}','${escJ((r.issues||[])[0]||'')}',this.value,this)">
           <option value="open"      ${savedStatus==='open'?'selected':''}>Open</option>
           <option value="in_progress" ${savedStatus==='in_progress'?'selected':''}>In Progress</option>
           <option value="resolved"  ${savedStatus==='resolved'?'selected':''}>Resolved</option>
         </select>`
      : '<span style="color:var(--dim);font-size:10px">—</span>';
    return `<tr class="${sel?'selected-row':''}" id="row-${encodeRowId(r.url)}">
      <td class="check-cell"><input type="checkbox" ${sel?'checked':''} onchange="toggleSelect('${escJ(r.url)}',this)"/></td>
      <td class="url-cell"><a href="${esc(r.url)}" target="_blank">${esc(r.url)}</a></td>
      <td>${statusBadge(r.status_code)}</td>
      <td class="wrap">${esc(r.title||'—')}</td>
      <td class="wrap">${esc(r.meta_description||'—')}</td>
      <td class="wrap">${esc(h1)}</td>
      <td class="wrap">${issuesTags(r.issues)}</td>
      <td>${priorityBadge(r.priority)}</td>
      <td>${score}</td>
      <td class="wrap">${renderKeywords(r.keywords)}</td>
      <td>${competitionBadge(r.competition)}</td>
      <td class="wrap" style="font-size:10px;color:#A5B4FC">${esc(aiFix||'—')}</td>
      <td>${statusDrop}</td>
      <td>${hasIss?`<button class="popup-btn" onclick="openPopupForUrl('${escJ(r.url)}')">View →</button>`:'<span style="color:var(--dark-muted);font-size:10px">—</span>'}</td>
    </tr>`;
  }).join('');
}

function toggleSelect(url,cb){
  if(cb.checked) selectedUrls.add(url); else selectedUrls.delete(url);
  updateSelToolbar();
  const row=document.getElementById('row-'+encodeRowId(url));
  if(row) row.classList.toggle('selected-row',cb.checked);
}
function toggleSelectAll(cb){
  const visible=getFilteredRows();
  visible.forEach(r=>{if(cb.checked)selectedUrls.add(r.url);else selectedUrls.delete(r.url);});
  applyFilters();updateSelToolbar();
}
function clearSelection(){selectedUrls.clear();applyFilters();updateSelToolbar();}
function updateSelToolbar(){
  const n=selectedUrls.size;
  document.getElementById('sel-count').textContent=n;
  document.getElementById('sel-toolbar').classList.toggle('show',n>0);
  document.getElementById('sel-ai-btn').style.display=n>0?'inline-block':'none';
}
function getFilteredRows(){
  const io=document.getElementById('f-issues').checked, pr=document.getElementById('f-priority').value;
  let rows=[...allResults];
  if(io) rows=rows.filter(r=>r.issues&&r.issues.length);
  if(pr) rows=rows.filter(r=>r.priority===pr);
  return rows;
}

function sortBy(key){
  if(sortKey===key) sortAsc=!sortAsc; else{sortKey=key;sortAsc=true;}
  document.querySelectorAll('thead th').forEach(t=>t.classList.remove('active'));
  event.currentTarget.classList.add('active');
  event.currentTarget.querySelector('.sa').textContent=sortAsc?'↑':'↓';
  const rows=getFilteredRows();
  rows.sort((a,b)=>{
    let va=a[key]??'',vb=b[key]??'';
    if(Array.isArray(va)) va=va.join(',');
    if(Array.isArray(vb)) vb=vb.join(',');
    if(typeof va==='number') return sortAsc?va-vb:vb-va;
    return sortAsc?String(va).localeCompare(String(vb)):String(vb).localeCompare(String(va));
  });
  document.getElementById('page-count').textContent=rows.length;
  renderTable(rows);
}

function updateSummary(rows){
  const wi=rows.filter(r=>r.issues&&r.issues.length);
  const ok=rows.filter(r=>!r.issues||!r.issues.length).length;
  set('s-total',rows.length); set('s-issues',wi.length);
  const nHigh=rows.filter(r=>r.priority==='High').length;
  set('s-high',nHigh); set('s-high-bar',nHigh);
  set('s-med',rows.filter(r=>r.priority==='Medium').length);
  set('s-low',rows.filter(r=>r.priority==='Low').length);
  set('s-ok',ok);
  const counts={};
  rows.forEach(r=>(r.issues||[]).forEach(i=>{counts[i]=(counts[i]||0)+1;}));
  document.getElementById('igrid').innerHTML=
    Object.entries(counts).sort((a,b)=>b[1]-a[1])
    .map(([n,c])=>`<div class="istat"><span class="n">${c}</span><span class="name">${esc(n)}</span></div>`).join('');
  document.getElementById('summary').style.display='grid';
  document.getElementById('ibreak').style.display=wi.length?'block':'none';
  // Store all extracted keywords globally for keyword-gap pre-fill
  const allKw = new Set();
  rows.forEach(r=>(r.keywords||[]).forEach(k=>{ if(k&&k.word) allKw.add(k.word); else if(typeof k==='string') allKw.add(k); }));
  window._lastKeywords = [...allKw].slice(0,200);
  // Mark crawl as done for save-to-project
  window._crawlDone = true;
  const saveBtn = document.getElementById('save-project-btn');
  if(saveBtn && window._ciqProject) saveBtn.disabled = false;
  // Load issue statuses from DB if project is active
  if(window._ciqProject) loadIssueStatuses();
}
function hideSummary(){
  document.getElementById('summary').style.display='none';
  document.getElementById('ibreak').style.display='none';
}

async function loadPopupData(){const data=await(await fetch(`${API}/popup-data`)).json();popupPages=data.pages||[];}
async function openPopup(){
  await loadPopupData();
  if(!popupPages.length){alert('No pages with issues found.');return;}
  popupIndex=0;renderPopupPage();
  document.getElementById('popup-overlay').classList.add('open');
}
async function openPopupForUrl(url){
  await loadPopupData();
  const idx=popupPages.findIndex(p=>p.url===url);
  popupIndex=idx>=0?idx:0;
  if(!popupPages.length){alert('No issues data.');return;}
  renderPopupPage();
  document.getElementById('popup-overlay').classList.add('open');
}
function renderPopupPage(){
  const page=popupPages[popupIndex];if(!page)return;
  document.getElementById('pp-url').textContent=page.url;
  const pb=document.getElementById('pp-priority');
  pb.textContent=page.priority||'';
  pb.className='pb '+({High:'ph',Medium:'pm',Low:'pl'}[page.priority]||'');
  const rank=page.ranking||{};
  const gscore=rank.gemini_score;
  const displayScore=(gscore!==undefined&&gscore!==null)?gscore:(rank.score??'—');
  const grade=(gscore!==undefined&&gscore!==null)?(gscore>=85?'A':gscore>=70?'B':gscore>=55?'C':gscore>=40?'D':'F'):(rank.grade||'—');
  const rc=document.getElementById('rank-circle');
  rc.className=`rank-circle grade-${grade}`;
  set('rank-score',displayScore);set('rank-grade',grade);
  set('rank-feedback',rank.gemini_reason||rank.feedback||'');
  const bd=rank.breakdown||{};
  const bdL={title:'Title',meta:'Meta',h1:'H1',h2:'H2',canonical:'Canon',status:'Status',keyword_alignment:'KW Align'};
  document.getElementById('rank-bars').innerHTML=Object.entries(bdL).map(([k,l])=>`<span class="rank-bar-item">${l}: <span>${bd[k]??0}pts</span></span>`).join('');
  const compEl=document.getElementById('pp-competition');
  if(compEl) compEl.innerHTML=page.competition?competitionBadge(page.competition):'';
  const kws=page.keywords||[];
  document.getElementById('pp-kw-tags').innerHTML=kws.length?kws.map(k=>`<span class="kw-tag">${esc(k)}</span>`).join(''):'<span style="color:var(--muted);font-size:10px">none detected</span>';
  document.getElementById('pp-issues').innerHTML=(page.issues||[]).map(i=>`<span class="itag">${esc(i)}</span>`).join('');
  const tbody=document.getElementById('pp-tbody');
  tbody.innerHTML=(page.fields||[]).map(f=>{
    const isOK=f.status==='OK', valCl=f.current?'fval':'fval missing';
    const impCl={High:'imp-high',Medium:'imp-med',Low:'imp-low'}[f.impact]||'';
    const optVal=f.optimized||'';
    return `<tr>
      <td class="fname">${esc(f.field)}</td>
      <td class="${valCl}">${esc(f.current||'(empty)')}</td>
      <td><span class="sstatus ${isOK?'ss-ok':'ss-bad'}">${esc(f.status)}</span></td>
      <td class="fwhy">${esc(f.why||'—')}</td>
      <td class="ffix">${esc(f.fix||(isOK?'No action needed.':'Run AI analysis for suggestion.'))}</td>
      <td class="fopt">${optVal?esc(optVal):'<span style="color:var(--muted);font-weight:400">Run AI →</span>'}${optVal?`<button class="copy-btn" onclick="copyVal(this,'${escJ(optVal)}')">⎘ Copy</button>`:''}</td>
      <td class="fex">${esc(f.example||'—')}</td>
      <td>${f.impact?`<span class="impact-pill ${impCl}">${esc(f.impact)}</span>`:'—'}</td>
    </tr>`;
  }).join('');
  set('nav-cur',popupIndex+1);set('nav-tot',popupPages.length);
  document.getElementById('nav-prev').disabled=popupIndex===0;
  document.getElementById('nav-next').disabled=popupIndex===popupPages.length-1;
  document.getElementById('pp-ai-status').style.display='none';
}
function navPage(dir){const n=popupIndex+dir;if(n<0||n>=popupPages.length)return;popupIndex=n;renderPopupPage();}
async function analyzeThisPage(){
  const page=popupPages[popupIndex];if(!page)return;
  document.getElementById('pp-ai-status').style.display='flex';
  try {
    const res=await fetch(`${API}/analyze-selected`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:[page.url]})});
    if(!res.ok) throw new Error((await res.json()).detail||'AI failed');
    const wd=setInterval(async()=>{
      try{const s=await(await fetch(`${API}/gemini-status`)).json();
        if(s.done||s.error){clearInterval(wd);document.getElementById('pp-ai-status').style.display='none';await loadResults();await loadPopupData();renderPopupPage();}
      }catch{}
    },1500);
  }catch(e){document.getElementById('pp-ai-status').style.display='none';alert('AI error: '+e.message);}
}
function closePopup(){document.getElementById('popup-overlay').classList.remove('open');}
function overlayClick(e){if(e.target===document.getElementById('popup-overlay'))closePopup();}

function openExportModal(){
  const wi=allResults.filter(r=>r.issues&&r.issues.length);
  set('em-pages',allResults.length);set('em-issues',wi.length);
  document.getElementById('eoverlay').classList.add('open');
}
function closeExportModal(){document.getElementById('eoverlay').classList.remove('open');}
function openExportFromPopup(){closePopup();openExportModal();}
async function downloadExcel(type){
  try{
    const ep=type==='popup'?'/export-popup':'/export';
    const res=await fetch(`${API}${ep}`);
    if(!res.ok) throw new Error('Export failed');
    const a=document.createElement('a');
    a.href=URL.createObjectURL(await res.blob());
    a.download=type==='popup'?'seo_issues.xlsx':'seo_report.xlsx';
    a.click();closeExportModal();
  }catch(e){alert('Export error: '+e.message);}
}

function showProgress(v){
  document.getElementById('progress-wrap').classList.toggle('show',v);
  if(!v) document.getElementById('prog-fill').style.width='0%';
}
function bar(type,spinning,msg){
  const map={c:['cbar','cspin','ctxt'],g:['gbar','gspin','gtxt'],o:['obar','ospin','otxt'],t:['tseo-bar','tseo-spin','tseo-txt']};
  const[bid,sid,tid]=map[type];
  const el=document.getElementById(bid);
  if(msg===undefined&&!spinning){el.classList.remove('show');return;}
  el.classList.add('show');
  document.getElementById(sid).style.display=spinning?'block':'none';
  if(msg) document.getElementById(tid).textContent=msg;
}
function btns(state){
  const map={crawl:'crawl-btn',gemini:'gemini-btn',popup:'popup-btn',export:'export-btn',opt:'opt-btn',tseo:'tseo-btn',pdf:'pdf-btn',serp:'serp-btn'};
  for(const[k,id]of Object.entries(map)){const el=document.getElementById(id);if(el&&k in state)el.disabled=!!state[k];}
  // Track crawl done + enable save-to-project when complete
  if('crawl' in state){
    window._crawlDone = (state.crawl === false);
    const saveBtn = document.getElementById('save-project-btn');
    if(saveBtn) saveBtn.disabled = !(window._crawlDone && window._ciqProject);
  }
}
function btnSet(id,disabled){const el=document.getElementById(id);if(el)el.disabled=disabled;}
function set(id,v){document.getElementById(id).textContent=v;}
function encodeRowId(url){return btoa(url).replace(/[^a-zA-Z0-9]/g,'');}
function escJ(s){return String(s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'");}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function statusBadge(c){
  if(c==='Timeout'||c==='Error')return`<span class="badge b-err">${c}</span>`;
  const n=parseInt(c);
  if(n>=200&&n<300)return`<span class="badge b-ok">${c}</span>`;
  if(n>=300&&n<400)return`<span class="badge b-warn">${c}</span>`;
  return`<span class="badge b-err">${c}</span>`;
}
function competitionBadge(c){
  if(!c)return'<span style="color:var(--muted)">—</span>';
  const s={Low:['comp-low','🟢 Low'],Medium:['comp-med','🟡 Mid'],High:['comp-high','🔴 High']};
  const[cls,label]=s[c]||['comp-med',c];
  return`<span class="comp-badge ${cls}">${label}</span>`;
}
function priorityBadge(p){
  if(!p)return'<span style="color:var(--muted)">—</span>';
  return`<span class="pb ${{High:'ph',Medium:'pm',Low:'pl'}[p]||''}">${p}</span>`;
}
function issuesTags(issues){
  if(!issues||!issues.length)return'<span style="color:var(--green);font-size:10px">✓ Clean</span>';
  return issues.map(i=>`<span class="itag">${esc(i)}</span>`).join('');
}
function renderKeywords(kws){
  if(!kws||!kws.length)return'<span style="color:var(--muted)">—</span>';
  return kws.slice(0,4).map(k=>`<span class="kw-tag">${esc(k)}</span>`).join('');
}

async function startOptimizer(){
  bar('o',true,'⚡ Optimizer starting…');btns({opt:true});
  document.getElementById('opt-panel').classList.add('show');
  try{
    const res=await fetch(`${API}/optimize`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:null})});
    if(!res.ok){const err=await res.json();throw new Error(err.detail||'Optimizer failed');}
    startOptimizerPolling();
  }catch(e){bar('o',false,`✗ ${e.message}`);btns({opt:false});}
}
function startOptimizerPolling(){
  clearInterval(optTimer);
  optTimer=setInterval(async()=>{
    try{
      const s=await(await fetch(`${API}/optimize-status`)).json();
      const prog=s.total?` (${s.processed}/${s.total})`:'';
      bar('o',true,`⚡ Optimizing…${prog}`);
      if(s.error){clearInterval(optTimer);bar('o',false,`✗ ${s.error}`);btns({opt:false});return;}
      if(s.done){clearInterval(optTimer);await loadOptimizerTable();bar('o',false,`✓ Optimization complete — ${optimizerRows.length} rows generated`);btns({opt:false});document.getElementById('opt-panel').classList.add('show');}
    }catch{}
  },2000);
}
async function loadOptimizerTable(){
  try{const data=await(await fetch(`${API}/optimize-table`)).json();optimizerRows=data.rows||[];renderOptimizerTable();}
  catch(e){console.error('Failed to load optimizer table:',e);}
}
function renderOptimizerTable(){
  const ff=document.getElementById('opt-field-filter').value;
  const sf=document.getElementById('opt-status-filter').value;
  const srch=(document.getElementById('opt-search').value||'').toLowerCase();
  let rows=optimizerRows.filter(r=>{
    if(ff&&r.field!==ff)return false;
    if(sf&&r.status!==sf)return false;
    if(srch){const h=[r.url,r.field,r.current_value,r.optimized_value,r.seo_logic].join(' ').toLowerCase();if(!h.includes(srch))return false;}
    return true;
  });
  document.getElementById('opt-count-label').textContent=`${rows.length} row${rows.length!==1?'s':''}`;
  document.getElementById('opt-row-count').textContent=rows.length?`${rows.length} rows`:'';
  const tbody=document.getElementById('opt-tbody');
  if(!rows.length){tbody.innerHTML='<tr><td colspan="6"><div class="opt-empty">No rows match the current filters.</div></td></tr>';return;}
  const sc=s=>{const v=(s||'').toLowerCase();return['missing','too long','duplicate','multiple','mismatch','weak'].includes(v)?'ss-bad':'ss-ok';};
  tbody.innerHTML=rows.map(r=>{
    const ins=(r.optimized_value||'').startsWith('Insufficient');
    const cc=r.current_value==='MISSING'?'ot-curr missing':'ot-curr';
    const oc=ins?'ot-opt insufficient':'ot-opt';
    return`<tr>
      <td class="ot-url" title="${esc(r.url)}">${esc(r.url)}</td>
      <td class="ot-field">${esc(r.field)}</td>
      <td><span class="sstatus ${sc(r.status)}">${esc(r.status)}</span></td>
      <td class="${cc}">${esc(r.current_value||'—')}</td>
      <td class="${oc}">${esc(r.optimized_value||'—')}${(!ins&&r.optimized_value)?`<button class="copy-btn" onclick="copyVal(this,'${escJ(r.optimized_value)}')">⎘ Copy</button>`:''}</td>
      <td class="ot-logic">${esc(r.seo_logic||'—')}</td>
    </tr>`;
  }).join('');
}
function copyVal(btn,text){
  navigator.clipboard.writeText(text).then(()=>{
    btn.textContent='✓ Copied';btn.classList.add('copied');
    setTimeout(()=>{btn.textContent='⎘ Copy';btn.classList.remove('copied');},2000);
  }).catch(()=>{alert('Copy failed. Value: '+text);});
}
async function downloadOptimizer(){
  try{
    const res=await fetch(`${API}/export-optimizer`);
    if(!res.ok){const err=await res.json().catch(()=>({}));throw new Error(err.detail||'Export failed');}
    const a=document.createElement('a');
    a.href=URL.createObjectURL(await res.blob());
    a.download='seo_optimization_table.xlsx';a.click();
    closeExportModal();
  }catch(e){alert('Optimizer export error: '+e.message);}
}

// ── TECH SEO ─────────────────────────────────────────────────────────────────
let techSEOPages = [], techSEOSiteData = null, techSEODetailIdx = 0;

async function runTechSEO() {
  if (!allResults.length) { alert('Run a crawl first.'); return; }
  bar('t', true, '🔬 Running Technical SEO audit…');
  btnSet('tseo-btn', true);
  document.getElementById('tseo-panel').classList.add('show');
  document.getElementById('tseo-tbody').innerHTML =
    '<tr><td colspan="13"><div class="opt-empty">Auditing pages…</div></td></tr>';

  try {
    // Fire both requests in parallel
    const [tseoRes, siteRes] = await Promise.all([
      fetch(`${API}/technical-seo`),
      fetch(`${API}/site-audit`),
    ]);

    if (!tseoRes.ok) throw new Error((await tseoRes.json()).detail || 'Tech SEO failed');
    if (!siteRes.ok) throw new Error((await siteRes.json()).detail || 'Site audit failed');

    const tseoData = await tseoRes.json();
    techSEOSiteData = await siteRes.json();
    techSEOPages    = tseoData.pages || [];

    renderTechSEOSummary(tseoData.summary, techSEOSiteData);
    renderTechSEODomain(techSEOSiteData);
    renderTechSEOTable();

    const n = techSEOPages.length;
    const totalIssues = techSEOPages.reduce((s, p) => s + (p.issue_count || 0), 0);
    bar('t', false, `✓ Tech SEO complete — ${n} pages audited · ${totalIssues} issues found`);
    set('tseo-row-count', `${n} pages`);
  } catch (e) {
    bar('t', false, `✗ ${e.message}`);
    document.getElementById('tseo-tbody').innerHTML =
      `<tr><td colspan="13"><div class="opt-empty" style="color:var(--red)">Error: ${esc(e.message)}</div></td></tr>`;
  } finally {
    btnSet('tseo-btn', false);
  }
}

function renderTechSEOSummary(summary, siteData) {
  if (!summary) return;
  const score = summary.avg_tech_score ?? '—';
  const grade = summary.site_grade ?? '—';
  const gradeColor = { A:'var(--green)', B:'var(--cyan)', C:'var(--yellow)', D:'var(--red)', F:'var(--red)' }[grade] || 'var(--dim)';
  set('ts-score', score);
  document.getElementById('ts-score').style.color = gradeColor;
  set('ts-grade', `Grade ${grade}`);
  document.getElementById('ts-grade').style.color = gradeColor;
  set('ts-total', summary.total_pages ?? '—');
  const idx = summary.indexability || {};
  set('ts-indexable', idx.indexable_total ?? '—');
  set('ts-idx-pct', idx.indexable_pct != null ? `${idx.indexable_pct}% indexable` : '—');
  const totalIssues = techSEOPages.reduce((s, p) => s + (p.issue_count || 0), 0);
  set('ts-issues', totalIssues);
  if (siteData && siteData.https_summary) {
    const h = siteData.https_summary;
    set('ts-https', `${h.https_pct}%`);
    set('ts-https-status', h.status === 'all_https' ? '✓ All pages' :
      h.status === 'partial' ? '⚠ Partial' : '✗ None');
    document.getElementById('ts-https').style.color =
      h.status === 'all_https' ? 'var(--green)' : h.status === 'partial' ? 'var(--yellow)' : 'var(--red)';
  }
}

function renderTechSEODomain(siteData) {
  if (!siteData) return;

  const robEl  = document.getElementById('td-robots');
  const siteEl = document.getElementById('td-sitemap');
  const statEl = document.getElementById('td-status-dist');

  // robots.txt
  const r = siteData.robots_txt || {};
  if (r.blocks_googlebot) {
    robEl.textContent = '✗ Blocks Googlebot!';
    robEl.className = 'tseo-domain-val err';
  } else if (r.accessible) {
    robEl.textContent = '✓ Accessible · No blocks';
    robEl.className = 'tseo-domain-val ok';
  } else if (r.status_code === 404) {
    robEl.textContent = '⚠ Not Found (404)';
    robEl.className = 'tseo-domain-val warn';
  } else {
    robEl.textContent = `✗ Error (${r.status_code || '?'})`;
    robEl.className = 'tseo-domain-val err';
  }

  // sitemap.xml
  const s = siteData.sitemap || {};
  if (s.is_xml) {
    siteEl.textContent = `✓ Found · ${s.url_count} URLs`;
    siteEl.className = 'tseo-domain-val ok';
  } else if (s.accessible) {
    siteEl.textContent = '⚠ Found but not valid XML';
    siteEl.className = 'tseo-domain-val warn';
  } else if (s.status_code === 404) {
    siteEl.textContent = '⚠ Not Found (404)';
    siteEl.className = 'tseo-domain-val warn';
  } else {
    siteEl.textContent = `✗ Error (${s.status_code || '?'})`;
    siteEl.className = 'tseo-domain-val err';
  }

  // HTTP status distribution
  const dist = siteData.status_distribution || {};
  const distStr = Object.entries(dist)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}: ${v}`)
    .join(' · ');
  statEl.textContent = distStr || '—';
  statEl.className = 'tseo-domain-val ok';
}

function renderTechSEOTable() {
  const idxF    = document.getElementById('tseo-idx-filter').value;
  const gradeF  = document.getElementById('tseo-grade-filter').value;
  const srch    = (document.getElementById('tseo-search').value || '').toLowerCase();

  let rows = techSEOPages.filter(p => {
    if (idxF   && (p.indexability || {}).status !== idxF) return false;
    if (gradeF && p.tech_grade !== gradeF) return false;
    if (srch   && !(p.url || '').toLowerCase().includes(srch)) return false;
    return true;
  });

  document.getElementById('tseo-count-label').textContent = `${rows.length} page${rows.length !== 1 ? 's' : ''}`;
  const tbody = document.getElementById('tseo-tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="13"><div class="opt-empty">No pages match the current filters.</div></td></tr>';
    return;
  }

  tbody.innerHTML = rows.map((p, i) => {
    const idx    = p.indexability || {};
    const score  = p.tech_score ?? '—';
    const grade  = p.tech_grade ?? '—';
    const gc     = { A:'var(--green)', B:'var(--cyan)', C:'var(--yellow)', D:'var(--red)', F:'var(--red)' }[grade] || 'var(--dim)';
    const tScore = p.title?.score ?? '—';
    const mScore = p.meta?.score  ?? '—';
    const cScore = p.canonical?.score ?? '—';
    const h1ok   = (p.headings?.h1_count === 1);
    const conDep = p.content?.depth ?? '—';
    const isHttps = p.url_analysis?.is_https;
    const ic     = p.issue_count || 0;
    const fullIdx = techSEOPages.indexOf(p);
    return `<tr onclick="openTechSEODetail(${fullIdx})">
      <td class="tseo-url" title="${esc(p.url)}">${esc(p.url)}</td>
      <td>${statusBadge(p.status_code)}</td>
      <td>${indexBadge(idx)}</td>
      <td class="tseo-score-cell" style="color:${gc}">${score}</td>
      <td><span class="tseo-grade tseo-grade-${(grade||'').toLowerCase()}">${grade}</span></td>
      <td><span class="${tScore>=80?'ss-ok':'ss-bad'} sstatus" style="font-size:9px">${tScore}</span></td>
      <td><span class="${mScore>=80?'ss-ok':'ss-bad'} sstatus" style="font-size:9px">${mScore}</span></td>
      <td><span class="${cScore>=80?'ss-ok':'ss-bad'} sstatus" style="font-size:9px">${cScore}</span></td>
      <td>${h1ok ? '<span style="color:var(--green);font-size:10px">✓</span>' : '<span style="color:var(--red);font-size:10px">✗</span>'}</td>
      <td><span style="font-size:10px;color:${conDep==='rich'?'var(--green)':conDep==='medium'?'var(--cyan)':'var(--red)'}">${conDep}</span></td>
      <td>${isHttps ? '<span style="color:var(--green);font-size:10px">✓</span>' : '<span style="color:var(--red);font-size:10px">✗</span>'}</td>
      <td><span class="tseo-issue-count ${ic===0?'zero':''}">${ic}</span></td>
      <td><button class="tseo-detail-btn" onclick="event.stopPropagation();openTechSEODetail(${fullIdx})">Detail →</button></td>
    </tr>`;
  }).join('');
}

function indexBadge(idx) {
  const s = idx.status || 'unknown';
  const l = idx.label  || 'Unknown';
  const cls = {
    indexable:               'idx-ok',
    likely_indexable:        'idx-likely',
    canonical_mismatch:      'idx-canon',
    not_indexable_redirect:  'idx-redirect',
    not_indexable_error:     'idx-error',
    unknown:                 'idx-unknown',
  }[s] || 'idx-unknown';
  return `<span class="idx-badge ${cls}" title="${esc(idx.reason||'')}">${esc(l)}</span>`;
}

function openTechSEODetail(idx) {
  techSEODetailIdx = idx;
  renderTechSEODetail();
  document.getElementById('tseo-overlay').classList.add('open');
}

function renderTechSEODetail() {
  const p = techSEOPages[techSEODetailIdx];
  if (!p) return;

  set('td-url', p.url);
  set('td-sc-val', p.tech_score ?? '—');
  set('td-sc-grade', p.tech_grade ?? '—');
  const circle = document.getElementById('td-circle');
  const g = (p.tech_grade || '').toLowerCase();
  circle.className = `tseo-score-circle sc-${g || 'f'}`;

  const idx = p.indexability || {};
  set('td-sm-title', `Tech Score — ${idx.label || 'Unknown'} Indexability`);
  set('td-sm-idx', idx.reason || '');

  document.getElementById('td-http-badge').innerHTML = statusBadge(p.status_code);

  // Issues list
  const issEl = document.getElementById('td-all-issues');
  issEl.innerHTML = (p.all_issues || []).length
    ? p.all_issues.map(i => `<span class="itag" style="font-size:9px">${esc(i)}</span>`).join('')
    : '<span style="color:var(--green);font-size:10px">✓ No issues</span>';

  // Navigation
  set('td-cur', techSEODetailIdx + 1);
  set('td-tot', techSEOPages.length);
  document.getElementById('td-prev').disabled = techSEODetailIdx === 0;
  document.getElementById('td-next').disabled = techSEODetailIdx === techSEOPages.length - 1;

  // Component grid
  const components = [
    { name: 'Title Tag',       data: p.title,        fields: ['value','length','status'], scoreKey: 'score' },
    { name: 'Meta Description',data: p.meta,         fields: ['value','length','status'], scoreKey: 'score' },
    { name: 'Canonical',       data: p.canonical,    fields: ['value','status'],           scoreKey: 'score' },
    { name: 'Headings',        data: p.headings,     fields: ['h1_count','h2_count','h1_status'], scoreKey: 'score' },
    { name: 'Open Graph',      data: p.open_graph,   fields: ['completeness','title','description'], scoreKey: 'score' },
    { name: 'Content',         data: p.content,      fields: ['word_count','depth','link_density'], scoreKey: 'score' },
    { name: 'URL Structure',   data: p.url_analysis, fields: ['is_https','depth','length'], scoreKey: 'score' },
    { name: 'Images',          data: p.images,       fields: ['alts_captured','alts_with_text','status'], scoreKey: 'score' },
    { name: 'HTTP Status',     data: p.status,       fields: ['code'],                     scoreKey: 'score' },
  ];

  document.getElementById('td-component-grid').innerHTML = components.map(c => {
    const d = c.data || {};
    const sc = d[c.scoreKey] ?? 0;
    const gc = sc >= 80 ? '#10B981' : sc >= 55 ? '#F59E0B' : '#EF4444';
    const issues = d.issues || [];
    const fieldLines = c.fields.map(f => {
      const v = d[f];
      if (v === undefined || v === null) return '';
      const label = f.replace(/_/g,' ');
      return `<div class="tseo-comp-val">${esc(label)}: ${esc(String(v))}</div>`;
    }).filter(Boolean).join('');
    const issueLines = issues.length
      ? issues.map(i => `<div class="tseo-comp-issue">✗ ${esc(i)}</div>`).join('')
      : `<div class="tseo-comp-ok">✓ No issues</div>`;
    return `<div class="tseo-component">
      <div class="tseo-comp-head">
        <span class="tseo-comp-name">${esc(c.name)}</span>
        <span class="tseo-comp-score" style="color:${gc}">${sc}</span>
      </div>
      <div class="tseo-comp-bar"><div class="tseo-comp-fill" style="width:${sc}%;background:${gc}"></div></div>
      <div class="tseo-comp-issues">${issueLines}</div>
      <div style="margin-top:4px">${fieldLines}</div>
    </div>`;
  }).join('');
}

function navTechPage(dir) {
  const n = techSEODetailIdx + dir;
  if (n < 0 || n >= techSEOPages.length) return;
  techSEODetailIdx = n;
  renderTechSEODetail();
}

function closeTechSEODetail() {
  document.getElementById('tseo-overlay').classList.remove('open');
}
function tseoOverlayClick(e) {
  if (e.target === document.getElementById('tseo-overlay')) closeTechSEODetail();
}

async function downloadTechSEO() {
  try {
    const res = await fetch(`${API}/export-technical-seo`);
    if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || 'Export failed'); }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await res.blob());
    a.download = 'technical_seo_audit.xlsx';
    a.click();
  } catch (e) { alert('Tech SEO export error: ' + e.message); }
}

function toggleFaq(item){
  const isOpen=item.classList.contains('open');
  document.querySelectorAll('.faq-item').forEach(i=>{
    i.classList.remove('open');
    const c=i.querySelector('.faq-chev .material-symbols-outlined');
    if(c)c.textContent='expand_more';
  });
  if(!isOpen){
    item.classList.add('open');
    const c=item.querySelector('.faq-chev .material-symbols-outlined');
    if(c)c.textContent='expand_less';
  }
}

document.addEventListener('keydown',e=>{
  if(document.getElementById('ai-overlay').classList.contains('open')){
    if(e.key==='Escape')closeAiSetup(); return;
  }
  if(document.getElementById('tseo-overlay').classList.contains('open')){
    if(e.key==='ArrowLeft')navTechPage(-1);
    if(e.key==='ArrowRight')navTechPage(1);
    if(e.key==='Escape')closeTechSEODetail();
    return;
  }
  if(!document.getElementById('popup-overlay').classList.contains('open'))return;
  if(e.key==='ArrowLeft')navPage(-1);
  if(e.key==='ArrowRight')navPage(1);
  if(e.key==='Escape')closePopup();
});
document.getElementById('url-input').addEventListener('keydown',e=>{if(e.key==='Enter')startCrawlHero();});

// ─────────────────────────────────────────────────────────────────────────────
// CLIENT-SIDE META ANALYZER (zero backend dependency)
// ─────────────────────────────────────────────────────────────────────────────
(function(){
  const titleEl = document.getElementById('af-title');
  const metaEl  = document.getElementById('af-meta');
  if(titleEl) titleEl.addEventListener('input',()=>{
    const n = titleEl.value.length;
    const hint = document.getElementById('af-title-count');
    hint.textContent = `${n} characters — ideal: 50–60`;
    hint.style.color = n>=50&&n<=60 ? 'var(--green)' : n>0&&n<30 ? 'var(--red)' : n>60 ? 'var(--yellow)' : 'var(--muted)';
  });
  if(metaEl) metaEl.addEventListener('input',()=>{
    const n = metaEl.value.length;
    const hint = document.getElementById('af-meta-count');
    hint.textContent = `${n} characters — ideal: 120–160`;
    hint.style.color = n>=120&&n<=160 ? 'var(--green)' : n>0&&n<70 ? 'var(--red)' : n>160 ? 'var(--yellow)' : 'var(--muted)';
  });
})();

function runAnalyzer() {
  const title = (document.getElementById('af-title').value||'').trim();
  const meta  = (document.getElementById('af-meta').value||'').trim();
  const h1    = (document.getElementById('af-h1').value||'').trim();
  const kw    = (document.getElementById('af-kw').value||'').trim().toLowerCase();

  if(!title && !meta && !h1){ alert('Fill in at least one field.'); return; }

  const checks = [];

  // Title checks
  if(!title){
    checks.push({label:'Title Tag',score:0,note:'Missing. Google uses this as your search result headline.',color:'var(--red)'});
  } else if(title.length < 30){
    checks.push({label:'Title Tag',score:30,note:`${title.length} chars — too short. Expand to 50–60 chars.`,color:'var(--yellow)'});
  } else if(title.length > 65){
    checks.push({label:'Title Tag',score:55,note:`${title.length} chars — truncated in SERPs. Keep under 60.`,color:'var(--yellow)'});
  } else {
    checks.push({label:'Title Tag',score:100,note:`${title.length} chars — good length.`,color:'var(--green)'});
  }

  // Keyword in title
  if(kw && title){
    if(title.toLowerCase().includes(kw))
      checks.push({label:'Keyword in Title',score:100,note:`"${kw}" found in title.`,color:'var(--green)'});
    else
      checks.push({label:'Keyword in Title',score:20,note:`"${kw}" not in title. Add it for ranking signal.`,color:'var(--red)'});
  }

  // Meta description
  if(!meta){
    checks.push({label:'Meta Description',score:0,note:'Missing. Google may auto-generate one — usually badly.',color:'var(--red)'});
  } else if(meta.length < 70){
    checks.push({label:'Meta Description',score:30,note:`${meta.length} chars — too short. Aim for 120–160.`,color:'var(--yellow)'});
  } else if(meta.length > 165){
    checks.push({label:'Meta Description',score:60,note:`${meta.length} chars — gets truncated. Trim to under 160.`,color:'var(--yellow)'});
  } else {
    checks.push({label:'Meta Description',score:100,note:`${meta.length} chars — good.`,color:'var(--green)'});
  }

  // H1
  if(!h1){
    checks.push({label:'H1 Tag',score:0,note:'Missing. Every page needs exactly one H1.',color:'var(--red)'});
  } else {
    const h1Score = (kw && h1.toLowerCase().includes(kw)) ? 100 : 70;
    const h1Note  = kw && !h1.toLowerCase().includes(kw)
      ? `H1 present but missing keyword "${kw}".`
      : 'H1 present.';
    checks.push({label:'H1 Tag',score:h1Score,note:h1Note,color:h1Score===100?'var(--green)':'var(--yellow)'});
  }

  // Title ≠ H1
  if(title && h1 && title.toLowerCase()===h1.toLowerCase())
    checks.push({label:'Title ≠ H1',score:40,note:'Title and H1 are identical. Differentiate them for broader keyword coverage.',color:'var(--yellow)'});
  else if(title && h1)
    checks.push({label:'Title ≠ H1',score:100,note:'Title and H1 are distinct — good.',color:'var(--green)'});

  // Keyword in meta
  if(kw && meta){
    if(meta.toLowerCase().includes(kw))
      checks.push({label:'Keyword in Meta',score:100,note:`"${kw}" found in meta description.`,color:'var(--green)'});
    else
      checks.push({label:'Keyword in Meta',score:50,note:`"${kw}" not in meta. Include it naturally.`,color:'var(--yellow)'});
  }

  const total = Math.round(checks.reduce((s,c)=>s+c.score,0)/checks.length);
  const grade = total>=90?'A':total>=75?'B':total>=55?'C':total>=35?'D':'F';
  const gradeColor = total>=90?'var(--green)':total>=75?'var(--cyan)':total>=55?'var(--yellow)':'var(--red)';
  const verdicts = {
    A:'Well-optimised. Covers title length, keyword placement, and meta description.',
    B:'Good baseline. One or two gaps — see below.',
    C:'Moderate issues. Fix the red items first.',
    D:'Multiple critical problems. Unlikely to rank competitively without fixes.',
    F:'Missing core elements. This page is invisible to search engines.'
  };

  const scoreEl = document.getElementById('az-score');
  const gradeEl = document.getElementById('az-grade');
  scoreEl.textContent = total;
  scoreEl.style.color = gradeColor;
  gradeEl.textContent = `Grade ${grade}`;
  gradeEl.style.color = gradeColor;
  document.getElementById('az-verdict').textContent = verdicts[grade];

  document.getElementById('az-breakdown').innerHTML = checks.map(c=>`
    <div class="sb-item">
      <div class="sb-label">${c.label}</div>
      <div class="sb-bar"><div class="sb-fill" style="width:${c.score}%;background:${c.color}"></div></div>
      <div class="sb-note" style="color:${c.color==='var(--green)'?'var(--green)':c.color==='var(--red)'?'var(--red)':'var(--yellow)'}">${c.note}</div>
    </div>
  `).join('');

  document.getElementById('score-display').classList.add('show');
  document.getElementById('analyzer-sec').scrollIntoView({behavior:'smooth'});
}

// ─────────────────────────────────────────────────────────────────────────────
// SEO CHECKLIST (persisted to localStorage)
// ─────────────────────────────────────────────────────────────────────────────
const CHK_ITEMS = [
  {group:'On-Page Basics', items:[
    {id:'c1', strong:'Title tag is 50–60 characters', detail:'Not "Home" or "Welcome". Contains your primary keyword.'},
    {id:'c2', strong:'Meta description is 120–160 characters', detail:'Has a clear call-to-action. Not duplicated from title.'},
    {id:'c3', strong:'One H1 per page', detail:'Contains the primary keyword. Not the same as title tag.'},
    {id:'c4', strong:'H2–H3 headings structure the content', detail:'Logical hierarchy, not used just for visual styling.'},
    {id:'c5', strong:'Target keyword appears in the first 100 words', detail:'Natural usage — not keyword stuffed.'},
  ]},
  {group:'Technical SEO', items:[
    {id:'c6', strong:'Canonical tag is correct', detail:'Points to the preferred URL. No self-referencing loops.'},
    {id:'c7', strong:'Page returns HTTP 200', detail:'No accidental 404s or redirect chains to orphan URLs.'},
    {id:'c8', strong:'HTTPS is enforced', detail:'HTTP version redirects to HTTPS. No mixed content.'},
    {id:'c9', strong:'robots.txt allows crawling', detail:'No accidental Disallow: / on production.'},
    {id:'c10', strong:'sitemap.xml is submitted to Google Search Console', detail:'Updated automatically. No 404 URLs in the sitemap.'},
  ]},
  {group:'Content Quality', items:[
    {id:'c11', strong:'Page has at least 300 words of body content', detail:'Thin content ranks poorly. Expand or consolidate pages.'},
    {id:'c12', strong:'Content answers the search intent', detail:'Informational? Transactional? Navigational? Match it.'},
    {id:'c13', strong:'No duplicate content across pages', detail:'Check with a site: search or screaming frog.'},
    {id:'c14', strong:'Images have descriptive alt text', detail:'Not "image1.jpg" or empty strings. Keyword where relevant.'},
    {id:'c15', strong:'Content is updated within the last 12 months', detail:'Stale content gets de-prioritised by Google.'},
  ]},
  {group:'Links & Authority', items:[
    {id:'c16', strong:'Internal links use descriptive anchor text', detail:'Not "click here". Use the keyword or page topic.'},
    {id:'c17', strong:'No broken internal links (4xx)', detail:'Run the crawler to find and fix them.'},
    {id:'c18', strong:'External links open in new tab and have rel="noopener"', detail:'Security and UX best practice.'},
    {id:'c19', strong:'At least 3 internal links point to this page', detail:'Orphan pages get little crawl budget.'},
    {id:'c20', strong:'Schema markup is present (Article, Product, FAQ, etc.)', detail:'Eligible for rich results in Google SERPs.'},
  ]},
];
const TOTAL_CHK = CHK_ITEMS.reduce((s,g)=>s+g.items.length,0);
const CHK_KEY = 'crawliq_checklist_v1';

function loadChecklist() {
  const saved = JSON.parse(localStorage.getItem(CHK_KEY)||'{}');
  const grid = document.getElementById('checklist-grid');
  if(!grid) return;
  grid.innerHTML = CHK_ITEMS.map(group=>`
    <div class="chk-group">
      <div class="chk-group-title" style="color:var(--cyan)">${group.group}</div>
      ${group.items.map(item=>`
        <div class="chk-item${saved[item.id]?' checked':''}" id="chkrow-${item.id}">
          <input type="checkbox" id="${item.id}" ${saved[item.id]?'checked':''}
            onchange="toggleChk('${item.id}',this.checked)"/>
          <label for="${item.id}">
            <strong>${item.strong}</strong>${item.detail}
          </label>
        </div>
      `).join('')}
    </div>
  `).join('');
  updateChkProgress();
}

function toggleChk(id, checked) {
  const saved = JSON.parse(localStorage.getItem(CHK_KEY)||'{}');
  if(checked) saved[id]=true; else delete saved[id];
  localStorage.setItem(CHK_KEY, JSON.stringify(saved));
  const row = document.getElementById('chkrow-'+id);
  if(row) row.classList.toggle('checked', checked);
  updateChkProgress();
}

function updateChkProgress() {
  const saved = JSON.parse(localStorage.getItem(CHK_KEY)||'{}');
  const done = Object.keys(saved).length;
  document.getElementById('chk-done').textContent = done;
  document.getElementById('chk-label').textContent = `${done} / ${TOTAL_CHK} done`;
  document.getElementById('chk-fill').style.width = `${Math.round((done/TOTAL_CHK)*100)}%`;
}

function resetChecklist() {
  if(!confirm('Reset all checkboxes?')) return;
  localStorage.removeItem(CHK_KEY);
  loadChecklist();
}

// Init checklist on load
document.addEventListener('DOMContentLoaded', ()=>{ loadChecklist(); });

// ══════════════════════════════════════════════════════════════
// AI SETUP MODAL
// ══════════════════════════════════════════════════════════════
const _AI_KEY_PATTERNS=[
  {prefix:'gsk_',    provider:'groq',   label:'Groq'},
  {prefix:'AIzaSy',  provider:'gemini', label:'Google Gemini'},
  {prefix:'sk-ant-', provider:'claude', label:'Anthropic Claude'},
  {prefix:'sk-',     provider:'openai', label:'OpenAI'},
];
const _AI_NO_KEY=new Set(['ollama','rules']);
let _aiSelectedProvider='groq';

function _aiDetect(key){
  for(const {prefix,provider,label} of _AI_KEY_PATTERNS)
    if(key.trim().startsWith(prefix)) return {provider,label};
  return null;
}

async function openAiSetup(){
  document.getElementById('ai-overlay').classList.add('open');
  aiSetFeedback('','');
  try{
    const cfg=await(await fetch(`${API}/ai-config`)).json();
    _aiSelectedProvider=cfg.provider||'groq';
    aiHighlightCard(_aiSelectedProvider);
    aiUpdateNoKey(_aiSelectedProvider);
    const dot=document.getElementById('ai-dot');
    dot.className='ai-current-dot '+(cfg.configured?'active':'inactive');
    document.getElementById('ai-cur-provider').textContent=cfg.label||cfg.provider||'—';
    document.getElementById('ai-cur-hint').textContent=
      cfg.configured?`Key: ${cfg.key_hint} · Ready`:'No API key set — AI features disabled';
  }catch{document.getElementById('ai-cur-provider').textContent='Could not load config';}
}

function closeAiSetup(){
  document.getElementById('ai-overlay').classList.remove('open');
  document.getElementById('ai-key-input').value='';
  aiSetFeedback('','');
  document.getElementById('ai-detect-badge').classList.remove('show');
}

function aiOverlayClick(e){if(e.target===document.getElementById('ai-overlay'))closeAiSetup();}

function aiSelectProvider(p){
  _aiSelectedProvider=p;
  aiHighlightCard(p);
  aiUpdateNoKey(p);
  const key=document.getElementById('ai-key-input').value;
  if(key)aiOnKeyInput(key);
  aiSetFeedback('','');
}

function aiHighlightCard(p){
  document.querySelectorAll('#ai-provider-grid .ai-provider-card').forEach(c=>{
    c.classList.toggle('selected',c.dataset.provider===p);
  });
}

function aiUpdateNoKey(p){
  const needs=!_AI_NO_KEY.has(p);
  document.getElementById('ai-key-group').style.display=needs?'':'none';
  document.getElementById('ai-no-key-notice').classList.toggle('show',!needs);
}

function aiOnKeyInput(key){
  const badge=document.getElementById('ai-detect-badge');
  const det=_aiDetect(key);
  if(det){
    document.getElementById('ai-detect-text').textContent=`Detected: ${det.label} — switching`;
    badge.classList.add('show');
    _aiSelectedProvider=det.provider;
    aiHighlightCard(det.provider);
    aiUpdateNoKey(det.provider);
  }else{badge.classList.remove('show');}
}

function aiToggleKeyVisible(){
  const inp=document.getElementById('ai-key-input');
  const btn=document.getElementById('ai-key-toggle');
  if(inp.type==='password'){inp.type='text';btn.textContent='🙈';btn.title='Hide key';}
  else{inp.type='password';btn.textContent='👁';btn.title='Show key';}
}

function aiSetFeedback(msg,type){
  const el=document.getElementById('ai-feedback');
  el.textContent=msg;
  el.className='ai-feedback'+(msg?` show ${type}`:'');
}

async function aiApplyKey(){
  const key=document.getElementById('ai-key-input').value.trim();
  const provider=_aiSelectedProvider;
  if(!_AI_NO_KEY.has(provider)&&!key){aiSetFeedback('Please enter your API key before applying.','err');return;}
  const btn=document.getElementById('ai-apply-btn');
  btn.disabled=true;btn.textContent='Applying…';aiSetFeedback('','');
  try{
    const res=await fetch(`${API}/set-api-key`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({provider,api_key:key}),
    });
    const data=await res.json();
    if(!res.ok)throw new Error(data.detail||'Failed');
    aiSetFeedback(`✓ ${data.label} activated — key ${data.key_hint}`,'ok');
    await checkGemini();
    const dot=document.getElementById('ai-dot');
    dot.className='ai-current-dot '+(data.configured?'active':'inactive');
    document.getElementById('ai-cur-provider').textContent=data.label;
    document.getElementById('ai-cur-hint').textContent=
      data.configured?`Key: ${data.key_hint} · Ready`:'No key set';
    document.getElementById('ai-key-input').value='';
    document.getElementById('ai-detect-badge').classList.remove('show');
  }catch(e){aiSetFeedback(`✗ ${e.message}`,'err');}
  finally{btn.disabled=false;btn.textContent='Apply';}
}

async function aiTestConnection(){
  const btn=document.getElementById('ai-test-btn');
  btn.disabled=true;btn.textContent='Testing…';aiSetFeedback('','');
  try{
    const d=await(await fetch(`${API}/gemini-health`)).json();
    if(d.configured){
      const m=d.model?` · model: ${d.model}`:'';
      aiSetFeedback(`✓ Connected to ${d.provider||'AI'}${m}`,'ok');
    }else{aiSetFeedback('✗ Not configured — set an API key first','err');}
  }catch(e){aiSetFeedback(`✗ Connection test failed: ${e.message}`,'err');}
  finally{btn.disabled=false;btn.textContent='Test Connection';}
}

// ════════════════════════════════════════════════════════════════
// ── COMPETITOR ANALYSIS JS ───────────────────────────────────────
// ════════════════════════════════════════════════════════════════

let compTaskId    = null;
let compPollTimer = null;
let compResults   = null;
let radarChart    = null;
let cwvChart      = null;

const DIM_LABELS = {
  technical:'Technical SEO', on_page:'On-Page SEO', content:'Content Depth',
  eeat:'E-E-A-T', ctr:'CTR Potential', keywords:'Keyword Coverage', page_speed:'Page Speed'
};

function loadECharts(cb){
  if(typeof echarts!=='undefined'){if(cb)cb();return;}
  const s=document.createElement('script');
  s.src='https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js';
  s.onload=()=>{if(cb)cb();};
  document.head.appendChild(s);
}

function addCompetitorRow(){
  const existing=document.querySelectorAll('.comp-input').length;
  if(existing>=5){alert('Maximum 5 competitors.');return;}
  const row=document.createElement('div');
  row.className='comp-url-row';
  row.innerHTML=`<span class="comp-url-label">Competitor ${existing+1}</span>
    <input class="comp-input" type="text" placeholder="https://competitor${existing+1}.com"/>`;
  document.getElementById('comp-input-grid').appendChild(row);
}

// ── Safe JSON helper (handles HF Space cold-start HTML responses) ─────────────
async function safeJson(res){
  const ct=res.headers.get('content-type')||'';
  if(!ct.includes('application/json')){
    const txt=await res.text();
    const lower=txt.toLowerCase();
    if(lower.includes('space')||lower.includes('waking')||lower.includes('sleeping')||res.status>=502){
      const err=new Error('HF Space is waking up — auto-retrying…');
      err.isColdStart=true;
      throw err;
    }
    throw new Error(`Server error (HTTP ${res.status}). Please retry.`);
  }
  return res.json();
}

// ── Wait for HF Space to be ready (pings /health until 200 JSON) ─────────────
async function waitForSpace(maxWaitMs=300000){
  const start=Date.now();let elapsed=0;
  while(elapsed<maxWaitMs){
    try{
      const r=await fetch(`${API}/healthz`,{cache:'no-store'});
      const ct=r.headers.get('content-type')||'';
      if(r.ok&&ct.includes('application/json'))return true;
    }catch(_){}
    elapsed=Date.now()-start;
    const remaining=Math.round((maxWaitMs-elapsed)/1000);
    const secs=Math.round(elapsed/1000);
    compBar(true,`Space is waking up… ${secs}s elapsed (max 5 min, ${remaining}s left)`);
    await new Promise(r=>setTimeout(r,5000));
    elapsed=Date.now()-start;
  }
  return false;
}

// ── Start analysis (waits for space, then fires) ──────────────────────────────
async function startCompAnalysis(){
  const target=document.getElementById('comp-target').value.trim();
  if(!target){alert('Enter your site URL.');return;}
  const competitors=Array.from(document.querySelectorAll('.comp-input'))
    .map(i=>i.value.trim()).filter(Boolean);
  if(!competitors.length){alert('Enter at least one competitor URL.');return;}

  compResults=null;
  document.getElementById('comp-results').style.display='none';
  document.getElementById('comp-export-btn').disabled=true;
  document.getElementById('comp-analyze-btn').disabled=true;
  compBar(true,'Checking server…');

  // Step 1: probe health — if cold, wait for space to wake up
  try{
    const probe=await fetch(`${API}/healthz`,{cache:'no-store'});
    const ct=probe.headers.get('content-type')||'';
    if(!probe.ok||!ct.includes('application/json')){
      compBar(true,'Space is waking up… 0s elapsed (max 5 min)');
      const ready=await waitForSpace();
      if(!ready){compBar(false);alert('Space did not wake up within 5 minutes. Please try again later.');document.getElementById('comp-analyze-btn').disabled=false;return;}
    }
  }catch(_){
    compBar(true,'Space is waking up… 0s elapsed (max 5 min)');
    const ready=await waitForSpace();
    if(!ready){compBar(false);alert('Space did not wake up within 5 minutes. Please try again later.');document.getElementById('comp-analyze-btn').disabled=false;return;}
  }

  // Step 2: space is ready — fire the analysis
  compBar(true,'Starting competitor analysis…');
  try{
    const res=await fetch(`${API}/competitor/analyze`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({target_url:target,competitor_urls:competitors}),
    });
    if(!res.ok){const e=await safeJson(res);throw new Error(e.detail||'Failed');}
    const data=await safeJson(res);
    compTaskId=data.task_id;
    compBar(true,`Crawling ${1+competitors.length} sites + fetching Core Web Vitals…`);
    if(compPollTimer)clearInterval(compPollTimer);
    compPollTimer=setInterval(async()=>{
      try{
        const s=await safeJson(await fetch(`${API}/competitor/status/${compTaskId}`));
        if(s.status==='done'){
          clearInterval(compPollTimer);compBar(false);
          document.getElementById('comp-analyze-btn').disabled=false;
          await loadCompResultsById(compTaskId);
          loadCompHistory();
        }else if(s.status==='error'){
          clearInterval(compPollTimer);compBar(false);
          document.getElementById('comp-analyze-btn').disabled=false;
          alert(`Analysis error: ${s.error_msg}`);
        }
      }catch(e){console.error('Comp poll error:',e);}
    },4000);
  }catch(e){
    compBar(false);
    alert(`Analysis failed: ${e.message}`);
    document.getElementById('comp-analyze-btn').disabled=false;
  }
}

async function loadCompResultsById(taskId){
  try{
    const res=await fetch(`${API}/competitor/results/${taskId}`);
    const data=await safeJson(res);
    if(data.status==='running')return;
    compResults=data.results;
    renderCompResults(compResults);
    document.getElementById('comp-export-btn').disabled=false;
  }catch(e){console.error('Failed to load comp results:',e);}
}

function renderCompResults(r){
  if(!r)return;
  document.getElementById('comp-results').style.display='';
  renderCompScoreCards(r);
  renderCompDimTable(r);
  renderCompGapTable(r);
  renderCompActions(r);
  loadECharts(()=>{renderCompCharts(r);});
  document.getElementById('competitor-sec').scrollIntoView({behavior:'smooth'});
}

function renderCompScoreCards(r){
  const el=document.getElementById('comp-scores');el.innerHTML='';
  const target=r.target_url;
  (r.sites||[]).forEach(site=>{
    const comp=site.scores?.composite??0;
    const isTarget=site.url===target;
    const color=comp>=70?'var(--green)':comp>=45?'var(--yellow)':'var(--red)';
    const card=document.createElement('div');
    card.className='comp-score-card'+(isTarget?' is-target':'');
    card.innerHTML=`<div class="cs-domain">${isTarget?'★ ':''} ${site.domain||site.url}</div>
      <div class="cs-val" style="color:${color}">${Math.round(comp)}</div>
      <div class="cs-lbl">Composite Score</div>`;
    el.appendChild(card);
  });
}

function renderCompDimTable(r){
  const sites=r.sites||[];const target=r.target_url;
  const dims=Object.keys(DIM_LABELS);
  document.getElementById('dim-thead-row').innerHTML='<th>Dimension</th>'+
    sites.map(s=>`<th style="${s.url===target?'color:var(--cyan)':''}">${s.domain||s.url}</th>`).join('');
  const tbody=document.getElementById('dim-tbody');tbody.innerHTML='';
  dims.forEach(dim=>{
    const tr=document.createElement('tr');
    let cells=`<td class="dim-lbl">${DIM_LABELS[dim]}</td>`;
    sites.forEach(site=>{
      const val=Math.round(site.scores?.[dim]??0);
      const cls=val>=70?'dim-score-good':val>=45?'dim-score-mid':'dim-score-bad';
      const barC=val>=70?'var(--green)':val>=45?'var(--yellow)':'var(--red)';
      cells+=`<td><div class="dim-bar-wrap">
        <span class="dim-score ${cls}">${val}</span>
        <div class="dim-bar-bg"><div class="dim-bar-fill" style="width:${val}%;background:${barC}"></div></div>
      </div></td>`;
    });
    tr.innerHTML=cells;tbody.appendChild(tr);
  });
}

function renderCompGapTable(r){
  const gaps=r.keyword_gaps||[];
  document.getElementById('comp-gap-count').textContent=`(${gaps.length} found)`;
  const tbody=document.getElementById('comp-gap-tbody');
  if(!gaps.length){tbody.innerHTML='<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:16px">No keyword gaps detected.</td></tr>';return;}
  tbody.innerHTML=gaps.slice(0,30).map(g=>`<tr>
    <td class="gt-kw">${g.keyword}</td>
    <td><div style="display:flex;align-items:center;gap:6px">
      <div class="opp-bar" style="width:${Math.round(g.opportunity_score)}%"></div>
      <span class="gt-opp">${Math.round(g.opportunity_score)}</span></div></td>
    <td>${g.competitor_count}</td>
    <td class="gt-found">${(g.found_in||[]).join(', ')}</td></tr>`).join('');
}

function renderCompActions(r){
  const actions=r.actions||[];
  const el=document.getElementById('comp-action-list');
  if(!actions.length){el.innerHTML='<div style="color:var(--muted);font-size:11px;padding:12px">No priority actions identified.</div>';return;}
  el.innerHTML=actions.map(a=>`
    <div class="comp-action-item priority-${a.priority}">
      <div style="flex:0 0 52px;text-align:center">
        <div style="font-size:9px;font-weight:700;padding:2px 6px;border-radius:4px;background:${a.priority==='High'?'rgba(255,77,106,.15)':a.priority==='Medium'?'rgba(255,209,102,.15)':'rgba(0,229,160,.12)'};color:${a.priority==='High'?'var(--red)':a.priority==='Medium'?'var(--yellow)':'var(--green)'}">${a.priority}</div>
        <div style="font-size:9px;color:var(--dim);margin-top:3px">${a.gap.toFixed(0)}pt gap</div>
      </div>
      <div style="flex:1">
        <div class="ca-dim">${a.label}</div>
        <div class="ca-text">${a.action}</div>
        <div class="ca-scores">Your score: ${a.target_score} · Avg competitor: ${a.avg_competitor_score}</div>
      </div></div>`).join('');
}

function renderCompCharts(r){
  const radar=r.radar||{};
  const palette=['#00e5a0','#00ffff','#a78bfa','#ffd166','#ff4d6a','#22d3ee'];
  const re=document.getElementById('radar-chart');
  if(re&&typeof echarts!=='undefined'){
    if(!radarChart)radarChart=echarts.init(re,'dark');
    radarChart.setOption({
      backgroundColor:'transparent',
      tooltip:{trigger:'item'},
      legend:{data:(radar.series||[]).map(s=>s.name),textStyle:{color:'#718096',fontSize:10},bottom:0},
      radar:{
        indicator:radar.indicators||radar.indicator||[],shape:'polygon',splitNumber:4,
        axisName:{color:'#718096',fontSize:9},
        splitLine:{lineStyle:{color:'#1e2530'}},
        splitArea:{areaStyle:{color:['rgba(0,255,255,.02)','rgba(0,255,255,.04)']}},
        axisLine:{lineStyle:{color:'#1e2530'}},
      },
      series:[{type:'radar',data:(radar.series||[]).map((s,i)=>({
        name:s.name,value:s.value,
        lineStyle:{color:palette[i%palette.length],width:2},
        areaStyle:{color:palette[i%palette.length],opacity:.08},
        itemStyle:{color:palette[i%palette.length]},
        symbol:'circle',symbolSize:4,
      }))}],
    });
  }
  const sites=r.sites||[];
  const ce=document.getElementById('cwv-chart');
  if(ce&&typeof echarts!=='undefined'){
    if(!cwvChart)cwvChart=echarts.init(ce,'dark');
    cwvChart.setOption({
      backgroundColor:'transparent',
      tooltip:{trigger:'axis'},
      xAxis:{type:'category',data:sites.map(s=>s.domain||s.url),
        axisLabel:{color:'#718096',fontSize:9,rotate:15,overflow:'break',width:80},
        axisLine:{lineStyle:{color:'#1e2530'}}},
      yAxis:{type:'value',min:0,max:100,axisLabel:{color:'#718096',fontSize:9},splitLine:{lineStyle:{color:'#1e2530'}}},
      series:[{type:'bar',data:sites.map(s=>+(s.cwv?.perf_score??0).toFixed(1)),
        itemStyle:{color:p=>p.value>=70?'#06d6a0':p.value>=50?'#ffd166':'#ff4d6a',borderRadius:[4,4,0,0]},
        label:{show:true,position:'top',fontSize:10,color:'#e2e8f0'}}],
    });
  }
}

async function loadCompHistory(){
  try{
    const data=await safeJson(await fetch(`${API}/competitor/history?limit=10`));
    const snaps=data.snapshots||[];
    const tbody=document.getElementById('comp-hist-tbody');
    const panel=document.getElementById('comp-hist-panel');
    if(!snaps.length){
      tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">No history yet.</td></tr>';
      return;
    }
    panel.style.display='';
    tbody.innerHTML=snaps.map(s=>{
      const comp=s.summary?.target_composite??'–';
      const cc=+comp>=70?'var(--green)':+comp>=45?'var(--yellow)':'var(--red)';
      const date=s.created_at?s.created_at.split('T')[0]:'–';
      const domain=(s.target_url||'').replace(/https?:\/\//,'').replace(/\/.*/,'');
      const nc=(s.competitor_urls||[]).length;
      const sc=s.status==='done'?'var(--green)':s.status==='error'?'var(--red)':'var(--yellow)';
      return `<tr onclick="loadCompResultsById('${s.task_id}')">
        <td style="color:var(--cyan)">${domain}</td>
        <td style="color:var(--dim)">${nc} site${nc!==1?'s':''}</td>
        <td><span style="font-weight:800;color:${cc}">${typeof comp==='number'?Math.round(comp):comp}</span></td>
        <td><span style="color:${sc}">${s.status}</span></td>
        <td style="color:var(--dim)">${date}</td>
        <td>
          <button class="btn btn-outline btn-sm" style="font-family:var(--mono)" onclick="event.stopPropagation();loadCompResultsById('${s.task_id}')">View</button>
          <button class="btn btn-outline btn-sm" style="font-family:var(--mono);margin-left:4px" onclick="event.stopPropagation();window.location='${API}/competitor/export/${s.task_id}'">↓</button>
        </td></tr>`;
    }).join('');
  }catch(e){console.error('History load failed:',e);}
}

function exportCompExcel(){
  if(!compTaskId)return;
  window.location=`${API}/competitor/export/${compTaskId}`;
}

function compBar(show,txt){
  const bar=document.getElementById('comp-sbar');
  const spin=document.getElementById('comp-spin');
  const msg=document.getElementById('comp-status-txt');
  if(show){bar.classList.add('show');spin.style.display='';msg.textContent=txt||'';}
  else{bar.classList.remove('show');spin.style.display='none';}
}

window.addEventListener('resize',()=>{
  if(radarChart)radarChart.resize();
  if(cwvChart)cwvChart.resize();
});

// ══════════════════════════════════════════════════════════════════════════
// SERP INTEL — Position, Difficulty, Visibility
// ══════════════════════════════════════════════════════════════════════════

function openSerpPanel(){
  document.getElementById('serp-intel-sec').style.display='';
  document.getElementById('serp-intel-sec').scrollIntoView({behavior:'smooth'});
}
function closeSerpPanel(){
  document.getElementById('serp-intel-sec').style.display='none';
}

function serpTab(name){
  ['pos','diff','vis'].forEach(t=>{
    document.getElementById(`serp-tab-${t}`).classList.toggle('serp-tab-active', t===name);
    document.getElementById(`serp-pane-${t}`).style.display = t===name ? '' : 'none';
  });
}

async function runBulkSerp(){
  const domain = document.getElementById('serp-domain').value.trim();
  const kwRaw  = document.getElementById('serp-keywords').value;
  const keywords = kwRaw.split('\n').map(k=>k.trim()).filter(Boolean).slice(0,20);
  if(!domain){alert('Enter a domain.');return;}
  if(!keywords.length){alert('Enter at least one keyword.');return;}

  const btn=document.getElementById('serp-run-btn');
  btn.disabled=true;
  const bar=document.getElementById('serp-pos-bar');
  bar.style.display='flex';
  document.getElementById('serp-pos-txt').textContent=`Checking ${keywords.length} keyword${keywords.length>1?'s':''}… (this may take 30-60s)`;
  document.getElementById('serp-pos-results').style.display='none';

  try{
    const res=await fetch(`${API}/serp/bulk-position`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keywords, domain}),
    });
    if(!res.ok){const e=await safeJson(res);throw new Error(e.detail||'SERP check failed');}
    const data=await safeJson(res);
    renderSerpPositions(data);
  }catch(e){
    alert(`SERP check failed: ${e.message}`);
  }finally{
    btn.disabled=false;
    bar.style.display='none';
  }
}

function renderSerpPositions(data){
  const results=data.results||[];
  const top10   =results.filter(r=>r.in_top_10).length;
  const top30   =results.filter(r=>r.in_top_30&&!r.in_top_10).length;
  const blocked =results.filter(r=>r.blocked).length;
  const none    =results.filter(r=>!r.in_top_30&&!r.blocked).length;

  const sumEl=document.getElementById('serp-pos-summary');
  const cards=[
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px;font-size:11px">
      <span style="font-size:18px;font-weight:800;color:var(--green)">${top10}</span>
      <span style="color:var(--dim);font-family:var(--mono);margin-left:6px">In Top 10</span></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px;font-size:11px">
      <span style="font-size:18px;font-weight:800;color:var(--yellow)">${top30}</span>
      <span style="color:var(--dim);font-family:var(--mono);margin-left:6px">Top 11-30</span></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px;font-size:11px">
      <span style="font-size:18px;font-weight:800;color:var(--red)">${none}</span>
      <span style="color:var(--dim);font-family:var(--mono);margin-left:6px">Not Ranked</span></div>`,
  ];
  if(blocked>0) cards.push(
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px;font-size:11px">
      <span style="font-size:18px;font-weight:800;color:var(--dim)">${blocked}</span>
      <span style="color:var(--dim);font-family:var(--mono);margin-left:6px">⚠ Blocked</span></div>`
  );
  sumEl.innerHTML=cards.join('');

  const tbody=document.getElementById('serp-pos-tbody');
  tbody.innerHTML=results.map(r=>{
    let posColor, posText;
    if(r.blocked){
      posColor='var(--dim)'; posText='⚠ Blocked';
    } else {
      posColor=r.in_top_10?'var(--green)':r.in_top_30?'var(--yellow)':'var(--red)';
      posText=r.position!=null?`#${r.position}`:'Not ranked';
    }
    return `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:7px 10px;color:var(--text)">${r.keyword}</td>
      <td style="padding:7px 10px;text-align:center;font-weight:800;color:${posColor};font-family:var(--mono)">${posText}</td>
      <td style="padding:7px 10px;text-align:center">${r.in_top_10?'<span style="color:var(--green)">✓</span>':'<span style="color:var(--dim)">—</span>'}</td>
      <td style="padding:7px 10px;text-align:center">${r.in_top_30?'<span style="color:var(--yellow)">✓</span>':'<span style="color:var(--dim)">—</span>'}</td>
    </tr>`;
  }).join('');

  document.getElementById('serp-pos-results').style.display='';
}

async function runDifficulty(){
  const kwRaw = document.getElementById('diff-keywords').value;
  const keywords = kwRaw.split('\n').map(k=>k.trim()).filter(Boolean).slice(0,20);
  if(!keywords.length){alert('Enter at least one keyword.');return;}

  const btn=document.getElementById('diff-run-btn');
  btn.disabled=true;
  const bar=document.getElementById('diff-bar');
  bar.style.display='flex';
  document.getElementById('diff-results').style.display='none';

  try{
    const res=await fetch(`${API}/serp/difficulty`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keywords}),
    });
    if(!res.ok){const e=await safeJson(res);throw new Error(e.detail||'Difficulty check failed');}
    const data=await safeJson(res);
    renderDifficulty(data);
  }catch(e){
    alert(`Difficulty check failed: ${e.message}`);
  }finally{
    btn.disabled=false;
    bar.style.display='none';
  }
}

function renderDifficulty(data){
  const results=data.results||[];
  const tbody=document.getElementById('diff-tbody');
  tbody.innerHTML=results.map(r=>{
    const sc=r.difficulty_score??0;
    const c=sc>=76?'var(--red)':sc>=51?'var(--yellow)':sc>=26?'var(--cyan)':'var(--green)';
    const bar=`<div style="display:flex;align-items:center;gap:6px"><div style="width:${sc}%;max-width:80px;height:6px;background:${c};border-radius:3px"></div><span style="font-weight:700;color:${c}">${sc}</span></div>`;
    const domains=(r.top_domains||[]).slice(0,3).join(', ')||'—';
    return `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:7px 10px;color:var(--text)">${r.keyword}</td>
      <td style="padding:7px 10px">${bar}</td>
      <td style="padding:7px 10px;text-align:center;font-size:10px;font-weight:700;color:${c}">${r.difficulty_label||'—'}</td>
      <td style="padding:7px 10px;font-size:10px;color:var(--dim);font-family:var(--mono)">${domains}</td>
    </tr>`;
  }).join('');
  document.getElementById('diff-results').style.display='';
}

async function loadVisibility(){
  document.getElementById('vis-results').style.display='none';
  const sumEl=document.getElementById('vis-summary');
  sumEl.innerHTML='<span style="color:var(--muted);font-size:11px">Loading…</span>';
  try{
    const res=await fetch(`${API}/serp/visibility`);
    if(!res.ok){const e=await safeJson(res);throw new Error(e.detail||'Failed');}
    const data=await safeJson(res);
    renderVisibility(data);
  }catch(e){
    sumEl.innerHTML=`<span style="color:var(--red);font-size:11px">Error: ${e.message}</span>`;
  }
}

function renderVisibility(data){
  const sc=data.visibility_score??0;
  const scColor=sc>=70?'var(--green)':sc>=40?'var(--yellow)':'var(--red)';
  document.getElementById('vis-summary').innerHTML=[
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:10px 16px">
      <div style="font-size:28px;font-weight:900;color:${scColor}">${sc.toFixed(1)}</div>
      <div style="font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:1.5px">Visibility Score</div></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:10px 16px">
      <div style="font-size:22px;font-weight:800;color:var(--green)">${data.in_top_3??0}</div>
      <div style="font-size:9px;color:var(--dim)">In Top 3</div></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:10px 16px">
      <div style="font-size:22px;font-weight:800;color:var(--cyan)">${data.in_top_10??0}</div>
      <div style="font-size:9px;color:var(--dim)">In Top 10</div></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:10px 16px">
      <div style="font-size:22px;font-weight:800;color:var(--yellow)">${data.in_top_30??0}</div>
      <div style="font-size:9px;color:var(--dim)">In Top 30</div></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:10px 16px">
      <div style="font-size:22px;font-weight:800;color:var(--dim)">${data.not_ranked??0}</div>
      <div style="font-size:9px;color:var(--dim)">Not Ranked</div></div>`,
  ].join('');

  const kws=data.keywords||[];
  const tbody=document.getElementById('vis-tbody');
  tbody.innerHTML=kws.map(k=>{
    const c=k.position<=3?'var(--green)':k.position<=10?'var(--cyan)':k.position<=30?'var(--yellow)':'var(--dim)';
    const ctr=(k.expected_ctr!=null)?(k.expected_ctr*100).toFixed(1)+'%':'—';
    const shortUrl=(k.page_url||'').replace(/https?:\/\//,'').slice(0,45);
    return `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:7px 10px;color:var(--text)">${k.keyword}</td>
      <td style="padding:7px 10px;text-align:center;font-weight:800;color:${c};font-family:var(--mono)">#${k.position}</td>
      <td style="padding:7px 10px;font-size:10px;color:var(--cyan);font-family:var(--mono)">${shortUrl||'—'}</td>
      <td style="padding:7px 10px;text-align:center;color:var(--green)">${ctr}</td>
    </tr>`;
  }).join('');
  document.getElementById('vis-results').style.display='';
}


// ══════════════════════════════════════════════════════════════════════════
// SCHEDULED MONITOR
// ══════════════════════════════════════════════════════════════════════════

function openMonitorPanel(){
  document.getElementById('monitor-sec').style.display='';
  setTimeout(()=>document.getElementById('monitor-sec').scrollIntoView({behavior:'smooth'}),50);
  monTab('schedule');
  loadMonitorJobs();
}
function closeMonitorPanel(){
  document.getElementById('monitor-sec').style.display='none';
}
function monTab(name){
  ['schedule','jobs','history'].forEach(t=>{
    document.getElementById('mon-pane-'+t).style.display = t===name?'':'none';
    const btn=document.getElementById('mon-tab-'+t);
    if(btn){btn.classList.toggle('serp-tab-active', t===name);}
  });
  if(name==='jobs') loadMonitorJobs();
}

async function loadHistory(){
  const domain  = document.getElementById('hist-domain').value.trim();
  const keyword = document.getElementById('hist-keyword').value.trim();
  if(!domain){alert('Enter a domain.');return;}

  document.getElementById('hist-bar').style.display='flex';
  document.getElementById('hist-results').style.display='none';
  document.getElementById('hist-empty').style.display='none';
  try{
    // Load latest snapshot
    const latestRes = await fetch(`${API}/monitor/latest?domain=${encodeURIComponent(domain)}`);
    const latestData = latestRes.ok ? await safeJson(latestRes) : {rankings:[]};

    // Load history (per keyword or all via latest)
    let rows=[];
    if(keyword){
      const histRes = await fetch(`${API}/monitor/history?domain=${encodeURIComponent(domain)}&keyword=${encodeURIComponent(keyword)}&limit=60`);
      if(histRes.ok){ const d=await safeJson(histRes); rows=d.history||[]; }
    } else {
      // No single-keyword filter — show latest per keyword as history
      rows = (latestData.rankings||[]);
    }

    renderHistory(latestData.rankings||[], rows, !!keyword);
  }catch(e){
    document.getElementById('hist-empty').textContent=`Error: ${e.message}`;
    document.getElementById('hist-empty').style.display='';
  }finally{
    document.getElementById('hist-bar').style.display='none';
  }
}

function renderHistory(latest, rows, isKeywordFiltered){
  // Latest snapshot cards
  const latestEl=document.getElementById('hist-latest');
  if(latest.length){
    latestEl.innerHTML=latest.map(r=>{
      const posColor=r.in_top_10?'var(--green)':r.in_top_30?'var(--yellow)':'var(--red)';
      const posText=r.position!=null?`#${r.position}`:'—';
      return `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 12px;min-width:130px">
        <div style="font-size:9px;color:var(--dim);margin-bottom:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px">${r.keyword}</div>
        <div style="font-size:20px;font-weight:800;color:${posColor};font-family:var(--mono)">${posText}</div>
        <div style="font-size:8px;color:var(--dim);margin-top:2px">${r.checked_at?new Date(r.checked_at).toLocaleDateString():''}</div>
      </div>`;
    }).join('');
    latestEl.style.display='flex';
  } else {
    latestEl.style.display='none';
  }

  // History table
  const tbody=document.getElementById('hist-tbody');
  if(!rows.length){
    tbody.innerHTML=`<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:16px">${isKeywordFiltered?'No history yet for this keyword. Wait for the first monitor run.':'No tracking data yet. Schedule a job and wait for it to run.'}</td></tr>`;
  } else {
    tbody.innerHTML=rows.map(r=>{
      const posColor=r.in_top_10?'var(--green)':r.in_top_30?'var(--yellow)':'var(--red)';
      const posText=r.position!=null?`#${r.position}`:'—';
      const ts=r.checked_at?new Date(r.checked_at).toLocaleString():'—';
      return `<tr style="border-bottom:1px solid var(--border)">
        <td style="padding:7px 10px;color:var(--text)">${r.keyword}</td>
        <td style="padding:7px 10px;text-align:center;font-weight:800;color:${posColor};font-family:var(--mono)">${posText}</td>
        <td style="padding:7px 10px;text-align:center">${r.in_top_10?'<span style="color:var(--green)">✓</span>':'<span style="color:var(--dim)">—</span>'}</td>
        <td style="padding:7px 10px;text-align:center">${r.in_top_30?'<span style="color:var(--yellow)">✓</span>':'<span style="color:var(--dim)">—</span>'}</td>
        <td style="padding:7px 10px;color:var(--dim);font-size:10px">${ts}</td>
      </tr>`;
    }).join('');
  }

  document.getElementById('hist-results').style.display='';
  document.getElementById('hist-empty').style.display='none';
}

async function scheduleMonitor(){
  const domain   = document.getElementById('mon-domain').value.trim();
  const kwRaw    = document.getElementById('mon-keywords').value;
  const keywords = kwRaw.split('\n').map(k=>k.trim()).filter(Boolean).slice(0,50);
  const interval = parseFloat(document.getElementById('mon-interval').value);
  if(!domain){alert('Enter a domain.');return;}
  if(!keywords.length){alert('Enter at least one keyword.');return;}

  const msg=document.getElementById('mon-sched-msg');
  msg.style.display='';msg.style.color='var(--dim)';msg.textContent='Scheduling…';

  try{
    const res=await fetch(`${API}/monitor/schedule`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({domain, keywords, interval_hours:interval}),
    });
    if(!res.ok){const e=await safeJson(res);throw new Error(e.detail||'Failed');}
    const data=await safeJson(res);
    msg.style.color='var(--green)';
    msg.textContent=`Job scheduled (ID: ${(data.job||{}).job_id||'—'}). First run triggers in ~60s.`;
    loadMonitorJobs();
  }catch(e){
    msg.style.color='var(--red)';
    msg.textContent=`Error: ${e.message}`;
  }
}

async function loadMonitorJobs(){
  try{
    const res=await fetch(`${API}/monitor/jobs`);
    if(!res.ok)return;
    const data=await safeJson(res);
    renderMonitorJobs(data.jobs||[]);
  }catch(e){console.warn('loadMonitorJobs:',e);}
}

function renderMonitorJobs(jobs){
  const tbody=document.getElementById('mon-jobs-tbody');
  if(!jobs.length){
    tbody.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:16px;font-size:11px">No monitoring jobs yet.</td></tr>';
    return;
  }
  tbody.innerHTML=jobs.map(j=>{
    const sc=j.active?'var(--green)':'var(--dim)';
    const status=j.active?'Active':'Paused';
    const kws=(j.keywords||[]).slice(0,3).join(', ')+(j.keywords?.length>3?'…':'');
    const nextRun=j.next_run_at?j.next_run_at.replace('T',' ').slice(0,16):'-';
    return `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:7px 10px;color:var(--cyan);font-family:var(--mono)">${j.domain}</td>
      <td style="padding:7px 10px;color:var(--dim);font-size:10px">${kws}</td>
      <td style="padding:7px 10px;text-align:center">${j.interval_hours}h</td>
      <td style="padding:7px 10px;text-align:center;font-weight:700;color:var(--yellow)">${j.run_count||0}</td>
      <td style="padding:7px 10px;text-align:center;font-weight:700;color:${sc}">${status}</td>
      <td style="padding:7px 10px;font-size:10px;color:var(--dim)">${nextRun}</td>
      <td style="padding:7px 10px">
        ${j.active?`<button class="btn btn-outline btn-sm" onclick="monitorAction('cancel','${j.job_id}')">Pause</button>`:''}
        <button class="btn btn-sm" style="border:1px solid var(--red);color:var(--red);background:transparent;margin-left:4px" onclick="monitorAction('delete','${j.job_id}')">✕</button>
      </td>
    </tr>`;
  }).join('');
}

async function monitorAction(action, jobId){
  if(action==='delete'&&!confirm('Delete this monitoring job?'))return;
  const url=action==='cancel'?`${API}/monitor/job/${jobId}/cancel`:`${API}/monitor/job/${jobId}`;
  const method=action==='cancel'?'PATCH':'DELETE';
  try{
    const res=await fetch(url,{method});
    if(res.ok)loadMonitorJobs();
    else{const e=await safeJson(res);alert(e.detail||'Action failed');}
  }catch(e){alert(`Error: ${e.message}`);}
}


// ══════════════════════════════════════════════════════════════════════════
// PDF EXPORT
// ══════════════════════════════════════════════════════════════════════════

function exportPDF(){
  const urlParam=encodeURIComponent(document.getElementById('url-input')?.value?.trim()||'');
  // Use branded PDF if user is logged in with a brand name set
  const brandName = localStorage.getItem('ciq_brand_name')||'';
  if(brandName){
    window.location=`${API}/export-pdf/branded?url=${urlParam}&brand_name=${encodeURIComponent(brandName)}`;
  } else {
    window.location=`${API}/export-pdf?url=${urlParam}`;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// ── SaaS: Auth, Projects, Settings, Keyword Gap, Sitemap, Score History ───────
// ══════════════════════════════════════════════════════════════════════════════

// ── State ─────────────────────────────────────────────────────────────────────
let _ciqToken   = localStorage.getItem('ciq_token') || '';
let _ciqUser    = null;
window._ciqProject = null;   // window-scoped so btns() (defined earlier) can read it

// ── Token helpers ──────────────────────────────────────────────────────────────
function ciqHeaders(){
  const h = {'Content-Type':'application/json'};
  if(_ciqToken) h['Authorization']='Bearer '+_ciqToken;
  return h;
}
async function safeAuthFetch(url, opts={}){
  opts.headers = {...(opts.headers||{}), ...ciqHeaders()};
  return fetch(url, opts);
}

// ── Auth modal ────────────────────────────────────────────────────────────────
function openAuthModal(){
  document.getElementById('auth-modal').style.display='flex';
  document.getElementById('auth-error').style.display='none';
}
function closeAuthModal(){
  document.getElementById('auth-modal').style.display='none';
}
function authTab(tab){
  document.getElementById('auth-form-login').style.display    = tab==='login'    ? '' : 'none';
  document.getElementById('auth-form-register').style.display = tab==='register' ? '' : 'none';
  document.getElementById('tab-login').classList.toggle('active', tab==='login');
  document.getElementById('tab-register').classList.toggle('active', tab==='register');
}
async function doLogin(){
  const email = document.getElementById('login-email').value.trim();
  const pass  = document.getElementById('login-pass').value;
  const errEl = document.getElementById('auth-error');
  errEl.style.display='none';
  try{
    const res = await fetch(`${API}/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password:pass})});
    const d   = await safeJson(res);
    if(!res.ok) throw new Error(d.detail||'Login failed');
    _ciqToken = d.token; _ciqUser = d.user;
    localStorage.setItem('ciq_token', _ciqToken);
    closeAuthModal();
    applyAuthState();
  }catch(e){errEl.textContent=e.message; errEl.style.display='';}
}
async function doRegister(){
  const name  = document.getElementById('reg-name').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const pass  = document.getElementById('reg-pass').value;
  const errEl = document.getElementById('auth-error');
  errEl.style.display='none';
  try{
    const res = await fetch(`${API}/auth/register`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,email,password:pass})});
    const d   = await safeJson(res);
    if(!res.ok) throw new Error(d.detail||'Registration failed');
    _ciqToken = d.token; _ciqUser = d.user;
    localStorage.setItem('ciq_token', _ciqToken);
    closeAuthModal();
    applyAuthState();
  }catch(e){errEl.textContent=e.message; errEl.style.display='';}
}
function doLogout(){
  _ciqToken=''; _ciqUser=null; window._ciqProject=null;
  localStorage.removeItem('ciq_token');
  localStorage.removeItem('ciq_brand_name');
  applyAuthState();
}
async function loadCurrentUser(){
  if(!_ciqToken) return;
  try{
    const res = await safeAuthFetch(`${API}/auth/me`);
    if(res.ok){ _ciqUser = await safeJson(res); applyAuthState(); }
    else{ _ciqToken=''; localStorage.removeItem('ciq_token'); }
  }catch(e){ _ciqToken=''; }
}
function applyAuthState(){
  const loggedIn = !!_ciqUser;
  document.getElementById('nav-auth-guest').style.display = loggedIn ? 'none' : 'flex';
  document.getElementById('nav-auth-user').style.display  = loggedIn ? 'flex' : 'none';
  if(loggedIn){
    const name    = _ciqUser.name || _ciqUser.email || 'User';
    const initials= name.substring(0,2).toUpperCase();
    const tier    = _ciqUser.tier || 'free';
    document.getElementById('user-avatar-initials').textContent = initials;
    document.getElementById('user-display-name').textContent    = name.split(' ')[0];
    const badge = document.getElementById('user-tier-badge');
    badge.textContent = tier.charAt(0).toUpperCase()+tier.slice(1);
    badge.className   = `tier-badge tier-${tier}`;
    // Enable save-to-project button when crawl is done
    const saveBtn = document.getElementById('save-project-btn');
    if(saveBtn) saveBtn.disabled = !crawlDone;
  }
}
function toggleUserMenu(){
  document.getElementById('user-menu').classList.toggle('open');
}
document.addEventListener('click', e=>{
  if(!e.target.closest('.user-chip')) document.getElementById('user-menu')?.classList.remove('open');
});

// ── Settings modal ─────────────────────────────────────────────────────────────
function openSettings(){
  document.getElementById('user-menu').classList.remove('open');
  document.getElementById('settings-modal').style.display='flex';
  // Populate fields from user
  if(_ciqUser){
    document.getElementById('set-name').value           = _ciqUser.name || '';
    document.getElementById('set-alert-email').value    = _ciqUser.alert_email || '';
    document.getElementById('set-drop-threshold').value = _ciqUser.rank_drop_threshold || 5;
    document.getElementById('api-key-display').value    = _ciqUser.api_key || '(not loaded)';
    localStorage.setItem('ciq_brand_name', _ciqUser.name || '');
  }
  loadUsage();
  checkGscStatus();
}
function closeSettings(){
  document.getElementById('settings-modal').style.display='none';
}
async function saveSettings(){
  if(!_ciqUser){ alert('Sign in first'); return; }
  const body = {
    name:                document.getElementById('set-name').value.trim()||null,
    alert_email:         document.getElementById('set-alert-email').value.trim()||null,
    rank_drop_threshold: parseInt(document.getElementById('set-drop-threshold').value)||5,
  };
  try{
    const res = await safeAuthFetch(`${API}/user/settings`,{method:'PATCH',body:JSON.stringify(body)});
    if(res.ok){
      if(body.name){ _ciqUser.name=body.name; localStorage.setItem('ciq_brand_name',body.name); applyAuthState(); }
      const brandInput = document.getElementById('set-brand-name');
      if(brandInput && body.name) brandInput.value = body.name;
      alert('Settings saved!');
    } else { const d=await safeJson(res); alert(d.detail||'Save failed'); }
  }catch(e){ alert(e.message); }
}
async function loadUsage(){
  try{
    const res = await safeAuthFetch(`${API}/user/usage`);
    if(!res.ok) return;
    const d = await safeJson(res);
    const limit  = d.pages_limit === -1 ? '∞' : d.pages_limit;
    const used   = d.pages_used || 0;
    const pct    = d.pages_limit === -1 ? 0 : Math.min(100, Math.round(used / (d.pages_limit||1) * 100));
    document.getElementById('usage-count').textContent = `${used} / ${limit}`;
    document.getElementById('usage-bar').style.width   = pct+'%';
    document.getElementById('usage-bar').style.background = pct>80?'var(--red)':pct>60?'var(--yellow)':'var(--indigo)';
    const tier = (d.tier||'free').toLowerCase();
    const tierLabel = tier.charAt(0).toUpperCase()+tier.slice(1);
    document.getElementById('usage-tier').textContent = tierLabel+' tier';
    renderBillingActions(tier);
  }catch(e){}
}

function renderBillingActions(tier){
  const el = document.getElementById('billing-actions');
  if(!el) return;
  el.style.display='block';
  if(tier==='free'){
    el.innerHTML=`
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn btn-green" style="font-size:11px" onclick="startCheckout('pro')">Upgrade to Pro</button>
        <button class="btn btn-outline" style="font-size:11px" onclick="startCheckout('agency')">Upgrade to Agency</button>
      </div>`;
  } else {
    el.innerHTML=`
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <button class="btn btn-outline" style="font-size:11px" onclick="openBillingPortal()">Manage / Cancel Subscription</button>
        ${tier!=='agency'?'<button class="btn btn-green" style="font-size:11px" onclick="startCheckout(\'agency\')">Upgrade to Agency</button>':''}
      </div>`;
  }
}

async function startCheckout(tier){
  try{
    const res = await safeAuthFetch(`${API}/billing/checkout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tier})});
    const d   = await safeJson(res);
    if(!res.ok) throw new Error(d.detail||'Checkout failed');
    if(d.checkout_url) window.open(d.checkout_url,'_blank');
  }catch(e){ alert(e.message); }
}

async function openBillingPortal(){
  try{
    const res = await safeAuthFetch(`${API}/billing/portal`);
    const d   = await safeJson(res);
    if(!res.ok) throw new Error(d.detail||'Portal session failed');
    if(d.portal_url) window.open(d.portal_url,'_blank');
  }catch(e){ alert(e.message); }
}
function toggleApiKeyVisibility(){
  const el = document.getElementById('api-key-display');
  el.type = el.type==='password' ? 'text' : 'password';
}
async function rotateApiKey(){
  if(!confirm('Generate a new API key? Your current key will stop working.')) return;
  try{
    const res = await safeAuthFetch(`${API}/auth/api-key/rotate`,{method:'POST'});
    const d   = await safeJson(res);
    if(res.ok){
      document.getElementById('api-key-display').value = d.api_key;
      if(_ciqUser) _ciqUser.api_key = d.api_key;
      alert('New API key generated!');
    }
  }catch(e){ alert(e.message); }
}
async function uploadLogo(input){
  const file = input.files[0];
  if(!file) return;
  const statusEl = document.getElementById('logo-status');
  statusEl.textContent = 'Uploading…';
  const form = new FormData();
  form.append('file', file);
  try{
    const res = await fetch(`${API}/user/logo`,{method:'POST',headers:{Authorization:'Bearer '+_ciqToken},body:form});
    const d   = await safeJson(res);
    if(res.ok){ statusEl.style.color='var(--green)'; statusEl.textContent='Logo uploaded ('+Math.round(d.size/1024)+' KB)'; }
    else { statusEl.style.color='var(--red)'; statusEl.textContent=d.detail||'Upload failed'; }
  }catch(e){ statusEl.style.color='var(--red)'; statusEl.textContent=e.message; }
}
async function checkGscStatus(){
  try{
    const res = await fetch(`${API}/gsc/status`);
    const d   = await safeJson(res);
    if(d.connected){
      document.getElementById('gsc-not-connected').style.display='none';
      document.getElementById('gsc-connected-panel').style.display='block';
      await loadGscSites();
    } else {
      document.getElementById('gsc-not-connected').style.display='block';
      document.getElementById('gsc-connected-panel').style.display='none';
    }
  }catch(e){}
}

async function connectGSC(){
  const statusEl = document.getElementById('gsc-status');
  try{
    const res = await fetch(`${API}/gsc/auth-url`);
    const d   = await safeJson(res);
    if(!d.available){
      statusEl.style.color='var(--red)';
      statusEl.textContent = d.message || 'GSC not configured on this server.';
      return;
    }
    const popup = window.open(d.auth_url, 'gsc_oauth', 'width=520,height=640');
    statusEl.style.color='var(--dim)';
    statusEl.textContent = 'Waiting for Google authorization…';
    window.addEventListener('message', async function handler(e){
      if(e.data && e.data.gsc === 'connected'){
        window.removeEventListener('message', handler);
        statusEl.textContent = '';
        await checkGscStatus();
      }
    });
    // fallback poll if popup closes without postMessage
    const poll = setInterval(async ()=>{
      if(popup && popup.closed){
        clearInterval(poll);
        statusEl.textContent='';
        await checkGscStatus();
      }
    }, 1000);
  }catch(e){ statusEl.style.color='var(--red)'; statusEl.textContent=e.message; }
}

async function disconnectGSC(){
  await fetch(`${API}/gsc/disconnect`, {method:'DELETE'});
  document.getElementById('gsc-not-connected').style.display='block';
  document.getElementById('gsc-connected-panel').style.display='none';
  document.getElementById('gsc-status').textContent='';
}

async function loadGscSites(){
  try{
    const res   = await fetch(`${API}/gsc/sites`);
    const d     = await safeJson(res);
    const sel   = document.getElementById('gsc-site-select');
    sel.innerHTML = (d.sites||[]).map(s=>`<option value="${escHtml(s)}">${escHtml(s)}</option>`).join('');
    if(d.sites && d.sites.length) await loadGscData();
  }catch(e){ document.getElementById('gsc-status').textContent=e.message; }
}

async function loadGscData(){
  const sel     = document.getElementById('gsc-site-select');
  const siteUrl = sel.value;
  if(!siteUrl) return;
  const sumEl  = document.getElementById('gsc-summary');
  const kwEl   = document.getElementById('gsc-kw-table');
  const drEl   = document.getElementById('gsc-date-range');
  sumEl.innerHTML = '<div style="grid-column:1/-1;color:var(--dim);font-size:11px">Loading…</div>';
  kwEl.innerHTML  = '';
  try{
    const res = await fetch(`${API}/gsc/data?site_url=${encodeURIComponent(siteUrl)}&days=28`);
    const d   = await safeJson(res);
    if(d.detail){ sumEl.innerHTML=`<div style="grid-column:1/-1;color:var(--red);font-size:11px">${escHtml(d.detail)}</div>`; return; }
    const s = d.summary;
    sumEl.innerHTML = [
      {label:'Clicks',      val:s.clicks.toLocaleString()},
      {label:'Impressions', val:s.impressions.toLocaleString()},
      {label:'CTR',         val:s.ctr+'%'},
      {label:'Avg Position',val:'#'+s.avg_position},
    ].map(m=>`
      <div style="background:var(--surf2);border:1px solid var(--border);border-radius:6px;padding:8px;text-align:center">
        <div style="font-size:16px;font-weight:700;color:var(--text)">${escHtml(m.val)}</div>
        <div style="font-size:9px;color:var(--dim);margin-top:2px;text-transform:uppercase">${escHtml(m.label)}</div>
      </div>`).join('');
    if(d.top_keywords && d.top_keywords.length){
      kwEl.innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:10px">
          <thead><tr style="color:var(--dim);text-align:left">
            <th style="padding:4px 6px">Keyword</th>
            <th style="padding:4px 6px;text-align:right">Clicks</th>
            <th style="padding:4px 6px;text-align:right">Impr</th>
            <th style="padding:4px 6px;text-align:right">CTR%</th>
            <th style="padding:4px 6px;text-align:right">Pos</th>
          </tr></thead>
          <tbody>${d.top_keywords.map(k=>`
            <tr style="border-top:1px solid var(--border)">
              <td style="padding:4px 6px;color:var(--text);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(k.keyword)}</td>
              <td style="padding:4px 6px;text-align:right;color:var(--green)">${k.clicks}</td>
              <td style="padding:4px 6px;text-align:right;color:var(--dim)">${k.impressions}</td>
              <td style="padding:4px 6px;text-align:right;color:var(--cyan)">${k.ctr}%</td>
              <td style="padding:4px 6px;text-align:right;color:var(--yellow)">${k.position}</td>
            </tr>`).join('')}
          </tbody>
        </table>`;
    } else {
      kwEl.innerHTML = '<div style="font-size:11px;color:var(--dim);padding:8px">No keyword data yet — GSC needs a few days after verification to populate data.</div>';
    }
    drEl.textContent = `Data: ${d.date_range.start} → ${d.date_range.end} (last 28 days, 3-day lag)`;
  }catch(e){ sumEl.innerHTML=`<div style="grid-column:1/-1;color:var(--red);font-size:11px">${escHtml(e.message)}</div>`; }
}

// ── Projects ──────────────────────────────────────────────────────────────────
function openProjects(){
  document.getElementById('user-menu').classList.remove('open');
  document.getElementById('projects-modal').style.display='flex';
  loadProjects();
}
function closeProjects(){ document.getElementById('projects-modal').style.display='none'; }

async function loadProjects(){
  const listEl = document.getElementById('proj-list');
  listEl.innerHTML='<div style="text-align:center;color:var(--dim);padding:16px;font-size:11px">Loading…</div>';
  try{
    const res = await safeAuthFetch(`${API}/projects`);
    const d   = await safeJson(res);
    const projs = d.projects||[];
    if(!projs.length){
      listEl.innerHTML='<div style="text-align:center;color:var(--dim);padding:20px;font-size:11px">No projects yet. Create one above to save crawl results.</div>';
      return;
    }
    listEl.innerHTML = projs.map(p=>`
      <div class="proj-card" onclick="activateProject(${p.id},'${escHtml(p.name)}','${escHtml(p.url)}')">
        <div style="display:flex;align-items:flex-start;justify-content:space-between">
          <div>
            <div class="proj-name">${escHtml(p.name)}</div>
            <div class="proj-url">${escHtml(p.url)}</div>
          </div>
          <button onclick="event.stopPropagation();deleteProject(${p.id})" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:12px;padding:0">✕</button>
        </div>
        <div class="proj-meta">
          <span>${p.page_count||0} pages</span>
          <span>${p.issue_count||0} issues</span>
          <span style="color:${(p.health_score||0)>=70?'var(--green)':(p.health_score||0)>=50?'var(--yellow)':'var(--red)'}">Health: ${Math.round(p.health_score||0)}</span>
          <span style="margin-left:auto">${p.last_crawl_at?new Date(p.last_crawl_at).toLocaleDateString():'Never crawled'}</span>
        </div>
      </div>
    `).join('');
  }catch(e){ listEl.innerHTML=`<div style="color:var(--red);padding:12px;font-size:11px">Error: ${e.message}</div>`; }
}
function escHtml(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

async function createProject(){
  const name = document.getElementById('new-proj-name').value.trim();
  const url  = document.getElementById('new-proj-url').value.trim();
  if(!name||!url){ alert('Enter both a name and URL'); return; }
  try{
    const res = await safeAuthFetch(`${API}/projects`,{method:'POST',body:JSON.stringify({name,url})});
    const d   = await safeJson(res);
    if(res.ok){
      document.getElementById('new-proj-name').value='';
      document.getElementById('new-proj-url').value='';
      loadProjects();
    } else { alert(d.detail||'Create failed'); }
  }catch(e){ alert(e.message); }
}
async function deleteProject(id){
  if(!confirm('Delete this project and all its history?')) return;
  await safeAuthFetch(`${API}/projects/${id}`,{method:'DELETE'});
  if(window._ciqProject?.id===id){ window._ciqProject=null; updateActiveProjectBadge(); }
  loadProjects();
}
function activateProject(id,name,url){
  window._ciqProject={id,name,url};
  updateActiveProjectBadge();
  closeProjects();
  if(window._crawlDone) document.getElementById('save-project-btn').disabled = false;
}
function updateActiveProjectBadge(){
  let badge = document.getElementById('active-proj-badge');
  if(!badge){
    badge = document.createElement('div');
    badge.id = 'active-proj-badge';
    badge.style.cssText = 'font-size:10px;color:var(--dim);text-align:right;padding:4px 8px;font-family:var(--mono)';
    const dashActions = document.querySelector('.dash-actions');
    if(dashActions) dashActions.insertAdjacentElement('afterend', badge);
  }
  badge.textContent = window._ciqProject ? `Active project: ${window._ciqProject.name}` : '';
}

window._crawlDone = false;  // track when crawl completes to enable save button

async function saveToProject(){
  if(!window._ciqProject){ openProjects(); return; }
  const btn = document.getElementById('save-project-btn');
  btn.disabled = true;
  btn.textContent = '💾 Saving…';
  try{
    const res = await safeAuthFetch(`${API}/projects/${window._ciqProject.id}/snapshot`,{method:'POST'});
    const d   = await safeJson(res);
    if(res.ok){
      btn.textContent = '✓ Saved';
      setTimeout(()=>{ btn.textContent='💾 Save to Project'; btn.disabled=false; }, 2000);
    } else { alert(d.detail||'Save failed'); btn.textContent='💾 Save to Project'; btn.disabled=false; }
  }catch(e){ alert(e.message); btn.textContent='💾 Save to Project'; btn.disabled=false; }
}

// ── Score History ─────────────────────────────────────────────────────────────
function openScoreHistory(){
  document.getElementById('user-menu').classList.remove('open');
  if(!window._ciqProject){ openProjects(); return; }
  document.getElementById('score-history-modal').style.display='flex';
  loadScoreHistory();
}
async function loadScoreHistory(){
  if(!window._ciqProject) return;
  try{
    const res  = await safeAuthFetch(`${API}/projects/${window._ciqProject.id}/history`);
    const d    = await safeJson(res);
    const hist = (d.history||[]).reverse();   // oldest first for chart
    renderScoreHistory(hist);
  }catch(e){}
}
function renderScoreHistory(hist){
  // Table
  const tbody = document.getElementById('score-history-tbody');
  if(!hist.length){
    tbody.innerHTML='<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:16px">No snapshots yet. Save a crawl to this project first.</td></tr>';
  } else {
    tbody.innerHTML = hist.slice().reverse().map(h=>{
      const scoreColor=(h.health_score||0)>=70?'var(--green)':(h.health_score||0)>=50?'var(--yellow)':'var(--red)';
      return `<tr style="border-bottom:1px solid var(--border)">
        <td style="padding:6px 10px;color:var(--dim)">${new Date(h.crawled_at).toLocaleString()}</td>
        <td style="padding:6px 10px;text-align:center;font-family:var(--mono)">${h.page_count||0}</td>
        <td style="padding:6px 10px;text-align:center;color:var(--red);font-family:var(--mono)">${h.issue_count||0}</td>
        <td style="padding:6px 10px;text-align:center;font-weight:800;color:${scoreColor};font-family:var(--mono)">${Math.round(h.health_score||0)}</td>
      </tr>`;
    }).join('');
  }
  // Simple canvas chart (no external lib needed)
  const canvas = document.getElementById('score-chart');
  if(!canvas || !hist.length) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.offsetWidth || 560;
  canvas.width = W; canvas.height = 180;
  ctx.clearRect(0,0,W,180);
  ctx.fillStyle='#1A1D2E'; ctx.fillRect(0,0,W,180);
  // Grid lines
  ctx.strokeStyle='#2D3048'; ctx.lineWidth=1;
  [25,50,75,100].forEach(y=>{
    const cy = 160 - (y/100)*140;
    ctx.beginPath(); ctx.moveTo(30,cy); ctx.lineTo(W-10,cy); ctx.stroke();
    ctx.fillStyle='#6B7280'; ctx.font='9px monospace';
    ctx.fillText(y, 2, cy+4);
  });
  if(hist.length < 2) return;
  const scores = hist.map(h=>Math.min(100,Math.max(0,h.health_score||0)));
  const step   = (W-40) / (scores.length-1);
  ctx.strokeStyle='#6366F1'; ctx.lineWidth=2; ctx.beginPath();
  scores.forEach((s,i)=>{
    const x = 30 + i*step, y = 160 - (s/100)*140;
    i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
  });
  ctx.stroke();
  // Dots
  scores.forEach((s,i)=>{
    const x=30+i*step, y=160-(s/100)*140;
    const color = s>=70?'#10B981':s>=50?'#F59E0B':'#EF4444';
    ctx.fillStyle=color; ctx.beginPath(); ctx.arc(x,y,4,0,2*Math.PI); ctx.fill();
    ctx.fillStyle='#E5E7EB'; ctx.font='9px monospace'; ctx.fillText(Math.round(s),x-8,y-8);
  });
}

// ── Keyword Gap ───────────────────────────────────────────────────────────────
function openKwGap(){
  document.getElementById('user-menu').classList.remove('open');
  document.getElementById('kwgap-modal').style.display='flex';
  // Pre-fill "yours" from crawl keywords if available
  if(window._lastKeywords && window._lastKeywords.length){
    document.getElementById('kwgap-yours').value = window._lastKeywords.slice(0,100).join('\n');
  }
}
async function runKeywordGap(){
  const yours  = document.getElementById('kwgap-yours').value.split('\n').map(k=>k.trim()).filter(Boolean);
  const theirs = document.getElementById('kwgap-theirs').value.split('\n').map(k=>k.trim()).filter(Boolean);
  if(!yours.length||!theirs.length){ alert('Enter keywords for both sides'); return; }
  try{
    const res = await fetch(`${API}/keyword-gap`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({your_keywords:yours,competitor_keywords:theirs})});
    const d   = await safeJson(res);
    if(!res.ok) throw new Error(d.detail||'Gap analysis failed');
    renderKeywordGap(d);
  }catch(e){ alert(e.message); }
}
function renderKeywordGap(d){
  const sumEl = document.getElementById('kwgap-summary');
  sumEl.innerHTML=[
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px"><span style="font-size:16px;font-weight:800;color:var(--red)">${d.gap_count}</span><span style="color:var(--dim);margin-left:6px;font-size:11px">Gap Opportunities</span></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px"><span style="font-size:16px;font-weight:800;color:var(--green)">${(d.only_you||[]).length}</span><span style="color:var(--dim);margin-left:6px;font-size:11px">Your Unique</span></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px"><span style="font-size:16px;font-weight:800;color:var(--yellow)">${(d.shared||[]).length}</span><span style="color:var(--dim);margin-left:6px;font-size:11px">Shared</span></div>`,
  ].join('');
  const pills = arr => arr.map(k=>`<span class="kw-gap-pill">${escHtml(k)}</span>`).join('') || '<span style="color:var(--dim);font-size:10px">None</span>';
  document.getElementById('kwgap-only-comp').innerHTML = pills(d.only_competitor||[]);
  document.getElementById('kwgap-only-you').innerHTML  = pills(d.only_you||[]);
  document.getElementById('kwgap-shared').innerHTML    = pills(d.shared||[]);
  document.getElementById('kwgap-results').style.display='';
}

// ── Issue Status tracking ─────────────────────────────────────────────────────

window._issueStatusCache = {};   // { "url|issue_type": "open|in_progress|resolved" }

async function loadIssueStatuses(){
  if(!window._ciqProject) return;
  try{
    const res = await safeAuthFetch(`${API}/issues/status?project_id=${window._ciqProject.id}`);
    if(!res.ok) return;
    const d = await safeJson(res);
    const cache = {};
    (d.statuses||[]).forEach(s=>{ cache[s.url+'|'+s.issue_type] = s.status; });
    window._issueStatusCache = cache;
    // Re-render table to show loaded statuses
    if(window.allResults?.length) renderTable(window.allResults);
  }catch(e){ console.warn('loadIssueStatuses:', e); }
}

async function updateIssueStatus(url, issueType, newStatus, selectEl){
  selectEl.className = `issue-status-sel ${newStatus}`;
  const key = url+'|'+issueType;
  if(!window._issueStatusCache) window._issueStatusCache={};
  window._issueStatusCache[key] = newStatus;
  try{
    await safeAuthFetch(`${API}/issues/status`,{
      method:'PATCH',
      body:JSON.stringify({project_id:window._ciqProject?.id||null, url, issue_type:issueType, status:newStatus}),
    });
  }catch(e){ console.warn('Issue status update failed:', e); }
}

// ── Sitemap Crawl ─────────────────────────────────────────────────────────────
function openSitemapCrawl(){
  document.getElementById('sitemap-modal').style.display='flex';
}
async function startSitemapCrawl(){
  const sitemapUrl = document.getElementById('sitemap-url-input').value.trim();
  const maxPages   = parseInt(document.getElementById('sitemap-max-pages').value)||100;
  const errEl      = document.getElementById('sitemap-error');
  errEl.style.display='none';
  if(!sitemapUrl){ errEl.textContent='Enter a sitemap URL'; errEl.style.display=''; return; }
  try{
    const res = await fetch(`${API}/sitemap-crawl?sitemap_url=${encodeURIComponent(sitemapUrl)}&max_pages=${maxPages}`,{method:'POST'});
    const d   = await safeJson(res);
    if(!res.ok) throw new Error(d.detail||'Sitemap crawl failed');
    document.getElementById('sitemap-modal').style.display='none';
    // Start polling just like a normal crawl
    btns({crawl:true,gemini:true,popup:true,export:true,opt:true,tseo:true,pdf:true,serp:true});
    startCrawlPolling();
  }catch(e){ errEl.textContent=e.message; errEl.style.display=''; }
}

// ── Crawl Diff ────────────────────────────────────────────────────────────────
async function openCrawlDiff(){
  document.getElementById('user-menu').classList.remove('open');
  if(!window._ciqProject){ openProjects(); return; }
  document.getElementById('diff-modal').style.display='flex';
  document.getElementById('diff-loading').style.display='';
  document.getElementById('diff-content').style.display='none';
  document.getElementById('diff-no-data').style.display='none';
  try{
    const res = await safeAuthFetch(`${API}/projects/${window._ciqProject.id}/diff`);
    const d   = await safeJson(res);
    document.getElementById('diff-loading').style.display='none';
    if(!d.has_diff){
      document.getElementById('diff-no-data').textContent = d.message || 'Not enough data.';
      document.getElementById('diff-no-data').style.display='';
      return;
    }
    renderDiff(d);
  }catch(e){
    document.getElementById('diff-loading').textContent='Error: '+e.message;
  }
}
function renderDiff(d){
  const sdEl = document.getElementById('diff-summary');
  const deltaColor = d.score_delta>0?'var(--green)':d.score_delta<0?'var(--red)':'var(--dim)';
  const deltaSign  = d.score_delta>0?'+':'';
  sdEl.innerHTML=[
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px"><span style="font-size:16px;font-weight:800;color:${deltaColor}">${deltaSign}${d.score_delta}</span><span style="color:var(--dim);margin-left:6px;font-size:11px">Score Change</span></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px"><span style="font-size:16px;font-weight:800;color:var(--red)">${d.new_issue_count}</span><span style="color:var(--dim);margin-left:6px;font-size:11px">New Issues</span></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px"><span style="font-size:16px;font-weight:800;color:var(--green)">${d.fixed_issue_count}</span><span style="color:var(--dim);margin-left:6px;font-size:11px">Fixed Issues</span></div>`,
    `<div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:8px 14px;font-size:9px;color:var(--dim)">${new Date(d.older_date).toLocaleDateString()} → ${new Date(d.newer_date).toLocaleDateString()}</div>`,
  ].join('');
  document.getElementById('diff-new-count').textContent = `(${d.new_issue_count})`;
  document.getElementById('diff-fixed-count').textContent = `(${d.fixed_issue_count})`;
  const issueRow = (i,color) =>
    `<div style="padding:5px 0;border-bottom:1px solid var(--border)"><span style="color:${color}">${escHtml(i.issue)}</span><br><span style="font-size:9px;color:var(--dim)">${escHtml(i.url.replace(/^https?:\/\//,'').slice(0,60))}</span></div>`;
  document.getElementById('diff-new-list').innerHTML   = d.new_issues.length   ? d.new_issues.map(i=>issueRow(i,'var(--red)')).join('')   : '<div style="color:var(--dim)">No new issues — great!</div>';
  document.getElementById('diff-fixed-list').innerHTML = d.fixed_issues.length ? d.fixed_issues.map(i=>issueRow(i,'var(--green)')).join('') : '<div style="color:var(--dim)">Nothing fixed yet.</div>';
  document.getElementById('diff-content').style.display='';
}

// ── Team workspace ────────────────────────────────────────────────────────────
async function openTeam(){
  document.getElementById('user-menu').classList.remove('open');
  if(!window._ciqProject){ openProjects(); return; }
  document.getElementById('team-proj-name').textContent = `Project: ${window._ciqProject.name}`;
  document.getElementById('team-modal').style.display='flex';
  loadTeamMembers();
}
async function loadTeamMembers(){
  const listEl = document.getElementById('team-members-list');
  listEl.innerHTML='<div style="color:var(--muted);padding:8px 0">Loading…</div>';
  try{
    const res = await safeAuthFetch(`${API}/team/members/${window._ciqProject.id}`);
    const d   = await safeJson(res);
    const members = d.members||[];
    if(!members.length){ listEl.innerHTML='<div style="color:var(--dim);padding:8px 0">No team members yet. Invite someone above.</div>'; return; }
    listEl.innerHTML = members.map(m=>`
      <div style="display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--border)">
        <div style="width:28px;height:28px;border-radius:50%;background:var(--indigo);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;color:#fff;flex-shrink:0">${(m.name||m.email).substring(0,2).toUpperCase()}</div>
        <div style="flex:1"><div style="color:var(--text)">${escHtml(m.name||m.email)}</div><div style="font-size:9px;color:var(--dim)">${escHtml(m.email)}</div></div>
        <span class="tier-badge ${m.role==='editor'?'tier-pro':'tier-free'}">${m.role}</span>
        <button onclick="removeTeamMember('${escHtml(m.email)}')" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:11px">✕</button>
      </div>
    `).join('');
  }catch(e){ listEl.innerHTML=`<div style="color:var(--red);padding:8px 0">Error: ${e.message}</div>`; }
}
async function inviteTeamMember(){
  const email = document.getElementById('invite-email').value.trim();
  const role  = document.getElementById('invite-role').value;
  const msgEl = document.getElementById('invite-msg');
  if(!email){ alert('Enter an email'); return; }
  msgEl.style.display='none';
  try{
    const res = await safeAuthFetch(`${API}/team/invite`,{
      method:'POST',
      body:JSON.stringify({project_id:window._ciqProject.id, email, role}),
    });
    const d = await safeJson(res);
    if(res.ok){
      document.getElementById('invite-email').value='';
      msgEl.style.color='var(--green)'; msgEl.textContent=`Invited ${email} as ${role}`;
      msgEl.style.display='';
      loadTeamMembers();
    } else { msgEl.style.color='var(--red)'; msgEl.textContent=d.detail||'Invite failed'; msgEl.style.display=''; }
  }catch(e){ msgEl.style.color='var(--red)'; msgEl.textContent=e.message; msgEl.style.display=''; }
}
async function removeTeamMember(email){
  if(!confirm(`Remove ${email} from this project?`)) return;
  await safeAuthFetch(`${API}/team/member`,{method:'DELETE',body:JSON.stringify({project_id:window._ciqProject.id, email})});
  loadTeamMembers();
}

// Keywords are stored in updateSummary() above via window._lastKeywords

// ── Init on page load ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', ()=>{
  loadCurrentUser();
  // Keyboard dismiss for modals
  document.addEventListener('keydown', e=>{
    if(e.key==='Escape'){
      ['auth-modal','settings-modal','projects-modal','kwgap-modal',
       'score-history-modal','sitemap-modal'].forEach(id=>{
        const el=document.getElementById(id);
        if(el) el.style.display='none';
      });
    }
  });
});




/* ── Panel nav ── */
/* ── Panel registry ──────────────────────────────────────────────────────── */
const _PANELS = {
  dashboard:    { el: 'dash-sec',           sn: 'sn-dashboard',    title: 'Audit Dashboard' },
  serp:         { el: 'serp-intel-sec',     sn: 'sn-serp',         title: 'SERP Intelligence' },
  monitor:      { el: 'monitor-sec',        sn: 'sn-monitor',      title: 'Rank Monitor' },
  competitors:  { el: 'competitor-sec',     sn: 'sn-competitors',  title: 'Competitor Analysis' },
  schema:       { el: 'panel-schema-intel', sn: 'sn-schema',       title: 'Schema Intelligence' },
  'content-lab':{ el: 'panel-content-lab',  sn: 'sn-content-lab',  title: 'Content Lab' },
};
let _currentPanel = 'dashboard';

function showPanel(name) {
  if (!_PANELS[name]) return;
  _currentPanel = name;
  Object.entries(_PANELS).forEach(([k, cfg]) => {
    const el = document.getElementById(cfg.el);
    const sn = document.getElementById(cfg.sn);
    if (el) {
      if (k === name) el.classList.remove('panel-hidden');
      else el.classList.add('panel-hidden');
    }
    if (sn) {
      if (k === name) sn.classList.add('active');
      else sn.classList.remove('active');
    }
  });
  const title = document.getElementById('app-topbar-title');
  if (title) title.textContent = _PANELS[name].title;
  if (name === 'serp' || name === 'monitor') {
    const el = document.getElementById(_PANELS[name].el);
    if (el) el.style.display = 'block';
  }
}

/* ── App mode enter / exit ───────────────────────────────────────────────── */
function enterAppMode() {
  document.body.classList.add('app-mode');
  showPanel('dashboard');
  const dashSec = document.getElementById('dash-sec');
  if (dashSec) dashSec.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function exitAppMode() {
  document.body.classList.remove('app-mode');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function syncTopbarAuth() {
  const userLanding = document.getElementById('nav-auth-user');
  const guestTb     = document.getElementById('nav-auth-guest-topbar');
  const userTb      = document.getElementById('nav-auth-user-topbar');
  if (!guestTb || !userTb) return;
  const loggedIn = userLanding && userLanding.style.display !== 'none';
  guestTb.style.display = loggedIn ? 'none' : 'flex';
  userTb.style.display  = loggedIn ? 'flex' : 'none';
  const nameEl = document.getElementById('user-display-name');
  const nameTb = document.getElementById('user-display-name-tb');
  if (nameEl && nameTb) nameTb.textContent = nameEl.textContent;
  const avEl = document.getElementById('user-avatar-initials');
  const avTb = document.getElementById('user-avatar-initials-tb');
  if (avEl && avTb) avTb.textContent = avEl.textContent;
  /* sync app-url-input with hero input value */
  const heroInput = document.getElementById('url-input');
  const appInput  = document.getElementById('url-input-app');
  if (heroInput && appInput && heroInput.value && !appInput.value) appInput.value = heroInput.value;
}
function syncSidebarAI() {}

/* ── startCrawlHero — redirect to tool page with URL ────────────────────── */
window.startCrawlHero = function() {
  const appInput = document.getElementById('url-input-app');
  const heroInput = document.getElementById('url-input');
  const raw = ((appInput && appInput.value) || (heroInput && heroInput.value) || '').trim();
  if (!raw) { if (heroInput) heroInput.focus(); return; }
  const url = raw.startsWith('http') ? raw : 'https://' + raw;
  localStorage.setItem('ciq_last_url', url);
  window.location.href = 'backend/pages/tech-seo.html?url=' + encodeURIComponent(url);
};

/* ── Override openSerpPanel / closeSerpPanel ─────────────────────────────── */
const _origOpenSerp = window.openSerpPanel;
window.openSerpPanel = function() {
  if (document.body.classList.contains('app-mode')) {
    showPanel('serp');
    const el = document.getElementById('serp-intel-sec');
    if (el) { el.style.display = 'block'; el.style.paddingTop = '0'; }
    if (typeof loadSerpPanel === 'function') loadSerpPanel();
  } else if (_origOpenSerp) _origOpenSerp();
};
const _origCloseSerp = window.closeSerpPanel;
window.closeSerpPanel = function() {
  if (document.body.classList.contains('app-mode')) showPanel('dashboard');
  else if (_origCloseSerp) _origCloseSerp();
};

/* ── Override openMonitorPanel / closeMonitorPanel ───────────────────────── */
const _origOpenMon = window.openMonitorPanel;
window.openMonitorPanel = function() {
  if (document.body.classList.contains('app-mode')) {
    showPanel('monitor');
    const el = document.getElementById('monitor-sec');
    if (el) { el.style.display = 'block'; el.style.paddingTop = '0'; }
    if (typeof loadMonitorJobs === 'function') loadMonitorJobs();
  } else if (_origOpenMon) _origOpenMon();
};
const _origCloseMon = window.closeMonitorPanel;
window.closeMonitorPanel = function() {
  if (document.body.classList.contains('app-mode')) showPanel('dashboard');
  else if (_origCloseMon) _origCloseMon();
};

/* ── AI Drawer ───────────────────────────────────────────────────────────── */
let _aidProvider = 'groq';
let _aidPageData = null;

function openAiDrawer(pageData) {
  _aidPageData = pageData || null;
  const drawer = document.getElementById('ai-drawer');
  const overlay = document.getElementById('ai-drawer-overlay');
  const urlEl = document.getElementById('aid-url');
  if (urlEl && pageData) urlEl.textContent = pageData.url || 'Analyzing…';
  if (pageData) renderAidCards(pageData);
  drawer.classList.add('open');
  overlay.classList.add('open');
}

function closeAiDrawer() {
  document.getElementById('ai-drawer').classList.remove('open');
  document.getElementById('ai-drawer-overlay').classList.remove('open');
}

function aidSelectProvider(btn, provider) {
  _aidProvider = provider;
  document.querySelectorAll('.aid-pill').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  const keyRequired = ['groq','openai','claude'].includes(provider);
  document.getElementById('aid-key-wrap').style.display = keyRequired ? 'block' : 'none';
  if (keyRequired) {
    document.getElementById('aid-api-key').placeholder = `Enter ${provider.toUpperCase()} API Key…`;
  }
}

function renderAidCards(page) {
  const wrap = document.getElementById('aid-cards-wrap');
  if (!wrap) return;
  const fields = [
    { field: 'Title', current: page.title || '—', issue: !page.title ? 'Missing' : page.title.length > 60 ? 'Too Long' : 'Weak keyword', impact: 'High' },
    { field: 'Meta Description', current: page.meta_description || '—', issue: !page.meta_description ? 'Missing' : 'No call to action', impact: 'Medium' },
    { field: 'H1', current: page.h1 || '—', issue: !page.h1 ? 'Missing' : 'Not keyword-optimized', impact: 'High' },
  ];
  wrap.innerHTML = fields.map(f => `
    <div class="aid-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span class="aid-card-field">${f.field}</span>
        <span style="font-size:10px;font-weight:700;text-transform:uppercase;color:${f.impact==='High'?'#dc2626':'#d97706'}">${f.impact} Priority</span>
      </div>
      <div class="aid-card-grid">
        <div><div class="aid-card-item-label">Issue</div><div class="aid-card-item-val">${f.issue}</div></div>
        <div><div class="aid-card-item-label">Current Value</div><div class="aid-card-item-val" style="color:#94a3b8;font-size:11px">${(f.current||'—').slice(0,80)}${(f.current||'').length>80?'…':''}</div></div>
      </div>
      <div class="aid-optimized">
        <div class="aid-optimized-label">AI Fix Preview</div>
        <div class="aid-optimized-val"><span class="aid-optimized-arrow material-symbols-outlined" style="font-size:14px">arrow_forward</span>Run AI analysis to generate optimized value</div>
      </div>
    </div>
  `).join('');
}

function aidTestConnection() {
  const btn = document.getElementById('aid-test-btn');
  const origText = btn.innerHTML;
  btn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px;animation:spin .7s linear infinite">refresh</span> Testing…';
  btn.disabled = true;
  fetch(`${API}/ai-config`)
    .then(r => r.json())
    .then(d => {
      btn.innerHTML = d.configured ? '✓ Connected' : '✗ Not configured';
      setTimeout(() => { btn.innerHTML = origText; btn.disabled = false; }, 2000);
    })
    .catch(() => {
      btn.innerHTML = '✗ Error';
      setTimeout(() => { btn.innerHTML = origText; btn.disabled = false; }, 2000);
    });
}

function aidApplyFixes() {
  if (!_aidPageData) { alert('No page selected. Click AI Fix on a row first.'); return; }
  closeAiDrawer();
  if (typeof analyzeThisPage === 'function' && typeof openPopup === 'function') {
    const idx = allResults.findIndex(r => r.url === _aidPageData.url);
    if (idx >= 0) { popupPages = allResults; popupIndex = idx; openPopup(); }
  }
}

/* ── Schema Intelligence ─────────────────────────────────────────────────── */
function renderSchemaPanel() {
  if (!allResults || !allResults.length) return;
  const rows = allResults.filter(r => r.status_code === 200 || r.status_code === '200');
  let valid = 0, warnings = 0;
  const tbody = document.getElementById('si-tbody');
  if (!tbody) return;
  tbody.innerHTML = rows.slice(0, 50).map((page, idx) => {
    const hasSchema = !!(page.canonical || page.title);
    const status = page.issues > 2 ? 'Error' : page.issues > 0 ? 'Warning' : 'Valid';
    if (status === 'Valid') valid++;
    if (status === 'Warning') warnings++;
    const statusClass = status === 'Valid' ? 'si-status-valid' : status === 'Warning' ? 'si-status-warning' : 'si-status-error';
    const shortUrl = (page.url || '').replace(/^https?:\/\//, '').slice(0, 45);
    const types = hasSchema ? ['WebPage'] : [];
    if (page.title) types.push('TitleOK');
    return `<tr onclick="toggleSchemaRow(${idx}, '${(page.url||'').replace(/'/g,"\\'")}')">
      <td><div class="si-url">${shortUrl}</div><div class="si-url-sub">Asset Link</div></td>
      <td><div class="si-schema-id"><span class="material-symbols-outlined" style="font-size:14px;color:#4f46e5">data_object</span>page-schema-${idx}</div></td>
      <td>${types.map(t => `<span class="si-type-badge">${t}</span>`).join('')}</td>
      <td><span class="si-status ${statusClass}">${status}</span></td>
      <td style="text-align:center"><span style="font-size:11px;font-weight:700;color:${page.issues>0?'#dc2626':'#059669'}">${page.issues||0}</span></td>
      <td><button class="si-expand-btn" id="si-exp-${idx}"><span class="material-symbols-outlined" style="font-size:14px">code</span></button></td>
    </tr>
    <tr id="si-row-detail-${idx}" style="display:none"><td colspan="6" class="si-expanded-cell">
      <div class="si-expanded-content">
        <div class="si-code-actions">
          <button class="si-code-btn" onclick="navigator.clipboard.writeText(getSchemaSample(${idx}))">📋 Copy JSON-LD</button>
          <button class="si-code-btn" style="color:#93c5fd">↗ Open URL</button>
        </div>
        <div class="si-code-wrap"><pre id="si-code-${idx}">${getSchemaSampleEscaped(page)}</pre></div>
        ${page.issues > 0 ? `<div class="si-fault-bar"><span class="si-fault-icon material-symbols-outlined">shield</span><div class="si-fault-body"><h4>Schema Integrity Fault</h4><p>Missing ${page.issues} required properties. Click Repair to auto-generate with AI.</p></div><button class="si-repair-btn" onclick="repairSchema('${(page.url||'').replace(/'/g,"\\'")}')">Repair with AI</button></div>` : ''}
      </div>
    </td></tr>`;
  }).join('');

  const coverage = rows.length > 0 ? Math.round((valid / rows.length) * 100) : 0;
  const covEl = document.getElementById('si-coverage');
  const valEl = document.getElementById('si-valid');
  const warnEl = document.getElementById('si-warnings');
  if (covEl) covEl.textContent = coverage + '%';
  if (valEl) valEl.textContent = valid;
  if (warnEl) warnEl.textContent = warnings;
}

function getSchemaSample(idx) {
  const page = allResults[idx];
  if (!page) return '{}';
  return JSON.stringify({"@context":"https://schema.org","@type":"WebPage","name":page.title||"","url":page.url||"","description":page.meta_description||""}, null, 2);
}

function getSchemaSampleEscaped(page) {
  const j = {"@context":"https://schema.org","@type":"WebPage","name":page.title||"","url":page.url||"","description":page.meta_description||""};
  return JSON.stringify(j, null, 2).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function toggleSchemaRow(idx, url) {
  const detail = document.getElementById(`si-row-detail-${idx}`);
  const btn = document.getElementById(`si-exp-${idx}`);
  if (!detail) return;
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : 'table-row';
  if (btn) btn.innerHTML = isOpen
    ? '<span class="material-symbols-outlined" style="font-size:14px">code</span>'
    : '<span class="material-symbols-outlined" style="font-size:14px;color:#4f46e5">expand_less</span>';
}

function filterSchemaMatrix(val) {
  const v = val.toLowerCase();
  document.querySelectorAll('#si-tbody tr:not([id^="si-row-detail"])').forEach(tr => {
    tr.style.display = tr.textContent.toLowerCase().includes(v) ? '' : 'none';
  });
}

function validateSchemaLive() { alert('Paste your URL in the dashboard to run a live schema validation.'); }
function generateSchemaAI() { alert('Schema generator: run a crawl first, then this will auto-generate JSON-LD for each page.'); }
function repairSchema(url) { alert(`Repair schema for: ${url}\n\nThis will call the AI endpoint to generate missing required properties.`); }

/* ── Content Lab ─────────────────────────────────────────────────────────── */
let _clabModel = 'gemini';

function clabSelectModel(btn, model) {
  _clabModel = model;
  document.querySelectorAll('.clab-model-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

async function runContentLabSynthesis() {
  const original = (document.getElementById('clab-original') || {}).value || '';
  const context  = (document.getElementById('clab-context') || {}).value || '';
  if (!original.trim()) { alert('Please paste your original content first.'); return; }

  const procEl   = document.getElementById('clab-processing');
  const resultEl = document.getElementById('clab-result');
  const genBtn   = document.getElementById('clab-generate-btn');
  if (procEl) { procEl.classList.add('show'); }
  if (resultEl) resultEl.classList.remove('show');
  if (genBtn) genBtn.disabled = true;

  try {
    const res = await fetch(`${API}/generate-content`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: original, context: context, provider: _clabModel })
    });
    const d = await res.json();
    const text = d.content || d.result || d.generated || JSON.stringify(d);
    const textEl = document.getElementById('clab-result-text');
    if (textEl) textEl.textContent = text;
    if (resultEl) resultEl.classList.add('show');
  } catch (e) {
    const textEl = document.getElementById('clab-result-text');
    if (textEl) textEl.textContent = `Error: ${e.message}\n\nCheck that the backend is running and the AI provider is configured.`;
    if (resultEl) resultEl.classList.add('show');
  } finally {
    if (procEl) procEl.classList.remove('show');
    if (genBtn) genBtn.disabled = false;
  }
}

function clabCopyResult() {
  const text = (document.getElementById('clab-result-text') || {}).textContent || '';
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.querySelector('.clab-copy-btn');
    if (btn) { const orig = btn.textContent; btn.textContent = '✓ Copied!'; setTimeout(() => btn.textContent = orig, 1500); }
  });
}

/* ── Patch AI button in results table to open drawer ────────────────────── */
const _origRenderTable = window.renderTable;
window.renderTable = function(data) {
  if (_origRenderTable) _origRenderTable(data);
  if (document.body.classList.contains('app-mode')) patchAiFixButtons();
};
function patchAiFixButtons() {
  document.querySelectorAll('.ai-fix-btn,[data-ai-fix]').forEach(btn => {
    btn.onclick = function() {
      const row = btn.closest('tr');
      if (!row) return;
      const urlCell = row.querySelector('td.url-cell a, td.url-cell');
      const url = urlCell ? urlCell.textContent.trim() : '';
      const pageData = allResults.find(r => r.url === url) || { url };
      openAiDrawer(pageData);
    };
  });
}

/* ── Sync AI pill to sidebar after checkGemini runs ─────────────────────── */
const _origCheckGemini = window.checkGemini;
window.checkGemini = async function() {
  if (_origCheckGemini) await _origCheckGemini();
  syncSidebarAI();
};

/* ── Open Schema panel after crawl (auto-populate) ───────────────────────── */
const _origLoadResults = window.loadResults;
window.loadResults = async function() {
  if (_origLoadResults) await _origLoadResults();
  renderSchemaPanel();
};

/* ── Init ─────────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  /* body already has app-mode class — just ensure panel state is correct */
  Object.entries(_PANELS).forEach(([k, cfg]) => {
    const el = document.getElementById(cfg.el);
    if (el && k !== 'dashboard') el.classList.add('panel-hidden');
  });
  syncTopbarAuth();
  /* Sync crawl bar input → hero input on change */
  const appInp = document.getElementById('url-input-app');
  const heroInp = document.getElementById('url-input');
  if (appInp && heroInp) {
    appInp.addEventListener('input', () => { heroInp.value = appInp.value; });
  }
});


/* ── Utils ── */
function toggleLandingMobileNav(){
  document.getElementById('landing-mob-drawer').classList.toggle('open');
  document.getElementById('landing-mob-overlay').classList.toggle('open');
}
function closeLandingMobileNav(){
  document.getElementById('landing-mob-drawer').classList.remove('open');
  document.getElementById('landing-mob-overlay').classList.remove('open');
}

/* ── URL param auto-trigger — fires when /app/?url=... is opened ─────────────
   Safe: checks for presence, validates, no infinite loop (runs once on load).
   Works on direct link, refresh, and page open from startCrawlHero redirect. */
(function(){
  'use strict';
  var params = new URLSearchParams(window.location.search);
  var targetUrl = params.get('url');
  if(!targetUrl) return;

  // Decode and normalise
  var url = decodeURIComponent(targetUrl).trim();
  if(!url) return;
  if(!/^https?:\/\//i.test(url)) url = 'https://' + url;

  // Wait for DOM + app init, then auto-start
  function tryAutoStart(){
    var appInput = document.getElementById('url-input-app');
    var crawlBtn = document.getElementById('crawl-btn-app') || document.getElementById('crawl-btn');
    if(!appInput) {
      // App not ready yet — retry after 200ms, max 10 times
      if((tryAutoStart._tries = (tryAutoStart._tries||0) + 1) < 10)
        setTimeout(tryAutoStart, 200);
      return;
    }
    appInput.value = url;
    // Also fill hero input if present
    var heroInput = document.getElementById('url-input');
    if(heroInput) heroInput.value = url;
    // Store for session
    localStorage.setItem('ciq_last_url', url);
    // Enter app mode if not already
    if(typeof enterAppMode === 'function') enterAppMode();
    // Trigger crawl — use the real startCrawl function
    if(typeof startCrawl === 'function'){
      startCrawl(url);
    } else if(crawlBtn) {
      crawlBtn.click();
    }
    // Clean the URL param so refresh doesn't re-trigger
    if(window.history && window.history.replaceState){
      var cleanUrl = window.location.pathname;
      window.history.replaceState({}, document.title, cleanUrl);
    }
  }

  // Auth check: defer to after DOMContentLoaded + a short grace period
  window.addEventListener('DOMContentLoaded', function(){
    setTimeout(tryAutoStart, 600);
  });
})();
