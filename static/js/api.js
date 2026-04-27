/* ── CrawlIQ shared API utilities — loaded by both landing page and app ── */

const API = 'https://bhavani7-seo-project.hf.space';

/* ── Auth state ── */
let _ciqToken = localStorage.getItem('ciq_token') || '';
let _ciqUser  = null;
window._ciqProject = null;

function ciqHeaders() {
  const h = { 'Content-Type': 'application/json' };
  if (_ciqToken) h['Authorization'] = 'Bearer ' + _ciqToken;
  return h;
}

async function safeAuthFetch(url, opts = {}) {
  opts.headers = { ...(opts.headers || {}), ...ciqHeaders() };
  return fetch(url, opts);
}

async function safeJson(res) {
  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    const txt   = await res.text();
    const lower = txt.toLowerCase();
    if (lower.includes('space') || lower.includes('waking') || lower.includes('sleeping') || res.status >= 502) {
      const err = new Error('HF Space is waking up — auto-retrying…');
      err.isColdStart = true;
      throw err;
    }
    throw new Error(`Server error (HTTP ${res.status}). Please retry.`);
  }
  return res.json();
}

/* ── Auth modal ── */
function openAuthModal() {
  document.getElementById('auth-modal').style.display = 'flex';
  document.getElementById('auth-error').style.display = 'none';
}
function closeAuthModal() {
  document.getElementById('auth-modal').style.display = 'none';
}
function authTab(tab) {
  document.getElementById('auth-form-login').style.display    = tab === 'login'    ? '' : 'none';
  document.getElementById('auth-form-register').style.display = tab === 'register' ? '' : 'none';
  document.getElementById('tab-login').classList.toggle('active', tab === 'login');
  document.getElementById('tab-register').classList.toggle('active', tab === 'register');
}

async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const pass  = document.getElementById('login-pass').value;
  const errEl = document.getElementById('auth-error');
  errEl.style.display = 'none';
  try {
    const res = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password: pass }),
    });
    const d = await safeJson(res);
    if (!res.ok) throw new Error(d.detail || 'Login failed');
    _ciqToken = d.token;
    _ciqUser  = d.user;
    localStorage.setItem('ciq_token', _ciqToken);
    closeAuthModal();
    applyAuthState();
  } catch (e) {
    errEl.textContent    = e.message;
    errEl.style.display  = '';
  }
}

async function doRegister() {
  const name  = document.getElementById('reg-name').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const pass  = document.getElementById('reg-pass').value;
  const errEl = document.getElementById('auth-error');
  errEl.style.display = 'none';
  try {
    const res = await fetch(`${API}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, email, password: pass }),
    });
    const d = await safeJson(res);
    if (!res.ok) throw new Error(d.detail || 'Registration failed');
    _ciqToken = d.token;
    _ciqUser  = d.user;
    localStorage.setItem('ciq_token', _ciqToken);
    closeAuthModal();
    applyAuthState();
  } catch (e) {
    errEl.textContent   = e.message;
    errEl.style.display = '';
  }
}

function doLogout() {
  _ciqToken = '';
  _ciqUser  = null;
  window._ciqProject = null;
  localStorage.removeItem('ciq_token');
  localStorage.removeItem('ciq_brand_name');
  applyAuthState();
}

async function loadCurrentUser() {
  if (!_ciqToken) return;
  try {
    const res = await safeAuthFetch(`${API}/auth/me`);
    if (res.ok) {
      _ciqUser = await safeJson(res);
      applyAuthState();
    } else {
      _ciqToken = '';
      localStorage.removeItem('ciq_token');
    }
  } catch {
    _ciqToken = '';
  }
}

function applyAuthState() {
  const loggedIn = !!_ciqUser;
  const guestEl  = document.getElementById('nav-auth-guest');
  const userEl   = document.getElementById('nav-auth-user');
  if (guestEl) guestEl.style.display = loggedIn ? 'none' : 'flex';
  if (userEl)  userEl.style.display  = loggedIn ? 'flex' : 'none';
  if (loggedIn) {
    const name     = _ciqUser.name || _ciqUser.email || 'User';
    const initials = name.substring(0, 2).toUpperCase();
    const tier     = _ciqUser.tier || 'free';
    const avatarEl = document.getElementById('user-avatar-initials');
    const nameEl   = document.getElementById('user-display-name');
    const badgeEl  = document.getElementById('user-tier-badge');
    if (avatarEl) avatarEl.textContent = initials;
    if (nameEl)   nameEl.textContent   = name.split(' ')[0];
    if (badgeEl) {
      badgeEl.textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
      badgeEl.className   = `tier-badge tier-${tier}`;
    }
  }
}

function toggleUserMenu() {
  document.getElementById('user-menu')?.classList.toggle('open');
}

document.addEventListener('click', e => {
  if (!e.target.closest('.user-chip')) {
    document.getElementById('user-menu')?.classList.remove('open');
  }
});
