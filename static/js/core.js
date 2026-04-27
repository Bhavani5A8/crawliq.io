/* CrawlIQ core.js v2.0.0 — App orchestrator
   Renders full dashboard HTML, handles URL param routing, lazy-loads modules */

// ── API base ──────────────────────────────────────────────────────────────────
const API = 'https://bhavani7-seo-project.hf.space';

// ── Globals (shared with crawl.js / ui.js / dashboard.js) ────────────────────
let allResults = [], sortKey = '', sortAsc = true, crawlTimer = null, geminiTimer = null;
let _tableRows = [], _tablePage = 0;
const _TABLE_PAGE_SIZE = 50;
let _sseSource = null, _currentJobId = null;
let selectedUrls = new Set(), popupPages = [], popupIndex = 0, maxPages = 50;
let optimizerRows = [], optTimer = null;
let _ciqToken = localStorage.getItem('ciq_token') || '';
let _ciqUser = null;
window._ciqProject = null;

// ── Lazy script loader ────────────────────────────────────────────────────────
function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = reject;
    document.body.appendChild(s);
  });
}

// ── Full app HTML template ────────────────────────────────────────────────────
function getAppHTML() {
  return `
<!-- Hidden URL input synced with topbar input — read by startCrawl() in crawl.js -->
<input type="hidden" id="url-input" value=""/>

<!-- ══ APP TOPBAR ════════════════════════════════════════════════════════════ -->
<div class="app-topbar" id="app-topbar" style="display:none">
  <div class="app-topbar-main">
    <a class="nav-logo" href="../" style="text-decoration:none;flex-shrink:0">
      <span class="material-symbols-outlined nav-logo-icon" style="font-size:20px;font-variation-settings:'FILL' 1,'wght' 700">bolt</span>
      <span class="nav-logo-text" style="font-size:15px">Crawl<em>IQ</em></span>
    </a>
    <div class="app-crawl-bar" id="app-crawl-bar" style="margin:0;flex:1;max-width:580px">
      <div class="app-crawl-wrap">
        <span class="material-symbols-outlined app-crawl-icon">language</span>
        <input id="url-input-app" type="text" class="app-crawl-input" placeholder="Enter website URL… e.g. mysite.com"
          oninput="document.getElementById('url-input').value=this.value"
          onkeydown="if(event.key==='Enter'){document.getElementById('url-input').value=this.value;startCrawl();}"/>
      </div>
      <button class="app-crawl-btn" onclick="document.getElementById('url-input').value=document.getElementById('url-input-app').value;startCrawl()">
        <span class="material-symbols-outlined" style="font-size:16px">bolt</span>
        Analyse
      </button>
    </div>
    <div class="app-topbar-spacer"></div>
    <div class="app-topbar-actions">
      <div class="ai-pill" onclick="openAiSetup()" style="cursor:pointer;" title="AI Setup">
        <div class="ai-pill-dot"></div>
        <span id="gemini-pill" class="gemini-pill">AI</span>
      </div>
      <div id="nav-auth-user" style="display:none;align-items:center;gap:8px">
        <div class="user-chip" onclick="toggleUserMenu()">
          <div class="user-avatar" id="user-avatar-initials">U</div>
          <span id="user-display-name" style="font-size:12px;color:var(--text)">User</span>
        </div>
        <div class="user-menu" id="user-menu">
          <button onclick="openSettings()" style="background:none;border:none;color:var(--dim);cursor:pointer;padding:8px 14px;font-size:12px;width:100%;text-align:left">⚙ Settings</button>
          <button onclick="openProjects()" style="background:none;border:none;color:var(--dim);cursor:pointer;padding:8px 14px;font-size:12px;width:100%;text-align:left">📁 Projects</button>
          <button onclick="doLogout()" style="background:none;border:none;color:var(--red);cursor:pointer;padding:8px 14px;font-size:12px;width:100%;text-align:left">Sign Out</button>
        </div>
      </div>
      <div id="nav-auth-guest" style="display:flex;gap:8px;align-items:center">
        <button class="btn btn-outline btn-sm" onclick="openAuthModal()" style="font-size:11px">Sign In</button>
        <button class="btn btn-sm" onclick="authTab('register');openAuthModal()" style="font-size:11px;background:var(--primary);color:#1a1c40;border:none;font-weight:700">Sign Up</button>
      </div>
    </div>
  </div>
  <div class="app-panel-tabs">
    <button class="apt active" id="sn-dashboard" onclick="showPanel('dashboard')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">dashboard</span>Dashboard
    </button>
    <button class="apt" id="sn-serp" onclick="showPanel('serp')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">bar_chart</span>SERP Intel
    </button>
    <button class="apt" id="sn-monitor" onclick="showPanel('monitor')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">notifications</span>Rank Monitor
    </button>
    <button class="apt" id="sn-competitors" onclick="showPanel('competitors')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">compare_arrows</span>Competitors
    </button>
    <button class="apt" id="sn-schema" onclick="showPanel('schema')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">data_object</span>Schema
    </button>
    <button class="apt" id="sn-content-lab" onclick="showPanel('content-lab')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">auto_fix_high</span>Content Lab
    </button>
    <button class="apt" onclick="openKwGap()" style="margin-left:auto">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">search</span>Keyword Gap
    </button>
    <button class="apt" onclick="openSettings()">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">settings</span>Settings
    </button>
  </div>
</div>

<!-- ══ WELCOME SCREEN (shown when no ?start param) ═════════════════════════ -->
<div id="app-welcome" style="display:none;min-height:100vh;align-items:center;justify-content:center;flex-direction:column;padding:48px 24px;text-align:center;position:relative;z-index:1;">
  <div style="position:absolute;left:25%;top:25%;width:384px;height:384px;border-radius:50%;background:var(--primary);filter:blur(120px);opacity:.12;pointer-events:none;"></div>
  <div style="position:absolute;right:25%;bottom:25%;width:300px;height:300px;border-radius:50%;background:var(--tertiary);filter:blur(100px);opacity:.10;pointer-events:none;"></div>
  <div style="font-family:var(--headline);font-size:clamp(32px,5vw,64px);font-weight:800;color:white;letter-spacing:-.04em;margin-bottom:16px;position:relative;">
    ⚡ Crawl<span style="color:var(--primary)">IQ</span>
  </div>
  <p style="font-size:16px;color:var(--dim);max-width:480px;margin:0 auto 40px;line-height:1.7;">
    Enter a website URL to begin your technical SEO evaluation.
    Examine 50+ on-page signals, detect structural issues, and receive AI remediation guidance.
  </p>
  <div class="hero-input-row" style="max-width:600px;width:100%;background:var(--surf-low);border-radius:20px;padding:8px;border:1px solid rgba(70,69,84,.2);box-shadow:0 24px 80px rgba(0,0,0,.5);display:flex;gap:8px;flex-direction:column;">
    <div style="display:flex;align-items:center;gap:10px;padding:0 18px;flex:1;">
      <span class="material-symbols-outlined" style="color:var(--muted);font-size:20px;">language</span>
      <input id="welcome-url-input" type="text" placeholder="Enter website URL… e.g. example.com"
        style="flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:15px;font-family:var(--sans);padding:16px 0;"
        onkeydown="if(event.key==='Enter') startFromWelcome()"
        oninput="document.getElementById('url-input').value=this.value"/>
    </div>
    <button onclick="startFromWelcome()"
      style="background:var(--primary);border:none;color:#1a1c40;font-family:var(--headline);font-weight:700;font-size:14px;padding:14px 32px;border-radius:var(--r-xl);cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;transition:opacity .15s;white-space:nowrap;letter-spacing:-.2px;">
      ⚡ Begin Technical Assessment
    </button>
  </div>
  <div style="margin-top:16px;font-size:11px;color:var(--muted);">No signup required · Up to 50 pages free · <a href="../" style="color:var(--dim);text-decoration:underline;">Documentation →</a></div>
</div>

<!-- ══ AUTH MODAL ════════════════════════════════════════════════════════════ -->
<div id="auth-modal" class="modal-overlay" style="display:none">
<div class="modal-box">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
    <svg width="22" height="22" viewBox="0 0 28 28" fill="none"><polygon points="16,2 9,14 14,14 7,26 21,10 15,10 22,2" fill="#22D3EE"/></svg>
    <span style="font-size:16px;font-weight:800;color:var(--text)">Crawl<em style="color:var(--cyan)">IQ</em></span>
    <span style="margin-left:auto;font-size:10px;color:var(--dim)">Sign in to save projects &amp; track rankings</span>
  </div>
  <div class="auth-tabs">
    <button class="auth-tab active" id="tab-login" onclick="authTab('login')">Sign In</button>
    <button class="auth-tab" id="tab-register" onclick="authTab('register')">Create Account</button>
  </div>
  <div id="auth-error" class="auth-error" style="display:none"></div>
  <div id="auth-form-login">
    <div class="auth-field"><label>Email</label><input id="login-email" type="email" placeholder="you@example.com" autocomplete="email"/></div>
    <div class="auth-field"><label>Password</label><input id="login-pass" type="password" placeholder="••••••••" autocomplete="current-password"/></div>
    <button class="btn btn-green" style="width:100%;margin-top:4px" onclick="doLogin()">Sign In</button>
    <button class="btn btn-outline" style="width:100%;margin-top:8px;font-size:11px" onclick="closeAuthModal()">Continue without account</button>
  </div>
  <div id="auth-form-register" style="display:none">
    <div class="auth-field"><label>Name</label><input id="reg-name" type="text" placeholder="Your name" autocomplete="name"/></div>
    <div class="auth-field"><label>Email</label><input id="reg-email" type="email" placeholder="you@example.com" autocomplete="email"/></div>
    <div class="auth-field"><label>Password</label><input id="reg-pass" type="password" placeholder="Min 6 characters" autocomplete="new-password"/></div>
    <button class="btn btn-green" style="width:100%;margin-top:4px" onclick="doRegister()">Create Free Account</button>
    <div style="font-size:10px;color:var(--dim);text-align:center;margin-top:8px">Free plan: 200 pages/month · 3 projects · No credit card required</div>
    <button class="btn btn-outline" style="width:100%;margin-top:8px;font-size:11px" onclick="closeAuthModal()">Continue without account</button>
  </div>
</div>
</div>

<!-- ══ SETTINGS MODAL ════════════════════════════════════════════════════════ -->
<div id="settings-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:500px;max-height:90vh;overflow-y:auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
    <h2>⚙ Settings</h2>
    <button onclick="closeSettings()" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <div class="settings-section">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:8px">Usage This Month</div>
    <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text);margin-bottom:4px">
      <span id="usage-label">Pages crawled</span><span id="usage-count" style="font-family:var(--mono)">0 / 200</span>
    </div>
    <div class="usage-bar-wrap"><div id="usage-bar" class="usage-bar" style="width:0%;background:var(--indigo)"></div></div>
    <div id="usage-tier" style="font-size:10px;color:var(--dim);margin-top:4px">Loading…</div>
    <div id="billing-actions" style="margin-top:10px;display:none"></div>
  </div>
  <div class="settings-section">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:8px">Profile</div>
    <div class="auth-field"><label>Display Name</label><input id="set-name" type="text" placeholder="Your name"/></div>
    <div class="auth-field"><label>Alert Email (rank-drop notifications)</label><input id="set-alert-email" type="email" placeholder="alerts@example.com"/></div>
    <div class="auth-field">
      <label>Rank Drop Threshold (alert when position drops by)</label>
      <input id="set-drop-threshold" type="number" min="1" max="50" value="5" style="width:80px"/>
    </div>
    <button class="btn btn-green" onclick="saveSettings()">Save Profile</button>
  </div>
  <div class="settings-section">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:8px">White-Label PDF Branding</div>
    <div class="auth-field"><label>Brand Name</label><input id="set-brand-name" type="text" placeholder="Your Company Name"/></div>
    <div class="auth-field"><label>Logo (PNG/JPG, max 512 KB)</label><input type="file" id="logo-upload" accept="image/*" onchange="uploadLogo(this)"/></div>
    <div id="logo-status" style="font-size:10px;color:var(--dim);margin-top:4px"></div>
  </div>
  <div class="settings-section">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:8px">API Access</div>
    <div style="display:flex;gap:8px;align-items:center">
      <input id="api-key-display" type="password" readonly style="flex:1;background:var(--surf2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:12px;font-family:var(--mono);color:var(--text)"/>
      <button class="btn btn-outline btn-sm" onclick="toggleApiKeyVisibility()">👁</button>
      <button class="btn btn-outline btn-sm" onclick="rotateApiKey()">Rotate</button>
    </div>
    <div style="font-size:10px;color:var(--dim);margin-top:6px">Header: <code style="color:var(--cyan)">X-API-Key: &lt;key&gt;</code></div>
  </div>
  <div class="settings-section">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:8px">Google Search Console</div>
    <div id="gsc-not-connected">
      <div style="font-size:11px;color:var(--dim);margin-bottom:8px">Connect GSC to pull real impressions, CTR, and ranking data.</div>
      <button class="btn btn-outline" onclick="connectGSC()" id="gsc-connect-btn">Connect Google Search Console</button>
    </div>
    <div id="gsc-connected-panel" style="display:none">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span style="color:var(--green);font-size:11px;font-weight:600">Connected</span>
        <select id="gsc-site-select" class="serp-input" style="flex:1;font-size:11px" onchange="loadGscData()"></select>
        <button class="btn btn-outline btn-sm" onclick="disconnectGSC()" style="font-size:10px;padding:4px 8px">Disconnect</button>
      </div>
      <div id="gsc-summary" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:6px;margin-bottom:10px"></div>
      <div id="gsc-kw-table" style="max-height:220px;overflow-y:auto"></div>
      <div style="font-size:9px;color:var(--dim);margin-top:6px" id="gsc-date-range"></div>
    </div>
    <div id="gsc-status" style="font-size:10px;color:var(--dim);margin-top:6px"></div>
  </div>
</div>
</div>

<!-- ══ PROJECTS MODAL ════════════════════════════════════════════════════════ -->
<div id="projects-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:640px;max-height:90vh;overflow-y:auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
    <h2>📁 Projects</h2>
    <button onclick="closeProjects()" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <div style="display:flex;gap:8px;margin-bottom:16px">
    <input id="new-proj-name" class="serp-input" placeholder="Project name" style="flex:1"/>
    <input id="new-proj-url" class="serp-input" placeholder="https://example.com" style="flex:2"/>
    <button class="btn btn-green" onclick="createProject()">+ Create</button>
  </div>
  <div id="proj-list" style="display:flex;flex-direction:column;gap:8px">
    <div style="text-align:center;color:var(--dim);font-size:11px;padding:20px">Loading projects…</div>
  </div>
</div>
</div>

<!-- ══ KEYWORD GAP MODAL ═════════════════════════════════════════════════════ -->
<div id="kwgap-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:700px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <h2>🔍 Keyword Gap Analysis</h2>
    <button onclick="document.getElementById('kwgap-modal').style.display='none'" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">
    <div style="flex:1;min-width:220px">
      <div style="font-size:10px;color:var(--dim);margin-bottom:4px;font-weight:700">YOUR KEYWORDS (one per line)</div>
      <textarea id="kwgap-yours" class="serp-input" rows="6" placeholder="keyword 1&#10;keyword 2&#10;..." style="width:100%;resize:vertical"></textarea>
    </div>
    <div style="flex:1;min-width:220px">
      <div style="font-size:10px;color:var(--dim);margin-bottom:4px;font-weight:700">COMPETITOR KEYWORDS (one per line)</div>
      <textarea id="kwgap-theirs" class="serp-input" rows="6" placeholder="keyword 1&#10;keyword 2&#10;..." style="width:100%;resize:vertical"></textarea>
    </div>
  </div>
  <button class="btn btn-green" onclick="runKeywordGap()">▶ Analyze Gap</button>
  <div id="kwgap-results" style="display:none;margin-top:16px">
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px" id="kwgap-summary"></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <div class="kw-gap-box" style="flex:2"><h4 style="color:var(--red)">Gap Opportunities</h4><div id="kwgap-only-comp" style="max-height:200px;overflow-y:auto"></div></div>
      <div class="kw-gap-box" style="flex:1"><h4 style="color:var(--green)">Your Unique Keywords</h4><div id="kwgap-only-you" style="max-height:200px;overflow-y:auto"></div></div>
      <div class="kw-gap-box" style="flex:1"><h4 style="color:var(--yellow)">Shared Keywords</h4><div id="kwgap-shared" style="max-height:200px;overflow-y:auto"></div></div>
    </div>
  </div>
</div>
</div>

<!-- ══ CRAWL DIFF MODAL ═══════════════════════════════════════════════════════ -->
<div id="diff-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:680px;max-height:90vh;overflow-y:auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <h2>🔄 Crawl Comparison</h2>
    <button onclick="document.getElementById('diff-modal').style.display='none'" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <div id="diff-loading" style="text-align:center;color:var(--dim);padding:20px;font-size:12px">Loading diff…</div>
  <div id="diff-content" style="display:none">
    <div id="diff-summary" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px"></div>
    <div style="display:flex;gap:10px">
      <div style="flex:1"><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--red);margin-bottom:8px">New Issues <span id="diff-new-count" style="color:var(--red)"></span></div><div id="diff-new-list" style="max-height:250px;overflow-y:auto;font-size:11px;font-family:var(--mono)"></div></div>
      <div style="flex:1"><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--green);margin-bottom:8px">Fixed Issues <span id="diff-fixed-count" style="color:var(--green)"></span></div><div id="diff-fixed-list" style="max-height:250px;overflow-y:auto;font-size:11px;font-family:var(--mono)"></div></div>
    </div>
  </div>
  <div id="diff-no-data" style="display:none;text-align:center;color:var(--dim);padding:20px;font-size:11px"></div>
</div>
</div>

<!-- ══ TEAM MODAL ════════════════════════════════════════════════════════════ -->
<div id="team-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:500px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <h2>👥 Team Access</h2>
    <button onclick="document.getElementById('team-modal').style.display='none'" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <div id="team-proj-name" style="font-size:12px;color:var(--cyan);font-family:var(--mono);margin-bottom:14px"></div>
  <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
    <input id="invite-email" class="serp-input" type="email" placeholder="colleague@example.com" style="flex:2"/>
    <select id="invite-role" class="serp-input" style="flex:1"><option value="viewer">Viewer</option><option value="editor">Editor</option></select>
    <button class="btn btn-green" onclick="inviteTeamMember()">Invite</button>
  </div>
  <div id="invite-msg" style="font-size:10px;color:var(--dim);margin-bottom:10px;display:none"></div>
  <div id="team-members-list" style="font-size:11px;font-family:var(--mono)"><div style="color:var(--muted);padding:10px 0">Loading…</div></div>
</div>
</div>

<!-- ══ SITEMAP CRAWL MODAL ═══════════════════════════════════════════════════ -->
<div id="sitemap-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:480px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <h2>🗺 Sitemap Crawl</h2>
    <button onclick="document.getElementById('sitemap-modal').style.display='none'" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <p style="font-size:11px;color:var(--dim);margin-bottom:16px">Parse a <code style="color:var(--cyan)">sitemap.xml</code> and evaluate only those URLs. Useful for large sites.</p>
  <div class="auth-field"><label>Sitemap URL</label><input id="sitemap-url-input" type="url" placeholder="https://example.com/sitemap.xml"/></div>
  <div class="auth-field"><label>Max Pages</label><input id="sitemap-max-pages" type="number" min="1" max="500" value="100" style="width:100px"/></div>
  <div id="sitemap-error" style="font-size:11px;color:var(--red);margin-bottom:8px;display:none"></div>
  <button class="btn btn-green" style="width:100%" onclick="startSitemapCrawl()">▶ Start Sitemap Crawl</button>
</div>
</div>

<!-- ══ SCORE HISTORY MODAL ═══════════════════════════════════════════════════ -->
<div id="score-history-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:640px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <h2>📈 Score History</h2>
    <button onclick="document.getElementById('score-history-modal').style.display='none'" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <div id="score-chart-wrap" style="background:var(--surf2);border:1px solid var(--border);border-radius:8px;padding:12px"><canvas id="score-chart" height="180"></canvas></div>
  <div id="score-history-table" style="margin-top:12px;overflow-x:auto">
    <table style="width:100%;font-size:11px;border-collapse:collapse;font-family:var(--mono)">
      <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
        <th style="padding:6px 10px;text-align:left">Date</th><th style="padding:6px 10px;text-align:center">Pages</th>
        <th style="padding:6px 10px;text-align:center">Issues</th><th style="padding:6px 10px;text-align:center">Health Score</th>
      </tr></thead>
      <tbody id="score-history-tbody"></tbody>
    </table>
  </div>
</div>
</div>

<!-- ══ COLD-START BANNER ══════════════════════════════════════════════════════ -->
<div id="cold-banner">
  <div class="cold-banner-row">
    <div class="spin-sm"></div>
    <span class="cold-banner-label">Starting up</span>
    <span id="cold-banner-text">Warming up the evaluation engine — first load takes ~30 seconds</span>
    <span class="cold-elapsed" id="cold-elapsed">90s</span>
  </div>
  <div class="cold-progress-track"><div class="cold-progress-fill"></div></div>
</div>

<!-- ══ MAIN DASHBOARD (shown when app-mode active) ═══════════════════════════ -->
<div id="dash-sec" style="display:none">
  <div class="sec-inner">
    <div class="dash-card">
      <div class="dash-chrome">
        <div class="wb wb-r"></div><div class="wb wb-y"></div><div class="wb wb-g"></div>
        <span class="tab">crawliq.io/dashboard · Site Assessment Report</span>
      </div>
      <div class="metric-grid">
        <div class="mc"><div class="mc-lbl">Pages Evaluated</div><div class="mc-val" id="s-total" style="color:white">0</div><div class="mc-sub" style="color:var(--green)" id="s-ok-sub"></div><div class="mc-ico" style="background:rgba(99,102,241,.1)">🔍</div></div>
        <div class="mc"><div class="mc-lbl">Issues Found</div><div class="mc-val" id="s-issues" style="color:var(--red)">0</div><div class="mc-sub" style="color:var(--red)" id="s-high-sub"></div><div class="mc-ico" style="background:rgba(239,68,68,.1)">⚠</div></div>
        <div class="mc"><div class="mc-lbl">Clean Pages</div><div class="mc-val" id="s-ok" style="color:var(--green)">0</div><div class="mc-sub" style="color:var(--green)">No issues detected</div><div class="mc-ico" style="background:rgba(16,185,129,.1)">✓</div></div>
        <div class="mc"><div class="mc-lbl">High Priority</div><div class="mc-val" id="s-high" style="color:var(--red)">0</div><div class="mc-sub" style="color:var(--muted)" id="s-med-sub"></div><div class="mc-ico" style="background:rgba(239,68,68,.1)">🔴</div></div>
      </div>
      <div id="cbar" class="sbar c-bar"><div class="spin g" id="cspin"></div><span id="ctxt">Evaluating…</span></div>
      <div id="gbar" class="sbar g-bar"><div class="spin p" id="gspin"></div><span id="gtxt">AI running…</span></div>
      <div id="obar" class="sbar" style="display:none;border-left:3px solid var(--yellow)"><div class="spin" style="border-top-color:var(--yellow);display:block" id="ospin"></div><span id="otxt">⚡ Optimizer running…</span></div>
      <div id="tseo-bar" class="sbar tseo-bar-accent" style="display:none"><div class="spin" style="border-top-color:var(--green);display:none" id="tseo-spin"></div><span id="tseo-txt">🔬 Tech SEO ready</span></div>
      <div class="progress-wrap" id="progress-wrap">
        <div class="progress-info"><span id="prog-text">0 / 0 pages evaluated</span><span id="prog-elapsed">0s</span></div>
        <div class="progress-track"><div class="progress-fill" id="prog-fill" style="width:0%"></div></div>
      </div>
      <div class="dash-actions">
        <button class="btn btn-cyan" id="gemini-btn" onclick="startGeminiAll()" disabled>✨ AI (All)</button>
        <button class="btn btn-cyan" id="sel-ai-btn" onclick="startGeminiSelected()" disabled style="display:none">✨ AI (Selected)</button>
        <button class="btn btn-orange" id="opt-btn" onclick="startOptimizer()" disabled>⚡ Optimize</button>
        <button class="btn btn-purple" id="popup-btn" onclick="openPopup()" disabled>◈ Popup</button>
        <button class="btn btn-outline" id="export-btn" onclick="openExportModal()" disabled>↓ Export</button>
        <button class="btn" style="border:1px solid var(--green);color:var(--green);background:transparent" id="tseo-btn" onclick="runTechSEO()" disabled>🔬 Tech SEO</button>
        <button class="btn" style="border:1px solid var(--cyan);color:var(--cyan);background:transparent" id="serp-btn" onclick="openSerpPanel()" disabled>📊 SERP Intel</button>
        <button class="btn" style="border:1px solid var(--indigo);color:var(--indigo);background:transparent" id="monitor-btn" onclick="openMonitorPanel()">🔔 Monitor</button>
        <button class="btn" style="border:1px solid var(--yellow);color:var(--yellow);background:transparent" id="pdf-btn" onclick="exportPDF()" disabled>⬇ PDF</button>
        <button class="btn" style="border:1px solid var(--green);color:var(--green);background:transparent" id="save-project-btn" onclick="saveToProject()" disabled>💾 Save to Project</button>
        <button class="btn btn-outline" onclick="openSitemapCrawl()" style="font-size:11px" title="Evaluate from sitemap.xml">🗺 Sitemap</button>
        <button class="btn btn-outline" onclick="openKwGap()" style="font-size:11px">🔍 Keyword Gap</button>
      </div>
      <div class="summary" id="summary">
        <div class="card" style="border-color:rgba(239,68,68,.3)"><span class="val" id="s-high-bar" style="color:var(--red)">0</span><span class="lbl">High</span></div>
        <div class="card warn"><span class="val" id="s-med">0</span><span class="lbl">Medium</span></div>
        <div class="card"><span class="val" id="s-low">0</span><span class="lbl">Low</span></div>
      </div>
      <div class="ibreak" id="ibreak"><h3>Issue Breakdown</h3><div class="igrid" id="igrid"></div></div>
      <div class="sel-toolbar" id="sel-toolbar">
        <span><span class="sel-count" id="sel-count">0</span> pages selected</span>
        <button class="btn btn-cyan btn-sm" onclick="startGeminiSelected()">✨ Analyze Selected with AI</button>
        <button class="btn btn-sm btn-outline" onclick="clearSelection()">✕ Clear</button>
      </div>
      <div class="opt-panel" id="opt-panel">
        <div class="opt-panel-head">
          <h3>⚡ Live Optimization Table</h3>
          <span id="opt-row-count" style="font-size:10px;color:var(--muted)"></span>
          <button class="btn btn-outline btn-sm" onclick="downloadOptimizer()" style="margin-left:auto">↓ Export (.xlsx)</button>
        </div>
        <div class="opt-filters">
          <select id="opt-field-filter" onchange="renderOptimizerTable()"><option value="">All Fields</option><option value="Title">Title</option><option value="Meta Description">Meta Description</option><option value="H1">H1</option><option value="H2">H2</option><option value="Canonical">Canonical</option><option value="URL Slug">URL Slug</option></select>
          <select id="opt-status-filter" onchange="renderOptimizerTable()"><option value="">All Statuses</option><option value="Missing">Missing</option><option value="Too Long">Too Long</option><option value="Duplicate">Duplicate</option><option value="Multiple">Multiple</option><option value="Mismatch">Mismatch</option><option value="Weak">Weak</option></select>
          <input id="opt-search" type="text" placeholder="Search URL or value…" oninput="renderOptimizerTable()"/>
          <span class="opt-count" id="opt-count-label">0 rows</span>
        </div>
        <div class="opt-wrap"><table class="otable"><thead><tr><th>URL</th><th>Field</th><th>Status</th><th>Current Value</th><th style="color:var(--cyan)">✦ Optimized Value</th><th>SEO Logic</th></tr></thead><tbody id="opt-tbody"><tr><td colspan="6"><div class="opt-empty">Run ⚡ Optimize after evaluation to generate the Live Optimization Table.</div></td></tr></tbody></table></div>
      </div>
      <div class="tseo-panel" id="tseo-panel">
        <div class="tseo-panel-head"><h3>🔬 Technical SEO Assessment</h3><span id="tseo-row-count" style="font-size:10px;color:var(--muted)"></span><button class="btn btn-outline btn-sm" onclick="downloadTechSEO()" style="margin-left:auto;border-color:var(--green);color:var(--green)">↓ Export (.xlsx)</button></div>
        <div class="tseo-summary-grid" id="tseo-summary-grid">
          <div class="tseo-mc"><div class="tv" id="ts-score" style="color:var(--cyan)">—</div><div class="tl">Avg Tech Score</div><div class="ts" id="ts-grade" style="color:var(--cyan)">—</div></div>
          <div class="tseo-mc"><div class="tv" id="ts-total" style="color:white">—</div><div class="tl">Pages Audited</div><div class="ts" style="color:var(--dim)">all evaluated</div></div>
          <div class="tseo-mc"><div class="tv" id="ts-indexable" style="color:var(--green)">—</div><div class="tl">Indexable Pages</div><div class="ts" id="ts-idx-pct" style="color:var(--green)">—</div></div>
          <div class="tseo-mc"><div class="tv" id="ts-issues" style="color:var(--red)">—</div><div class="tl">Critical Issues</div><div class="ts" style="color:var(--dim)">across all pages</div></div>
          <div class="tseo-mc"><div class="tv" id="ts-https" style="color:var(--green)">—</div><div class="tl">HTTPS Coverage</div><div class="ts" id="ts-https-status" style="color:var(--dim)">—</div></div>
        </div>
        <div class="tseo-domain-grid" id="tseo-domain-grid">
          <div class="tseo-domain-card"><div class="tseo-domain-ico">🤖</div><div class="tseo-domain-info"><div class="tseo-domain-title">robots.txt</div><div class="tseo-domain-val" id="td-robots">Checking…</div></div></div>
          <div class="tseo-domain-card"><div class="tseo-domain-ico">🗺</div><div class="tseo-domain-info"><div class="tseo-domain-title">sitemap.xml</div><div class="tseo-domain-val" id="td-sitemap">Checking…</div></div></div>
          <div class="tseo-domain-card"><div class="tseo-domain-ico">📊</div><div class="tseo-domain-info"><div class="tseo-domain-title">HTTP Status Distribution</div><div class="tseo-domain-val" id="td-status-dist">—</div></div></div>
        </div>
        <div class="tseo-filters">
          <select id="tseo-idx-filter" onchange="renderTechSEOTable()"><option value="">All Indexability</option><option value="indexable">Indexable</option><option value="likely_indexable">Likely Indexable</option><option value="canonical_mismatch">Canonical Mismatch</option><option value="not_indexable_redirect">Redirect</option><option value="not_indexable_error">Error</option></select>
          <select id="tseo-grade-filter" onchange="renderTechSEOTable()"><option value="">All Grades</option><option value="A">A (85-100)</option><option value="B">B (70-84)</option><option value="C">C (55-69)</option><option value="D">D (40-54)</option><option value="F">F (0-39)</option></select>
          <input id="tseo-search" type="text" placeholder="Search URL…" oninput="renderTechSEOTable()"/>
          <span class="tseo-count" id="tseo-count-label">0 pages</span>
        </div>
        <div class="tseo-wrap"><table class="tseo-table"><thead><tr><th>URL</th><th>HTTP</th><th>Indexability</th><th>Tech Score</th><th>Grade</th><th>Title</th><th>Meta</th><th>Canonical</th><th>H1</th><th>Content</th><th>HTTPS</th><th>Issues</th><th>Detail</th></tr></thead><tbody id="tseo-tbody"><tr><td colspan="13"><div class="opt-empty">Run 🔬 Tech SEO after evaluation to assess all pages.</div></td></tr></tbody></table></div>
      </div>
      <div class="toolbar">
        <div class="filters">
          <label><input type="checkbox" id="f-issues" onchange="applyFilters()"/> Issues only</label>
          <select id="f-priority" onchange="applyFilters()"><option value="">All Priorities</option><option value="High">High</option><option value="Medium">Medium</option><option value="Low">Low</option></select>
          <label><input type="checkbox" id="f-selected" onchange="applyFilters()"/> Selected only</label>
        </div>
        <div class="count">Showing <span id="page-count">0</span> pages</div>
      </div>
      <div class="twrap">
        <table>
          <thead><tr>
            <th class="check-cell"><input type="checkbox" id="select-all" onchange="toggleSelectAll(this)"/></th>
            <th onclick="sortBy('url')">URL <span class="sa">↕</span></th>
            <th onclick="sortBy('status_code')">Status <span class="sa">↕</span></th>
            <th onclick="sortBy('title')">Title <span class="sa">↕</span></th>
            <th onclick="sortBy('meta_description')">Meta <span class="sa">↕</span></th>
            <th onclick="sortBy('h1')">H1 <span class="sa">↕</span></th>
            <th onclick="sortBy('issues')">Issues <span class="sa">↕</span></th>
            <th onclick="sortBy('priority')">Priority <span class="sa">↕</span></th>
            <th>Score</th><th>Keywords</th><th>Competition</th><th>AI Fix</th><th>Status</th><th>Action</th>
          </tr></thead>
          <tbody id="results-body"><tr><td colspan="14"><div class="empty-state"><div class="icon">🔍</div><p>Enter a URL above to begin your technical SEO evaluation.</p></div></td></tr></tbody>
        </table>
        <div id="table-pagination"></div>
      </div>
    </div>
  </div>
</div>

<!-- ══ TECH SEO DETAIL MODAL ═════════════════════════════════════════════════ -->
<div class="tseo-overlay" id="tseo-overlay" onclick="tseoOverlayClick(event)">
<div class="tseo-modal" id="tseo-modal">
  <div class="tseo-modal-head">
    <div style="flex:1;min-width:0"><h2>Technical SEO Detail</h2><span class="tseo-modal-url" id="td-url"></span></div>
    <button class="popup-close" onclick="closeTechSEODetail()">✕</button>
  </div>
  <div class="tseo-modal-score">
    <div class="tseo-score-circle" id="td-circle"><span class="sc-val" id="td-sc-val">—</span><span class="sc-grade" id="td-sc-grade">—</span></div>
    <div class="tseo-score-meta"><div class="sm-title" id="td-sm-title">Technical Score</div><div class="sm-idx" id="td-sm-idx"></div><div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px" id="td-all-issues"></div></div>
    <div style="text-align:right;flex-shrink:0"><div style="font-size:10px;color:var(--muted);font-family:var(--mono);margin-bottom:4px">HTTP STATUS</div><div id="td-http-badge"></div></div>
  </div>
  <div class="tseo-component-grid" id="td-component-grid"></div>
  <div class="tseo-modal-foot">
    <div class="tseo-modal-nav"><button class="nav-btn" id="td-prev" onclick="navTechPage(-1)">←</button><span class="nav-counter"><strong id="td-cur">1</strong> / <strong id="td-tot">1</strong></span><button class="nav-btn" id="td-next" onclick="navTechPage(1)">→</button></div>
    <div class="spacer"></div>
    <button class="btn btn-sm" style="border:1px solid var(--green);color:var(--green);background:transparent" onclick="downloadTechSEO()">↓ Export Tech SEO</button>
  </div>
</div>
</div>

<!-- ══ POPUP ═════════════════════════════════════════════════════════════════ -->
<div class="overlay" id="popup-overlay" onclick="overlayClick(event)" style="display:none">
<div class="popup" id="popup">
  <div class="popup-head">
    <div class="popup-head-left">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px"><h2>SEO Analysis</h2><span id="pp-priority" class="pb"></span></div>
      <div class="purl" id="pp-url"></div>
    </div>
    <div class="popup-head-right">
      <span id="pp-ai-status" class="ai-status-row" style="display:none"><div class="spin p" style="width:10px;height:10px;border-width:1.5px"></div> AI running…</span>
      <button class="btn btn-cyan btn-sm" onclick="analyzeThisPage()">✨ AI This Page</button>
      <button class="popup-close" onclick="closePopup()">✕</button>
    </div>
  </div>
  <div class="rank-card" id="rank-card">
    <div class="rank-circle" id="rank-circle"><span class="rs" id="rank-score">—</span><span class="rg" id="rank-grade">—</span></div>
    <div class="rank-details"><div class="rank-feedback" id="rank-feedback"></div><div class="rank-bars" id="rank-bars"></div></div>
  </div>
  <div class="popup-kw" id="pp-kw"><span class="lbl">Keywords:</span><div id="pp-kw-tags"></div></div>
  <div class="popup-meta"><span style="font-size:10px;color:var(--muted)">Competition:</span><span id="pp-competition"></span><span style="font-size:10px;color:var(--muted);margin-left:10px">Issues:</span><div id="pp-issues" style="display:flex;flex-wrap:wrap;gap:4px"></div></div>
  <div class="popup-body">
    <table class="ptable">
      <thead><tr><th>Field</th><th>Current Value</th><th>Issue</th><th>Why It Matters</th><th>Exact Fix</th><th style="color:var(--cyan)">✦ Optimized Value</th><th>Real Example</th><th>Impact</th></tr></thead>
      <tbody id="pp-tbody"></tbody>
    </table>
  </div>
  <div class="popup-foot">
    <button class="nav-btn" id="nav-prev" onclick="navPage(-1)">←</button>
    <span class="nav-counter"><strong id="nav-cur">1</strong> / <strong id="nav-tot">1</strong></span>
    <button class="nav-btn" id="nav-next" onclick="navPage(1)">→</button>
    <div class="spacer"></div>
    <button class="btn btn-outline btn-sm" onclick="openExportFromPopup()">↓ Export</button>
  </div>
</div>
</div>

<!-- ══ AI SETUP MODAL ════════════════════════════════════════════════════════ -->
<div class="ai-overlay" id="ai-overlay" onclick="aiOverlayClick(event)">
<div class="ai-modal" id="ai-modal">
  <div class="ai-modal-head"><h2>⚙ AI Setup</h2><button class="ai-modal-close" onclick="closeAiSetup()">✕</button></div>
  <div class="ai-modal-body">
    <div class="ai-current-status"><div class="ai-current-dot" id="ai-dot"></div><div><div class="ai-current-provider" id="ai-cur-provider">Loading…</div><div class="ai-current-hint" id="ai-cur-hint"></div></div></div>
    <div class="ai-providers-label">Select AI Provider</div>
    <div class="ai-provider-grid" id="ai-provider-grid">
      <div class="ai-provider-card" data-provider="groq" onclick="aiSelectProvider('groq')"><div class="ai-pcard-icon">⚡</div><div class="ai-pcard-name">Groq</div><div class="ai-pcard-desc">Llama 3 · Free · Fast</div></div>
      <div class="ai-provider-card" data-provider="gemini" onclick="aiSelectProvider('gemini')"><div class="ai-pcard-icon">✨</div><div class="ai-pcard-name">Gemini</div><div class="ai-pcard-desc">Google AI · Free quota</div></div>
      <div class="ai-provider-card" data-provider="openai" onclick="aiSelectProvider('openai')"><div class="ai-pcard-icon">🤖</div><div class="ai-pcard-name">OpenAI</div><div class="ai-pcard-desc">GPT-4o-mini · Paid</div></div>
      <div class="ai-provider-card" data-provider="claude" onclick="aiSelectProvider('claude')"><div class="ai-pcard-icon">🧠</div><div class="ai-pcard-name">Claude</div><div class="ai-pcard-desc">Anthropic · Haiku</div></div>
      <div class="ai-provider-card" data-provider="ollama" onclick="aiSelectProvider('ollama')"><div class="ai-pcard-icon">🏠</div><div class="ai-pcard-name">Ollama</div><div class="ai-pcard-desc">Local · No key needed</div></div>
      <div class="ai-provider-card" data-provider="rules" onclick="aiSelectProvider('rules')"><div class="ai-pcard-icon">📋</div><div class="ai-pcard-name">Rules Only</div><div class="ai-pcard-desc">No AI · Always works</div></div>
    </div>
    <div class="ai-no-key-notice" id="ai-no-key-notice">This provider does not require an API key. Click <strong>Apply</strong> to activate it.</div>
    <div class="ai-key-group" id="ai-key-group">
      <label class="ai-key-label" for="ai-key-input">API Key</label>
      <div class="ai-key-row">
        <input class="ai-key-input" id="ai-key-input" type="password" placeholder="Paste your API key here…" oninput="aiOnKeyInput(this.value)" autocomplete="off" spellcheck="false"/>
        <button class="ai-key-toggle" onclick="aiToggleKeyVisible()" id="ai-key-toggle" title="Show / hide key">👁</button>
      </div>
      <div class="ai-detect-badge" id="ai-detect-badge"><span>🔍</span><span id="ai-detect-text"></span></div>
    </div>
    <div class="ai-feedback" id="ai-feedback"></div>
    <div class="ai-modal-foot">
      <button class="btn btn-outline btn-sm" style="font-size:11px;padding:6px 14px" onclick="closeAiSetup()">Cancel</button>
      <button class="btn btn-cyan btn-sm" style="font-size:11px;padding:6px 14px" onclick="aiTestConnection()" id="ai-test-btn">Test Connection</button>
      <button class="btn btn-green btn-sm" style="font-size:11px;padding:6px 14px" onclick="aiApplyKey()" id="ai-apply-btn">Apply</button>
    </div>
  </div>
</div>
</div>

<!-- ══ EXPORT MODAL ═══════════════════════════════════════════════════════════ -->
<div class="eoverlay" id="eoverlay" style="display:none">
<div class="emodal">
  <button class="eclose" onclick="closeExportModal()">✕</button>
  <h2>Export Report</h2>
  <p>Download the full SEO assessment as Excel including ranking scores, issues, and AI fixes.</p>
  <div class="estat"><div><span class="ev" id="em-pages">0</span><span class="el">Pages</span></div><div><span class="ev" id="em-issues">0</span><span class="el">Issues</span></div></div>
  <div style="display:flex;flex-direction:column;gap:8px">
    <button class="btn btn-green" style="width:100%" onclick="downloadExcel('full')">↓ Full Report (.xlsx)</button>
    <button class="btn btn-outline" style="width:100%" onclick="downloadExcel('popup')">↓ Per-Field Issues (.xlsx)</button>
    <button class="btn btn-orange" style="width:100%" onclick="downloadOptimizer()">⚡ Live Optimization Table (.xlsx)</button>
  </div>
</div>
</div>

<!-- ══ AI DRAWER ══════════════════════════════════════════════════════════════ -->
<div id="ai-drawer-overlay" onclick="closeAiDrawer()"></div>
<div id="ai-drawer" role="dialog" aria-label="Neural Fix Engine">
  <div class="aid-head">
    <div><div class="aid-title"><span class="material-symbols-outlined" style="font-size:18px;color:#4f46e5;font-variation-settings:'FILL' 1">auto_fix_high</span>Neural Fix Engine</div><div class="aid-url" id="aid-url">Analyzing…</div></div>
    <button class="aid-close" onclick="closeAiDrawer()" aria-label="Close">✕</button>
  </div>
  <div class="aid-body">
    <div class="aid-section-label">Select AI Intelligence</div>
    <div class="aid-pills" id="aid-pills">
      <button class="aid-pill active" onclick="aidSelectProvider(this,'groq')">⚡ Groq</button>
      <button class="aid-pill" onclick="aidSelectProvider(this,'gemini')">✨ Gemini</button>
      <button class="aid-pill" onclick="aidSelectProvider(this,'openai')">🤖 OpenAI</button>
      <button class="aid-pill" onclick="aidSelectProvider(this,'claude')">🧠 Claude</button>
      <button class="aid-pill" onclick="aidSelectProvider(this,'ollama')">🏠 Ollama</button>
      <button class="aid-pill" onclick="aidSelectProvider(this,'rules')">📋 Rules Only</button>
    </div>
    <div class="aid-key-wrap" id="aid-key-wrap" style="display:none">
      <span class="aid-key-icon material-symbols-outlined">lock</span>
      <input type="password" id="aid-api-key" placeholder="Enter API Key…" autocomplete="off"/>
    </div>
    <div class="aid-section-label" style="margin-top:20px">Optimization Strategy</div>
    <div id="aid-cards-wrap"><div style="text-align:center;padding:30px;color:#94a3b8;font-size:12px">Click "AI Fix" on any row to see optimization strategy here.</div></div>
  </div>
  <div class="aid-foot">
    <button class="aid-btn-test" id="aid-test-btn" onclick="aidTestConnection()"><span class="material-symbols-outlined" style="font-size:16px;color:#4f46e5">bolt</span>Test Connection</button>
    <button class="aid-btn-apply" onclick="aidApplyFixes()"><span class="material-symbols-outlined" style="font-size:16px">check_circle</span>Apply Fixes</button>
  </div>
</div>

<!-- ══ BUILDER BADGE ═════════════════════════════════════════════════════════ -->
<a id="builder-badge" href="https://www.linkedin.com/in/teki-bhavani-shankar-seo-professional/" target="_blank" rel="noopener" title="Built by Teki Bhavani Shankar — Technical SEO Specialist">
  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 0 1-2.063-2.065 2.064 2.064 0 1 1 2.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
  Built by Teki Bhavani Shankar
</a>
`;
}

// ── Token helpers ─────────────────────────────────────────────────────────────
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
  if (!ct.includes('application/json')) throw new Error('Backend is starting up — please wait');
  return res.json();
}

// ── Auth modal ────────────────────────────────────────────────────────────────
function openAuthModal() {
  document.getElementById('auth-modal').style.display = 'flex';
  const err = document.getElementById('auth-error');
  if (err) err.style.display = 'none';
}
function closeAuthModal() { document.getElementById('auth-modal').style.display = 'none'; }
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
    const res = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password: pass }) });
    const d = await safeJson(res);
    if (!res.ok) throw new Error(d.detail || 'Login failed');
    _ciqToken = d.token; _ciqUser = d.user;
    localStorage.setItem('ciq_token', _ciqToken);
    closeAuthModal(); applyAuthState();
  } catch(e) { errEl.textContent = e.message; errEl.style.display = ''; }
}
async function doRegister() {
  const name  = document.getElementById('reg-name').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const pass  = document.getElementById('reg-pass').value;
  const errEl = document.getElementById('auth-error');
  errEl.style.display = 'none';
  try {
    const res = await fetch(`${API}/auth/register`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, email, password: pass }) });
    const d = await safeJson(res);
    if (!res.ok) throw new Error(d.detail || 'Registration failed');
    _ciqToken = d.token; _ciqUser = d.user;
    localStorage.setItem('ciq_token', _ciqToken);
    closeAuthModal(); applyAuthState();
  } catch(e) { errEl.textContent = e.message; errEl.style.display = ''; }
}
function doLogout() {
  _ciqToken = ''; _ciqUser = null; window._ciqProject = null;
  localStorage.removeItem('ciq_token');
  localStorage.removeItem('ciq_brand_name');
  applyAuthState();
}
async function loadCurrentUser() {
  if (!_ciqToken) return;
  try {
    const res = await safeAuthFetch(`${API}/auth/me`);
    if (res.ok) { _ciqUser = await safeJson(res); applyAuthState(); }
    else { _ciqToken = ''; localStorage.removeItem('ciq_token'); }
  } catch(e) { _ciqToken = ''; }
}
function applyAuthState() {
  const loggedIn = !!_ciqUser;
  const guestEl = document.getElementById('nav-auth-guest');
  const userEl  = document.getElementById('nav-auth-user');
  if (guestEl) guestEl.style.display = loggedIn ? 'none' : 'flex';
  if (userEl)  userEl.style.display  = loggedIn ? 'flex' : 'none';
  if (loggedIn) {
    const name = _ciqUser.name || _ciqUser.email || 'User';
    const initials = name.substring(0, 2).toUpperCase();
    const tier = _ciqUser.tier || 'free';
    const avEl = document.getElementById('user-avatar-initials');
    const nmEl = document.getElementById('user-display-name');
    if (avEl) avEl.textContent = initials;
    if (nmEl) nmEl.textContent = name.split(' ')[0];
  }
}
function toggleUserMenu() {
  document.getElementById('user-menu')?.classList.toggle('open');
}

// ── Welcome screen helper ─────────────────────────────────────────────────────
function startFromWelcome() {
  const input = document.getElementById('welcome-url-input');
  const val = (input?.value || '').trim();
  if (!val) { input?.focus(); return; }
  const url = val.startsWith('http') ? val : 'https://' + val;
  document.getElementById('url-input').value = url;
  const appInput = document.getElementById('url-input-app');
  if (appInput) appInput.value = url;
  activateCrawlMode();
  startCrawl();
}

// ── App mode toggle ───────────────────────────────────────────────────────────
function activateCrawlMode() {
  const welcome = document.getElementById('app-welcome');
  const topbar  = document.getElementById('app-topbar');
  const dash    = document.getElementById('dash-sec');
  if (welcome) welcome.style.display = 'none';
  if (topbar)  topbar.style.display  = '';
  if (dash)    dash.style.display    = '';
}
function exitAppMode() {
  window.location.href = '../';
}

// ── Stub functions (prevent undefined errors before modules load) ─────────────
['openAiSetup', 'closeAiSetup', 'showPanel', 'openKwGap', 'openSettings', 'closeSettings',
 'openProjects', 'closeProjects', 'openSitmapCrawl', 'startCrawl', 'startGeminiAll',
 'startGeminiSelected', 'startOptimizer', 'openPopup', 'openExportModal', 'runTechSEO',
 'openSerpPanel', 'openMonitorPanel', 'exportPDF', 'saveToProject', 'openSitemapCrawl',
 'openExportFromPopup', 'closePopup', 'navPage', 'navTechPage', 'closeTechSEODetail',
 'overlayClick', 'tseoOverlayClick', 'renderTechSEOTable', 'renderOptimizerTable',
 'applyFilters', 'sortBy', 'toggleSelectAll', 'clearSelection', 'downloadOptimizer',
 'downloadTechSEO', 'downloadExcel', 'closeExportModal', 'analyzeThisPage',
 'aiSelectProvider', 'aiToggleKeyVisible', 'aiOnKeyInput', 'aiTestConnection', 'aiApplyKey',
 'aiOverlayClick', 'aidSelectProvider', 'aidTestConnection', 'aidApplyFixes', 'closeAiDrawer',
 'runKeywordGap', 'saveSettings', 'uploadLogo', 'toggleApiKeyVisibility', 'rotateApiKey',
 'connectGSC', 'disconnectGSC', 'loadGscData', 'inviteTeamMember', 'createProject',
 'startSitemapCrawl', 'checkGemini'
].forEach(fn => { if (!window[fn]) window[fn] = function() {}; });

// ── Main initialiser ──────────────────────────────────────────────────────────
async function initApp() {
  // 1. Inject full app HTML into #app-root
  document.getElementById('app-root').innerHTML = getAppHTML();

  // 2. Load crawl + ui modules (needed before any user interaction)
  try {
    await Promise.all([
      loadScript('../static/js/crawl.js?v=2.0.0'),
      loadScript('../static/js/ui.js?v=1.0.3'),
    ]);
  } catch(e) { console.warn('Module load error:', e); }

  // 3. Lazy-load heavy modules after core is ready
  loadScript('../static/js/dashboard.js?v=1.0.3').catch(() => {});
  setTimeout(() => loadScript('../static/js/noncritical.js?v=1.0.4').catch(() => {}), 2000);

  // 4. Restore auth
  loadCurrentUser();

  // 5. Keyboard shortcuts
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      ['auth-modal', 'settings-modal', 'projects-modal', 'kwgap-modal',
       'score-history-modal', 'sitemap-modal'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
      });
    }
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.user-chip')) document.getElementById('user-menu')?.classList.remove('open');
  });

  // 6. URL param routing
  const params   = new URLSearchParams(window.location.search);
  const start    = params.get('start');
  const urlParam = params.get('url');

  if (start === 'true' || urlParam) {
    // Show full crawl interface immediately
    activateCrawlMode();
    if (urlParam) {
      const decoded = decodeURIComponent(urlParam);
      const normalised = decoded.startsWith('http') ? decoded : 'https://' + decoded;
      const hiddenInput = document.getElementById('url-input');
      const topbarInput = document.getElementById('url-input-app');
      if (hiddenInput) hiddenInput.value = normalised;
      if (topbarInput) topbarInput.value = normalised;
    } else {
      setTimeout(() => document.getElementById('url-input-app')?.focus(), 100);
    }
  } else {
    // Show welcome / entry screen — Section 9 compliance
    const welcome = document.getElementById('app-welcome');
    if (welcome) {
      welcome.style.display = 'flex';
      setTimeout(() => document.getElementById('welcome-url-input')?.focus(), 100);
    }
  }

  // 7. Deferred backend health check (never blocks FCP)
  setTimeout(checkGemini, 3000);
}

document.addEventListener('DOMContentLoaded', initApp);
