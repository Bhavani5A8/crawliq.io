/**
 * CrawlIQ — landing.js
 * Lightweight landing-page JS only. No API calls, no auth, no app panels.
 * All app functionality lives in app.js, loaded only by /app/index.html.
 */
'use strict';

window.__SAFE_MODE = true;

/* ── Safe stubs — prevent console errors if any inline onclick still refs these ── */
['exitAppMode','openAiSetup','openAuthModal','authTab','showPanel',
 'openKwGap','openSettings','toggleUserMenu','closePopup','overlayClick',
 'analyzeThisPage','doLogin','doRegister','doLogout','tseoOverlayClick',
 'closeTechSEODetail','downloadTechSEO','closeAiDrawer','aidSelectProvider',
 'openProjects','openScoreHistory','openCrawlDiff','openTeam',
 'closeExportModal','aiOverlayClick','aiTestConnection','aiApplyKey',
 'aiToggleKeyVisible','enterAppMode','showProgress'].forEach(function(fn){
  if(!window[fn]) window[fn] = function(){};
});

/* ── Nav ── */
function toggleLandingMobileNav(){
  document.getElementById('landing-mob-drawer').classList.toggle('open');
  document.getElementById('landing-mob-overlay').classList.toggle('open');
}
function closeLandingMobileNav(){
  document.getElementById('landing-mob-drawer').classList.remove('open');
  document.getElementById('landing-mob-overlay').classList.remove('open');
}

/* ── FAQ accordion ── */
function toggleFaq(item){
  var isOpen = item.classList.contains('open');
  document.querySelectorAll('.faq-item').forEach(function(i){
    i.classList.remove('open');
    var c = i.querySelector('.faq-chev .material-symbols-outlined');
    if(c) c.textContent = 'expand_more';
  });
  if(!isOpen){
    item.classList.add('open');
    var c = item.querySelector('.faq-chev .material-symbols-outlined');
    if(c) c.textContent = 'expand_less';
  }
}

/* ── Hero URL → redirect to /app/ ── */
function startCrawlHero(){
  var inp = document.getElementById('url-input');
  if(!inp) return;
  var raw = inp.value.trim();
  if(!raw){ inp.focus(); return; }
  var url = /^https?:\/\//i.test(raw) ? raw : 'https://' + raw;
  window.location.href = 'app/?url=' + encodeURIComponent(url);
}

/* Keyboard shortcut — Enter in hero input */
document.addEventListener('DOMContentLoaded', function(){
  var inp = document.getElementById('url-input');
  if(inp) inp.addEventListener('keydown', function(e){ if(e.key==='Enter') startCrawlHero(); });
});

/* ── Meta Analyzer (client-side, zero backend) ── */
(function(){
  document.addEventListener('DOMContentLoaded', function(){
    var titleEl = document.getElementById('af-title');
    var metaEl  = document.getElementById('af-meta');
    if(titleEl) titleEl.addEventListener('input', function(){
      var n = titleEl.value.length;
      var hint = document.getElementById('af-title-count');
      if(!hint) return;
      hint.textContent = n + ' characters — ideal: 50–60';
      hint.style.color = (n>=50&&n<=60)?'var(--green)':(n>0&&n<30)?'var(--red)':n>60?'var(--yellow)':'var(--muted)';
    });
    if(metaEl) metaEl.addEventListener('input', function(){
      var n = metaEl.value.length;
      var hint = document.getElementById('af-meta-count');
      if(!hint) return;
      hint.textContent = n + ' characters — ideal: 120–160';
      hint.style.color = (n>=120&&n<=160)?'var(--green)':(n>0&&n<70)?'var(--red)':n>160?'var(--yellow)':'var(--muted)';
    });
  });
})();

function runAnalyzer(){
  var title = (document.getElementById('af-title').value||'').trim();
  var meta  = (document.getElementById('af-meta').value||'').trim();
  var h1    = (document.getElementById('af-h1').value||'').trim();
  var kw    = (document.getElementById('af-kw').value||'').trim().toLowerCase();

  if(!title && !meta && !h1){ alert('Fill in at least one field.'); return; }

  var checks = [];
  if(!title){
    checks.push({label:'Title Tag',score:0,note:'Missing.',color:'var(--red)'});
  } else if(title.length < 30){
    checks.push({label:'Title Tag',score:30,note:title.length+' chars — too short. Expand to 50–60.',color:'var(--yellow)'});
  } else if(title.length > 65){
    checks.push({label:'Title Tag',score:55,note:title.length+' chars — truncated in SERPs. Keep under 60.',color:'var(--yellow)'});
  } else {
    checks.push({label:'Title Tag',score:100,note:title.length+' chars — good.',color:'var(--green)'});
  }
  if(kw && title){
    if(title.toLowerCase().indexOf(kw)!==-1)
      checks.push({label:'Keyword in Title',score:100,note:'"'+kw+'" found in title.',color:'var(--green)'});
    else
      checks.push({label:'Keyword in Title',score:20,note:'"'+kw+'" not in title.',color:'var(--red)'});
  }
  if(!meta){
    checks.push({label:'Meta Description',score:0,note:'Missing.',color:'var(--red)'});
  } else if(meta.length < 70){
    checks.push({label:'Meta Description',score:30,note:meta.length+' chars — too short.',color:'var(--yellow)'});
  } else if(meta.length > 165){
    checks.push({label:'Meta Description',score:60,note:meta.length+' chars — gets truncated.',color:'var(--yellow)'});
  } else {
    checks.push({label:'Meta Description',score:100,note:meta.length+' chars — good.',color:'var(--green)'});
  }
  if(!h1){
    checks.push({label:'H1 Tag',score:0,note:'Missing.',color:'var(--red)'});
  } else {
    var h1Score = (kw && h1.toLowerCase().indexOf(kw)!==-1) ? 100 : 70;
    var h1Note  = kw && h1.toLowerCase().indexOf(kw)===-1 ? 'H1 present but missing keyword "'+kw+'".': 'H1 present.';
    checks.push({label:'H1 Tag',score:h1Score,note:h1Note,color:h1Score===100?'var(--green)':'var(--yellow)'});
  }
  if(title && h1 && title.toLowerCase()===h1.toLowerCase())
    checks.push({label:'Title ≠ H1',score:40,note:'Title and H1 are identical. Differentiate them.',color:'var(--yellow)'});
  else if(title && h1)
    checks.push({label:'Title ≠ H1',score:100,note:'Title and H1 are distinct — good.',color:'var(--green)'});
  if(kw && meta){
    if(meta.toLowerCase().indexOf(kw)!==-1)
      checks.push({label:'Keyword in Meta',score:100,note:'"'+kw+'" found in meta.',color:'var(--green)'});
    else
      checks.push({label:'Keyword in Meta',score:50,note:'"'+kw+'" not in meta.',color:'var(--yellow)'});
  }

  var total = Math.round(checks.reduce(function(s,c){return s+c.score;},0)/checks.length);
  var grade = total>=90?'A':total>=75?'B':total>=55?'C':total>=35?'D':'F';
  var gradeColor = total>=90?'var(--green)':total>=75?'var(--cyan)':total>=55?'var(--yellow)':'var(--red)';
  var verdicts = {A:'Well-optimised.',B:'Good baseline. One or two gaps.',C:'Moderate issues. Fix red items first.',D:'Multiple critical problems.',F:'Missing core elements.'};

  document.getElementById('az-score').textContent = total;
  document.getElementById('az-score').style.color = gradeColor;
  document.getElementById('az-grade').textContent = 'Grade ' + grade;
  document.getElementById('az-grade').style.color = gradeColor;
  document.getElementById('az-verdict').textContent = verdicts[grade];
  document.getElementById('az-breakdown').innerHTML = checks.map(function(c){
    return '<div class="sb-item"><div class="sb-label">'+c.label+'</div><div class="sb-bar"><div class="sb-fill" style="width:'+c.score+'%;background:'+c.color+'"></div></div><div class="sb-note" style="color:'+c.color+'">'+c.note+'</div></div>';
  }).join('');
  document.getElementById('score-display').classList.add('show');
  document.getElementById('analyzer-sec').scrollIntoView({behavior:'smooth'});
}

/* ── SEO Checklist (localStorage, zero backend) ── */
var CHK_ITEMS = [
  {group:'On-Page Basics', items:[
    {id:'c1',strong:'Title tag is 50–60 characters',detail:'Not "Home" or "Welcome". Contains your primary keyword.'},
    {id:'c2',strong:'Meta description is 120–160 characters',detail:'Has a clear call-to-action. Not duplicated from title.'},
    {id:'c3',strong:'One H1 per page',detail:'Contains the primary keyword. Not the same as title tag.'},
    {id:'c4',strong:'H2–H3 headings structure the content',detail:'Logical hierarchy, not used just for visual styling.'},
    {id:'c5',strong:'Target keyword appears in the first 100 words',detail:'Natural usage — not keyword stuffed.'},
  ]},
  {group:'Technical SEO', items:[
    {id:'c6',strong:'Canonical tag is correct',detail:'Points to the preferred URL. No self-referencing loops.'},
    {id:'c7',strong:'Page returns HTTP 200',detail:'No accidental 404s or redirect chains to orphan URLs.'},
    {id:'c8',strong:'HTTPS is enforced',detail:'HTTP version redirects to HTTPS. No mixed content.'},
    {id:'c9',strong:'robots.txt allows crawling',detail:'No accidental Disallow: / on production.'},
    {id:'c10',strong:'sitemap.xml is submitted to Google Search Console',detail:'Updated automatically. No 404 URLs in the sitemap.'},
  ]},
  {group:'Content Quality', items:[
    {id:'c11',strong:'Page has at least 300 words of body content',detail:'Thin content ranks poorly. Expand or consolidate pages.'},
    {id:'c12',strong:'Content answers the search intent',detail:'Informational? Transactional? Navigational? Match it.'},
    {id:'c13',strong:'No duplicate content across pages',detail:'Check with a site: search or screaming frog.'},
    {id:'c14',strong:'Images have descriptive alt text',detail:'Not "image1.jpg" or empty strings. Keyword where relevant.'},
    {id:'c15',strong:'Content is updated within the last 12 months',detail:'Stale content gets de-prioritised by Google.'},
  ]},
  {group:'Links & Authority', items:[
    {id:'c16',strong:'Internal links use descriptive anchor text',detail:'Not "click here". Use the keyword or page topic.'},
    {id:'c17',strong:'No broken internal links (4xx)',detail:'Run the crawler to find and fix them.'},
    {id:'c18',strong:'External links open in new tab and have rel="noopener"',detail:'Security and UX best practice.'},
    {id:'c19',strong:'At least 3 internal links point to this page',detail:'Orphan pages get little crawl budget.'},
    {id:'c20',strong:'Schema markup is present (Article, Product, FAQ, etc.)',detail:'Eligible for rich results in Google SERPs.'},
  ]},
];
var TOTAL_CHK = CHK_ITEMS.reduce(function(s,g){return s+g.items.length;},0);
var CHK_KEY = 'crawliq_checklist_v1';

function loadChecklist(){
  var saved = JSON.parse(localStorage.getItem(CHK_KEY)||'{}');
  var grid = document.getElementById('checklist-grid');
  if(!grid) return;
  grid.innerHTML = CHK_ITEMS.map(function(group){
    return '<div class="chk-group"><div class="chk-group-title" style="color:var(--cyan)">'+group.group+'</div>'+
      group.items.map(function(item){
        return '<div class="chk-item'+(saved[item.id]?' checked':'')+'" id="chkrow-'+item.id+'">'+
          '<input type="checkbox" id="'+item.id+'" '+(saved[item.id]?'checked':'')+' onchange="toggleChk(\''+item.id+'\',this.checked)"/>'+
          '<label for="'+item.id+'"><strong>'+item.strong+'</strong>'+item.detail+'</label>'+
          '</div>';
      }).join('')+'</div>';
  }).join('');
  updateChkProgress();
}
function toggleChk(id, checked){
  var saved = JSON.parse(localStorage.getItem(CHK_KEY)||'{}');
  if(checked) saved[id]=true; else delete saved[id];
  localStorage.setItem(CHK_KEY, JSON.stringify(saved));
  var row = document.getElementById('chkrow-'+id);
  if(row) row.classList.toggle('checked', checked);
  updateChkProgress();
}
function updateChkProgress(){
  var saved = JSON.parse(localStorage.getItem(CHK_KEY)||'{}');
  var done = Object.keys(saved).length;
  var doneEl = document.getElementById('chk-done');
  var labelEl = document.getElementById('chk-label');
  var fillEl = document.getElementById('chk-fill');
  if(doneEl) doneEl.textContent = done;
  if(labelEl) labelEl.textContent = done+' / '+TOTAL_CHK+' done';
  if(fillEl) fillEl.style.width = Math.round((done/TOTAL_CHK)*100)+'%';
}
function resetChecklist(){
  if(!confirm('Reset all checkboxes?')) return;
  localStorage.removeItem(CHK_KEY);
  loadChecklist();
}

document.addEventListener('DOMContentLoaded', function(){ loadChecklist(); });
