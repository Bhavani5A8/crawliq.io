/* CrawlIQ core.js — globals, auth, token helpers | Part of app.js split v1.0.3 */
/* CrawlIQ app.js v1.0.2 */
const API = 'https://bhavani7-seo-project.hf.space';
let allResults=[], sortKey='', sortAsc=true, crawlTimer=null, geminiTimer=null;
let _tableRows=[], _tablePage=0;
const _TABLE_PAGE_SIZE=50;
let _sseSource=null, _currentJobId=null;
let selectedUrls=new Set(), popupPages=[], popupIndex=0, maxPages=50;
let optimizerRows=[], optTimer=null;

// Delay HF backend ping — runs 3s after load so it never blocks FCP/LCP
window.addEventListener('load', () => setTimeout(checkGemini, 3000));
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




