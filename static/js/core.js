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
let _ppAllRows = [];
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
    <a class="nav-logo" href="https://bhavani5a8.github.io/crawliq.io/" style="text-decoration:none;flex-shrink:0">
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
    <button class="apt" id="sn-sitemap" onclick="showPanel('sitemap-crawl')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">account_tree</span>Sitemap
    </button>
    <button class="apt" id="sn-tech-seo" onclick="showPanel('tech-seo')">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">biotech</span>Tech SEO
    </button>
    <button class="apt" id="sn-kwgap" onclick="showPanel('kwgap')" style="margin-left:auto">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">search</span>Keyword Gap
    </button>
    <button class="apt" onclick="openHistory()">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">history</span>History
    </button>
    <button class="apt" onclick="openScoreHistory()">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">show_chart</span>Score History
    </button>
    <button class="apt" onclick="openSettings()">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">settings</span>Settings
    </button>
    <a class="apt" href="https://bhavani5a8.github.io/crawliq.io/" style="text-decoration:none;color:inherit">
      <span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:4px">home</span>Home
    </a>
  </div>
</div>

<!-- ══ CRAWL TARGET BAR ════════════════════════════════════════════════════ -->
<div id="crawl-target-bar" style="display:none;background:var(--surf-low);border-bottom:1px solid rgba(70,69,84,.2);padding:5px 20px;align-items:center;gap:10px;font-size:12px;color:var(--dim);">
  <span class="material-symbols-outlined" style="font-size:13px;color:var(--muted)">language</span>
  <span id="crawl-target-url" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text);font-family:monospace;font-size:12px"></span>
  <button onclick="editCrawlTarget()" style="background:none;border:1px solid rgba(100,100,120,.3);color:var(--dim);font-size:11px;padding:3px 10px;border-radius:6px;cursor:pointer;font-family:inherit;">✏ Edit URL</button>
  <button onclick="rerunCrawl()" style="background:none;border:1px solid rgba(100,100,120,.3);color:var(--dim);font-size:11px;padding:3px 10px;border-radius:6px;cursor:pointer;font-family:inherit;">↺ Re-run</button>
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
  <div style="margin-top:16px;font-size:11px;color:var(--muted);">No signup required · Up to 50 pages free · <a href="https://bhavani5a8.github.io/crawliq.io/" style="color:var(--dim);text-decoration:underline;">Home →</a></div>
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
      <!-- ── Post-crawl charts ── -->
      <div id="dash-charts-row" style="display:none;gap:16px;flex-wrap:wrap;margin:0 0 16px">
        <div style="flex:1;min-width:260px;background:var(--surf);border:1px solid var(--border);border-radius:var(--r-lg);padding:16px">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:10px">Issues by Type</div>
          <div id="dash-issues-chart" style="height:180px"></div>
        </div>
        <div style="flex:1;min-width:260px;background:var(--surf);border:1px solid var(--border);border-radius:var(--r-lg);padding:16px">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:10px">Priority Distribution</div>
          <div id="dash-priority-chart" style="height:180px"></div>
        </div>
      </div>
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
<div id="popup-overlay" style="display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.7);backdrop-filter:blur(4px);flex-direction:column">
<div id="popup" style="display:flex;flex-direction:column;width:100%;height:100%;background:var(--bg,#0f1117);overflow:hidden">

  <!-- Header bar -->
  <div style="display:flex;align-items:center;gap:12px;padding:12px 20px;background:var(--surf,#1a1d2e);border-bottom:1px solid var(--border,#2d3048);flex-shrink:0">
    <span class="material-symbols-outlined" style="color:var(--cyan);font-size:20px">manage_search</span>
    <div style="flex:1">
      <div style="font-size:13px;font-weight:700;color:var(--white,#e5e7eb)">On-Page SEO Clusters</div>
      <div id="pp-site-url" style="font-size:10px;color:var(--muted);font-family:monospace;margin-top:1px"></div>
    </div>
    <div id="pp-crawl-stats" style="display:flex;gap:16px;font-size:11px;color:var(--muted)"></div>
    <button class="btn btn-outline btn-sm" onclick="openExportFromPopup()" style="font-size:11px;padding:5px 12px">↓ Export</button>
    <button class="btn btn-orange btn-sm" onclick="exportPDF()" style="font-size:11px;padding:5px 12px">⬇ PDF</button>
    <button onclick="closePopup()" style="background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;padding:4px 8px;line-height:1">✕</button>
  </div>

  <!-- Cluster tabs -->
  <div id="pp-cluster-nav" style="display:flex;gap:4px;padding:10px 20px 0;background:var(--surf,#1a1d2e);border-bottom:1px solid var(--border,#2d3048);flex-shrink:0;overflow-x:auto;white-space:nowrap"></div>

  <!-- Stats strip -->
  <div id="pp-cluster-stats" style="display:flex;gap:0;flex-shrink:0;border-bottom:1px solid var(--border,#2d3048);background:var(--bg,#0f1117)"></div>

  <!-- Table area -->
  <div style="flex:1;overflow:auto;padding:0 20px 20px">
    <div style="display:flex;align-items:center;gap:10px;padding:12px 0 8px;flex-shrink:0">
      <input id="pp-search" type="text" placeholder="Filter pages…" oninput="ppFilterTable()" style="flex:1;max-width:320px;padding:6px 10px;font-size:11px;background:var(--surf);border:1px solid var(--border);border-radius:6px;color:var(--white);outline:none">
      <span id="pp-row-count" style="font-size:11px;color:var(--muted)"></span>
    </div>
    <table class="ptable" style="width:100%;min-width:1000px">
      <thead id="pp-thead"></thead>
      <tbody id="pp-tbody"></tbody>
    </table>
  </div>

  <!-- Page detail drawer (slides up when a row is clicked) -->
  <div id="pp-detail-drawer" style="display:none;position:absolute;inset:0;z-index:10;background:rgba(0,0,0,.75);align-items:flex-end">
    <div style="background:var(--surf);border-radius:12px 12px 0 0;width:100%;max-height:70vh;overflow:auto;padding:20px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <div>
          <div style="font-size:12px;font-weight:700;color:var(--white)" id="ppd-url"></div>
          <div style="font-size:10px;color:var(--muted);margin-top:2px" id="ppd-meta"></div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <span id="ppd-ai-status" style="display:none;font-size:10px;color:var(--muted)"><div class="spin p" style="width:8px;height:8px;border-width:1.5px;display:inline-block;vertical-align:middle;margin-right:4px"></div>AI running…</span>
          <button class="btn btn-cyan btn-sm" style="font-size:10px;padding:4px 10px" onclick="ppAnalyzeDrawerPage()">✨ AI Fix</button>
          <button onclick="closePpDrawer()" style="background:none;border:none;color:var(--muted);font-size:16px;cursor:pointer">✕</button>
        </div>
      </div>
      <table class="ptable" style="width:100%">
        <thead><tr><th>Field</th><th>Current Value</th><th>Status</th><th>Why It Matters</th><th>Fix</th><th style="color:var(--cyan)">✦ Optimized</th><th>Impact</th></tr></thead>
        <tbody id="ppd-tbody"></tbody>
      </table>
    </div>
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
    <button class="btn btn-cyan" style="width:100%" onclick="downloadExcel('full-report')">↓ Comprehensive Report — All Sheets (.xlsx)</button>
    <button class="btn btn-green" style="width:100%" onclick="downloadExcel('full')">↓ On-Page Overview (.xlsx)</button>
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

<!-- ══ SERP INTELLIGENCE PANEL ════════════════════════════════════════════════ -->
<section id="serp-intel-sec" class="panel-hidden" style="padding:0 32px 40px">
<div class="sec-inner">
<div class="dash-card" id="serp-panel-card">
  <div class="dash-chrome">
    <div class="wb wb-r"></div><div class="wb wb-y"></div><div class="wb wb-g"></div>
    <span class="tab">SERP Intelligence — Position &amp; Keyword Difficulty</span>
    <button onclick="showPanel('dashboard')" style="margin-left:auto;background:none;border:none;color:var(--dim);cursor:pointer;font-size:14px">✕</button>
  </div>
  <div style="display:flex;gap:0;border-bottom:1px solid var(--border);background:var(--surf2)">
    <button id="serp-tab-pos"  class="serp-tab serp-tab-active" onclick="serpTab('pos')">📍 Position Check</button>
    <button id="serp-tab-diff" class="serp-tab" onclick="serpTab('diff')">🎯 Keyword Difficulty</button>
    <button id="serp-tab-vis"  class="serp-tab" onclick="serpTab('vis')">👁 Visibility Score</button>
  </div>
  <div id="serp-pane-pos" class="serp-pane" style="padding:16px 20px">
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
      <input id="serp-domain" class="serp-input" placeholder="Domain e.g. example.com" style="flex:1;min-width:180px"/>
      <textarea id="serp-keywords" class="serp-input" placeholder="Keywords (one per line, max 20)" rows="3" style="flex:2;min-width:220px;resize:vertical"></textarea>
      <button class="btn btn-green" onclick="runBulkSerp()" id="serp-run-btn">▶ Check Positions</button>
    </div>
    <div id="serp-pos-bar" class="sbar" style="display:none;margin:0 0 10px"><div class="spin g"></div><span id="serp-pos-txt">Checking positions…</span></div>
    <div id="serp-pos-results" style="display:none">
      <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap" id="serp-pos-summary"></div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:var(--mono)">
          <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
            <th style="padding:8px 10px;text-align:left">Keyword</th>
            <th style="padding:8px 10px;text-align:center">Position</th>
            <th style="padding:8px 10px;text-align:center">Top 10?</th>
            <th style="padding:8px 10px;text-align:center">Top 30?</th>
          </tr></thead>
          <tbody id="serp-pos-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
  <div id="serp-pane-diff" class="serp-pane" style="display:none;padding:16px 20px">
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
      <textarea id="diff-keywords" class="serp-input" placeholder="Keywords (one per line, max 20)" rows="4" style="flex:2;min-width:220px;resize:vertical"></textarea>
      <button class="btn btn-green" onclick="runDifficulty()" id="diff-run-btn" style="align-self:flex-start">▶ Check Difficulty</button>
    </div>
    <div id="diff-bar" class="sbar" style="display:none;margin:0 0 10px"><div class="spin g"></div><span>Checking difficulty…</span></div>
    <div id="diff-results" style="display:none;overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:var(--mono)">
        <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
          <th style="padding:8px 10px;text-align:left">Keyword</th>
          <th style="padding:8px 10px;text-align:center">Difficulty</th>
          <th style="padding:8px 10px;text-align:center">Label</th>
          <th style="padding:8px 10px;text-align:left">Top Domains</th>
        </tr></thead>
        <tbody id="diff-tbody"></tbody>
      </table>
    </div>
  </div>
  <div id="serp-pane-vis" class="serp-pane" style="display:none;padding:16px 20px">
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px">
      <button class="btn btn-green" onclick="loadVisibility()">↻ Refresh Visibility</button>
      <span style="font-size:11px;color:var(--muted)">Uses positions from the Position Check tab</span>
    </div>
    <div id="vis-summary" style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px"></div>
    <div id="vis-results" style="display:none;overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:var(--mono)">
        <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
          <th style="padding:8px 10px;text-align:left">Keyword</th>
          <th style="padding:8px 10px;text-align:center">Position</th>
          <th style="padding:8px 10px;text-align:left">Page</th>
          <th style="padding:8px 10px;text-align:center">Expected CTR</th>
        </tr></thead>
        <tbody id="vis-tbody"></tbody>
      </table>
    </div>
  </div>
</div>
</div>
</section>

<!-- ══ RANK MONITOR PANEL ══════════════════════════════════════════════════════ -->
<section id="monitor-sec" class="panel-hidden" style="padding:0 32px 40px">
<div class="sec-inner">
<div class="dash-card">
  <div class="dash-chrome">
    <div class="wb wb-r"></div><div class="wb wb-y"></div><div class="wb wb-g"></div>
    <span class="tab">🔔 SERP Monitor — Scheduled Position Tracking</span>
    <button onclick="showPanel('dashboard')" style="margin-left:auto;background:none;border:none;color:var(--dim);cursor:pointer;font-size:14px">✕</button>
  </div>
  <div style="display:flex;gap:0;border-bottom:1px solid var(--border);padding:0 20px">
    <button class="serp-tab serp-tab-active" id="mon-tab-schedule" onclick="monTab('schedule')">+ Schedule</button>
    <button class="serp-tab" id="mon-tab-jobs"    onclick="monTab('jobs')">Active Jobs</button>
    <button class="serp-tab" id="mon-tab-history" onclick="monTab('history')">📈 Position History</button>
  </div>
  <div id="mon-pane-schedule" class="serp-pane" style="padding:16px 20px">
    <div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px">
      <div style="font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--dim);margin-bottom:10px">Schedule New Tracking Job</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <input id="mon-domain" class="serp-input" placeholder="Domain e.g. example.com" style="flex:1;min-width:160px"/>
        <textarea id="mon-keywords" class="serp-input" placeholder="Keywords (one per line)" rows="3" style="flex:2;min-width:200px;resize:vertical"></textarea>
        <div style="display:flex;flex-direction:column;gap:8px;align-self:flex-start">
          <select id="mon-interval" class="serp-input">
            <option value="6">Every 6 hours</option>
            <option value="12">Every 12 hours</option>
            <option value="24" selected>Daily (24h)</option>
            <option value="48">Every 2 days</option>
            <option value="168">Weekly</option>
          </select>
          <button class="btn btn-green" onclick="scheduleMonitor()">+ Schedule Job</button>
        </div>
      </div>
      <div id="mon-sched-msg" style="font-size:10px;margin-top:8px;display:none"></div>
    </div>
  </div>
  <div id="mon-pane-jobs" class="serp-pane" style="display:none;padding:16px 20px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
      <span style="font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--dim)">Active Jobs</span>
      <button class="btn btn-outline btn-sm" onclick="loadMonitorJobs()" style="margin-left:4px">↻ Refresh</button>
    </div>
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:var(--mono)">
        <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
          <th style="padding:7px 10px;text-align:left">Domain</th>
          <th style="padding:7px 10px;text-align:left">Keywords</th>
          <th style="padding:7px 10px;text-align:center">Interval</th>
          <th style="padding:7px 10px;text-align:center">Runs</th>
          <th style="padding:7px 10px;text-align:center">Status</th>
          <th style="padding:7px 10px;text-align:left">Next Run</th>
          <th style="padding:7px 10px"></th>
        </tr></thead>
        <tbody id="mon-jobs-tbody">
          <tr><td colspan="7" style="text-align:center;color:var(--muted);padding:16px;font-size:11px">No monitoring jobs yet.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div id="mon-pane-history" class="serp-pane" style="display:none;padding:16px 20px">
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px">
      <div>
        <div style="font-size:9px;color:var(--dim);margin-bottom:4px">DOMAIN</div>
        <input id="hist-domain" class="serp-input" placeholder="e.g. example.com" style="width:200px"/>
      </div>
      <div>
        <div style="font-size:9px;color:var(--dim);margin-bottom:4px">KEYWORD (optional)</div>
        <input id="hist-keyword" class="serp-input" placeholder="e.g. seo audit tool" style="width:220px"/>
      </div>
      <button class="btn btn-green" onclick="loadHistory()">▶ Load History</button>
    </div>
    <div id="hist-bar" class="sbar" style="display:none;margin-bottom:10px"><div class="spin g"></div><span>Loading…</span></div>
    <div id="hist-results" style="display:none">
      <div id="hist-latest" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px"></div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:var(--mono)">
          <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
            <th style="padding:7px 10px;text-align:left">Keyword</th>
            <th style="padding:7px 10px;text-align:center">Position</th>
            <th style="padding:7px 10px;text-align:center">Top 10</th>
            <th style="padding:7px 10px;text-align:center">Top 30</th>
            <th style="padding:7px 10px;text-align:left">Checked At</th>
          </tr></thead>
          <tbody id="hist-tbody"></tbody>
        </table>
      </div>
    </div>
    <div id="hist-empty" style="color:var(--dim);font-size:11px;text-align:center;padding:20px">Enter a domain above and click Load History.</div>
  </div>
</div>
</div>
</section>

<!-- ══ COMPETITOR ANALYSIS PANEL ═════════════════════════════════════════════ -->
<section id="competitor-sec" class="panel-hidden" style="padding:0 32px 40px">
  <div class="sec-inner">
    <div class="sec-hd">
      <div class="sec-lbl" style="background:rgba(0,255,255,.12);color:var(--cyan)">COMPETITOR INTELLIGENCE</div>
      <h2>⚔ Competitor Analysis</h2>
      <p>Compare your site against up to 5 competitors across 7 SEO dimensions</p>
    </div>
    <div class="dash-card" style="padding:20px">
      <div class="comp-input-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <div>
            <div style="font-family:var(--sans);font-size:14px;font-weight:700;color:var(--cyan)">Enter URLs to compare</div>
            <div style="font-size:10px;color:var(--dim);margin-top:2px">Crawls all sites + fetches Core Web Vitals in ~30–90 seconds</div>
          </div>
          <button class="btn btn-outline btn-sm" style="border-color:var(--dim);color:var(--dim)" onclick="loadCompHistory()" title="View past analyses">↺ History</button>
        </div>
        <div class="comp-input-grid" id="comp-input-grid">
          <div class="comp-url-row target-row"><span class="comp-url-label">Your Site</span><input id="comp-target" type="text" placeholder="https://yoursite.com"/></div>
          <div class="comp-url-row"><span class="comp-url-label">Competitor 1</span><input class="comp-input" type="text" placeholder="https://competitor1.com"/></div>
          <div class="comp-url-row"><span class="comp-url-label">Competitor 2</span><input class="comp-input" type="text" placeholder="https://competitor2.com"/></div>
          <div class="comp-url-row"><span class="comp-url-label">Competitor 3</span><input class="comp-input" type="text" placeholder="https://competitor3.com (optional)"/></div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn btn-green" id="comp-analyze-btn" onclick="startCompAnalysis()">⚔ Analyze</button>
          <button class="btn btn-outline btn-sm" onclick="addCompetitorRow()">+ Add Competitor</button>
          <button class="btn btn-outline btn-sm" style="margin-left:auto" id="comp-export-btn" onclick="exportCompExcel()" disabled>↓ Export (.xlsx)</button>
        </div>
      </div>
      <div class="comp-sbar" id="comp-sbar"><div class="spin g" id="comp-spin"></div><span id="comp-status-txt">Starting analysis…</span></div>
      <div id="comp-results" style="display:none">
        <div class="comp-scores" id="comp-scores"></div>
        <div class="comp-charts">
          <div class="comp-chart-box"><h4>7-Dimension Radar Comparison</h4><div class="comp-chart-canvas" id="radar-chart"></div></div>
          <div class="comp-chart-box"><h4>Core Web Vitals — Performance Score</h4><div class="comp-chart-canvas" id="cwv-chart"></div></div>
        </div>
        <div class="comp-dim-panel">
          <div class="comp-dim-head"><h4>Dimension Breakdown</h4></div>
          <div style="overflow-x:auto"><table class="dim-table" id="dim-table"><thead><tr id="dim-thead-row"></tr></thead><tbody id="dim-tbody"></tbody></table></div>
        </div>
        <div class="comp-gap-panel">
          <div class="comp-gap-head"><h4>⬆ Keyword Gaps <span id="comp-gap-count" style="font-size:10px;color:var(--dim)"></span></h4></div>
          <div class="comp-gap-wrap"><table class="gtable"><thead><tr><th>Keyword</th><th>Opportunity</th><th>Competitors</th><th>Found In</th></tr></thead><tbody id="comp-gap-tbody"></tbody></table></div>
        </div>
        <div class="comp-actions-panel">
          <div class="comp-actions-head"><h4>⚡ Priority Action Plan</h4></div>
          <div class="comp-action-list" id="comp-action-list"></div>
        </div>
      </div>
      <div id="comp-hist-panel" class="comp-hist-panel" style="display:none">
        <div class="comp-hist-head">
          <h4>Analysis History</h4>
          <button class="btn btn-outline btn-sm" onclick="document.getElementById('comp-hist-panel').style.display='none'">✕ Close</button>
        </div>
        <div style="overflow-x:auto">
          <table class="htable"><thead><tr><th>Target</th><th>Competitors</th><th>Score</th><th>Status</th><th>Date</th><th>Action</th></tr></thead>
          <tbody id="comp-hist-tbody"><tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">No history yet.</td></tr></tbody></table>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ══ SCHEMA INTELLIGENCE PANEL ══════════════════════════════════════════════ -->
<section id="panel-schema-intel" class="panel-hidden" style="padding:0 32px 40px">
<div class="sec-inner">
  <div class="sec-hd">
    <div class="sec-lbl" style="background:rgba(99,102,241,.12);color:var(--indigo)">SCHEMA INTELLIGENCE</div>
    <h2>◈ Schema Intelligence</h2>
    <p>Validate JSON-LD markup across all crawled pages — detect missing properties, warnings, and errors</p>
  </div>
  <div class="dash-card" style="padding:20px">
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
      <div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:12px 18px;flex:1;min-width:120px;text-align:center">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:4px">Coverage</div>
        <div style="font-family:var(--headline);font-size:24px;font-weight:800;color:var(--green)" id="si-coverage">—</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:12px 18px;flex:1;min-width:120px;text-align:center">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:4px">Valid Schemas</div>
        <div style="font-family:var(--headline);font-size:24px;font-weight:800;color:var(--green)" id="si-valid">—</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:12px 18px;flex:1;min-width:120px;text-align:center">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:4px">Warnings</div>
        <div style="font-family:var(--headline);font-size:24px;font-weight:800;color:var(--yellow)" id="si-warnings">—</div>
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
      <input id="si-search" class="serp-input" placeholder="Filter by URL…" oninput="filterSchemaMatrix(this.value)" style="flex:1;min-width:200px"/>
      <button class="btn btn-outline btn-sm" onclick="renderSchemaPanel()">↻ Refresh</button>
      <button class="btn btn-outline btn-sm" onclick="generateSchemaAI()">✨ AI Generate</button>
    </div>
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:11px">
        <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
          <th style="padding:8px 10px;text-align:left">URL</th>
          <th style="padding:8px 10px;text-align:left">Schema ID</th>
          <th style="padding:8px 10px;text-align:left">Types Found</th>
          <th style="padding:8px 10px;text-align:center">Status</th>
          <th style="padding:8px 10px;text-align:center">Issues</th>
          <th style="padding:8px 10px;text-align:center">View</th>
        </tr></thead>
        <tbody id="si-tbody">
          <tr><td colspan="6" style="text-align:center;color:var(--muted);padding:28px;font-size:11px">Run a crawl first, then open Schema Intelligence to see JSON-LD analysis.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
</section>

<!-- ══ CONTENT LAB PANEL ═══════════════════════════════════════════════════════ -->
<section id="panel-content-lab" class="panel-hidden" style="padding:0 32px 40px">
<div class="sec-inner">
  <div class="sec-hd">
    <div class="sec-lbl" style="background:rgba(255,183,131,.12);color:var(--tertiary)">AI CONTENT GENERATION</div>
    <h2>✦ Content Lab</h2>
    <p>Generate SEO-optimized content with title, meta description, H1, and body — powered by Groq, Gemini, Claude, or OpenAI</p>
    <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
      <button class="btn btn-green" onclick="runContentLabSynthesis()" id="clab-generate-btn">
        <span class="material-symbols-outlined" style="font-size:14px">auto_awesome</span>Generate Content
      </button>
      <button class="btn btn-outline btn-sm" onclick="clabExportExcel()" id="clab-export-btn" disabled>
        <span class="material-symbols-outlined" style="font-size:13px">download</span>Export Excel
      </button>
      <button class="btn btn-outline btn-sm" onclick="clabCopyResult()">
        <span class="material-symbols-outlined" style="font-size:13px">content_copy</span>Copy
      </button>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:minmax(260px,300px) 1fr;gap:20px;align-items:start">
    <!-- Left: provider + brief -->
    <div>
      <div class="dash-card" style="padding:16px;margin-bottom:0">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:10px">AI Provider</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">
          <button class="clab-model-btn active" onclick="clabSelectModel(this,'groq')" style="padding:12px 8px;font-size:11px;border:1px solid var(--border);border-radius:var(--r);background:var(--surf2);color:var(--text);cursor:pointer;text-align:center">
            <div style="font-size:18px;margin-bottom:4px">⚡</div>
            <div style="font-weight:700">Groq</div>
            <div style="font-size:9px;color:var(--muted)">Llama 3 · Free</div>
          </button>
          <button class="clab-model-btn" onclick="clabSelectModel(this,'gemini')" style="padding:12px 8px;font-size:11px;border:1px solid var(--border);border-radius:var(--r);background:var(--surf2);color:var(--muted);cursor:pointer;text-align:center">
            <div style="font-size:18px;margin-bottom:4px">✦</div>
            <div style="font-weight:700">Gemini</div>
            <div style="font-size:9px;color:var(--muted)">Google AI · Free</div>
          </button>
          <button class="clab-model-btn" onclick="clabSelectModel(this,'claude')" style="padding:12px 8px;font-size:11px;border:1px solid var(--border);border-radius:var(--r);background:var(--surf2);color:var(--muted);cursor:pointer;text-align:center">
            <div style="font-size:18px;margin-bottom:4px">◆</div>
            <div style="font-weight:700">Claude</div>
            <div style="font-size:9px;color:var(--muted)">Anthropic · Reasoning</div>
          </button>
          <button class="clab-model-btn" onclick="clabSelectModel(this,'openai')" style="padding:12px 8px;font-size:11px;border:1px solid var(--border);border-radius:var(--r);background:var(--surf2);color:var(--muted);cursor:pointer;text-align:center">
            <div style="font-size:18px;margin-bottom:4px">◉</div>
            <div style="font-weight:700">OpenAI</div>
            <div style="font-size:9px;color:var(--muted)">GPT-4 · Advanced</div>
          </button>
        </div>
        <div style="font-size:9px;color:var(--muted);margin-bottom:8px;padding:6px 10px;background:var(--surf2);border-radius:var(--r);font-family:var(--mono)">API KEY (OPTIONAL — USED SERVICE KEY IF BLANK)</div>
        <input id="clab-api-key" class="serp-input" style="width:100%;margin-bottom:14px" placeholder="sk-… or leave blank for free tier"/>
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:10px">Content Brief</div>
        <div style="display:flex;flex-direction:column;gap:8px">
          <div>
            <div style="font-size:9px;color:var(--muted);margin-bottom:3px">Target Keyword *</div>
            <input id="clab-target-kw" class="serp-input" style="width:100%" placeholder="e.g. technical seo audit tool"/>
          </div>
          <div>
            <div style="font-size:9px;color:var(--muted);margin-bottom:3px">Secondary Keywords</div>
            <input id="clab-secondary-kws" class="serp-input" style="width:100%" placeholder="seo checker, site crawler, seo analysis"/>
          </div>
          <div>
            <div style="font-size:9px;color:var(--muted);margin-bottom:3px">Content Type</div>
            <select id="clab-content-type" class="serp-input" style="width:100%">
              <option value="blog_post">Blog Post / Article</option>
              <option value="landing_page">Landing Page</option>
              <option value="product_page">Product Page</option>
              <option value="how_to_guide">How-To Guide</option>
              <option value="comparison">Comparison Page</option>
            </select>
          </div>
          <div>
            <div style="font-size:9px;color:var(--muted);margin-bottom:3px">Tone</div>
            <select id="clab-tone" class="serp-input" style="width:100%">
              <option value="professional">Professional</option>
              <option value="conversational">Conversational</option>
              <option value="technical">Technical</option>
              <option value="persuasive">Persuasive</option>
            </select>
          </div>
          <div>
            <div style="font-size:9px;color:var(--muted);margin-bottom:3px">Word Count Target</div>
            <select id="clab-word-count" class="serp-input" style="width:100%">
              <option value="800">~800 words (standard)</option>
              <option value="1500">~1500 words (long-form)</option>
              <option value="300">~300 words (short)</option>
              <option value="2500">~2500 words (pillar)</option>
            </select>
          </div>
          <div>
            <div style="font-size:9px;color:var(--muted);margin-bottom:3px">Additional Instructions</div>
            <textarea id="clab-original" class="serp-input" rows="3" style="width:100%;resize:vertical" placeholder="e.g. Include a comparison table, mention specific features, target audience…"></textarea>
          </div>
          <div style="display:none"><input id="clab-context" class="serp-input" style="width:100%"/></div>
        </div>
        <button class="btn btn-green" id="clab-generate-btn2" onclick="runContentLabSynthesis()" style="width:100%;margin-top:12px">
          <span class="material-symbols-outlined" style="font-size:14px">auto_awesome</span>Generate Content
        </button>
      </div>
    </div>
    <!-- Right: output -->
    <div>
      <!-- SEO Score -->
      <div class="dash-card" style="padding:16px;margin-bottom:16px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:11px;font-weight:700;color:var(--dim)">SEO Score</span>
          <span style="font-size:10px;color:var(--muted)" id="clab-seo-score-label">out of 100</span>
        </div>
        <div style="background:var(--surf2);border-radius:4px;overflow:hidden;height:6px;margin-bottom:6px">
          <div id="clab-seo-score-bar" style="width:0%;height:100%;background:var(--indigo);border-radius:4px;transition:width .5s"></div>
        </div>
        <div style="font-size:10px;color:var(--muted)" id="clab-seo-score-hint">Run generation to see factors</div>
      </div>
      <!-- Generated meta fields -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
        <div class="dash-card" style="padding:14px">
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:6px">Generated Title Tag</div>
          <div id="clab-gen-title" style="font-size:12px;color:var(--dim);font-family:var(--mono)">—</div>
          <div style="font-size:9px;color:var(--muted);margin-top:4px" id="clab-gen-title-len">0 / 60 chars</div>
        </div>
        <div class="dash-card" style="padding:14px">
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:6px">Generated Meta Description</div>
          <div id="clab-gen-meta" style="font-size:12px;color:var(--dim);font-family:var(--mono)">—</div>
          <div style="font-size:9px;color:var(--muted);margin-top:4px" id="clab-gen-meta-len">0 / 160 chars</div>
        </div>
        <div class="dash-card" style="padding:14px">
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:6px">Generated H1</div>
          <div id="clab-gen-h1" style="font-size:12px;color:var(--dim);font-family:var(--mono)">—</div>
        </div>
        <div class="dash-card" style="padding:14px">
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:6px">Keyword Info</div>
          <div id="clab-kw-info" style="font-size:11px;color:var(--dim)">—</div>
        </div>
      </div>
      <!-- Content preview -->
      <div class="dash-card" style="padding:20px;min-height:280px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <span style="font-size:12px;font-weight:700;color:var(--text)">Content Preview</span>
          <div style="display:flex;align-items:center;gap:8px">
            <span id="clab-word-count-out" style="font-size:10px;color:var(--muted);font-family:var(--mono)">0 words</span>
            <button class="btn btn-outline btn-sm clab-copy-btn" onclick="clabCopyResult()" style="font-size:11px">📋 Copy</button>
          </div>
        </div>
        <div id="clab-processing" style="display:none;text-align:center;padding:60px 20px">
          <div style="width:32px;height:32px;border:2px solid rgba(99,102,241,.2);border-top-color:var(--primary);border-radius:50%;animation:spin .7s linear infinite;margin:0 auto 16px"></div>
          <div style="font-size:12px;color:var(--muted)">Generating content with AI…</div>
        </div>
        <div id="clab-result" style="display:none">
          <pre id="clab-result-text" style="white-space:pre-wrap;font-size:13px;font-family:var(--sans);line-height:1.65;color:var(--text);max-height:500px;overflow-y:auto"></pre>
        </div>
        <div id="clab-empty" style="text-align:center;padding:60px 20px;color:var(--muted)">
          <div style="font-size:40px;margin-bottom:16px;opacity:.4">✦</div>
          <div style="font-size:13px;line-height:1.6;color:var(--dim)">Fill in the brief and click <strong style="color:var(--text)">Generate Content</strong>.<br>AI-optimized content will appear here.</div>
        </div>
      </div>
    </div>
  </div>
</div>
</section>

<!-- ══ SITEMAP CRAWL PANEL ════════════════════════════════════════════════════ -->
<section id="sitemap-crawl-sec" class="panel-hidden" style="padding:0 32px 40px">
<div class="sec-inner">
  <div class="sec-hd">
    <div class="sec-lbl" style="background:rgba(16,185,129,.12);color:var(--green)">SITEMAP ANALYSIS</div>
    <h2>🗺 Sitemap Crawl</h2>
    <p>Parse sitemap.xml — validate URLs, detect orphans, check lastmod, priority, and HTTP status</p>
  </div>
  <div class="dash-card" style="padding:20px">
    <div style="display:flex;gap:8px;background:var(--surf2);border:1px solid var(--border);border-radius:var(--r-xl);padding:6px 6px 6px 16px;margin-bottom:16px;transition:border-color .2s" onfocusin="this.style.borderColor='rgba(16,185,129,.4)'" onfocusout="this.style.borderColor=''">
      <span class="material-symbols-outlined" style="color:var(--muted);font-size:18px;margin-top:8px">account_tree</span>
      <input id="sitemap-url-input" type="text" placeholder="https://example.com/sitemap.xml or just example.com"
        style="flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:13px;padding:8px 0;font-family:var(--sans)"
        onkeydown="if(event.key==='Enter') runSitemapCrawl()"/>
      <button class="btn btn-green btn-sm" onclick="runSitemapCrawl()" id="sitemap-run-btn">
        <span class="material-symbols-outlined" style="font-size:14px">bolt</span>Crawl
      </button>
    </div>
    <div class="sbar" id="sitemap-status-bar" style="display:none;margin-bottom:14px">
      <div class="spin g"></div><span id="sitemap-status-txt">Fetching sitemap…</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:16px">
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:14px;position:relative">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:6px">Total URLs</div>
        <div style="font-family:var(--headline);font-size:26px;font-weight:800" id="sm-total">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">in sitemap</div>
        <div style="position:absolute;right:12px;top:12px;font-size:20px;opacity:.5">🗺</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:14px;position:relative">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:6px">Live (2xx)</div>
        <div style="font-family:var(--headline);font-size:26px;font-weight:800;color:var(--green)" id="sm-live">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px" id="sm-live-pct">—</div>
        <div style="position:absolute;right:12px;top:12px;font-size:20px;opacity:.5">✓</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:14px;position:relative">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:6px">Redirects (3xx)</div>
        <div style="font-family:var(--headline);font-size:26px;font-weight:800;color:var(--yellow)" id="sm-redirects">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">review recommended</div>
        <div style="position:absolute;right:12px;top:12px;font-size:20px;opacity:.5">⟳</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:14px;position:relative">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:6px">Errors (4xx/5xx)</div>
        <div style="font-family:var(--headline);font-size:26px;font-weight:800;color:var(--red)" id="sm-errors">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">need immediate fix</div>
        <div style="position:absolute;right:12px;top:12px;font-size:20px;opacity:.5">⚠</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:14px;position:relative">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:6px">No Lastmod</div>
        <div style="font-family:var(--headline);font-size:26px;font-weight:800;color:var(--yellow)" id="sm-nolastmod">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">freshness signal missing</div>
        <div style="position:absolute;right:12px;top:12px;font-size:20px;opacity:.5">📅</div>
      </div>
    </div>
    <div class="dash-card" style="padding:20px;background:var(--surf-lowest)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
        <div style="font-size:13px;font-weight:700;color:var(--text)">Sitemap URLs</div>
        <span style="font-size:11px;color:var(--muted);font-family:var(--mono)" id="sm-tbl-count">0 URLs</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <select id="sm-f-status" onchange="renderSitemapTable()" class="serp-input" style="font-size:11px">
          <option value="">All Statuses</option>
          <option value="2xx">2xx Live</option>
          <option value="3xx">3xx Redirect</option>
          <option value="4xx">4xx Error</option>
          <option value="no-lastmod">No Lastmod</option>
        </select>
        <input id="sm-f-search" onchange="renderSitemapTable()" oninput="renderSitemapTable()" class="serp-input" placeholder="Search URL…" style="flex:1;min-width:200px;font-size:11px"/>
        <button class="btn btn-outline btn-sm" onclick="exportSitemapExcel()" id="sm-export-btn" disabled style="font-size:11px">↓ Export Excel</button>
      </div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead><tr style="background:var(--surf2);color:var(--muted);font-size:9px;text-transform:uppercase">
            <th style="padding:8px 10px;text-align:left;cursor:pointer" onclick="smSort('url')">URL ↕</th>
            <th style="padding:8px 10px;text-align:center;cursor:pointer" onclick="smSort('status_code')">HTTP Status ↕</th>
            <th style="padding:8px 10px;text-align:left;cursor:pointer" onclick="smSort('lastmod')">Last Modified ↕</th>
            <th style="padding:8px 10px;text-align:left">Change Freq</th>
            <th style="padding:8px 10px;text-align:center;cursor:pointer" onclick="smSort('priority')">Priority ↕</th>
            <th style="padding:8px 10px;text-align:left">Issues</th>
          </tr></thead>
          <tbody id="sm-tbl-body">
            <tr><td colspan="6" style="text-align:center;color:var(--muted);padding:40px;font-size:11px">Enter a sitemap URL above and click Crawl to populate this table.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>
</section>

<!-- ══ TECH SEO AUDIT PANEL ════════════════════════════════════════════════════ -->
<section id="tech-seo-sec" class="panel-hidden" style="padding:0 32px 40px">
<div class="sec-inner">
  <div class="sec-hd">
    <div class="sec-lbl" style="background:rgba(16,185,129,.12);color:var(--green)">TECHNICAL SEO</div>
    <h2>🔬 Technical SEO Audit</h2>
    <p>Deep crawl, indexability funnel, Core Web Vitals — per-page scored from 0–100 with grade A–F</p>
    <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
      <button class="btn btn-green" onclick="runTechSEOPanel()" id="tseo-panel-run-btn">
        <span class="material-symbols-outlined" style="font-size:14px">play_arrow</span>Run Audit
      </button>
      <button class="btn btn-outline btn-sm" onclick="exportTechSEOExcel()" id="tseo-panel-export-btn" disabled>
        <span class="material-symbols-outlined" style="font-size:13px">download</span>Export Excel
      </button>
      <button class="btn btn-outline btn-sm" onclick="showPanel('dashboard')">→ Back to Dashboard</button>
    </div>
  </div>
  <!-- URL input -->
  <div class="dash-card" style="padding:14px 20px;margin-bottom:16px">
    <div style="display:flex;align-items:center;gap:10px;background:var(--surf2);border:1px solid var(--border);border-radius:var(--r-xl);padding:8px 8px 8px 16px">
      <span class="material-symbols-outlined" style="color:var(--muted);font-size:17px">language</span>
      <input id="tseo-url-input" type="text" placeholder="https://example.com" style="flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:13px;font-family:var(--mono)" onkeydown="if(event.key==='Enter') runTechSEOPanel()"/>
      <button class="btn btn-green btn-sm" onclick="runTechSEOPanel()">
        <span class="material-symbols-outlined" style="font-size:13px">bolt</span>Audit
      </button>
    </div>
  </div>
  <!-- Status bar -->
  <div class="sbar" id="tseo-panel-sbar" style="display:none;margin-bottom:16px">
    <div class="spin g"></div><span id="tseo-panel-status-txt">Running audit…</span>
  </div>
  <!-- Metric cards -->
  <div id="tseo-panel-metrics" style="display:none">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px">
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
        <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">AVG Tech Score</div>
        <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--green)" id="tseo-avg-score">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px" id="tseo-grade">Grade —</div>
        <div style="position:absolute;right:14px;top:14px;font-size:22px;opacity:.5">📊</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
        <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Pages Audited</div>
        <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:white" id="tseo-pages">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">of crawled pages</div>
        <div style="position:absolute;right:14px;top:14px;font-size:22px;opacity:.5">📄</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
        <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Indexable</div>
        <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--green)" id="tseo-indexable">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">100% indexable</div>
        <div style="position:absolute;right:14px;top:14px;font-size:22px;opacity:.5">✓</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
        <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Critical Issues</div>
        <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--red)" id="tseo-critical">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">across all pages</div>
        <div style="position:absolute;right:14px;top:14px;font-size:22px;opacity:.5">⚠</div>
      </div>
      <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
        <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">HTTPS Coverage</div>
        <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--green)" id="tseo-https">—</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">100% HTTPS</div>
        <div style="position:absolute;right:14px;top:14px;font-size:22px;opacity:.5">🔒</div>
      </div>
    </div>
    <!-- Tabs -->
    <div style="display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:0">
      <button class="serp-tab serp-tab-active" id="tseo-tab-overview" onclick="tseoTab('overview')" style="border-radius:var(--r) var(--r) 0 0">Overview</button>
      <button class="serp-tab" id="tseo-tab-perpage" onclick="tseoTab('perpage')" style="border-radius:var(--r) var(--r) 0 0">Per-Page Audit</button>
      <button class="serp-tab" id="tseo-tab-signals" onclick="tseoTab('signals')" style="border-radius:var(--r) var(--r) 0 0">Core Signals</button>
    </div>
    <!-- Overview pane -->
    <div id="tseo-pane-overview">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
        <!-- Site Health -->
        <div class="dash-card" style="padding:20px">
          <div style="font-size:12px;font-weight:700;color:var(--text);margin-bottom:4px">Site Health Score</div>
          <div style="font-size:10px;color:var(--muted);font-family:var(--mono);text-transform:uppercase;letter-spacing:.1em;margin-bottom:16px">Multidimensional technical score (0–100)</div>
          <div style="display:flex;align-items:center;gap:24px">
            <div style="position:relative;width:90px;height:90px;flex-shrink:0">
              <canvas id="tseo-health-donut" width="90" height="90"></canvas>
              <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">
                <span style="font-family:var(--headline);font-size:22px;font-weight:800;color:var(--green)" id="tseo-donut-score">—</span>
                <span style="font-size:9px;color:var(--muted)" id="tseo-donut-grade">—</span>
              </div>
            </div>
            <div style="flex:1;display:flex;flex-direction:column;gap:6px" id="tseo-signal-list">
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--dim)"><span>robots.txt</span><span id="tseo-robots-val" style="font-weight:600">—</span></div>
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--dim)"><span>sitemap.xml</span><span id="tseo-sitemap-val" style="font-weight:600">—</span></div>
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--dim)"><span>HTTPS Status</span><span id="tseo-https-val" style="font-weight:600">—</span></div>
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--dim)"><span>Canonical Issues</span><span id="tseo-canonical-val" style="font-weight:600">—</span></div>
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--dim)"><span>Duplicate Meta</span><span id="tseo-dupmeta-val" style="font-weight:600">—</span></div>
            </div>
          </div>
        </div>
        <!-- Status cards -->
        <div style="display:flex;flex-direction:column;gap:10px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
            <div id="tseo-robots-card" class="dash-card" style="padding:14px 16px;cursor:pointer">
              <div style="font-size:10px;font-weight:700;color:var(--muted);margin-bottom:4px;display:flex;align-items:center;gap:6px">🤖 robots.txt <span id="tseo-robots-badge" style="font-size:9px;padding:2px 6px;border-radius:12px;background:rgba(245,158,11,.15);color:var(--yellow)">CHECK</span></div>
              <div style="font-size:11px;color:var(--dim)" id="tseo-robots-sub">Not checked</div>
            </div>
            <div id="tseo-sitemap-card" class="dash-card" style="padding:14px 16px;cursor:pointer">
              <div style="font-size:10px;font-weight:700;color:var(--muted);margin-bottom:4px;display:flex;align-items:center;gap:6px">🗺 sitemap.xml <span id="tseo-sitemap-badge" style="font-size:9px;padding:2px 6px;border-radius:12px;background:rgba(245,158,11,.15);color:var(--yellow)">CHECK</span></div>
              <div style="font-size:11px;color:var(--dim)" id="tseo-sitemap-sub">Not checked</div>
            </div>
            <div class="dash-card" style="padding:14px 16px">
              <div style="font-size:10px;font-weight:700;color:var(--muted);margin-bottom:4px;display:flex;align-items:center;gap:6px">🔒 HTTPS/SSL <span id="tseo-ssl-badge" style="font-size:9px;padding:2px 6px;border-radius:12px;background:rgba(16,185,129,.15);color:var(--green)">VALID</span></div>
              <div style="font-size:11px;color:var(--dim)" id="tseo-ssl-sub">Valid</div>
            </div>
            <div class="dash-card" style="padding:14px 16px">
              <div style="font-size:10px;font-weight:700;color:var(--muted);margin-bottom:4px;display:flex;align-items:center;gap:6px">🌐 HTTP Status Dist. <span id="tseo-http-badge" style="font-size:9px;padding:2px 6px;border-radius:12px;background:rgba(16,185,129,.15);color:var(--green)">OK</span></div>
              <div style="font-size:11px;color:var(--dim)" id="tseo-http-sub">51OK · 0 4xx · 0 5xx</div>
            </div>
          </div>
        </div>
      </div>
      <!-- Indexability Funnel -->
      <div class="dash-card" style="padding:20px">
        <div style="font-size:12px;font-weight:700;color:var(--text);margin-bottom:4px">Indexability Funnel</div>
        <div style="font-size:10px;color:var(--muted);font-family:var(--mono);text-transform:uppercase;letter-spacing:.1em;margin-bottom:14px">Path from crawl to search-visible pages</div>
        <div id="tseo-funnel" style="display:flex;flex-direction:column;gap:6px">
          <div style="text-align:center;color:var(--muted);font-size:11px;padding:20px">Run audit to see funnel data.</div>
        </div>
      </div>
    </div>
    <!-- Per-Page pane -->
    <div id="tseo-pane-perpage" style="display:none">
      <div class="dash-card" style="padding:20px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
          <input id="tseo-pp-search" type="text" placeholder="Filter pages…" style="flex:1;min-width:160px;background:var(--surf2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:11px;padding:6px 10px;border-radius:var(--r);outline:none" oninput="tseoFilterPages()"/>
          <select id="tseo-pp-grade" onchange="tseoFilterPages()" style="background:var(--surf2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:11px;padding:6px 10px;border-radius:var(--r);outline:none">
            <option value="">All Grades</option>
            <option value="A">Grade A</option>
            <option value="B">Grade B</option>
            <option value="C">Grade C</option>
            <option value="D">Grade D</option>
            <option value="F">Grade F</option>
          </select>
          <span id="tseo-pp-count" style="font-family:var(--mono);font-size:11px;color:var(--muted)">0 pages</span>
        </div>
        <div style="overflow-x:auto;border-radius:var(--r-lg);border:1px solid var(--ghost)">
          <table style="width:100%;border-collapse:collapse;font-size:11px">
            <thead><tr style="background:var(--surf2);border-bottom:1px solid var(--ghost)">
              <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);text-align:left">URL</th>
              <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);text-align:center">Score</th>
              <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);text-align:center">Grade</th>
              <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);text-align:left">Issues</th>
              <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Status</th>
            </tr></thead>
            <tbody id="tseo-pp-tbody">
              <tr><td colspan="5" style="text-align:center;color:var(--muted);padding:28px;font-size:11px">Run audit to see per-page data.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
    <!-- Core Signals pane -->
    <div id="tseo-pane-signals" style="display:none">
      <div class="dash-card" style="padding:20px">
        <div style="font-size:12px;font-weight:700;color:var(--text);margin-bottom:16px">Core SEO Signals</div>
        <div id="tseo-signals-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
          <div style="text-align:center;color:var(--muted);font-size:11px;padding:28px;grid-column:1/-1">Run audit to see signal data.</div>
        </div>
      </div>
    </div>
  </div>
  <!-- Empty state before audit -->
  <div id="tseo-panel-empty" style="text-align:center;padding:60px 20px;color:var(--muted)">
    <div style="font-size:48px;margin-bottom:16px;opacity:.4">🔬</div>
    <div style="font-size:14px;margin-bottom:8px;color:var(--dim)">Enter a URL above and click Run Audit</div>
    <div style="font-size:11px">Analyzes robots.txt, sitemap, HTTPS, canonicals, indexability, and per-page signals</div>
  </div>
</div>
</section>

<!-- ══ KEYWORD GAP PANEL ══════════════════════════════════════════════════════ -->
<section id="kwgap-sec" class="panel-hidden" style="padding:0 32px 40px">
<div class="sec-inner">
  <div class="sec-hd">
    <div class="sec-lbl" style="background:rgba(99,102,241,.12);color:var(--indigo)">KEYWORD INTELLIGENCE</div>
    <h2>🔍 Keyword Gap Analysis</h2>
    <p>Find keywords competitors rank for but you don't — Missing, Weak, and Strong opportunities segmented and scored</p>
    <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
      <button class="btn btn-primary" onclick="runKwGapPanel()" id="kwgap-panel-run-btn">
        <span class="material-symbols-outlined" style="font-size:14px">search</span>Find Gaps
      </button>
      <button class="btn btn-outline btn-sm" onclick="exportKwGapExcel()" id="kwgap-panel-export-btn" disabled>
        <span class="material-symbols-outlined" style="font-size:13px">download</span>Export Excel
      </button>
      <button class="btn btn-outline btn-sm" onclick="showPanel('competitors')">→ Competitor Analysis</button>
      <button class="btn btn-outline btn-sm" onclick="showPanel('content-lab')">→ AI Content</button>
    </div>
  </div>
  <!-- Keyword inputs -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
    <div class="dash-card" style="padding:18px;border-color:rgba(99,102,241,.2);background:rgba(99,102,241,.03)">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.15em;color:var(--indigo);margin-bottom:8px">🎯 Your Keywords (one per line)</div>
      <textarea id="kwgap-your-kws" class="serp-input" rows="5" style="width:100%;resize:vertical" placeholder="best seo tool&#10;keyword research&#10;site audit"></textarea>
    </div>
    <div class="dash-card" style="padding:18px">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.15em;color:var(--muted);margin-bottom:8px">Competitor Keywords (one per line)</div>
      <textarea id="kwgap-comp-kws" class="serp-input" rows="5" style="width:100%;resize:vertical" placeholder="seo checker&#10;rank tracker&#10;backlink analysis"></textarea>
    </div>
  </div>
  <!-- Status bar -->
  <div class="sbar" id="kwgap-panel-sbar" style="display:none;margin-bottom:14px">
    <div class="spin g"></div><span id="kwgap-panel-status-txt">Analyzing keyword gaps…</span>
  </div>
  <!-- Metrics -->
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px">
    <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
      <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Total Keywords</div>
      <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:white" id="kwgap-m-total">—</div>
      <div style="font-size:10px;color:var(--muted);margin-top:2px">analyzed</div>
      <div style="position:absolute;right:14px;top:14px;font-size:20px;opacity:.5">🔑</div>
    </div>
    <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
      <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Missing (0 rank)</div>
      <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--red)" id="kwgap-m-missing">—</div>
      <div style="font-size:10px;color:var(--muted);margin-top:2px">not ranked at all</div>
      <div style="position:absolute;right:14px;top:14px;font-size:20px;opacity:.5">⚠</div>
    </div>
    <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
      <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Weak (pos 11–30)</div>
      <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--yellow)" id="kwgap-m-weak">—</div>
      <div style="font-size:10px;color:var(--muted);margin-top:2px">page 2–3 rankings</div>
      <div style="position:absolute;right:14px;top:14px;font-size:20px;opacity:.5">⬆</div>
    </div>
    <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
      <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Strong (top 10)</div>
      <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--green)" id="kwgap-m-strong">—</div>
      <div style="font-size:10px;color:var(--muted);margin-top:2px">winning keywords</div>
      <div style="position:absolute;right:14px;top:14px;font-size:20px;opacity:.5">✓</div>
    </div>
    <div style="background:var(--surf2);border:1px solid var(--ghost);border-radius:var(--r-lg);padding:16px 18px;position:relative">
      <div style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Avg Gap Score</div>
      <div style="font-family:var(--headline);font-size:28px;font-weight:800;color:var(--indigo)" id="kwgap-m-avggap">—</div>
      <div style="font-size:10px;color:var(--muted);margin-top:2px">opportunity index</div>
      <div style="position:absolute;right:14px;top:14px;font-size:20px;opacity:.5">📊</div>
    </div>
  </div>
  <!-- Gap table -->
  <div class="dash-card" style="padding:20px">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px">
      <div>
        <div style="font-size:14px;font-weight:800;color:var(--text)">Keyword Gap Matrix</div>
        <div style="font-size:10px;color:var(--muted);font-family:var(--mono);text-transform:uppercase;letter-spacing:.1em;margin-top:3px">Ranked opportunity by gap score</div>
      </div>
    </div>
    <div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">
      <button class="seg-btn active-all" id="kwgap-seg-all"     onclick="kwgapSetSegment('all')">All Keywords</button>
      <button class="seg-btn" id="kwgap-seg-miss"   onclick="kwgapSetSegment('missing')">🔴 Missing</button>
      <button class="seg-btn" id="kwgap-seg-weak"   onclick="kwgapSetSegment('weak')">🟡 Weak</button>
      <button class="seg-btn" id="kwgap-seg-strong" onclick="kwgapSetSegment('strong')">🟢 Strong</button>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
      <input id="kwgap-f-search" type="text" placeholder="Filter keywords…" style="flex:1;min-width:140px;background:var(--surf2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:11px;padding:6px 10px;border-radius:var(--r);outline:none" oninput="kwgapRenderTable()"/>
      <select id="kwgap-f-vol" onchange="kwgapRenderTable()" style="background:var(--surf2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:11px;padding:6px 10px;border-radius:var(--r);outline:none">
        <option value="">All Volumes</option>
        <option value="high">High (≥5k)</option>
        <option value="med">Medium (1k–5k)</option>
        <option value="low">Low (&lt;1k)</option>
      </select>
      <span id="kwgap-tbl-count" style="font-family:var(--mono);font-size:11px;color:var(--muted)">0 keywords</span>
    </div>
    <div style="overflow-x:auto;border-radius:var(--r-lg);border:1px solid var(--ghost)">
      <table style="width:100%;border-collapse:collapse;font-size:11px">
        <thead><tr style="background:var(--surf2);border-bottom:1px solid var(--ghost)">
          <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);text-align:left">Keyword</th>
          <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Type</th>
          <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Your Position</th>
          <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Comp 1</th>
          <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Search Vol</th>
          <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Gap Score</th>
          <th style="padding:9px 12px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Action</th>
        </tr></thead>
        <tbody id="kwgap-panel-tbody">
          <tr><td colspan="7" style="text-align:center;padding:40px;color:var(--muted);font-size:11px">Enter your keywords and competitor keywords above, then click Find Gaps.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
</section>

<!-- ══ CRAWL HISTORY MODAL ════════════════════════════════════════════════════ -->
<div id="history-modal" class="modal-overlay" style="display:none">
<div class="modal-box" style="max-width:700px;max-height:88vh;overflow-y:auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
    <h2>📋 Crawl History</h2>
    <button onclick="closeHistory()" style="background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px">✕</button>
  </div>
  <div style="font-size:11px;color:var(--muted);margin-bottom:16px">Previous crawls are stored locally in your browser. Click Re-run to repeat any crawl.</div>
  <div id="history-list" style="display:flex;flex-direction:column;gap:8px">
    <div style="text-align:center;color:var(--dim);font-size:11px;padding:20px">No crawl history yet.</div>
  </div>
  <div style="margin-top:16px;text-align:right">
    <button class="btn btn-outline btn-sm" onclick="clearCrawlHistory()" style="font-size:11px;color:var(--red);border-color:var(--red)">🗑 Clear History</button>
  </div>
</div>
</div>

<!-- ══ BUILDER BADGE ═════════════════════════════════════════════════════════ --> href="https://www.linkedin.com/in/teki-bhavani-shankar-seo-professional/" target="_blank" rel="noopener" title="Built by Teki Bhavani Shankar — Technical SEO Specialist">
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

// ── Crawl target bar ──────────────────────────────────────────────────────────
function showCrawlTarget(url) {
  const bar = document.getElementById('crawl-target-bar');
  const span = document.getElementById('crawl-target-url');
  if (bar) bar.style.display = 'flex';
  if (span) span.textContent = url;
}
function editCrawlTarget() {
  const span = document.getElementById('crawl-target-url');
  const url = span ? span.textContent : '';
  const inp = document.getElementById('url-input-app') || document.getElementById('url-input');
  if (inp) { inp.value = url; inp.focus(); inp.select(); }
  const ctBar = document.getElementById('crawl-target-bar');
  if (ctBar) ctBar.style.display = 'none';
}
function rerunCrawl() {
  const span = document.getElementById('crawl-target-url');
  const url = span ? span.textContent : '';
  if (!url) return;
  const inp = document.getElementById('url-input');
  const appInp = document.getElementById('url-input-app');
  if (inp) inp.value = url;
  if (appInp) appInp.value = url;
  startCrawl();
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
  document.body.classList.add('app-mode');
  if (typeof showPanel === 'function') showPanel('dashboard');
  // Auto-populate Tech SEO + Keyword Gap URLs from crawl target
  const urlInput = document.getElementById('url-input');
  const ctUrl = document.getElementById('crawl-target-url');
  const currentUrl = (urlInput || {}).value || (ctUrl || {}).textContent || '';
  if (currentUrl) {
    const tseoIn = document.getElementById('tseo-url-input');
    if (tseoIn && !tseoIn.value) tseoIn.value = currentUrl;
    // Pre-fill keyword gap with domain keywords hint
    const kwIn = document.getElementById('kwgap-your-kws');
    if (kwIn && !kwIn.value) {
      try {
        const domain = new URL(currentUrl.startsWith('http') ? currentUrl : 'https://' + currentUrl).hostname.replace('www.', '');
        kwIn.placeholder = `Keywords for ${domain}…\nbest seo tool\nsite audit`;
      } catch(e) {}
    }
  }
}
function exitAppMode() {
  window.location.href = '/';
}

// ── Crawl History (localStorage) ─────────────────────────────────────────────
function openHistory() {
  renderCrawlHistory();
  document.getElementById('history-modal').style.display = 'flex';
}
function closeHistory() {
  document.getElementById('history-modal').style.display = 'none';
}
function saveCrawlHistory(url, data) {
  try {
    const existing = JSON.parse(localStorage.getItem('ciq_crawl_history') || '[]');
    existing.unshift({
      url,
      timestamp: Date.now(),
      pages: data.pages_crawled || 0,
      issues: data.total_issues || 0,
    });
    localStorage.setItem('ciq_crawl_history', JSON.stringify(existing.slice(0, 25)));
  } catch(e) { console.warn('[CrawlIQ] History save error:', e); }
}
function renderCrawlHistory() {
  const list = document.getElementById('history-list');
  if (!list) return;
  let history = [];
  try { history = JSON.parse(localStorage.getItem('ciq_crawl_history') || '[]'); } catch {}
  if (!history.length) {
    list.innerHTML = '<div style="text-align:center;color:var(--dim);font-size:11px;padding:24px">No crawl history yet. Run your first crawl to see it here.</div>';
    return;
  }
  list.innerHTML = history.map(h => `
    <div style="background:var(--surf2);border:1px solid var(--border);border-radius:var(--r);padding:12px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <div style="flex:1;min-width:200px;overflow:hidden">
        <div style="font-size:12px;font-weight:600;color:var(--text);font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${h.url.replace(/"/g,'&quot;')}">${h.url}</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">${new Date(h.timestamp).toLocaleString()}</div>
      </div>
      <div style="display:flex;gap:14px;font-size:11px">
        <span style="color:var(--text)">${h.pages} pages</span>
        <span style="color:var(--red)">${h.issues} issues</span>
      </div>
      <button class="btn btn-outline btn-sm" style="font-size:11px" onclick="rerunFromHistory(${JSON.stringify(h.url)})">↺ Re-run</button>
    </div>`).join('');
}
function clearCrawlHistory() {
  if (!confirm('Clear all crawl history?')) return;
  localStorage.removeItem('ciq_crawl_history');
  renderCrawlHistory();
}
function rerunFromHistory(url) {
  closeHistory();
  const inp = document.getElementById('url-input');
  const appInp = document.getElementById('url-input-app');
  if (inp) inp.value = url;
  if (appInp) appInp.value = url;
  activateCrawlMode();
  setTimeout(() => startCrawl(), 100);
}

function openScoreHistory() {
  let history = [];
  try { history = JSON.parse(localStorage.getItem('ciq_crawl_history') || '[]'); } catch {}

  const tbody = document.getElementById('score-history-tbody');
  if (tbody) {
    if (!history.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;color:var(--muted)">No crawl history yet. Run a crawl to start tracking.</td></tr>';
    } else {
      tbody.innerHTML = history.map(h => {
        const score = h.pages > 0 ? Math.max(0, Math.round(100 - (h.issues / h.pages) * 100)) : (h.issues === 0 ? 100 : 0);
        const scoreColor = score >= 70 ? 'var(--green)' : score >= 40 ? 'var(--yellow)' : 'var(--red)';
        return `<tr style="border-bottom:1px solid var(--border)">
          <td style="padding:6px 10px;color:var(--dim)">${new Date(h.timestamp).toLocaleString()}</td>
          <td style="padding:6px 10px;text-align:center;font-weight:600">${h.pages}</td>
          <td style="padding:6px 10px;text-align:center;color:var(--red)">${h.issues}</td>
          <td style="padding:6px 10px;text-align:center;font-weight:700;color:${scoreColor}">${score}</td>
        </tr>`;
      }).join('');
    }
  }

  drawScoreHistoryChart(history);
  document.getElementById('score-history-modal').style.display = 'flex';
}

function drawScoreHistoryChart(history) {
  const canvas = document.getElementById('score-chart');
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.parentElement ? canvas.parentElement.offsetWidth - 24 : 580;
  canvas.width = Math.max(200, w);
  const h = 180;
  canvas.height = h;

  if (!history.length) {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#908fa0';
    ctx.font = '12px Inter,sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No data yet — run a crawl to see history', w / 2, h / 2);
    return;
  }

  // Oldest-first, cap at 15
  const entries = [...history].reverse().slice(-15);
  const scores = entries.map(e =>
    e.pages > 0 ? Math.max(0, Math.round(100 - (e.issues / e.pages) * 100)) : (e.issues === 0 ? 100 : 0)
  );

  ctx.clearRect(0, 0, w, h);
  const pad = { top: 16, right: 16, bottom: 32, left: 36 };
  const cw = w - pad.left - pad.right;
  const ch = h - pad.top - pad.bottom;

  // Grid lines + Y labels
  ctx.lineWidth = 1;
  [0, 25, 50, 75, 100].forEach(v => {
    const y = pad.top + ch - (v / 100) * ch;
    ctx.beginPath(); ctx.strokeStyle = 'rgba(70,69,84,.2)';
    ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cw, y); ctx.stroke();
    ctx.fillStyle = '#908fa0'; ctx.font = '9px monospace'; ctx.textAlign = 'right';
    ctx.fillText(v, pad.left - 4, y + 3);
  });

  if (scores.length === 1) {
    const x = pad.left + cw / 2;
    const y = pad.top + ch - (scores[0] / 100) * ch;
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = scores[0] >= 70 ? '#10B981' : scores[0] >= 40 ? '#F59E0B' : '#ff6b6b';
    ctx.fill();
    return;
  }

  const xStep = cw / (scores.length - 1);

  // Gradient fill under line
  const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ch);
  grad.addColorStop(0, 'rgba(99,102,241,.25)');
  grad.addColorStop(1, 'rgba(99,102,241,0)');
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top + ch);
  scores.forEach((s, i) => {
    ctx.lineTo(pad.left + i * xStep, pad.top + ch - (s / 100) * ch);
  });
  ctx.lineTo(pad.left + (scores.length - 1) * xStep, pad.top + ch);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.beginPath();
  scores.forEach((s, i) => {
    const x = pad.left + i * xStep;
    const y = pad.top + ch - (s / 100) * ch;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#6366F1'; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke();

  // Dots
  scores.forEach((s, i) => {
    const x = pad.left + i * xStep;
    const y = pad.top + ch - (s / 100) * ch;
    ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = s >= 70 ? '#10B981' : s >= 40 ? '#F59E0B' : '#ff6b6b';
    ctx.fill();
  });

  // X-axis date labels
  ctx.fillStyle = '#908fa0'; ctx.font = '8px monospace'; ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(scores.length / 5));
  entries.forEach((entry, i) => {
    if (i % step === 0 || i === entries.length - 1) {
      const x = pad.left + i * xStep;
      ctx.fillText(new Date(entry.timestamp).toLocaleDateString([], { month: 'numeric', day: 'numeric' }), x, h - 6);
    }
  });
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
 'startSitemapCrawl', 'checkGemini', 'openHistory', 'closeHistory', 'rerunFromHistory', 'clearCrawlHistory',
 'openScoreHistory',
 'runTechSEOPanel', 'exportTechSEOExcel', 'tseoTab', 'tseoFilterPages',
 'runKwGapPanel', 'exportKwGapExcel', 'kwgapSetSegment', 'kwgapRenderTable',
 'clabExportExcel', 'extractFromText'
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
      // Auto-start crawl — scripts are loaded at this point (awaited above)
      setTimeout(() => startCrawl(), 200);
    } else {
      // Attempt to restore previous session from localStorage + backend
      const savedUrl = localStorage.getItem('ciq_last_url') || '';
      if (savedUrl) {
        const hiddenInput = document.getElementById('url-input');
        const topbarInput = document.getElementById('url-input-app');
        if (hiddenInput) hiddenInput.value = savedUrl;
        if (topbarInput) topbarInput.value = savedUrl;
        showCrawlTarget(savedUrl);
        // Try to load existing backend results without re-crawling
        setTimeout(async () => {
          try {
            const res = await fetch(`${API}/results`);
            if (res.ok) {
              const data = await res.json();
              const results = data.results || [];
              if (results.length > 0) {
                allResults = results;
                if (typeof updateSummary === 'function') updateSummary(allResults);
                if (typeof applyFilters === 'function') applyFilters();
                const savedJobId = localStorage.getItem('ciq_last_job_id');
                if (savedJobId) _currentJobId = savedJobId;
                bar('c', false, `✓ Restored ${results.length} pages from last crawl · Use ↺ Re-run to refresh`);
                btns({ crawl:0, gemini:0, popup:0, export:0, opt:0, tseo:0, pdf:0, serp:0 });
              }
            }
          } catch {}
        }, 300);
      } else {
        setTimeout(() => document.getElementById('url-input-app')?.focus(), 100);
      }
    }
  } else {
    // Show welcome screen immediately — do not block on network
    const welcome = document.getElementById('app-welcome');
    const savedUrl = localStorage.getItem('ciq_last_url') || '';
    if (welcome) {
      welcome.style.display = 'flex';
      // Pre-fill with last URL so user doesn't have to retype
      if (savedUrl) {
        const welcomeInput = document.getElementById('welcome-url-input');
        if (welcomeInput) welcomeInput.value = savedUrl;
        document.getElementById('url-input').value = savedUrl;
      }
      setTimeout(() => document.getElementById('welcome-url-input')?.focus(), 100);
    }
    // Background check: if backend still has last session results, auto-restore
    if (savedUrl) {
      fetch(`${API}/results`).then(async r => {
        if (!r.ok) return;
        const data = await r.json();
        const results = data.results || [];
        if (results.length > 0 && welcome) {
          welcome.style.display = 'none';
          activateCrawlMode();
          const hiddenInput = document.getElementById('url-input');
          const topbarInput = document.getElementById('url-input-app');
          if (hiddenInput) hiddenInput.value = savedUrl;
          if (topbarInput) topbarInput.value = savedUrl;
          showCrawlTarget(savedUrl);
          allResults = results;
          if (typeof updateSummary === 'function') updateSummary(allResults);
          if (typeof applyFilters === 'function') applyFilters();
          const savedJobId = localStorage.getItem('ciq_last_job_id');
          if (savedJobId) _currentJobId = savedJobId;
          bar('c', false, `✓ Restored ${allResults.length} pages — last crawl of ${savedUrl} · Use ↺ Re-run to refresh`);
          btns({ crawl:0, gemini:0, popup:0, export:0, opt:0, tseo:0, pdf:0, serp:0 });
        }
      }).catch(() => {});
    }
  }

  // 7. Deferred backend health check (never blocks FCP)
  setTimeout(checkGemini, 3000);
}

document.addEventListener('DOMContentLoaded', initApp);
