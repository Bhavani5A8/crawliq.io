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
    const res = await fetch(`${API}/technical-seo`);
    if (!res.ok) return;
    const d = await res.json();
    const deepIssues = (d.pages || []).reduce((n, p) => n + (p.issue_count || (p.all_issues || []).length || 0), 0);
    if (deepIssues > 0) {
      const techEl = document.getElementById('tech-summary-text');
      if (techEl) techEl.textContent = `${deepIssues} issues (deep audit · ${d.summary?.total_pages || d.pages?.length || '?'} pages)`;
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
  // Render post-crawl dashboard charts
  if (typeof renderDashCharts === 'function') renderDashCharts(rows);
}
function hideSummary(){
  document.getElementById('summary').style.display='none';
  document.getElementById('ibreak').style.display='none';
}

async function loadPopupData(){const data=await(await fetch(`${API}/popup-data`)).json();popupPages=data.pages||[];}

/* ═══════════════════════════════════════════════════════════════════════════
   POPUP CLUSTER GRID — full-screen On-Page SEO cluster view
   ═══════════════════════════════════════════════════════════════════════════ */
const _SEO_CLUSTERS = [
  { id:'all',       label:'All Pages',       icon:'🔍', test: ()=>true },
  { id:'title',     label:'Title Tag',       icon:'📌', test: r=>!r.title||(r.issues||[]).some(i=>/title/i.test(i)) },
  { id:'meta',      label:'Meta',            icon:'📝', test: r=>!r.meta_description||(r.issues||[]).some(i=>/meta/i.test(i)) },
  { id:'headings',  label:'Headings',        icon:'📋', test: r=>(r.issues||[]).some(i=>/\bh1\b|\bh2\b|heading/i.test(i)) },
  { id:'canonical', label:'Canonical',       icon:'🔗', test: r=>(r.issues||[]).some(i=>/canonical/i.test(i)) },
  { id:'images',    label:'Images / Alt',    icon:'🖼', test: r=>(r.issues||[]).some(i=>/image|alt/i.test(i)) },
  { id:'content',   label:'Content',         icon:'📄', test: r=>(r.issues||[]).some(i=>/content|thin|word/i.test(i)) },
  { id:'links',     label:'Links',           icon:'🔀', test: r=>(r.issues||[]).some(i=>/\blink/i.test(i)) },
  { id:'status',    label:'HTTP Errors',     icon:'⚠', test: r=>r.status_code>=400||r.status_code<200 },
];

let _ppCluster='all', _ppFilteredRows=[], _ppDrawerPage=null;

async function openPopup(){
  await loadPopupData();
  if(!popupPages.length&&!allResults.length){alert('No crawl data yet.');return;}
  _ppCluster='all';
  _ppBuildGrid();
  document.getElementById('popup-overlay').style.display='flex';
}

async function openPopupForUrl(url){
  await loadPopupData();
  _ppCluster='all';
  _ppBuildGrid();
  const el=document.getElementById('popup-overlay');
  el.style.display='flex';
  // Search for the specific URL
  const s=document.getElementById('pp-search');
  if(s){s.value=url;ppFilterTable();}
}

function _ppBuildGrid(){
  // Merge popup detail into allResults using URL as key
  const detailMap={};
  for(const p of popupPages) detailMap[p.url]=p;
  _ppAllRows=allResults.map(r=>({...r,...(detailMap[r.url]||{})}));

  // Site URL display
  const siteEl=document.getElementById('pp-site-url');
  if(siteEl){
    const u=document.getElementById('crawl-target-url')||document.getElementById('url-input');
    siteEl.textContent=(u&&(u.textContent||u.value||'').trim())||(_ppAllRows[0]?.url||'');
  }

  // Crawl stats strip
  const total=_ppAllRows.length;
  const withIssues=_ppAllRows.filter(r=>(r.issues||[]).length>0).length;
  const highPri=_ppAllRows.filter(r=>r.priority==='High').length;
  const statsEl=document.getElementById('pp-crawl-stats');
  if(statsEl) statsEl.innerHTML=`<span><b style="color:var(--cyan)">${total}</b> pages</span><span><b style="color:var(--red)">${withIssues}</b> with issues</span><span><b style="color:var(--yellow)">${highPri}</b> high priority</span>`;

  // Build cluster tab bar
  const nav=document.getElementById('pp-cluster-nav');
  if(nav){
    nav.innerHTML=_SEO_CLUSTERS.map(c=>{
      const rows=_ppAllRows.filter(c.test);
      const cnt=rows.length;
      const active=_ppCluster===c.id;
      const bad=rows.filter(r=>(r.issues||[]).length>0).length;
      const pct=cnt>0?Math.round(100-bad/cnt*100):100;
      const col=pct>=80?'var(--green)':pct>=50?'var(--yellow)':'var(--red)';
      return `<button onclick="_ppSelectCluster('${c.id}')" style="padding:6px 12px;font-size:11px;border:none;border-bottom:2px solid ${active?col:'transparent'};background:${active?'var(--surf)':'transparent'};color:${active?'var(--white)':'var(--muted)'};cursor:pointer;border-radius:6px 6px 0 0;white-space:nowrap;transition:all .15s">${c.icon} ${c.label} <span style="font-size:10px;color:${col}">${cnt}</span></button>`;
    }).join('');
  }

  // Stats strip cards
  const statsStrip=document.getElementById('pp-cluster-stats');
  if(statsStrip){
    const clusterRows=_ppAllRows.filter(_SEO_CLUSTERS.find(c=>c.id===_ppCluster).test);
    const ok=clusterRows.filter(r=>!(r.issues||[]).length).length;
    const issues=clusterRows.length-ok;
    const high=clusterRows.filter(r=>r.priority==='High').length;
    const health=clusterRows.length>0?Math.round(ok/clusterRows.length*100):100;
    const hCol=health>=80?'var(--green)':health>=50?'var(--yellow)':'var(--red)';
    statsStrip.innerHTML=[
      ['Health',`<span style="color:${hCol}">${health}%</span>`],
      ['Pages',clusterRows.length],
      ['Clean',ok],
      ['Issues',`<span style="color:var(--red)">${issues}</span>`],
      ['High Priority',`<span style="color:var(--red)">${high}</span>`],
    ].map(([l,v])=>`<div style="flex:1;text-align:center;padding:10px 8px;border-right:1px solid var(--border)"><div style="font-size:16px;font-weight:700">${v}</div><div style="font-size:10px;color:var(--muted);margin-top:2px">${l}</div></div>`).join('');
  }

  // Build table headers
  const thead=document.getElementById('pp-thead');
  if(thead) thead.innerHTML='<tr><th style="width:30%">URL</th><th>Status</th><th>Priority</th><th>Score</th><th>Title</th><th>Meta</th><th>H1</th><th>Issues</th></tr>';

  // Render table
  const search=(document.getElementById('pp-search')?.value||'').toLowerCase();
  _ppRenderTable(search);
}

function _ppSelectCluster(id){
  _ppCluster=id;
  _ppBuildGrid();
}

function ppFilterTable(){
  const search=(document.getElementById('pp-search')?.value||'').toLowerCase();
  _ppRenderTable(search);
}

function _ppRenderTable(search=''){
  const cluster=_SEO_CLUSTERS.find(c=>c.id===_ppCluster)||_SEO_CLUSTERS[0];
  let rows=_ppAllRows.filter(cluster.test);
  if(search) rows=rows.filter(r=>(r.url||'').toLowerCase().includes(search)||(r.title||'').toLowerCase().includes(search)||(r.issues||[]).some(i=>i.toLowerCase().includes(search)));
  _ppFilteredRows=rows;
  const cnt=document.getElementById('pp-row-count');
  if(cnt) cnt.textContent=`${rows.length} page${rows.length!==1?'s':''}`;

  const tbody=document.getElementById('pp-tbody');
  if(!tbody) return;
  if(!rows.length){
    tbody.innerHTML='<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--muted)">No pages match this cluster.</td></tr>';
    return;
  }
  tbody.innerHTML=rows.map((r,i)=>{
    const issues=r.issues||[];
    const score=r.ranking?.score??r.ranking_score??'—';
    const grade=r.ranking?.grade??r.ranking_grade??'';
    const sc=typeof score==='number'?score:0;
    const scoreCol=sc>=80?'var(--green)':sc>=60?'var(--cyan)':sc>=40?'var(--yellow)':'var(--red)';
    const priCl={High:'ph',Medium:'pm',Low:'pl'}[r.priority]||'';
    const statusCol=r.status_code>=400?'var(--red)':r.status_code>=300?'var(--yellow)':'var(--green)';
    const h1=Array.isArray(r.h1)?r.h1[0]||'—':r.h1||'—';
    const rowBg=i%2===0?'var(--bg)':'var(--surf)';
    return `<tr style="background:${rowBg};cursor:pointer" onclick="_ppOpenDrawer('${escJ(r.url||'')}')" title="Click for full details">
      <td style="font-family:monospace;font-size:10px;color:var(--cyan);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.url||'')}</td>
      <td style="color:${statusCol};font-weight:700;font-size:11px">${r.status_code||'—'}</td>
      <td>${r.priority?`<span class="pb ${priCl}">${esc(r.priority)}</span>`:'—'}</td>
      <td style="color:${scoreCol};font-weight:700">${score}${grade?` <span style="font-size:9px;opacity:.7">${grade}</span>`:''}</td>
      <td style="font-size:10px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:${r.title?'var(--white)':'var(--muted)'}">${esc(r.title||'(missing)')}</td>
      <td style="font-size:10px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:${r.meta_description?'var(--white)':'var(--muted)'}">${esc(r.meta_description?r.meta_description.slice(0,60)+'…':'(missing)')}</td>
      <td style="font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(h1)}</td>
      <td style="max-width:200px">${issues.slice(0,3).map(i=>`<span class="itag" style="font-size:9px">${esc(i)}</span>`).join('')}${issues.length>3?`<span style="color:var(--muted);font-size:9px"> +${issues.length-3}</span>`:''}</td>
    </tr>`;
  }).join('');
}

function _ppOpenDrawer(url){
  const page=_ppAllRows.find(r=>r.url===url);
  if(!page) return;
  _ppDrawerPage=page;
  document.getElementById('ppd-url').textContent=url;
  const score=page.ranking?.score??page.ranking_score??'—';
  const grade=page.ranking?.grade??page.ranking_grade??'—';
  document.getElementById('ppd-meta').textContent=`Score: ${score} ${grade} · Priority: ${page.priority||'—'} · Status: ${page.status_code||'—'}`;
  const tbody=document.getElementById('ppd-tbody');
  const fields=page.fields||[];
  if(!fields.length){
    tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:20px;color:var(--muted)">No field detail. Run AI analysis for this page.</td></tr>';
  } else {
    tbody.innerHTML=fields.map(f=>{
      const isOK=f.status==='OK'||f.status==='ok';
      const impCl={High:'imp-high',Medium:'imp-med',Low:'imp-low'}[f.impact]||'';
      return `<tr>
        <td class="fname">${esc(f.field)}</td>
        <td class="${f.current?'fval':'fval missing'}" style="max-width:140px;overflow:hidden;text-overflow:ellipsis">${esc(f.current||'(empty)')}</td>
        <td><span class="sstatus ${isOK?'ss-ok':'ss-bad'}">${esc(f.status)}</span></td>
        <td class="fwhy" style="font-size:9px">${esc(f.why||'—')}</td>
        <td class="ffix" style="font-size:9px">${esc(f.fix||(isOK?'No action needed.':'Run AI →'))}</td>
        <td class="fopt" style="font-size:9px">${f.optimized?esc(f.optimized)+'<button class="copy-btn" onclick="copyVal(this,\''+escJ(f.optimized||'')+'\')">⎘</button>':'<span style="color:var(--muted)">Run AI →</span>'}</td>
        <td>${f.impact?`<span class="impact-pill ${impCl}">${esc(f.impact)}</span>`:'—'}</td>
      </tr>`;
    }).join('');
  }
  const drawer=document.getElementById('pp-detail-drawer');
  drawer.style.display='flex';
}

function closePpDrawer(){document.getElementById('pp-detail-drawer').style.display='none';_ppDrawerPage=null;}

async function ppAnalyzeDrawerPage(){
  if(!_ppDrawerPage) return;
  document.getElementById('ppd-ai-status').style.display='inline';
  try{
    const res=await fetch(`${API}/analyze-selected`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:[_ppDrawerPage.url]})});
    if(!res.ok) throw new Error((await res.json()).detail||'AI failed');
    const wd=setInterval(async()=>{
      try{const s=await(await fetch(`${API}/gemini-status`)).json();
        if(s.done||s.error){clearInterval(wd);document.getElementById('ppd-ai-status').style.display='none';await loadResults();await loadPopupData();_ppBuildGrid();_ppOpenDrawer(_ppDrawerPage.url);}
      }catch{}
    },1500);
  }catch(e){document.getElementById('ppd-ai-status').style.display='none';alert('AI error: '+e.message);}
}

function navPage(dir){} // kept for compat, no longer used
function renderPopupPage(){} // kept for compat
function analyzeThisPage(){if(_ppDrawerPage)ppAnalyzeDrawerPage();}
function closePopup(){document.getElementById('popup-overlay').style.display='none';closePpDrawer();}
function overlayClick(e){if(e.target===document.getElementById('popup-overlay'))closePopup();}

function openExportModal(){
  const wi=allResults.filter(r=>r.issues&&r.issues.length);
  set('em-pages',allResults.length);set('em-issues',wi.length);
  document.getElementById('eoverlay').style.display='flex';
}
function closeExportModal(){document.getElementById('eoverlay').style.display='none';}
function openExportFromPopup(){closePopup();openExportModal();}
async function downloadExcel(type){
  try{
    let ep, fname;
    if(type==='full-report'){ep='/export-full';fname='crawliq_full_report.xlsx';}
    else if(type==='popup'){ep='/export-popup';fname='seo_issues.xlsx';}
    else{ep='/export';fname='seo_report.xlsx';}
    bar('o',true,`Generating ${fname}…`);
    const res=await fetch(`${API}${ep}`);
    if(!res.ok) throw new Error((await res.json().catch(()=>({}))).detail||'Export failed');
    const a=document.createElement('a');
    a.href=URL.createObjectURL(await res.blob());
    a.download=fname;
    document.body.appendChild(a);a.click();
    setTimeout(()=>{URL.revokeObjectURL(a.href);document.body.removeChild(a);},1000);
    bar('o',false,`✓ ${fname} downloaded`);
    closeExportModal();
  }catch(e){bar('o',false,`✗ Export error: ${e.message}`);}
}
async function exportPDF(){
  const btn=document.getElementById('pdf-btn');
  if(btn) btn.disabled=true;
  bar('o',true,'Generating PDF report…');
  try{
    const urlEl=document.getElementById('crawl-target-url')||document.getElementById('url-input');
    const siteUrl=urlEl?(urlEl.textContent||urlEl.value||'').trim():'';
    const endpoint=`${API}/export-pdf${siteUrl?'?url='+encodeURIComponent(siteUrl):''}`;
    const res=await fetch(endpoint);
    if(!res.ok) throw new Error(((await res.json().catch(()=>({}))).detail)||'PDF export failed');
    const blob=await res.blob();
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download='crawliq_report.pdf';
    document.body.appendChild(a);
    a.click();
    setTimeout(()=>{URL.revokeObjectURL(a.href);document.body.removeChild(a);},1000);
    bar('o',false,'✓ PDF downloaded');
  }catch(e){
    bar('o',false,`✗ PDF error: ${e.message}`);
  }finally{
    if(btn) btn.disabled=false;
  }
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

