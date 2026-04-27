/**
 * CrawlIQ — landing.js v2.2.0
 * Pure SEO layer — zero app references, zero API calls.
 * Hero URL input uses native <form method="GET"> — no JS needed for submission.
 * FAQ uses <details>/<summary> — no JS needed for accordion.
 */
'use strict';

/* ── Meta Tag Character Counter (client-side, zero backend) ── */
(function(){
  document.addEventListener('DOMContentLoaded', function(){
    var titleEl = document.getElementById('af-title');
    var metaEl  = document.getElementById('af-meta');
    if(titleEl){
      titleEl.addEventListener('input', function(){
        var n = titleEl.value.length;
        var hint = document.getElementById('af-title-count');
        if(!hint) return;
        hint.textContent = n + ' characters — ideal: 50–60';
        hint.style.color = (n>=50&&n<=60) ? 'var(--green)' : (n>0&&n<30) ? 'var(--red)' : n>60 ? 'var(--yellow)' : 'var(--muted)';
      });
    }
    if(metaEl){
      metaEl.addEventListener('input', function(){
        var n = metaEl.value.length;
        var hint = document.getElementById('af-meta-count');
        if(!hint) return;
        hint.textContent = n + ' characters — ideal: 120–160';
        hint.style.color = (n>=120&&n<=160) ? 'var(--green)' : (n>0&&n<70) ? 'var(--red)' : n>160 ? 'var(--yellow)' : 'var(--muted)';
      });
    }
  });
})();
