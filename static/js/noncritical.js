/* CrawlIQ noncritical.js v1.0.2 — animations */
/* ══════════════════════════════════════════════════════════════════════════════
   ANIMATION SYSTEM v2 — scroll-reveal · stat counters · panel fade
                        · metric roll · row stagger · progress glow
   ══════════════════════════════════════════════════════════════════════════════ */

/* ── 0. MOBILE SIDEBAR TOGGLE ────────────────────────────────────────────── */
function toggleMobileSidebar() {
  document.body.classList.toggle('sidebar-open');
}
function closeMobileSidebar() {
  document.body.classList.remove('sidebar-open');
}
/* Close sidebar when a nav item is clicked on mobile */
document.querySelectorAll('.sn-item').forEach(btn => {
  btn.addEventListener('click', () => {
    if (window.innerWidth <= 768) closeMobileSidebar();
  });
});
/* Close on resize back to desktop */
window.addEventListener('resize', () => {
  if (window.innerWidth > 768) closeMobileSidebar();
});

/* ── 1. SCROLL-REVEAL (IntersectionObserver) ─────────────────────────────── */
(function initReveal() {
  /* Groups of selectors → reveal direction */
  const groups = [
    { sel: '.sec-hd',                  dir: 'reveal' },
    { sel: '#how-it-works .step-card', dir: 'reveal' },
    { sel: '#features .feat-card',     dir: 'reveal' },
    { sel: '.stat-card',               dir: 'reveal' },
    { sel: '#faq .faq-item',           dir: 'reveal' },
    { sel: '#checklist-sec .chk-group',dir: 'reveal' },
    { sel: '#checklist-sec .sb-item',  dir: 'reveal' },
    { sel: '#analyzer-sec .sa',        dir: 'reveal' },
    { sel: '#example-sec .ex-block',   dir: 'reveal' },
    { sel: '.cta-wrap',                dir: 'reveal' },
    { sel: '#seo-content-sec .feat-card', dir: 'reveal' },
    { sel: '.about-grid > :first-child',  dir: 'reveal-left' },
    { sel: '.about-grid > :last-child',   dir: 'reveal-right' },
  ];

  const io = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      const delay = parseInt(el.dataset.revDelay || '0', 10);
      setTimeout(() => el.classList.add('visible'), delay);
      io.unobserve(el);
    });
  }, { threshold: 0.1, rootMargin: '0px 0px -32px 0px' });

  function applyReveal() {
    groups.forEach(({ sel, dir }) => {
      document.querySelectorAll(sel).forEach((el, i) => {
        /* Skip if already set (avoids double-init) */
        if (el.classList.contains('reveal') ||
            el.classList.contains('reveal-left') ||
            el.classList.contains('reveal-right')) return;
        el.classList.add(dir);
        el.dataset.revDelay = String(i * 75); /* stagger per group */
        io.observe(el);
      });
    });
    /* Also observe elements that already carry reveal classes in HTML (e.g. hero section) */
    document.querySelectorAll('.reveal,.reveal-left,.reveal-right').forEach(el => {
      if (!el.classList.contains('visible') && !el.dataset.revObserved) {
        el.dataset.revObserved = '1';
        io.observe(el);
      }
    });
  }

  /* Run after DOM is ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyReveal);
  } else {
    applyReveal();
  }
})();

/* ── 2. STAT COUNTER ANIMATION (.stat-card .sv) ──────────────────────────── */
function animateCount(el, targetStr, duration) {
  const m = targetStr.match(/^([\d,]+(\.\d+)?)(.*)$/);
  if (!m) { el.textContent = targetStr; return; }
  const num   = parseFloat(m[1].replace(/,/g, ''));
  const dec   = (m[1].includes('.')) ? m[1].split('.')[1].length : 0;
  const suffix = m[3] || '';
  const t0 = performance.now();
  (function tick(ts) {
    const p = Math.min((ts - t0) / duration, 1);
    const e = 1 - Math.pow(1 - p, 3);          /* ease-out-cubic */
    const v = num * e;
    el.textContent = (dec ? v.toFixed(dec) : Math.round(v).toLocaleString()) + suffix;
    if (p < 1) requestAnimationFrame(tick);
    else        el.textContent = targetStr;
  })(t0);
}

(function initStatCounters() {
  const io = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const sv = entry.target.querySelector('.sv');
      if (sv && sv.dataset.target) {
        animateCount(sv, sv.dataset.target, 1400);
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.35 });

  function setup() {
    document.querySelectorAll('.stat-card').forEach(card => {
      const sv = card.querySelector('.sv');
      if (sv && !sv.dataset.target) {
        sv.dataset.target = sv.textContent.trim();
        sv.textContent    = '0';
        io.observe(card);
      }
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
  } else {
    setup();
  }
})();

/* ── 3. DASHBOARD METRIC CARD ROLL (patch set()) ────────────────────────── */
const _ANIM_IDS = new Set([
  's-total','s-issues','s-ok','s-high','s-med','s-low',
  'ts-score','ts-total','ts-indexable'
]);
const _metricPrev = {};

function animateMetricEl(el, newVal) {
  const target = parseInt(String(newVal), 10);
  if (isNaN(target)) { el.textContent = newVal; return; }
  const from   = parseInt(_metricPrev[el.id] || '0', 10) || 0;
  if (from === target) return;
  _metricPrev[el.id] = String(newVal);
  /* flash class for visual feedback */
  el.classList.remove('num-updated');
  void el.offsetWidth;
  el.classList.add('num-updated');
  const t0 = performance.now(), dur = 550;
  (function tick(ts) {
    const p = Math.min((ts - t0) / dur, 1);
    const e = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(from + (target - from) * e);
    if (p < 1) requestAnimationFrame(tick);
    else        el.textContent = newVal;
  })(t0);
}

/* Wrap the existing set() — defined further up in main script */
(function patchSet() {
  const _orig = window.set;
  window.set = function(id, v) {
    if (_ANIM_IDS.has(id)) {
      const el = document.getElementById(id);
      if (el) { animateMetricEl(el, v); return; }
    }
    if (_orig) _orig(id, v);
    else { const el = document.getElementById(id); if (el) el.textContent = v; }
  };
})();

/* ── 4. PANEL-SWITCH FADE TRANSITION ─────────────────────────────────────── */
(function patchShowPanel() {
  const _orig = window.showPanel;
  window.showPanel = function(name) {
    if (_orig) _orig(name);
    /* Animate the newly visible panel */
    if (typeof _PANELS !== 'undefined' && _PANELS[name]) {
      const el = document.getElementById(_PANELS[name].el);
      if (el && !el.classList.contains('panel-hidden')) {
        el.classList.remove('panel-enter');
        void el.offsetWidth;           /* force reflow */
        el.classList.add('panel-enter');
      }
    }
    /* Stagger metric cards when dashboard becomes visible */
    if (name === 'dashboard') staggerMcCards();
  };
})();

/* ── 5. METRIC CARD STAGGER (on panel show + page load) ─────────────────── */
function staggerMcCards() {
  const cards = document.querySelectorAll('#dash-sec .mc');
  cards.forEach((card, i) => {
    card.style.opacity    = '0';
    card.style.transform  = 'translateY(14px)';
    card.style.transition = `opacity .4s ease ${i * 70}ms, transform .4s ease ${i * 70}ms`;
    /* Double rAF ensures transition is active before we clear inline styles */
    requestAnimationFrame(() => requestAnimationFrame(() => {
      card.style.opacity   = '';
      card.style.transform = '';
    }));
  });
}
document.addEventListener('DOMContentLoaded', () => setTimeout(staggerMcCards, 300));

/* ── 6. RESULTS TABLE ROW STAGGER ───────────────────────────────────────── */
(function patchRenderTable() {
  const _orig = window.renderTable;
  window.renderTable = function(rows) {
    if (_orig) _orig(rows);
    requestAnimationFrame(() => {
      const tb = document.getElementById('results-body');
      if (!tb) return;
      Array.from(tb.querySelectorAll('tr')).forEach((tr, i) => {
        tr.style.opacity    = '0';
        tr.style.transform  = 'translateX(-10px)';
        tr.style.transition = `opacity .28s ease ${Math.min(i * 25, 400)}ms,
                               transform .28s ease ${Math.min(i * 25, 400)}ms`;
        requestAnimationFrame(() => requestAnimationFrame(() => {
          tr.style.opacity   = '';
          tr.style.transform = '';
        }));
      });
    });
  };
})();

/* ── 7. REAL-TIME CRAWL COUNTER PULSE ───────────────────────────────────── */
/* Watch #s-total for live changes and animate the progress label */
(function initCrawlPulse() {
  const el = document.getElementById('s-total');
  if (!el || typeof MutationObserver === 'undefined') return;
  let _last = el.textContent;
  const mo = new MutationObserver(() => {
    const curr = el.textContent;
    if (curr !== _last && curr !== '0') {
      _last = curr;
      el.classList.remove('num-updated');
      void el.offsetWidth;
      el.classList.add('num-updated');
    }
  });
  mo.observe(el, { childList: true, characterData: true, subtree: true });
})();
