/* CrawlIQ ui.js — results table, filters, export, badge utils | Part of app.js split v1.0.3 */
async function loadResults() {
  const data=await(await fetch(`${API}/results`)).json();
  allResults=data.results||[];
  updateSummary(allResults); applyFilters();
  flagXRobotsIssues(data);
  flagCanonicalIssues(data);
  loadTechnicalDeep();
}

// Flags pages where X-Robots-Tag HTTP header says noindex
// (crawl_engine.py already captures this in meta_robots — display it here)
function flagXRobotsIssues(data) {
  const pages = data.results || data.pages || [];
  const bad = pages.filter(p => {
    const mr = (p.meta_robots||'').toLowerCase();
    return mr.includes('noindex');
  });
  if (!bad.length) return;
  const techEl = document.getElementById('tech-summary-text');
  if (techEl) {
    const cur = techEl.textContent||'';
    techEl.textContent = cur + ` · ${bad.length} X-Robots noindex conflict(s)`;
  }
}

// Surfaces canonical chain issues returned by backend
function flagCanonicalIssues(data) {
  const pages = data.results || data.pages || [];
  const bad = pages.filter(p => p.canonical_status && p.canonical_status !== 'ok' && p.canonical_status !== 'self');
  if (!bad.length) return;
  const techEl = document.getElementById('tech-summary-text');
  if (techEl) {
    const cur = techEl.textContent||'';
    techEl.textContent = cur + ` · ${bad.length} canonical chain issue(s)`;
  }
}

// Calls /technical-seo for richer issue data; falls back gracefully to /results data
async function loadTechnicalDeep() {
  try {
    const urlInput = document.getElementById('url-input-app') || document.getElementById('url-input');
    const url = urlInput ? urlInput.value.trim() : (allResults[0]&&allResults[0].url)||'';
    if (!url) return;
    const res = await fetch(`${API}/technical-seo`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ url })
    });
    if (!res.ok) return;
    const d = await res.json();
    const deepIssues = d.issues || d.technical_issues || [];
    if (deepIssues.length > 0) {
      const techEl = document.getElementById('tech-summary-text');
      if (techEl) techEl.textContent = `${deepIssues.length} issues (deep audit · ${d.pages_checked||'?'} pages)`;
    }
  } catch {}
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
  _tableRows = rows;
  _tablePage = 0;
  _renderTablePage();
}

function _renderTablePage() {
  const rows = _tableRows;
  const tb=document.getElementById('results-body');
  if(!rows.length){tb.innerHTML=`<tr><td colspan="14"><div class="empty-state"><div class="icon">🔍</div><p>No results yet. Enter a URL above and crawl.</p></div></td></tr>`;
    _renderTablePagination(0,0); return;}
  const start=_tablePage*_TABLE_PAGE_SIZE, slice=rows.slice(start,start+_TABLE_PAGE_SIZE);
  tb.innerHTML=slice.map(r=>{
    const h1=Array.isArray(r.h1)?r.h1[0]||'—':r.h1||'—';
    const aiFix=(r.gemini_fields||[]).filter(f=>f.issue!=='OK'&&(f.fix||f.suggestion)).map(f=>f.fix||f.suggestion).join(' · ');
    const hasIss=r.issues&&r.issues.length>0;
    const sel=selectedUrls.has(r.url);
    const score=r.ranking?`<span class="score-pill score-${r.ranking.grade.toLowerCase()}">${r.ranking.score} ${r.ranking.grade}</span>`:'—';
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
  _renderTablePagination(rows.length, Math.ceil(rows.length/_TABLE_PAGE_SIZE));
}

function _renderTablePagination(total, pages) {
  const el = document.getElementById('table-pagination');
  if (!el) return;
  el.innerHTML = pages > 1 ? `
    <div style="display:flex;gap:8px;padding:10px 0;font-size:11px;align-items:center;font-family:var(--mono);">
      <button class="btn btn-outline btn-sm" onclick="_tableGo(-1)" ${_tablePage===0?'disabled':''}>← Prev</button>
      <span style="color:var(--dim);">Page ${_tablePage+1} of ${pages} &middot; ${total} pages total</span>
      <button class="btn btn-outline btn-sm" onclick="_tableGo(1)" ${_tablePage>=pages-1?'disabled':''}>Next →</button>
    </div>` : '';
}

function _tableGo(dir) {
  const pages=Math.ceil(_tableRows.length/_TABLE_PAGE_SIZE);
  _tablePage=Math.max(0,Math.min(pages-1,_tablePage+dir));
  _renderTablePage();
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

