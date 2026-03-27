"""
streamlit_app.py — Enhanced Streamlit UI for the SEO Crawler Dashboard.

New in this version:
  P1  Keywords + Keyword Importance + Issues columns in main table
  P2  Live table updates every 2s during crawl (no wait for completion)
  P3  AI popup: Field | Current | Generated | Issue  +  keywords used / missing
  P4  Popup prev/next navigation between pages with issues
  P5  Non-blocking AI trigger via threading — UI stays fully responsive
  P6  Export button inside popup (AI results for current page → Excel)

Connects to FastAPI backend at http://localhost:8000.
Does NOT modify any backend file.

Run:
    python main.py                     # Terminal 1 — FastAPI on port 8000
    streamlit run streamlit_app.py     # Terminal 2 — Streamlit on port 8501
"""

import io
import threading
import time
from collections import Counter

import pandas as pd
import requests
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────
API           = "http://localhost:8000"
POLL_INTERVAL = 2
AI_POLL_MAX   = 120

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SEO Crawler Dashboard",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .stApp { background-color: #0b0e11; color: #e2e8f0; }
  .block-container { padding-top: 1.2rem; padding-bottom: 3rem; }
  h1,h2,h3 { color: #00e5a0; }
  .stButton > button {
    background: #00e5a0; color: #000; font-weight: 700;
    border: none; border-radius: 6px;
  }
  .stButton > button:hover { opacity: 0.85; }
  .metric-card {
    background: #121620; border: 1px solid #1e2530;
    border-radius: 6px; padding: 12px; text-align: center;
  }
  .metric-val { font-size: 2rem; font-weight: 800; color: #00e5a0; }
  .metric-lbl { font-size: 0.68rem; color: #718096;
                text-transform: uppercase; letter-spacing: 1px; }
  .issue-tag {
    display: inline-block; background: rgba(255,77,106,.1);
    color: #ff4d6a; border: 1px solid rgba(255,77,106,.2);
    font-size: 0.7rem; padding: 2px 6px; border-radius: 3px; margin: 1px;
  }
  .kw-high {
    display: inline-block; background: rgba(0,229,160,.12);
    color: #00e5a0; border: 1px solid rgba(0,229,160,.25);
    font-size: 0.68rem; padding: 2px 6px; border-radius: 3px; margin: 1px;
    font-weight: 700;
  }
  .kw-medium {
    display: inline-block; background: rgba(255,209,102,.1);
    color: #ffd166; border: 1px solid rgba(255,209,102,.25);
    font-size: 0.68rem; padding: 2px 6px; border-radius: 3px; margin: 1px;
  }
  .kw-low {
    display: inline-block; background: rgba(113,128,150,.1);
    color: #a0aec0; border: 1px solid rgba(113,128,150,.2);
    font-size: 0.68rem; padding: 2px 5px; border-radius: 3px; margin: 1px;
  }
  .comp-high   { display:inline-block;padding:2px 7px;border-radius:4px;
                 font-size:0.7rem;font-weight:700;
                 background:rgba(255,77,106,.12);color:#ff4d6a; }
  .comp-medium { display:inline-block;padding:2px 7px;border-radius:4px;
                 font-size:0.7rem;font-weight:700;
                 background:rgba(255,209,102,.12);color:#ffd166; }
  .comp-low    { display:inline-block;padding:2px 7px;border-radius:4px;
                 font-size:0.7rem;font-weight:700;
                 background:rgba(0,229,160,.12);color:#00e5a0; }
  .live-pill {
    display:inline-block;background:rgba(0,229,160,.15);
    color:#00e5a0;border:1px solid rgba(0,229,160,.3);
    font-size:0.7rem;padding:2px 8px;border-radius:12px;margin-left:8px;
    animation: pulse 1.5s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .popup-header {
    background:#121620;border:1px solid #1e2530;border-radius:8px;
    padding:14px 18px;margin-bottom:12px;
  }
  div[data-testid="stExpander"] { border:1px solid #1e2530 !important; }
</style>
""", unsafe_allow_html=True)


# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(path: str, timeout: int = 10, silent: bool = False) -> dict | None:
    try:
        r = requests.get(f"{API}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if not silent:
            st.error(f"API error ({path}): {e}")
        return None


def api_post(path: str, payload: dict | None = None,
             timeout: int = 10, silent: bool = False) -> dict | None:
    try:
        r = requests.post(f"{API}{path}", json=payload or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if not silent:
            st.error(f"API error ({path}): {e}")
        return None


# ── Formatting helpers ────────────────────────────────────────────────────────

def priority_icon(p: str) -> str:
    return {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(p, "⚪")


def score_icon(s) -> str:
    if not isinstance(s, (int, float)): return "⚪"
    return "🟢" if s >= 70 else ("🟡" if s >= 45 else "🔴")


def render_issue_tags(issues: list) -> str:
    if not issues:
        return "<span style='color:#4a5568;font-size:0.75rem'>None</span>"
    return " ".join(f'<span class="issue-tag">{i}</span>' for i in issues)


def render_kw_importance(kws_scored: list, kws_raw: list) -> str:
    """Keyword badges with HIGH/MED/LOW colour coding."""
    if kws_scored and isinstance(kws_scored[0], dict):
        parts = []
        for s in kws_scored[:8]:
            kw  = s.get("keyword", "")
            imp = s.get("importance", "LOW")
            cls = {"HIGH": "kw-high", "MEDIUM": "kw-medium"}.get(imp, "kw-low")
            lbl = imp[:3]
            parts.append(f'<span class="{cls}">{kw} <b>{lbl}</b></span>')
        return " ".join(parts) if parts else "<span style='color:#4a5568'>—</span>"
    if kws_raw:
        return " ".join(f'<span class="kw-low">{k}</span>' for k in kws_raw[:6])
    return "<span style='color:#4a5568'>—</span>"


def comp_badge(level: str) -> str:
    cls = {"High": "comp-high", "Medium": "comp-medium",
           "Low": "comp-low"}.get(level, "comp-medium")
    return f'<span class="{cls}">{level}</span>'


def export_page_ai_to_excel(page: dict) -> bytes | None:
    """Build an Excel file for one page's AI results (fixes + generated content)."""
    gc     = page.get("generated_content") or {}
    fields = page.get("gemini_fields") or []
    rows   = []

    for f in fields:
        rows.append({
            "Section":   "AI Fix",
            "Field":     f.get("name", ""),
            "Issue":     f.get("issue", ""),
            "Current":   f.get("current", ""),
            "Generated": f.get("example") or f.get("fix", ""),
            "Impact":    f.get("impact", ""),
            "Why":       f.get("why", ""),
        })

    if gc:
        for fname, fval in [
            ("Title",     gc.get("title", "")),
            ("Meta",      gc.get("meta", "")),
            ("H1",        gc.get("h1", "")),
            ("H2",        " | ".join(gc.get("h2") or [])),
            ("H3",        " | ".join(gc.get("h3") or [])),
            ("Canonical", gc.get("canonical", "")),
            ("Paragraph", gc.get("content", "")),
        ]:
            if fval:
                rows.append({"Section": "Generated Content", "Field": fname,
                             "Issue": "", "Current": "", "Generated": fval,
                             "Impact": "", "Why": gc.get("reason", "")})
        rows.append({"Section": "Keywords", "Field": "Used",
                     "Generated": ", ".join(gc.get("keywords_used") or []),
                     "Issue": "", "Current": "", "Impact": "", "Why": ""})
        rows.append({"Section": "Keywords", "Field": "Missing",
                     "Generated": ", ".join(gc.get("keywords_missing") or []),
                     "Issue": "", "Current": "", "Impact": "", "Why": ""})

    if not rows:
        return None

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="AI Results")
    buf.seek(0)
    return buf.getvalue()


# ── Session state ─────────────────────────────────────────────────────────────
_ss = st.session_state
for k, v in {
    "crawl_done":    False,
    "crawl_running": False,
    "results":       [],
    "ai_running":    False,
    "ai_done":       False,
    "opt_done":      False,
    "opt_rows":      [],
    "selected_urls": set(),
    "cgen_running":  False,
    "cgen_done":     False,
    "_popup_idx":    0,
}.items():
    if k not in _ss:
        _ss[k] = v


# ════════════════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════════════════
hc1, hc2 = st.columns([3, 1])
with hc1:
    live = ('<span class="live-pill">● LIVE</span>'
            if _ss.crawl_running or _ss.ai_running else "")
    st.markdown(f"# 🔍 SEO Crawler Dashboard{live}", unsafe_allow_html=True)
    st.markdown(
        "<span style='color:#718096;font-size:0.8rem'>"
        "Streamlit UI · FastAPI backend · Live crawl updates</span>",
        unsafe_allow_html=True,
    )
with hc2:
    health = api_get("/gemini-health", silent=True) or {}
    provider = health.get("provider", "groq").upper()
    model    = health.get("model", "")
    if health.get("configured"):
        st.success(f"✨ AI ({provider}) {model}")
    else:
        st.warning(f"AI ({provider}): set API key — see console")

st.divider()


# ════════════════════════════════════════════════════════════════════════════
# INPUT PANEL
# ════════════════════════════════════════════════════════════════════════════
ic = st.columns([4, 1, 1, 1, 1, 1])
with ic[0]:
    url_input = st.text_input("URL", placeholder="https://example.com",
                              label_visibility="collapsed")
with ic[1]:
    max_pages = st.number_input("Max", min_value=1, max_value=100,
                                value=50, label_visibility="collapsed")
with ic[2]:
    crawl_btn = st.button("▶ Crawl", use_container_width=True,
                          disabled=_ss.crawl_running)
with ic[3]:
    ai_btn = st.button("✨ AI (All)", use_container_width=True,
                       disabled=not _ss.crawl_done or _ss.ai_running)
with ic[4]:
    opt_btn = st.button("⚡ Optimize", use_container_width=True,
                        disabled=not _ss.crawl_done)
with ic[5]:
    cgen_btn = st.button("🖊 Generate", use_container_width=True,
                         disabled=not _ss.crawl_done or _ss.cgen_running)


# ════════════════════════════════════════════════════════════════════════════
# CRAWL — start + P2: live table updates
# ════════════════════════════════════════════════════════════════════════════
if crawl_btn and url_input:
    url = url_input.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Reset
    for k, v in {
        "crawl_done": False, "crawl_running": False, "results": [],
        "ai_done": False, "ai_running": False,
        "opt_done": False, "opt_rows": [],
        "cgen_done": False, "cgen_running": False,
        "selected_urls": set(), "_popup_idx": 0,
    }.items():
        _ss[k] = v

    resp = api_post("/crawl", {"url": url, "max_pages": int(max_pages)})
    if resp:
        _ss.crawl_running = True
        prog   = st.progress(0.0, text="Starting crawl…")
        status = st.empty()

        while True:
            s       = api_get("/crawl-status", silent=True) or {}
            crawled = s.get("pages_crawled", 0)
            queued  = s.get("pages_queued",  0)
            errors  = s.get("errors",        0)
            elapsed = s.get("elapsed_s",     0)
            ssl_fb  = s.get("ssl_fallbacks",  0)
            cur_url = (s.get("current_url") or "")[:55]
            total   = max(crawled + queued, 1)
            pct     = min(crawled / total, 1.0)

            prog.progress(
                pct,
                text=f"Crawling… {crawled}/{total} pages · {elapsed}s"
                + (f" · SSL fallbacks: {ssl_fb}" if ssl_fb else "")
                + (f" · {cur_url}" if cur_url else ""),
            )

            # P2: pull partial results — table below re-renders from session_state
            live_data = api_get("/results/live", silent=True) or {}
            partial   = live_data.get("results", [])
            if partial:
                _ss.results = partial

            status.markdown(
                f"**Crawled:** {crawled} · **Queued:** {queued} · "
                f"**Errors:** {errors}"
                + (f" · **Loaded so far:** {len(partial)}" if partial else ""),
            )

            if s.get("error"):
                prog.empty()
                st.error(f"Crawl failed: {s['error']}")
                _ss.crawl_running = False
                break

            if s.get("done"):
                prog.progress(
                    1.0,
                    text=f"✓ Complete — {crawled} pages · {elapsed}s"
                    + (f" · {ssl_fb} SSL fallback(s)" if ssl_fb else ""),
                )
                status.empty()
                _ss.crawl_running = False
                _ss.crawl_done    = True
                final = api_get("/results", silent=True) or {}
                _ss.results = final.get("results", [])
                st.rerun()

            time.sleep(POLL_INTERVAL)


# ════════════════════════════════════════════════════════════════════════════
# P5 — NON-BLOCKING AI (background thread)
# ════════════════════════════════════════════════════════════════════════════

def _bg_ai(urls: list | None = None):
    """Runs AI endpoint in background. Updates session state when done."""
    _ss.ai_running = True
    _ss.ai_done    = False
    try:
        if urls:
            api_post("/analyze-selected", {"urls": urls}, silent=True)
        else:
            api_post("/analyze-gemini", silent=True)
        deadline = time.time() + AI_POLL_MAX
        while time.time() < deadline:
            s = api_get("/gemini-status", silent=True) or {}
            if s.get("done") or s.get("error"):
                break
            time.sleep(2)
        final      = api_get("/results", silent=True) or {}
        _ss.results = final.get("results", [])
        _ss.ai_done = True
    except Exception:
        pass
    finally:
        _ss.ai_running = False


if ai_btn and _ss.crawl_done and not _ss.ai_running:
    urls = list(_ss.selected_urls) or None
    threading.Thread(target=_bg_ai, args=(urls,), daemon=True).start()
    _ss.ai_running = True
    st.toast("✨ AI analysis running in background — UI stays responsive", icon="✨")
    st.rerun()

if _ss.ai_running:
    s = api_get("/gemini-status", silent=True) or {}
    proc  = s.get("processed", 0)
    total = s.get("total", 1)
    st.progress(
        min(proc / max(total, 1), 1.0),
        text=f"✨ AI running in background… {proc}/{total} pages · "
             "table updates automatically when complete",
    )


# Optimizer
if opt_btn and _ss.crawl_done:
    payload = {"urls": list(_ss.selected_urls)} if _ss.selected_urls else {}
    if api_post("/optimize", payload):
        pb = st.progress(0.0, text="⚡ Generating optimization table…")
        while True:
            s = api_get("/optimize-status", silent=True) or {}
            proc  = s.get("processed", 0)
            total = s.get("total", 1)
            pb.progress(min(proc / max(total, 1), 1.0),
                        text=f"⚡ Optimizing: {proc}/{total}…")
            if s.get("done") or s.get("error"):
                break
            time.sleep(POLL_INTERVAL)
        pb.empty()
        ot           = api_get("/optimize-table", silent=True) or {}
        _ss.opt_rows = ot.get("rows", [])
        _ss.opt_done = True
        st.rerun()


# Content generation (background)
def _bg_cgen():
    _ss.cgen_running = True
    try:
        api_post("/generate-content", silent=True)
        deadline = time.time() + AI_POLL_MAX
        while time.time() < deadline:
            s = api_get("/content-gen-status", silent=True) or {}
            if s.get("done") or s.get("error"):
                break
            time.sleep(2)
        final       = api_get("/results", silent=True) or {}
        _ss.results  = final.get("results", [])
        _ss.cgen_done = True
    except Exception:
        pass
    finally:
        _ss.cgen_running = False

if cgen_btn and _ss.crawl_done and not _ss.cgen_running:
    threading.Thread(target=_bg_cgen, daemon=True).start()
    _ss.cgen_running = True
    st.toast("🖊 Content generation started in background", icon="🖊")
    st.rerun()

if _ss.cgen_running:
    s = api_get("/content-gen-status", silent=True) or {}
    st.progress(
        min(s.get("processed", 0) / max(s.get("total", 1), 1), 1.0),
        text=f"🖊 Generating content… {s.get('processed',0)}/{s.get('total',0)} pages",
    )


# ════════════════════════════════════════════════════════════════════════════
# SUMMARY CARDS
# ════════════════════════════════════════════════════════════════════════════
results = _ss.results

if results:
    wi    = [r for r in results if r.get("issues")]
    high  = sum(1 for r in results if r.get("priority") == "High")
    med   = sum(1 for r in results if r.get("priority") == "Medium")
    low   = sum(1 for r in results if r.get("priority") == "Low")
    clean = sum(1 for r in results if not r.get("issues"))

    for col, (lbl, val, color) in zip(st.columns(6), [
        ("Total Pages",   len(results), "#00e5a0"),
        ("With Issues",   len(wi),      "#ff4d6a"),
        ("High Priority", high,         "#ffd166"),
        ("Medium",        med,          "#a78bfa"),
        ("Low",           low,          "#00e5a0"),
        ("Clean",         clean,        "#06d6a0"),
    ]):
        col.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-val" style="color:{color}">{val}</div>'
            f'<div class="metric-lbl">{lbl}</div></div>',
            unsafe_allow_html=True,
        )

    issue_counts = Counter(i for r in results for i in r.get("issues", []))
    if issue_counts:
        st.markdown("### Issue Breakdown")
        st.markdown(
            " ".join(
                f'<span class="issue-tag">{n} × {i}</span>'
                for i, n in sorted(issue_counts.items(), key=lambda x: -x[1])
            ),
            unsafe_allow_html=True,
        )

    st.divider()


# ════════════════════════════════════════════════════════════════════════════
# FILTERS
# ════════════════════════════════════════════════════════════════════════════
if results:
    fc = st.columns([1, 1, 1, 1, 3])
    with fc[0]: issues_only = st.checkbox("Issues only")
    with fc[1]: pf = st.selectbox("Priority", ["All","High","Medium","Low"],
                                  label_visibility="collapsed")
    with fc[2]: ai_only = st.checkbox("AI only")
    with fc[3]: imp_f = st.selectbox("Keyword tier",
                                     ["Any","HIGH","MEDIUM","LOW"],
                                     label_visibility="collapsed")
    with fc[4]: search_q = st.text_input("Search", placeholder="Filter URL / title…",
                                         label_visibility="collapsed")

    rows_f = list(results)
    if issues_only: rows_f = [r for r in rows_f if r.get("issues")]
    if pf != "All": rows_f = [r for r in rows_f if r.get("priority") == pf]
    if ai_only:     rows_f = [r for r in rows_f if r.get("gemini_fields") or
                                                     r.get("generated_content")]
    if imp_f != "Any":
        rows_f = [r for r in rows_f
                  if any(s.get("importance") == imp_f
                         for s in (r.get("keywords_scored") or [])
                         if isinstance(s, dict))]
    if search_q:
        q = search_q.lower()
        rows_f = [r for r in rows_f
                  if q in r.get("url","").lower()
                  or q in (r.get("title","") or "").lower()]

    popup_pages = [r for r in rows_f if r.get("issues")]
    st.markdown(
        f"**{len(rows_f)} / {len(results)} pages**"
        + (f" · {len(popup_pages)} have issues" if popup_pages else ""),
    )


# ════════════════════════════════════════════════════════════════════════════
# P3 + P4 — AI POPUP DIALOG
# ════════════════════════════════════════════════════════════════════════════
if results:

    @st.dialog("✨ Fix Page — AI Analysis", width="large")
    def show_ai_popup(page_list: list, start_idx: int):
        idx  = st.session_state.get("_popup_idx", start_idx)
        idx  = max(0, min(idx, len(page_list) - 1))
        page = page_list[idx]

        url         = page.get("url", "")
        issues      = page.get("issues", [])
        kws_sc      = page.get("keywords_scored") or []
        kws_raw     = page.get("keywords") or []
        gc          = page.get("generated_content") or {}
        gfields     = page.get("gemini_fields") or []
        ranking     = page.get("ranking") or {}
        score       = ranking.get("score", "—")
        gscore      = page.get("gemini_ranking_score")
        competition = page.get("competition", "")

        # Header
        st.markdown(
            f'<div class="popup-header">'
            f'<b style="color:#00e5a0">{url[:80]}</b><br>'
            f'<span style="font-size:0.8rem;color:#718096">'
            f'Score: {score_icon(score)} {score}/100'
            + (f' · AI Score: {gscore}/100' if gscore is not None else "")
            + (f' · Competition: ' + comp_badge(competition) if competition else "")
            + "</span></div>",
            unsafe_allow_html=True,
        )

        if issues:
            st.markdown(render_issue_tags(issues), unsafe_allow_html=True)
            st.markdown("")

        # ── P3: AI Fix table (Field | Current | Generated | Issue) ───────
        if gfields:
            st.markdown("#### ✨ AI Fix Recommendations")
            ai_rows = [
                {
                    "Field":     f.get("name", ""),
                    "Issue":     f.get("issue", "OK"),
                    "Current":   (f.get("current") or "—")[:120],
                    "Generated": (f.get("example") or f.get("fix") or "—")[:250],
                    "Impact":    f.get("impact", ""),
                    "Why":       (f.get("why") or "")[:150],
                }
                for f in gfields
            ]
            st.dataframe(
                pd.DataFrame(ai_rows), use_container_width=True, hide_index=True,
                column_config={
                    "Generated": st.column_config.TextColumn(width="large"),
                    "Why":       st.column_config.TextColumn(width="large"),
                },
            )

        # Generated content
        if gc:
            st.markdown("#### 🖊 Generated SEO Content")
            gen_rows = [
                {"Field": fn, "Generated": fv}
                for fn, fv in [
                    ("Title",     gc.get("title", "")),
                    ("Meta",      gc.get("meta", "")),
                    ("H1",        gc.get("h1", "")),
                    ("H2",        " | ".join(gc.get("h2") or [])),
                    ("H3",        " | ".join(gc.get("h3") or [])),
                    ("Canonical", gc.get("canonical", "")),
                    ("Paragraph", gc.get("content", "")),
                ]
                if fv
            ]
            if gen_rows:
                st.dataframe(
                    pd.DataFrame(gen_rows), use_container_width=True,
                    hide_index=True,
                    column_config={"Generated": st.column_config.TextColumn(width="large")},
                )
            if gc.get("reason"):
                st.caption(f"Reason: {gc['reason']}")

        if not gfields and not gc:
            st.info("No AI results yet. Click **✨ AI (All)** or **🖊 Generate** first.")
            if st.button("✨ Run AI on this page now", key="popup_trigger_ai"):
                threading.Thread(target=_bg_ai, args=([url],), daemon=True).start()
                _ss.ai_running = True
                st.toast("AI started — progress bar will appear above")

        st.markdown("---")

        # Keywords used / missing
        kc1, kc2 = st.columns(2)
        with kc1:
            st.markdown("**Keywords on this page**")
            if kws_sc and isinstance(kws_sc[0], dict):
                for s in kws_sc[:8]:
                    imp = s.get("importance", "LOW")
                    cls = {"HIGH": "kw-high", "MEDIUM": "kw-medium"}.get(imp, "kw-low")
                    st.markdown(
                        f'<span class="{cls}">{s["keyword"]} <b>{imp}</b></span>',
                        unsafe_allow_html=True,
                    )
            elif kws_raw:
                st.markdown(
                    " ".join(f'<span class="kw-low">{k}</span>' for k in kws_raw[:8]),
                    unsafe_allow_html=True,
                )
            else:
                st.caption("None detected")

        with kc2:
            if gc:
                used    = gc.get("keywords_used", [])
                missing = gc.get("keywords_missing", [])
                if used:
                    st.markdown("**Keywords used in generated content**")
                    st.markdown(
                        " ".join(f'<span class="kw-high">{k}</span>' for k in used),
                        unsafe_allow_html=True,
                    )
                if missing:
                    st.markdown("**Keywords not fitted**")
                    st.markdown(
                        " ".join(f'<span class="kw-low">{k}</span>' for k in missing),
                        unsafe_allow_html=True,
                    )

        st.markdown("---")

        # P4: Navigation + P6: Export
        nc = st.columns([1, 1, 2, 1])
        with nc[0]:
            if idx > 0 and st.button("← Prev", key="pp_prev", use_container_width=True):
                st.session_state["_popup_idx"] = idx - 1
                st.rerun()
        with nc[1]:
            if idx < len(page_list) - 1 and st.button("Next →", key="pp_next",
                                                        use_container_width=True):
                st.session_state["_popup_idx"] = idx + 1
                st.rerun()
        with nc[2]:
            st.caption(f"Page {idx + 1} of {len(page_list)} with issues")
        with nc[3]:
            excel = export_page_ai_to_excel(page)
            if excel:
                slug = url.replace("https://","").replace("http://","").replace("/","-")[:30]
                st.download_button(
                    "↓ Export AI",
                    data=excel,
                    file_name=f"ai_{slug}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="pp_export",
                )


    # ── Main table rows ───────────────────────────────────────────────────────
    for row_idx, r in enumerate(rows_f):
        url         = r.get("url", "")
        title       = r.get("title") or "—"
        issues      = r.get("issues", [])
        priority    = r.get("priority", "")
        kws_scored  = r.get("keywords_scored") or []
        kws_raw     = r.get("keywords") or []
        competition = r.get("competition", "")
        ranking     = r.get("ranking") or {}
        score       = ranking.get("score", "—")
        h1          = (r.get("h1") or ["—"])[0]
        status_code = r.get("status_code", "")
        has_ai      = bool(r.get("gemini_fields") or r.get("generated_content"))

        exp_label = (
            f"{priority_icon(priority)} **{url[:65]}**"
            f"  ·  {score_icon(score)} {score}/100"
            f"  ·  {len(issues)} issue(s)"
            + (" ✨" if has_ai else "")
        )

        with st.expander(exp_label, expanded=False):
            lc, rc = st.columns([3, 2])

            with lc:
                st.markdown("**SEO Fields**")
                st.dataframe(
                    pd.DataFrame([
                        {"Field": "URL",       "Value": url},
                        {"Field": "Status",    "Value": str(status_code)},
                        {"Field": "Title",     "Value": title},
                        {"Field": "Meta",      "Value": r.get("meta_description") or "—"},
                        {"Field": "H1",        "Value": h1},
                        {"Field": "H2",        "Value": " | ".join((r.get("h2") or [])[:3]) or "—"},
                        {"Field": "Canonical", "Value": r.get("canonical") or "—"},
                    ]),
                    use_container_width=True, hide_index=True,
                )

            with rc:
                # P1: Issues column
                st.markdown("**Issues**")
                st.markdown(render_issue_tags(issues), unsafe_allow_html=True)

                # P1: Keywords + Importance column
                st.markdown("**Keywords + Importance**")
                st.markdown(
                    render_kw_importance(kws_scored, kws_raw),
                    unsafe_allow_html=True,
                )

                # Competition
                if competition:
                    st.markdown(
                        f"**Competition:** {comp_badge(competition)}",
                        unsafe_allow_html=True,
                    )

                # Score
                if isinstance(score, (int, float)):
                    st.markdown(f"**Score:** {score_icon(score)} **{score}/100**")
                    if ranking.get("feedback"):
                        st.caption(ranking["feedback"])

            # AI one-liner preview
            if has_ai:
                gf = r.get("gemini_fields") or []
                gc = r.get("generated_content") or {}
                preview = []
                if gf:
                    fixes = [f.get("example") or f.get("fix","") for f in gf
                             if f.get("issue","OK") != "OK"]
                    if fixes: preview.append("Fix: " + fixes[0][:80])
                if gc.get("title"):
                    preview.append(f"Gen title: {gc['title'][:60]}")
                if preview:
                    st.markdown(
                        f"<span style='color:#a78bfa;font-size:0.8rem'>"
                        + " · ".join(preview) + "</span>",
                        unsafe_allow_html=True,
                    )

            # P3: Fix Page button → popup
            bc = st.columns([1, 1, 4])
            with bc[0]:
                if issues and st.button(
                    "🔧 Fix Page", key=f"fix_{url}_{row_idx}",
                    use_container_width=True,
                ):
                    pg_idx = next(
                        (i for i, p in enumerate(popup_pages)
                         if p.get("url") == url), 0
                    )
                    st.session_state["_popup_idx"] = pg_idx
                    show_ai_popup(popup_pages, pg_idx)

            with bc[1]:
                if st.button("✨ AI", key=f"ai_{url}_{row_idx}",
                             use_container_width=True):
                    threading.Thread(target=_bg_ai, args=([url],),
                                     daemon=True).start()
                    _ss.ai_running = True
                    st.toast(f"AI started for {url[:40]}…")
                    st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# OPTIMIZER TABLE
# ════════════════════════════════════════════════════════════════════════════
if _ss.opt_rows:
    st.divider()
    st.markdown("## ⚡ Live Optimization Table")
    oc = st.columns([1, 1, 2])
    with oc[0]: ff = st.selectbox("Field", ["All","Title","Meta Description",
                                             "H1","H2","Canonical"], key="opt_f")
    with oc[1]: sf = st.selectbox("Status", ["All","Missing","Too Long",
                                              "Duplicate","Multiple","Mismatch"],
                                  key="opt_s")
    with oc[2]: os_ = st.text_input("Search", placeholder="Filter…",
                                    key="opt_q", label_visibility="collapsed")

    fopt = _ss.opt_rows
    if ff  != "All": fopt = [r for r in fopt if r.get("field") == ff]
    if sf  != "All": fopt = [r for r in fopt if r.get("status") == sf]
    if os_:
        q = os_.lower()
        fopt = [r for r in fopt if q in r.get("url","").lower()
                or q in r.get("optimized_value","").lower()]

    st.markdown(f"**{len(fopt)} rows**")
    if fopt:
        st.dataframe(
            pd.DataFrame([{
                "URL":         r.get("url","")[:60],
                "Field":       r.get("field",""),
                "Status":      r.get("status",""),
                "Current":     r.get("current_value",""),
                "✦ Optimized": r.get("optimized_value",""),
                "SEO Logic":   r.get("seo_logic",""),
            } for r in fopt]),
            use_container_width=True, hide_index=True,
            column_config={
                "✦ Optimized": st.column_config.TextColumn(width="large"),
                "SEO Logic":   st.column_config.TextColumn(width="large"),
            },
        )


# ════════════════════════════════════════════════════════════════════════════
# EXPORT PANEL
# ════════════════════════════════════════════════════════════════════════════
if results and _ss.crawl_done:
    st.divider()
    st.markdown("## ↓ Export")
    ec = st.columns(4)

    with ec[0]:
        if st.button("↓ Full Report", use_container_width=True):
            try:
                resp = requests.get(f"{API}/export", timeout=30)
                resp.raise_for_status()
                st.download_button(
                    "Download seo_report.xlsx", data=resp.content,
                    file_name="seo_report.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as ex:
                st.error(str(ex))

    with ec[1]:
        if st.button("↓ Per-Field Issues", use_container_width=True):
            try:
                resp = requests.get(f"{API}/export-popup", timeout=30)
                resp.raise_for_status()
                st.download_button(
                    "Download seo_issues.xlsx", data=resp.content,
                    file_name="seo_issues.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as ex:
                st.error(str(ex))

    with ec[2]:
        if _ss.opt_rows and st.button("↓ Optimization Table", use_container_width=True):
            try:
                resp = requests.get(f"{API}/export-optimizer", timeout=30)
                resp.raise_for_status()
                st.download_button(
                    "Download optimization.xlsx", data=resp.content,
                    file_name="live_optimization_table.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as ex:
                st.error(str(ex))

    with ec[3]:
        if _ss.cgen_done and st.button("↓ Generated Content", use_container_width=True):
            try:
                resp = requests.get(f"{API}/export-generated-content", timeout=30)
                resp.raise_for_status()
                st.download_button(
                    "Download generated_seo_content.xlsx", data=resp.content,
                    file_name="generated_seo_content.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as ex:
                # Fallback: build in-memory
                rows_exp = []
                for r in results:
                    gc_exp = r.get("generated_content") or {}
                    if gc_exp:
                        rows_exp.append({
                            "URL":     gc_exp.get("url",""),
                            "Title":   gc_exp.get("title",""),
                            "Meta":    gc_exp.get("meta",""),
                            "H1":      gc_exp.get("h1",""),
                            "Content": gc_exp.get("content",""),
                            "Source":  gc_exp.get("_source",""),
                        })
                if rows_exp:
                    buf = io.BytesIO()
                    pd.DataFrame(rows_exp).to_excel(buf, index=False)
                    buf.seek(0)
                    st.download_button(
                        "Download generated_seo_content.xlsx",
                        data=buf.getvalue(),
                        file_name="generated_seo_content.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )


# ════════════════════════════════════════════════════════════════════════════
# FOOTER
# ════════════════════════════════════════════════════════════════════════════
st.divider()
st.markdown(
    "<span style='color:#4a5568;font-size:0.75rem'>"
    "SEO Crawler Dashboard · Streamlit UI · FastAPI backend on localhost:8000"
    "</span>",
    unsafe_allow_html=True,
)
