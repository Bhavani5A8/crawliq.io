"""
streamlit_app_ui.py — Light-Theme SEO Analyzer Dashboard (UI Redesign)

Matches the visual design in the reference screenshots:
  • Image 1 → Home view  : "Start New Analysis" card + "Recent Analyses" list
  • Image 2 → Results view: metric cards, tab strip, scored page rows, AI Fix buttons
  • Images 3-5 → Feature card grid, clean typography, blue / green accent palette

Backend contract (unchanged):
  • All API calls hit http://localhost:8000 exactly as in streamlit_app.py
  • Session-state keys are identical — no backend function was modified
  • Threading, progress polling, AI popup, optimizer table, export panel — all preserved

Run:
    python main.py                          # FastAPI on :8000
    streamlit run streamlit_app_ui.py       # Streamlit on :8501
"""

# ── Imports ───────────────────────────────────────────────────────────────────
import io
import threading
import time
from collections import Counter
from datetime import date as dt_date

import pandas as pd
import requests
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────
API           = "http://localhost:8000"
POLL_INTERVAL = 2
AI_POLL_MAX   = 120

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SEO Analyzer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL CSS  —  Light theme matching reference screenshots
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
/* ── Base ── */
.stApp {
    background-color: #f1f5f9 !important;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    color: #0f172a;
}
.block-container {
    padding-top: 2.2rem !important;
    padding-bottom: 4rem !important;
    max-width: 1180px !important;
}

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
.stDeployButton { display: none; }

/* ── App header ── */
.sa-header-wrap {
    display: flex;
    align-items: center;
    gap: 11px;
    margin-bottom: 3px;
}
.sa-icon-box {
    width: 38px; height: 38px;
    background: #eff6ff;
    border-radius: 9px;
    display: flex; align-items: center; justify-content: center;
    font-size: 19px;
    flex-shrink: 0;
}
.sa-title {
    font-size: 1.55rem;
    font-weight: 700;
    color: #0f172a;
    margin: 0; line-height: 1.2;
}
.sa-subtitle {
    color: #64748b;
    font-size: 0.875rem;
    margin: 0 0 26px 0;
}

/* ── Section label ── */
.sa-section-label {
    font-size: 0.95rem;
    font-weight: 600;
    color: #0f172a;
    margin-bottom: 14px;
}
.sa-section-sublabel {
    font-size: 0.78rem;
    color: #94a3b8;
    margin-bottom: 4px;
}

/* ── Card shell ── */
.sa-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 22px 26px 20px;
    margin-bottom: 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

/* ── Metric cards ── */
.metric-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 20px 18px;
    text-align: left;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.metric-label {
    font-size: 0.78rem;
    color: #64748b;
    font-weight: 500;
    margin-bottom: 8px;
    letter-spacing: 0;
}
.metric-value        { font-size: 2rem; font-weight: 700; color: #0f172a; line-height: 1; }
.metric-value.blue   { color: #2563eb; }
.metric-value.amber  { color: #d97706; }
.metric-value.red    { color: #dc2626; }
.metric-value.green  { color: #059669; }

/* ── Score badges (used in page rows) ── */
.score-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 64px;
    padding: 6px 10px;
    border-radius: 9px;
    font-size: 0.88rem;
    font-weight: 700;
    white-space: nowrap;
    line-height: 1;
}
.score-red   { background: #fee2e2; color: #dc2626; }
.score-amber { background: #fef3c7; color: #d97706; }
.score-green { background: #d1fae5; color: #059669; }
.score-grey  { background: #f1f5f9; color: #64748b; }

/* ── Status badges ── */
.badge {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 3px 11px;
    border-radius: 20px;
    letter-spacing: 0.02em;
}
.badge-completed { background: #d1fae5; color: #059669; }
.badge-running   { background: #dbeafe; color: #2563eb; animation: blink 1.4s infinite; }
.badge-error     { background: #fee2e2; color: #dc2626; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.45} }

/* ── Issue tags ── */
.issue-tag {
    display: inline-block;
    background: #fee2e2;
    color: #b91c1c;
    border: 1px solid #fecaca;
    font-size: 0.71rem;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 5px;
    margin: 2px 3px 2px 0;
}
.issue-tag-warn {
    display: inline-block;
    background: #fef3c7;
    color: #92400e;
    border: 1px solid #fde68a;
    font-size: 0.71rem;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 5px;
    margin: 2px 3px 2px 0;
}
.issue-more {
    display: inline-block;
    background: #f1f5f9;
    color: #64748b;
    border: 1px solid #e2e8f0;
    font-size: 0.71rem;
    padding: 2px 7px;
    border-radius: 5px;
    margin: 2px 3px 2px 0;
}

/* ── Keyword pills ── */
.kw-pill      { display:inline-block; background:#f0fdf4; color:#15803d; border:1px solid #bbf7d0; font-size:0.71rem; padding:2px 8px; border-radius:5px; margin:2px 3px 2px 0; }
.kw-pill-high { display:inline-block; background:#f0fdf4; color:#15803d; border:1px solid #86efac; font-size:0.71rem; font-weight:700; padding:2px 8px; border-radius:5px; margin:2px 3px 2px 0; }
.kw-pill-med  { display:inline-block; background:#fefce8; color:#854d0e; border:1px solid #fef08a; font-size:0.71rem; padding:2px 8px; border-radius:5px; margin:2px 3px 2px 0; }
.kw-pill-low  { display:inline-block; background:#f8fafc; color:#64748b; border:1px solid #e2e8f0; font-size:0.71rem; padding:2px 8px; border-radius:5px; margin:2px 3px 2px 0; }

/* ── Competition badges ── */
.comp-high   { background:#fee2e2; color:#dc2626; padding:2px 9px; border-radius:4px; font-size:0.72rem; font-weight:600; display:inline-block; }
.comp-medium { background:#fef3c7; color:#d97706; padding:2px 9px; border-radius:4px; font-size:0.72rem; font-weight:600; display:inline-block; }
.comp-low    { background:#d1fae5; color:#059669; padding:2px 9px; border-radius:4px; font-size:0.72rem; font-weight:600; display:inline-block; }

/* ── Page-row card ── */
.page-row {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 9px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.page-row-url  { font-size: 0.88rem; font-weight: 600; color: #0f172a; margin-bottom: 3px; word-break: break-all; }
.page-row-meta { font-size: 0.78rem; color: #64748b; margin-bottom: 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 680px; }

/* ── Recent-analysis row ── */
.recent-row {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 14px;
    cursor: pointer;
    transition: box-shadow 0.15s;
}
.recent-row:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.recent-row-icon {
    width: 32px; height: 32px;
    background: #f1f5f9;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; flex-shrink: 0;
}
.recent-row-url   { font-size: 0.88rem; font-weight: 600; color: #0f172a; }
.recent-row-meta  { font-size: 0.76rem; color: #94a3b8; margin-top: 2px; }
.recent-row-arrow { color: #94a3b8; font-size: 1.1rem; margin-left: auto; }

/* ── Feature cards (bottom section) ── */
.feat-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 22px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.feat-icon {
    width: 36px; height: 36px;
    background: #eff6ff;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 17px;
    margin-bottom: 12px;
}
.feat-title { font-size: 0.92rem; font-weight: 600; color: #0f172a; margin-bottom: 6px; }
.feat-desc  { font-size: 0.8rem; color: #64748b; line-height: 1.5; }

/* ── Tab strip (CSS-only, visual) ── */
.tab-pill-active   { background:#2563eb; color:#fff; padding:6px 18px; border-radius:20px; font-size:0.875rem; font-weight:600; display:inline-block; margin-right:8px; cursor:pointer; }
.tab-pill-inactive { background:#f1f5f9; color:#64748b; border:1px solid #e2e8f0; padding:6px 18px; border-radius:20px; font-size:0.875rem; font-weight:500; display:inline-block; margin-right:8px; cursor:pointer; }

/* ── Optimizer status chips ── */
.opt-missing  { background:#fee2e2; color:#dc2626; padding:2px 8px; border-radius:4px; font-size:0.74rem; font-weight:600; display:inline-block; }
.opt-toolong  { background:#fef3c7; color:#d97706; padding:2px 8px; border-radius:4px; font-size:0.74rem; font-weight:600; display:inline-block; }
.opt-dup      { background:#ede9fe; color:#7c3aed; padding:2px 8px; border-radius:4px; font-size:0.74rem; font-weight:600; display:inline-block; }
.opt-mismatch { background:#ffedd5; color:#c2410c; padding:2px 8px; border-radius:4px; font-size:0.74rem; font-weight:600; display:inline-block; }

/* ── AI popup header ── */
.popup-url-bar {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 9px;
    padding: 13px 16px;
    margin-bottom: 14px;
}

/* ── Buttons override ── */
.stButton > button {
    background: #2563eb !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    border: none !important;
    border-radius: 8px !important;
    font-size: 0.875rem !important;
    transition: background 0.15s !important;
}
.stButton > button:hover   { background: #1d4ed8 !important; opacity: 1 !important; }
.stButton > button:disabled{ background: #cbd5e1 !important; color: #94a3b8 !important; }

/* ── Inputs / selects ── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    border-radius: 8px !important;
    border: 1px solid #e2e8f0 !important;
    background: #ffffff !important;
    color: #0f172a !important;
    font-size: 0.9rem !important;
}
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {
    border-color: #2563eb !important;
    box-shadow: 0 0 0 3px rgba(37,99,235,0.1) !important;
}
.stSelectbox > div > div {
    border-radius: 8px !important;
    border: 1px solid #e2e8f0 !important;
    background: #ffffff !important;
}

/* ── Progress bar ── */
.stProgress > div > div { background: #2563eb !important; border-radius: 4px; }

/* ── Dividers ── */
hr { border-color: #e2e8f0 !important; margin: 18px 0 !important; }

/* ── Expanders (detail panel) ── */
div[data-testid="stExpander"] {
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
    background: #ffffff !important;
}

/* ── Toast ── */
div[data-testid="stToast"] { background: #0f172a !important; color: #fff !important; }

/* ── Success / info / warning boxes ── */
.stSuccess { background: #f0fdf4 !important; border-color: #86efac !important; color: #15803d !important; border-radius: 8px !important; }
.stWarning { background: #fffbeb !important; border-color: #fde68a !important; color: #92400e !important; border-radius: 8px !important; }
.stInfo    { background: #eff6ff !important; border-color: #bfdbfe !important; color: #1d4ed8 !important; border-radius: 8px !important; }

/* ── Checkbox ── */
.stCheckbox label { color: #334155 !important; font-size: 0.875rem; }

/* ── Dataframe ── */
.stDataFrame { border-radius: 8px !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE  —  same keys as streamlit_app.py + view routing extras
# ══════════════════════════════════════════════════════════════════════════════
_ss = st.session_state
for _k, _v in {
    # View routing
    "view":             "home",   # "home" | "results"
    "active_tab":       "all",    # "all"  | "issues"
    # Crawl
    "crawl_done":       False,
    "crawl_running":    False,
    "results":          [],
    "crawl_url":        "",
    "crawl_date":       "",
    "crawl_page_count": 0,
    # AI
    "ai_running":       False,
    "ai_done":          False,
    # Optimizer
    "opt_done":         False,
    "opt_rows":         [],
    # Content gen
    "cgen_running":     False,
    "cgen_done":        False,
    # Popup
    "_popup_idx":       0,
    # Misc
    "selected_urls":    set(),
    # Recent analyses history (persists within session)
    "recent_analyses":  [],
}.items():
    if _k not in _ss:
        _ss[_k] = _v


# ══════════════════════════════════════════════════════════════════════════════
# API HELPERS  —  identical to streamlit_app.py
# ══════════════════════════════════════════════════════════════════════════════

def api_get(path: str, timeout: int = 10, silent: bool = False):
    """GET request to FastAPI backend. Returns parsed JSON or None."""
    try:
        r = requests.get(f"{API}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if not silent:
            st.error(f"API error ({path}): {e}")
        return None


def api_post(path: str, payload: dict | None = None,
             timeout: int = 10, silent: bool = False):
    """POST request to FastAPI backend. Returns parsed JSON or None."""
    try:
        r = requests.post(f"{API}{path}", json=payload or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if not silent:
            st.error(f"API error ({path}): {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# FORMAT / RENDER HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _score_css(score) -> str:
    """Return CSS class name for a numeric score."""
    if not isinstance(score, (int, float)):
        return "score-grey"
    if score >= 75:
        return "score-green"
    if score >= 50:
        return "score-amber"
    return "score-red"


def score_badge_html(score) -> str:
    """Render a colored score badge: '88/100' in green, '40/100' in red."""
    css = _score_css(score)
    label = f"{score}/100" if isinstance(score, (int, float)) else "—"
    return f'<span class="score-badge {css}">{label}</span>'


def issue_tags_html(issues: list, max_show: int = 3) -> str:
    """
    Render up to max_show issue tags.
    Yellow for length/duplicate/mismatch warnings; red for missing/broken.
    """
    if not issues:
        return ""
    _warn = {"too long", "too short", "duplicate", "mismatch", "multiple"}
    parts = []
    for iss in issues[:max_show]:
        cls = "issue-tag-warn" if any(w in iss.lower() for w in _warn) else "issue-tag"
        parts.append(f'<span class="{cls}">{iss}</span>')
    rest = len(issues) - max_show
    if rest > 0:
        parts.append(f'<span class="issue-more">+{rest} more</span>')
    return " ".join(parts)


def keyword_pills_html(kws_scored: list, kws_raw: list, max_kw: int = 6) -> str:
    """
    Render keyword pills with HIGH/MEDIUM/LOW color coding.
    Falls back to plain green pills when scored list is unavailable.
    """
    if kws_scored and isinstance(kws_scored[0], dict):
        parts = []
        for s in kws_scored[:max_kw]:
            kw  = s.get("keyword", "")
            imp = s.get("importance", "LOW")
            cls = {"HIGH": "kw-pill-high", "MEDIUM": "kw-pill-med"}.get(imp, "kw-pill-low")
            parts.append(f'<span class="{cls}">{kw}</span>')
        return " ".join(parts) if parts else ""
    if kws_raw:
        return " ".join(f'<span class="kw-pill">{k}</span>' for k in kws_raw[:max_kw])
    return ""


def comp_badge_html(level: str) -> str:
    """Render a competition-level badge."""
    cls = {"High": "comp-high", "Medium": "comp-medium",
           "Low": "comp-low"}.get(level, "comp-medium")
    return f'<span class="{cls}">{level}</span>'


def priority_icon(p: str) -> str:
    return {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(p, "⚪")


def opt_status_html(status: str) -> str:
    """Colored chip for optimization row status."""
    cls = {
        "Missing":   "opt-missing",
        "Too Long":  "opt-toolong",
        "Duplicate": "opt-dup",
        "Multiple":  "opt-toolong",
        "Mismatch":  "opt-mismatch",
    }.get(status, "opt-missing")
    return f'<span class="{cls}">{status}</span>'


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT HELPER  —  identical to streamlit_app.py
# ══════════════════════════════════════════════════════════════════════════════

def export_page_ai_to_excel(page: dict) -> bytes | None:
    """Build an in-memory Excel file for one page's AI results."""
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


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND THREADS  —  identical logic to streamlit_app.py
# ══════════════════════════════════════════════════════════════════════════════

def _bg_ai(urls: list | None = None):
    """Run AI analysis in background thread. Keeps UI responsive (P5)."""
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


def _bg_cgen():
    """Run content generation in background thread."""
    _ss.cgen_running = True
    try:
        api_post("/generate-content", silent=True)
        deadline = time.time() + AI_POLL_MAX
        while time.time() < deadline:
            s = api_get("/content-gen-status", silent=True) or {}
            if s.get("done") or s.get("error"):
                break
            time.sleep(2)
        final        = api_get("/results", silent=True) or {}
        _ss.results  = final.get("results", [])
        _ss.cgen_done = True
    except Exception:
        pass
    finally:
        _ss.cgen_running = False


# ══════════════════════════════════════════════════════════════════════════════
# AI POPUP DIALOG  —  same logic as streamlit_app.py, refreshed light styling
# ══════════════════════════════════════════════════════════════════════════════

@st.dialog("✨ AI Analysis — Fix Page", width="large")
def show_ai_popup(page_list: list, start_idx: int):
    """
    Full-screen dialog:  AI Fix table (P3) + keyword analysis + Prev/Next nav (P4)
    + per-page Excel export button (P6).
    """
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
    score       = ranking.get("score", None)
    competition = page.get("competition", "")

    # ── URL header bar ────────────────────────────────────────────────────────
    score_html_str = score_badge_html(score)
    comp_str = (f"&nbsp;·&nbsp;Competition: {comp_badge_html(competition)}"
                if competition else "")
    st.markdown(
        f'<div class="popup-url-bar">'
        f'<strong style="color:#0f172a;font-size:0.95rem">{url[:90]}</strong>'
        f'<br><span style="font-size:0.8rem;color:#64748b">'
        f'SEO Score: {score_html_str}{comp_str}</span></div>',
        unsafe_allow_html=True,
    )

    # ── Issue tags ─────────────────────────────────────────────────────────────
    if issues:
        st.markdown(issue_tags_html(issues, max_show=len(issues)),
                    unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

    # ── P3: AI Fix Recommendations table ──────────────────────────────────────
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
            pd.DataFrame(ai_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Generated": st.column_config.TextColumn(width="large"),
                "Why":       st.column_config.TextColumn(width="large"),
            },
        )

    # ── Generated SEO Content ─────────────────────────────────────────────────
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
                pd.DataFrame(gen_rows),
                use_container_width=True,
                hide_index=True,
                column_config={"Generated": st.column_config.TextColumn(width="large")},
            )
        if gc.get("reason"):
            st.caption(f"Reason: {gc['reason']}")

    # ── No AI results yet ─────────────────────────────────────────────────────
    if not gfields and not gc:
        st.info("No AI results yet. Click **✨ AI (All)** or use the button below.")
        if st.button("✨ Run AI on this page", key="popup_trigger_ai"):
            threading.Thread(target=_bg_ai, args=([url],), daemon=True).start()
            _ss.ai_running = True
            st.toast("AI started — progress bar will appear in the results view")

    st.divider()

    # ── Keywords panel ────────────────────────────────────────────────────────
    kc1, kc2 = st.columns(2)
    with kc1:
        st.markdown("**Keywords on this page**")
        kw_html = keyword_pills_html(kws_sc, kws_raw, max_kw=10)
        if kw_html:
            st.markdown(kw_html, unsafe_allow_html=True)
        else:
            st.caption("None detected")

    with kc2:
        if gc:
            used    = gc.get("keywords_used", [])
            missing = gc.get("keywords_missing", [])
            if used:
                st.markdown("**Keywords used in generated content**")
                st.markdown(
                    " ".join(f'<span class="kw-pill-high">{k}</span>' for k in used),
                    unsafe_allow_html=True,
                )
            if missing:
                st.markdown("**Keywords not fitted**")
                st.markdown(
                    " ".join(f'<span class="kw-pill-low">{k}</span>' for k in missing),
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── P4: Navigation  +  P6: Per-page Export ───────────────────────────────
    nc = st.columns([1, 1, 3, 1])
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
            slug = url.replace("https://", "").replace("http://", "").replace("/", "-")[:30]
            st.download_button(
                "↓ Export AI",
                data=excel,
                file_name=f"ai_{slug}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="pp_export",
            )


# ══════════════════════════════════════════════════════════════════════════════
# HOME VIEW  ——  "Start New Analysis" card + "Recent Analyses" list
#              Matches Image 1 from reference screenshots
# ══════════════════════════════════════════════════════════════════════════════

def render_home():
    # ── App header (icon + title + subtitle) ──────────────────────────────────
    st.markdown("""
    <div class="sa-header-wrap">
        <div class="sa-icon-box">📈</div>
        <span class="sa-title">SEO Analyzer</span>
    </div>
    <p class="sa-subtitle">Crawl websites, detect SEO issues, and generate AI-powered optimizations</p>
    """, unsafe_allow_html=True)

    # ── AI provider health chip (top-right) ───────────────────────────────────
    health   = api_get("/gemini-health", silent=True) or {}
    provider = health.get("provider", "groq").upper()
    model    = health.get("model", "")
    if health.get("configured"):
        st.success(f"✨ AI ready — {provider} · {model}", icon="✅")
    else:
        st.warning(f"⚠️ AI ({provider}): set API key in environment — see README", icon="⚠️")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Start New Analysis card ───────────────────────────────────────────────
    st.markdown('<div class="sa-card"><div class="sa-section-label">Start New Analysis</div>',
                unsafe_allow_html=True)

    url_col, max_col, btn_col = st.columns([5, 1.4, 1.4])
    with url_col:
        url_input = st.text_input(
            "URL",
            placeholder="https://example.com",
            label_visibility="collapsed",
            key="home_url_input",
        )
    with max_col:
        max_pages = st.selectbox(
            "Max pages",
            options=[5, 10, 25, 50, 100],
            index=1,
            label_visibility="visible",
            key="home_max_pages",
        )
    with btn_col:
        st.markdown("<br>", unsafe_allow_html=True)
        analyze_btn = st.button(
            "🔍  Analyze",
            use_container_width=True,
            key="home_analyze_btn",
        )

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Handle Analyze click ──────────────────────────────────────────────────
    if analyze_btn:
        url = (url_input or "").strip()
        if not url:
            st.error("Please enter a URL before clicking Analyze.")
        else:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url

            # Reset all state
            for _k, _v in {
                "crawl_done": False, "crawl_running": False, "results": [],
                "ai_done": False, "ai_running": False,
                "opt_done": False, "opt_rows": [],
                "cgen_done": False, "cgen_running": False,
                "selected_urls": set(), "_popup_idx": 0,
                "crawl_url": url,
                "crawl_date": str(dt_date.today().strftime("%-m/%-d/%Y"))
                               if hasattr(dt_date.today(), "strftime") else str(dt_date.today()),
                "crawl_page_count": 0,
            }.items():
                _ss[_k] = _v

            resp = api_post("/crawl", {"url": url, "max_pages": int(max_pages)})
            if resp:
                _ss.crawl_running = True
                prog   = st.progress(0.0, text="Starting crawl…")
                status = st.empty()

                # ── P2: Live progress loop ─────────────────────────────────
                while True:
                    s       = api_get("/crawl-status", silent=True) or {}
                    crawled = s.get("pages_crawled", 0)
                    queued  = s.get("pages_queued",  0)
                    errors  = s.get("errors",        0)
                    elapsed = s.get("elapsed_s",     0)
                    ssl_fb  = s.get("ssl_fallbacks",  0)
                    cur_url = (s.get("current_url") or "")[:60]
                    total   = max(crawled + queued, 1)
                    pct     = min(crawled / total, 1.0)

                    prog.progress(
                        pct,
                        text=f"Crawling… {crawled}/{total} pages · {elapsed}s"
                        + (f" · SSL fallbacks: {ssl_fb}" if ssl_fb else "")
                        + (f" · {cur_url}" if cur_url else ""),
                    )

                    live_data = api_get("/results/live", silent=True) or {}
                    partial   = live_data.get("results", [])
                    if partial:
                        _ss.results = partial

                    status.markdown(
                        f"**Crawled:** {crawled} &nbsp;·&nbsp; "
                        f"**Queued:** {queued} &nbsp;·&nbsp; "
                        f"**Errors:** {errors}"
                        + (f" &nbsp;·&nbsp; **Loaded so far:** {len(partial)}"
                           if partial else ""),
                    )

                    if s.get("error"):
                        prog.empty(); status.empty()
                        st.error(f"Crawl failed: {s['error']}")
                        _ss.crawl_running = False
                        break

                    if s.get("done"):
                        prog.progress(
                            1.0,
                            text=f"✓ Crawl complete — {crawled} pages in {elapsed}s"
                            + (f" · {ssl_fb} SSL fallback(s)" if ssl_fb else ""),
                        )
                        status.empty()
                        _ss.crawl_running = False
                        _ss.crawl_done    = True
                        final             = api_get("/results", silent=True) or {}
                        _ss.results       = final.get("results", [])
                        _ss.crawl_page_count = len(_ss.results)

                        # Persist to recent analyses list (home view history)
                        today_str = dt_date.today().strftime("%-m/%-d/%Y") \
                            if hasattr(dt_date.today(), "strftime") else str(dt_date.today())
                        _ss.recent_analyses = [
                            entry for entry in _ss.recent_analyses
                            if entry.get("url") != url
                        ]
                        _ss.recent_analyses.insert(0, {
                            "url":    url,
                            "status": "Completed",
                            "pages":  _ss.crawl_page_count,
                            "date":   today_str,
                        })
                        _ss.recent_analyses = _ss.recent_analyses[:10]

                        # Switch to results view
                        _ss.view = "results"
                        st.rerun()

                    time.sleep(POLL_INTERVAL)

    # ── Recent Analyses list ──────────────────────────────────────────────────
    if _ss.recent_analyses:
        st.markdown('<div class="sa-section-label" style="margin-top:8px">Recent Analyses</div>',
                    unsafe_allow_html=True)

        for i, entry in enumerate(_ss.recent_analyses):
            ra_col, ra_btn_col = st.columns([10, 1.6])
            with ra_col:
                badge = (f'<span class="badge badge-completed">Completed</span>'
                         if entry["status"] == "Completed"
                         else f'<span class="badge badge-running">{entry["status"]}</span>')
                st.markdown(
                    f'<div class="recent-row">'
                    f'  <div class="recent-row-icon">🌐</div>'
                    f'  <div>'
                    f'    <div class="recent-row-url">{entry["url"]}</div>'
                    f'    <div class="recent-row-meta">'
                    f'      {badge}'
                    f'      &nbsp; {entry["pages"]} pages · {entry["date"]}'
                    f'    </div>'
                    f'  </div>'
                    f'  <div class="recent-row-arrow">→</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with ra_btn_col:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("View →", key=f"recent_view_{i}", use_container_width=True):
                    _ss.crawl_url  = entry["url"]
                    _ss.crawl_done = True
                    _ss.view       = "results"
                    st.rerun()

    # ── Feature grid  (matches images 3–5: "Everything You Need") ────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<h2 style="text-align:center;font-size:1.6rem;margin-bottom:6px">Everything You Need</h2>'
        '<p style="text-align:center;color:#64748b;font-size:0.88rem;margin-bottom:28px">'
        'A complete SEO pipeline from crawling to AI-generated content, all in one place.</p>',
        unsafe_allow_html=True,
    )

    feat_row1 = st.columns(3)
    feat_row2 = st.columns(3)
    features = [
        ("🌐", "Multi-Page BFS Crawling",
         "Automatically crawls entire websites using breadth-first search, "
         "handling SSL, retries, and bot-blocking."),
        ("⚠️", "Issue Detection",
         "Detects missing titles, meta descriptions, H1/H2/H3 tags, "
         "canonical issues, and broken pages."),
        ("🏷️", "Keyword Extraction",
         "Extracts top keywords with importance scoring (HIGH/MEDIUM/LOW) "
         "based on frequency and position."),
        ("✨", "AI-Powered Optimization",
         "Generates copy-paste ready SEO content: titles, meta descriptions, "
         "headings, and paragraphs."),
        ("📊", "SEO Scoring",
         "Each page gets a 0–100 SEO score based on detected issues, "
         "helping you prioritize fixes."),
        ("📄", "CSV / Excel Export",
         "Export full reports with all SEO fields, issues, keywords, "
         "and AI-generated fixes."),
    ]
    for col, (icon, title, desc) in zip([*feat_row1, *feat_row2], features):
        col.markdown(
            f'<div class="feat-card">'
            f'  <div class="feat-icon">{icon}</div>'
            f'  <div class="feat-title">{title}</div>'
            f'  <div class="feat-desc">{desc}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# RESULTS VIEW  ——  metric cards, tabs, page list, optimizer, export
#                  Matches Image 2 from reference screenshots
# ══════════════════════════════════════════════════════════════════════════════

def render_results():
    results = _ss.results

    # ── Results header row: back btn + URL + badge + Export CSV ───────────────
    h_back, h_url, h_exp = st.columns([1.2, 7, 2])
    with h_back:
        if st.button("← Back", key="results_back"):
            _ss.view = "home"
            st.rerun()
    with h_url:
        badge_html = ('<span class="badge badge-running">● Running</span>'
                      if _ss.crawl_running or _ss.ai_running or _ss.cgen_running
                      else '<span class="badge badge-completed">Completed</span>')
        st.markdown(
            f'<h2 style="font-size:1.2rem;margin:0;display:inline">{_ss.crawl_url or "Results"}</h2>'
            f'&nbsp;&nbsp;{badge_html}',
            unsafe_allow_html=True,
        )
    with h_exp:
        export_csv_btn = st.button("↓ Export CSV", key="header_export_csv",
                                   use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Handle Export CSV from header button
    if export_csv_btn and results:
        try:
            resp = requests.get(f"{API}/export", timeout=30)
            resp.raise_for_status()
            st.download_button(
                "Download seo_report.xlsx",
                data=resp.content,
                file_name="seo_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="header_dl_btn",
            )
        except Exception as ex:
            st.error(str(ex))

    # ── Action buttons row: AI, Optimize, Generate ────────────────────────────
    ab1, ab2, ab3, ab4, _ = st.columns([1.5, 1.5, 1.5, 1.5, 3])
    with ab1:
        ai_btn = st.button(
            "✨ AI (All)",
            use_container_width=True,
            key="results_ai_btn",
            disabled=not _ss.crawl_done or _ss.ai_running,
        )
    with ab2:
        opt_btn = st.button(
            "⚡ Optimize",
            use_container_width=True,
            key="results_opt_btn",
            disabled=not _ss.crawl_done,
        )
    with ab3:
        cgen_btn = st.button(
            "🖊 Generate",
            use_container_width=True,
            key="results_cgen_btn",
            disabled=not _ss.crawl_done or _ss.cgen_running,
        )
    with ab4:
        clear_sel_btn = st.button(
            "✕ Clear Selection",
            use_container_width=True,
            key="results_clear_sel",
            disabled=len(_ss.selected_urls) == 0,
        )

    # Handle action button clicks
    if ai_btn and _ss.crawl_done and not _ss.ai_running:
        urls = list(_ss.selected_urls) or None
        threading.Thread(target=_bg_ai, args=(urls,), daemon=True).start()
        _ss.ai_running = True
        st.toast("✨ AI analysis running in background — table updates automatically",
                 icon="✨")
        st.rerun()

    if opt_btn and _ss.crawl_done:
        payload = {"urls": list(_ss.selected_urls)} if _ss.selected_urls else {}
        if api_post("/optimize", payload):
            pb = st.progress(0.0, text="⚡ Generating optimization table…")
            while True:
                s     = api_get("/optimize-status", silent=True) or {}
                proc  = s.get("processed", 0)
                total = max(s.get("total", 1), 1)
                pb.progress(min(proc / total, 1.0),
                            text=f"⚡ Optimizing: {proc}/{s.get('total', 0)}…")
                if s.get("done") or s.get("error"):
                    break
                time.sleep(POLL_INTERVAL)
            pb.empty()
            ot           = api_get("/optimize-table", silent=True) or {}
            _ss.opt_rows = ot.get("rows", [])
            _ss.opt_done = True
            st.rerun()

    if cgen_btn and _ss.crawl_done and not _ss.cgen_running:
        threading.Thread(target=_bg_cgen, daemon=True).start()
        _ss.cgen_running = True
        st.toast("🖊 Content generation started in background", icon="🖊")
        st.rerun()

    if clear_sel_btn:
        _ss.selected_urls = set()
        st.rerun()

    # ── Progress bars when AI or content-gen is running ───────────────────────
    if _ss.ai_running:
        s     = api_get("/gemini-status", silent=True) or {}
        proc  = s.get("processed", 0)
        total = max(s.get("total", 1), 1)
        st.progress(
            min(proc / total, 1.0),
            text=f"✨ AI running… {proc}/{s.get('total', 0)} pages · "
                 "table updates automatically when complete",
        )
    if _ss.cgen_running:
        s = api_get("/content-gen-status", silent=True) or {}
        st.progress(
            min(s.get("processed", 0) / max(s.get("total", 1), 1), 1.0),
            text=f"🖊 Generating content… {s.get('processed', 0)}/{s.get('total', 0)} pages",
        )

    # ── 4 Metric cards  (matches Image 2) ────────────────────────────────────
    if results:
        pages_with_issues = [r for r in results if r.get("issues")]
        total_issues      = sum(len(r.get("issues", [])) for r in results)
        scores            = [
            (r.get("ranking") or {}).get("score")
            for r in results
            if isinstance((r.get("ranking") or {}).get("score"), (int, float))
        ]
        avg_score = round(sum(scores) / len(scores)) if scores else 0

        mc1, mc2, mc3, mc4 = st.columns(4)
        for col, label, value, css_color in [
            (mc1, "Pages Crawled",     len(results),            "blue"),
            (mc2, "Avg SEO Score",     f"{avg_score}/100",      "amber"),
            (mc3, "Total Issues",      total_issues,            "red"),
            (mc4, "Pages with Issues", len(pages_with_issues),  "green"),
        ]:
            col.markdown(
                f'<div class="metric-card">'
                f'  <div class="metric-label">{label}</div>'
                f'  <div class="metric-value {css_color}">{value}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Issue frequency breakdown ─────────────────────────────────────────
        issue_counts = Counter(i for r in results for i in r.get("issues", []))
        if issue_counts:
            st.markdown(
                " ".join(
                    f'<span class="issue-tag">{n} × {iss}</span>'
                    for iss, n in sorted(issue_counts.items(), key=lambda x: -x[1])
                ),
                unsafe_allow_html=True,
            )
            st.markdown("<br>", unsafe_allow_html=True)

    st.markdown('<hr style="margin:8px 0 18px 0">', unsafe_allow_html=True)

    # ── Tab strip + filter row ────────────────────────────────────────────────
    if results:
        all_count    = len(results)
        issues_count = sum(1 for r in results if r.get("issues"))

        # Tab strip (visual)
        tab_all    = ("tab-pill-active"   if _ss.active_tab == "all"    else "tab-pill-inactive")
        tab_issues = ("tab-pill-active"   if _ss.active_tab == "issues" else "tab-pill-inactive")

        tc1, tc2, tc3 = st.columns([1.8, 2, 6])
        with tc1:
            if st.button(f"All Pages ({all_count})", key="tab_all",
                         use_container_width=True):
                _ss.active_tab = "all"; st.rerun()
        with tc2:
            if st.button(f"With Issues ({issues_count})", key="tab_issues",
                         use_container_width=True):
                _ss.active_tab = "issues"; st.rerun()

        # ── Filter controls ───────────────────────────────────────────────────
        fc1, fc2, fc3, fc4, fc5 = st.columns([1, 1, 1, 1, 3])
        with fc1:
            issues_only = st.checkbox("Issues only", key="flt_issues")
        with fc2:
            pf = st.selectbox("Priority", ["All", "High", "Medium", "Low"],
                              label_visibility="collapsed", key="flt_priority")
        with fc3:
            ai_only = st.checkbox("AI only", key="flt_ai_only")
        with fc4:
            imp_f = st.selectbox("Keyword tier", ["Any", "HIGH", "MEDIUM", "LOW"],
                                 label_visibility="collapsed", key="flt_kw_tier")
        with fc5:
            search_q = st.text_input("Search", placeholder="Filter by URL or title…",
                                     label_visibility="collapsed", key="flt_search")

        # ── Apply filters ─────────────────────────────────────────────────────
        rows_f = list(results)
        if _ss.active_tab == "issues":
            rows_f = [r for r in rows_f if r.get("issues")]
        if issues_only:
            rows_f = [r for r in rows_f if r.get("issues")]
        if pf != "All":
            rows_f = [r for r in rows_f if r.get("priority") == pf]
        if ai_only:
            rows_f = [r for r in rows_f if r.get("gemini_fields") or r.get("generated_content")]
        if imp_f != "Any":
            rows_f = [r for r in rows_f
                      if any(s.get("importance") == imp_f
                             for s in (r.get("keywords_scored") or [])
                             if isinstance(s, dict))]
        if search_q:
            q = search_q.lower()
            rows_f = [r for r in rows_f
                      if q in r.get("url", "").lower()
                      or q in (r.get("title") or "").lower()]

        # Pages that have issues (used by AI popup navigation)
        popup_pages = [r for r in rows_f if r.get("issues")]

        st.markdown(
            f'<span style="font-size:0.82rem;color:#64748b">'
            f'Showing <strong>{len(rows_f)}</strong> of {all_count} pages'
            + (f' · {len(popup_pages)} with issues' if popup_pages else "")
            + "</span>",
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # ══════════════════════════════════════════════════════════════════════
        # PAGE ROWS  —  score badge | URL / meta / issues / keywords | AI Fix
        #               Matches the card list in Image 2
        # ══════════════════════════════════════════════════════════════════════
        for row_idx, r in enumerate(rows_f):
            url         = r.get("url", "")
            title       = r.get("title") or ""
            issues      = r.get("issues", [])
            kws_scored  = r.get("keywords_scored") or []
            kws_raw     = r.get("keywords") or []
            ranking     = r.get("ranking") or {}
            score       = ranking.get("score", None)
            has_ai      = bool(r.get("gemini_fields") or r.get("generated_content"))
            competition = r.get("competition", "")
            priority    = r.get("priority", "")

            # Three-column layout: score | content | actions
            sc_col, content_col, act_col = st.columns([1.1, 8, 2])

            # ── Score badge ──────────────────────────────────────────────────
            with sc_col:
                st.markdown(
                    f'<div style="padding:14px 0 0 4px">{score_badge_html(score)}</div>',
                    unsafe_allow_html=True,
                )

            # ── Page content ─────────────────────────────────────────────────
            with content_col:
                meta_preview = (title or "")[:120]
                issue_html   = issue_tags_html(issues)
                kw_html      = keyword_pills_html(kws_scored, kws_raw, max_kw=5)
                comp_str     = (f"&nbsp;{comp_badge_html(competition)}"
                                if competition else "")
                ai_badge     = ('&nbsp;<span style="color:#7c3aed;font-size:0.72rem">'
                                '✨ AI ready</span>' if has_ai else "")
                pri_str      = (f'&nbsp;<span style="font-size:0.72rem">'
                                f'{priority_icon(priority)} {priority}</span>'
                                if priority else "")

                st.markdown(
                    f'<div class="page-row">'
                    f'  <div class="page-row-url">{url}{ai_badge}{pri_str}{comp_str}</div>'
                    + (f'  <div class="page-row-meta">{meta_preview}</div>'
                       if meta_preview else "")
                    + (f'  <div style="margin:6px 0 4px">{issue_html}</div>'
                       if issue_html else "")
                    + (f'  <div>{kw_html}</div>' if kw_html else "")
                    + "</div>",
                    unsafe_allow_html=True,
                )

            # ── Action buttons ────────────────────────────────────────────────
            with act_col:
                st.markdown("<br>", unsafe_allow_html=True)

                # "AI Fix" button (opens dialog) — shown if page has issues
                if issues:
                    if st.button(
                        "✨ AI Fix",
                        key=f"aifix_{url}_{row_idx}",
                        use_container_width=True,
                    ):
                        pg_idx = next(
                            (i for i, p in enumerate(popup_pages)
                             if p.get("url") == url), 0
                        )
                        st.session_state["_popup_idx"] = pg_idx
                        show_ai_popup(popup_pages, pg_idx)

                # Quick "AI" button — fires AI for this single page
                if st.button(
                    "✨ AI",
                    key=f"ai_single_{url}_{row_idx}",
                    use_container_width=True,
                ):
                    threading.Thread(target=_bg_ai, args=([url],),
                                     daemon=True).start()
                    _ss.ai_running = True
                    st.toast(f"AI started for {url[:45]}…")
                    st.rerun()

            # Visual row separator
            st.markdown(
                '<div style="height:1px;background:#f1f5f9;margin:0"></div>',
                unsafe_allow_html=True,
            )

    elif _ss.crawl_done:
        st.info("No pages match the current filters.")
    else:
        st.info("No crawl data yet. Go back to Home and start an analysis.")

    # ════════════════════════════════════════════════════════════════════════
    # OPTIMIZER TABLE  —  Live Optimization Table (⚡ Optimize button output)
    # ════════════════════════════════════════════════════════════════════════
    if _ss.opt_rows:
        st.markdown('<hr style="margin:24px 0 18px">', unsafe_allow_html=True)
        st.markdown("## ⚡ Live Optimization Table")

        oc1, oc2, oc3 = st.columns([1, 1, 2])
        with oc1:
            ff = st.selectbox("Field",
                              ["All", "Title", "Meta Description", "H1", "H2", "Canonical"],
                              key="opt_field_flt")
        with oc2:
            sf = st.selectbox("Status",
                              ["All", "Missing", "Too Long", "Duplicate", "Multiple", "Mismatch"],
                              key="opt_status_flt")
        with oc3:
            os_ = st.text_input("Search optimizer", placeholder="Filter URL or optimized value…",
                                label_visibility="collapsed", key="opt_search")

        fopt = list(_ss.opt_rows)
        if ff  != "All": fopt = [row for row in fopt if row.get("field")  == ff]
        if sf  != "All": fopt = [row for row in fopt if row.get("status") == sf]
        if os_:
            q = os_.lower()
            fopt = [row for row in fopt
                    if q in row.get("url", "").lower()
                    or q in row.get("optimized_value", "").lower()]

        st.markdown(
            f'<span style="font-size:0.82rem;color:#64748b">'
            f'<strong>{len(fopt)}</strong> rows</span>',
            unsafe_allow_html=True,
        )

        if fopt:
            st.dataframe(
                pd.DataFrame([{
                    "URL":           row.get("url", "")[:60],
                    "Field":         row.get("field", ""),
                    "Status":        row.get("status", ""),
                    "Current":       row.get("current_value", ""),
                    "✦ Optimized":   row.get("optimized_value", ""),
                    "SEO Logic":     row.get("seo_logic", ""),
                } for row in fopt]),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "✦ Optimized": st.column_config.TextColumn(width="large"),
                    "SEO Logic":   st.column_config.TextColumn(width="large"),
                },
            )

    # ════════════════════════════════════════════════════════════════════════
    # EXPORT PANEL  —  full report, per-field issues, optimizer, generated content
    # ════════════════════════════════════════════════════════════════════════
    if results and _ss.crawl_done:
        st.markdown('<hr style="margin:24px 0 18px">', unsafe_allow_html=True)
        st.markdown("## ↓ Export")

        ec1, ec2, ec3, ec4 = st.columns(4)

        with ec1:
            if st.button("↓ Full Report", use_container_width=True, key="exp_full"):
                try:
                    resp = requests.get(f"{API}/export", timeout=30)
                    resp.raise_for_status()
                    st.download_button(
                        "Download seo_report.xlsx",
                        data=resp.content,
                        file_name="seo_report.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_full",
                    )
                except Exception as ex:
                    st.error(str(ex))

        with ec2:
            if st.button("↓ Per-Field Issues", use_container_width=True, key="exp_issues"):
                try:
                    resp = requests.get(f"{API}/export-popup", timeout=30)
                    resp.raise_for_status()
                    st.download_button(
                        "Download seo_issues.xlsx",
                        data=resp.content,
                        file_name="seo_issues.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_issues",
                    )
                except Exception as ex:
                    st.error(str(ex))

        with ec3:
            if _ss.opt_rows and st.button("↓ Optimization Table",
                                          use_container_width=True, key="exp_opt"):
                try:
                    resp = requests.get(f"{API}/export-optimizer", timeout=30)
                    resp.raise_for_status()
                    st.download_button(
                        "Download optimization.xlsx",
                        data=resp.content,
                        file_name="live_optimization_table.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_opt",
                    )
                except Exception as ex:
                    st.error(str(ex))

        with ec4:
            if _ss.cgen_done and st.button("↓ Generated Content",
                                            use_container_width=True, key="exp_cgen"):
                try:
                    resp = requests.get(f"{API}/export-generated-content", timeout=30)
                    resp.raise_for_status()
                    st.download_button(
                        "Download generated_seo_content.xlsx",
                        data=resp.content,
                        file_name="generated_seo_content.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_cgen",
                    )
                except Exception as ex:
                    # In-memory fallback
                    rows_exp = []
                    for r in results:
                        gc_exp = r.get("generated_content") or {}
                        if gc_exp:
                            rows_exp.append({
                                "URL":     gc_exp.get("url", ""),
                                "Title":   gc_exp.get("title", ""),
                                "Meta":    gc_exp.get("meta", ""),
                                "H1":      gc_exp.get("h1", ""),
                                "Content": gc_exp.get("content", ""),
                                "Source":  gc_exp.get("_source", ""),
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
                            key="dl_cgen_fallback",
                        )

    # ── Footer ─────────────────────────────────────────────────────────────────
    st.markdown('<hr style="margin:28px 0 12px">', unsafe_allow_html=True)
    st.markdown(
        '<span style="color:#94a3b8;font-size:0.75rem">'
        'SEO Analyzer · Streamlit UI · FastAPI backend on localhost:8000</span>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ROUTING  —  switch between home and results views
# ══════════════════════════════════════════════════════════════════════════════
if _ss.view == "home":
    render_home()
else:
    render_results()
