"""
pdf_export.py — CrawlIQ PDF Report Generator (Phase 5)

Generates a professionally structured PDF audit report using ReportLab.
Sections: Cover Page, Table of Contents, Executive Summary, On-Page SEO,
          Per-Page Audit, Technical SEO, Keywords Analysis.

Public API
──────────
  generate_pdf(crawl_results, crawl_status, output_path) → str
  generate_pdf_bytes(crawl_results, crawl_status) → bytes
"""

from __future__ import annotations

import io
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── ReportLab availability ─────────────────────────────────────────────────────
try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm, cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak, KeepTogether,
    )
    from reportlab.platypus.flowables import HRFlowable
    _REPORTLAB = True
except ImportError:
    _REPORTLAB = False
    logger.warning("reportlab not installed — PDF export unavailable. "
                   "Run: pip install reportlab>=4.0")


# ── Colour palette (matches CrawlIQ dark theme) ───────────────────────────────
if _REPORTLAB:
    _INDIGO = colors.HexColor("#6366F1")
    _CYAN   = colors.HexColor("#22D3EE")
    _GREEN  = colors.HexColor("#10B981")
    _YELLOW = colors.HexColor("#F59E0B")
    _RED    = colors.HexColor("#EF4444")
    _DARK   = colors.HexColor("#0F1117")
    _SURF   = colors.HexColor("#1A1D2E")
    _BORDER = colors.HexColor("#2D3048")
    _DIM    = colors.HexColor("#9CA3AF")
    _WHITE  = colors.HexColor("#E5E7EB")
    _BLACK  = colors.black
else:
    _INDIGO = _CYAN = _GREEN = _YELLOW = _RED = None
    _DARK = _SURF = _BORDER = _DIM = _WHITE = _BLACK = None

A4_W = 210  # mm reference (not used as a unit, just for column math when reportlab absent)

# ── TOC bookmark tracking ──────────────────────────────────────────────────────
# We build the TOC manually so it works without ReportLab's TableOfContents
# (which requires a two-pass build).  We collect section titles + descriptions
# and render them on page 2 as a styled list.
_TOC_ENTRIES: list[tuple[str, str]] = []   # (section_title, description)


def _score_color(score) -> Any:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return _DIM
    if s >= 80: return _GREEN
    if s >= 60: return _CYAN
    if s >= 40: return _YELLOW
    return _RED


def _priority_color(p: str) -> Any:
    if p == "High":   return _RED
    if p == "Medium": return _YELLOW
    return _GREEN


def _truncate(s: str, n: int = 60) -> str:
    s = (s or "").strip()
    return s[:n] + "…" if len(s) > n else s


# ── Styles ─────────────────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "title", parent=base["Heading1"],
            fontSize=26, textColor=_WHITE,
            spaceAfter=6, fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"],
            fontSize=11, textColor=_DIM, spaceAfter=4,
            alignment=TA_CENTER,
        ),
        "cover_url": ParagraphStyle(
            "cover_url", parent=base["Normal"],
            fontSize=13, textColor=_CYAN, fontName="Courier-Bold",
            alignment=TA_CENTER, spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "section", parent=base["Heading2"],
            fontSize=14, textColor=_CYAN,
            spaceBefore=16, spaceAfter=6, fontName="Helvetica-Bold",
        ),
        "section_num": ParagraphStyle(
            "section_num", parent=base["Normal"],
            fontSize=9, textColor=_DIM, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontSize=9, textColor=_WHITE, leading=13,
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["Normal"],
            fontSize=8, textColor=_CYAN, fontName="Courier",
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"],
            fontSize=8, textColor=_DIM,
        ),
        "toc_title": ParagraphStyle(
            "toc_title", parent=base["Heading2"],
            fontSize=16, textColor=_WHITE, fontName="Helvetica-Bold",
            spaceBefore=0, spaceAfter=10,
        ),
        "toc_entry": ParagraphStyle(
            "toc_entry", parent=base["Normal"],
            fontSize=10, textColor=_WHITE, leading=18,
        ),
        "toc_desc": ParagraphStyle(
            "toc_desc", parent=base["Normal"],
            fontSize=8, textColor=_DIM, leading=12, leftIndent=20,
        ),
        "metric_val": ParagraphStyle(
            "metric_val", parent=base["Normal"],
            fontSize=18, textColor=_CYAN, fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "metric_lbl": ParagraphStyle(
            "metric_lbl", parent=base["Normal"],
            fontSize=8, textColor=_DIM,
            alignment=TA_CENTER,
        ),
    }
    return styles


# ── Page template with header/footer ──────────────────────────────────────────

class _PageDecorator:
    """Adds running header + footer with page numbers to every page."""

    def __init__(self, brand: str, url: str, date_str: str):
        self.brand = brand
        self.url   = url
        self.date  = date_str

    def __call__(self, canvas, doc):
        canvas.saveState()
        w, h = A4

        # Header rule + brand name
        canvas.setStrokeColor(_BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(15*mm, h - 12*mm, w - 15*mm, h - 12*mm)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(_INDIGO)
        canvas.drawString(15*mm, h - 10*mm, self.brand)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_DIM)
        canvas.drawRightString(w - 15*mm, h - 10*mm, _truncate(self.url, 70))

        # Footer rule + page number
        canvas.line(15*mm, 12*mm, w - 15*mm, 12*mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_DIM)
        canvas.drawString(15*mm, 8*mm, f"Generated {self.date}")
        canvas.drawRightString(w - 15*mm, 8*mm, f"Page {doc.page}")

        canvas.restoreState()


# ── Cover page ─────────────────────────────────────────────────────────────────

def _cover_page(styles, url: str, crawl_status: dict, pages: list[dict],
                brand_name: str) -> list:
    real    = [p for p in pages if not p.get("_is_error")]
    issues  = sum(1 for p in real if p.get("issues"))
    high    = sum(1 for p in real if p.get("priority") == "High")
    elapsed = crawl_status.get("elapsed_s", 0)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    story = [Spacer(1, 20*mm)]
    story.append(Paragraph(f"<b>{brand_name}</b>", styles["title"]))
    story.append(Paragraph("SEO Audit Report", styles["cover_sub"]))
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="60%", color=_INDIGO, thickness=2))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(_truncate(url, 80) or "No URL recorded", styles["cover_url"]))
    story.append(Paragraph(f"Generated: {date_str}", styles["small"]))
    story.append(Spacer(1, 14*mm))

    # Key stat cards
    cards = [
        (str(len(real)),   "Pages Crawled",  _WHITE),
        (str(issues),      "With Issues",    _RED if issues else _GREEN),
        (str(high),        "High Priority",  _RED if high else _GREEN),
        (f"{elapsed}s",    "Crawl Time",     _CYAN),
    ]
    row_vals = [[Paragraph(f"<b>{v}</b>", ParagraphStyle("cv", fontSize=22,
                textColor=c, fontName="Helvetica-Bold", alignment=TA_CENTER))
                for v, _, c in cards]]
    row_lbls = [[Paragraph(l, styles["metric_lbl"]) for _, l, _ in cards]]
    t = Table(row_vals + row_lbls, colWidths=[44*mm]*4, rowHeights=[30, 14])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), _SURF),
        ("TOPPADDING",  (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("LINEBELOW",   (0, -1), (-1, -1), 1, _BORDER),
        ("LINEBEFORE",  (1, 0), (-1, -1), 1, _BORDER),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(t)
    story.append(PageBreak())
    return story


# ── Table of Contents ──────────────────────────────────────────────────────────

def _toc_page(styles) -> list:
    """Render the pre-collected TOC entries."""
    story = [
        Paragraph("Table of Contents", styles["toc_title"]),
        HRFlowable(width="100%", color=_BORDER, thickness=0.5),
        Spacer(1, 4*mm),
    ]
    for i, (title, desc) in enumerate(_TOC_ENTRIES, start=1):
        story.append(Paragraph(f"{i}.  <b>{title}</b>", styles["toc_entry"]))
        if desc:
            story.append(Paragraph(desc, styles["toc_desc"]))
    story.append(PageBreak())
    return story


def _section_header(styles, num: int, title: str, desc: str = "") -> list:
    """Consistent section header with optional description."""
    story = [
        Paragraph(f"Section {num}", styles["section_num"]),
        Paragraph(title, styles["section"]),
        HRFlowable(width="100%", color=_BORDER, thickness=0.5),
        Spacer(1, 3*mm),
    ]
    if desc:
        story.append(Paragraph(desc, styles["small"]))
        story.append(Spacer(1, 2*mm))
    return story


# ── Section 1: Executive Summary ──────────────────────────────────────────────

def _executive_summary(styles, pages: list[dict]) -> list:
    real    = [p for p in pages if not p.get("_is_error")]
    issues  = sum(1 for p in real if p.get("issues"))
    high    = sum(1 for p in real if p.get("priority") == "High")
    ok      = len(real) - issues

    # Compute avg score
    try:
        from gemini_analysis import compute_ranking_score
        scores = [compute_ranking_score(p)["score"] for p in real[:50]]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    except Exception:
        avg_score = 0

    story = _section_header(styles, 1, "Executive Summary",
        "High-level overview of your site's SEO health across all crawled pages.")

    cards = [
        (str(len(real)),   "Pages Crawled",  _WHITE),
        (str(issues),      "With Issues",    _RED if issues else _GREEN),
        (str(ok),          "Clean Pages",    _GREEN),
        (str(high),        "High Priority",  _RED if high else _GREEN),
        (str(avg_score),   "Avg SEO Score",  _score_color(avg_score)),
    ]
    row_vals = [[Paragraph(f"<b>{v}</b>", ParagraphStyle("cv", fontSize=18,
                textColor=c, fontName="Helvetica-Bold", alignment=TA_CENTER))
                for v, _, c in cards]]
    row_lbls = [[Paragraph(l, styles["metric_lbl"]) for _, l, _ in cards]]
    t = Table(row_vals + row_lbls, colWidths=[36*mm]*5, rowHeights=[26, 14])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), _DARK),
        ("TOPPADDING",  (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LINEBELOW",   (0, -1), (-1, -1), 1, _BORDER),
        ("LINEBEFORE",  (1, 0), (-1, -1), 1, _BORDER),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(t)
    return story


# ── Section 2: Issue Breakdown ─────────────────────────────────────────────────

def _issues_section(styles, pages: list[dict]) -> list:
    all_issues: list[str] = []
    for p in pages:
        all_issues.extend(p.get("issues") or [])
    counts = Counter(all_issues).most_common(15)
    if not counts:
        return []

    story = _section_header(styles, 2, "On-Page SEO — Issue Breakdown",
        "Most common SEO issues found across all crawled pages, sorted by frequency.")

    data = [["#", "Issue", "Count"]]
    for rank, (issue, cnt) in enumerate(counts, start=1):
        data.append([str(rank), _truncate(issue, 55), str(cnt)])

    t = Table(data, colWidths=[10*mm, 148*mm, 22*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  _SURF),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  _DIM),
        ("FONTSIZE",     (0, 0), (-1, 0),  8),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 1), (-1, -1), 8),
        ("TEXTCOLOR",    (0, 1), (-1, -1), _WHITE),
        ("TEXTCOLOR",    (0, 1), (0, -1),  _DIM),
        ("TEXTCOLOR",    (2, 1), (2, -1),  _YELLOW),
        ("ROWPADDING",   (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_DARK, _SURF]),
        ("LINEBELOW",    (0, 0), (-1, 0),  1, _BORDER),
        ("ALIGN",        (0, 0), (0, -1),  "CENTER"),
        ("ALIGN",        (2, 0), (2, -1),  "CENTER"),
        ("FONTNAME",     (2, 1), (2, -1),  "Courier-Bold"),
    ]))
    story.append(t)
    return story


# ── Section 3: Per-Page Audit ──────────────────────────────────────────────────

def _pages_section(styles, pages: list[dict]) -> list:
    real = [p for p in pages if not p.get("_is_error") and p.get("status_code") == 200]
    real.sort(key=lambda p: (p.get("priority", "Low") != "High",
                             p.get("priority", "Low") != "Medium"))
    real = real[:100]

    if not real:
        return []

    story = _section_header(styles, 3, "Per-Page Audit",
        f"Detailed SEO audit for each crawled page (showing top {len(real)} by priority).")

    headers = ["URL", "Title", "Priority", "Score", "Issues"]
    col_w   = [58*mm, 48*mm, 20*mm, 16*mm, 38*mm]
    data    = [headers]

    for p in real:
        url      = _truncate(p.get("url", ""), 50)
        title    = _truncate(p.get("title", "—"), 40)
        priority = p.get("priority", "—")
        issues   = "; ".join((p.get("issues") or [])[:3])
        issues   = _truncate(issues, 35)
        try:
            from gemini_analysis import compute_ranking_score
            sc = compute_ranking_score(p)
            score = str(int(sc.get("score", 0)))
        except Exception:
            score = "—"
        data.append([url, title, priority, score, issues])

    t = Table(data, colWidths=col_w, repeatRows=1)
    row_styles: list = [
        ("BACKGROUND",   (0, 0), (-1, 0),  _SURF),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  _DIM),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  8),
        ("FONTSIZE",     (0, 1), (-1, -1), 7.5),
        ("TEXTCOLOR",    (0, 1), (-1, -1), _WHITE),
        ("TEXTCOLOR",    (0, 1), (0, -1),  _CYAN),
        ("FONTNAME",     (0, 1), (0, -1),  "Courier"),
        ("ROWPADDING",   (0, 0), (-1, -1), 4),
        ("LINEBELOW",    (0, 0), (-1, 0),  1, _BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_DARK, _SURF]),
        ("WORDWRAP",     (0, 0), (-1, -1), True),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]
    for i, p in enumerate(real, start=1):
        c = _priority_color(p.get("priority", ""))
        row_styles.append(("TEXTCOLOR", (2, i), (2, i), c))
        row_styles.append(("FONTNAME",  (2, i), (2, i), "Helvetica-Bold"))
    t.setStyle(TableStyle(row_styles))
    story.append(t)
    return story


# ── Section 4: Technical SEO Summary ──────────────────────────────────────────

def _technical_section(styles, pages: list[dict]) -> list:
    story = _section_header(styles, 4, "Technical SEO Summary",
        "HTTP status distribution and technical health indicators across the crawl.")

    real = [p for p in pages if not p.get("_is_error")]
    status_counts: Counter = Counter(
        str(p.get("status_code", "?") or "?") for p in pages
    )
    groups = {
        "2xx (OK)":       sum(v for k, v in status_counts.items() if k.startswith("2")),
        "3xx (Redirect)": sum(v for k, v in status_counts.items() if k.startswith("3")),
        "4xx (Client Error)": sum(v for k, v in status_counts.items() if k.startswith("4")),
        "5xx (Server Error)": sum(v for k, v in status_counts.items() if k.startswith("5")),
        "Other / Unknown":    sum(v for k, v in status_counts.items()
                                  if not k[:1].isdigit() or k[0] not in "2345"),
    }
    total = max(1, sum(groups.values()))

    data = [["Status Group", "Count", "% of Total"]]
    for grp, cnt in groups.items():
        pct = f"{cnt / total * 100:.1f}%"
        data.append([grp, str(cnt), pct])

    t = Table(data, colWidths=[90*mm, 30*mm, 30*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  _SURF),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  _DIM),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 8),
        ("TEXTCOLOR",    (0, 1), (-1, -1), _WHITE),
        ("ROWPADDING",   (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_DARK, _SURF]),
        ("LINEBELOW",    (0, 0), (-1, 0),  1, _BORDER),
        ("ALIGN",        (1, 0), (2, -1),  "CENTER"),
    ]))
    story.append(t)
    story.append(Spacer(1, 4*mm))

    # Highlight tech issues if available
    tech_issues: list[str] = []
    for p in real:
        tech_issues.extend(p.get("tech_issues") or [])
    if tech_issues:
        top_tech = Counter(tech_issues).most_common(8)
        story.append(Paragraph("Top Technical Issues", styles["section"]))
        t2_data = [["Issue", "Count"]]
        for issue, cnt in top_tech:
            t2_data.append([_truncate(issue, 70), str(cnt)])
        t2 = Table(t2_data, colWidths=[148*mm, 22*mm])
        t2.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  _SURF),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  _DIM),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("TEXTCOLOR",    (0, 1), (-1, -1), _WHITE),
            ("TEXTCOLOR",    (1, 1), (1, -1),  _RED),
            ("ROWPADDING",   (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_DARK, _SURF]),
            ("LINEBELOW",    (0, 0), (-1, 0),  1, _BORDER),
            ("ALIGN",        (1, 0), (1, -1),  "CENTER"),
        ]))
        story.append(t2)
    return story


# ── Section 5: Keywords Analysis ───────────────────────────────────────────────

def _keywords_section(styles, pages: list[dict]) -> list:
    all_kw: list[str] = []
    for p in pages:
        all_kw.extend(p.get("keywords") or [])
    counts = Counter(all_kw).most_common(20)
    if not counts:
        return []

    story = _section_header(styles, 5, "Keywords Analysis",
        f"Top {len(counts)} keywords detected across the crawled site.")

    row_data: list = []
    row: list = []
    for kw, cnt in counts:
        row.append(Paragraph(f"<b>{_truncate(kw, 25)}</b>  ×{cnt}", styles["small"]))
        if len(row) == 4:
            row_data.append(row)
            row = []
    if row:
        while len(row) < 4:
            row.append(Paragraph("", styles["small"]))
        row_data.append(row)

    t = Table(row_data, colWidths=[44*mm]*4)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), _DARK),
        ("TEXTCOLOR",   (0, 0), (-1, -1), _WHITE),
        ("ROWPADDING",  (0, 0), (-1, -1), 5),
        ("GRID",        (0, 0), (-1, -1), 0.5, _BORDER),
    ]))
    story.append(t)
    return story


# ── Main entry points ──────────────────────────────────────────────────────────

def generate_pdf_bytes(
    crawl_results: list[dict],
    crawl_status:  dict,
    site_url:      str = "",
    brand_name:    str = "CrawlIQ",
) -> bytes:
    """
    Build a full PDF audit report and return raw bytes.
    Raises RuntimeError if reportlab is not installed.
    """
    if not _REPORTLAB:
        raise RuntimeError(
            "reportlab is not installed. Add 'reportlab>=4.0.0' to requirements.txt."
        )

    global _TOC_ENTRIES
    _TOC_ENTRIES = []

    buf     = io.BytesIO()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    deco    = _PageDecorator(brand_name, site_url, date_str)

    doc = SimpleDocTemplate(
        buf,
        pagesize    = A4,
        leftMargin  = 15*mm,
        rightMargin = 15*mm,
        topMargin   = 18*mm,
        bottomMargin= 18*mm,
        title       = f"{brand_name} SEO Audit Report",
        author      = brand_name,
    )
    styles = _build_styles()
    pages  = list(crawl_results)

    # ── Collect TOC entries (must match order of sections below) ──────────────
    real = [p for p in pages if not p.get("_is_error")]
    issue_pages = [p for p in real if p.get("issues")]
    all_issues_flat = [i for p in real for i in (p.get("issues") or [])]
    kw_pages = [p for p in pages if p.get("keywords")]

    _TOC_ENTRIES = [
        ("Executive Summary",
         f"{len(real)} pages · {sum(1 for p in real if p.get('issues'))} with issues"),
        ("On-Page SEO — Issue Breakdown",
         f"{len(Counter(all_issues_flat))} unique issue types across {len(issue_pages)} pages"),
        ("Per-Page Audit",
         f"Detailed audit for up to {min(100, len(real))} pages, sorted by priority"),
        ("Technical SEO Summary",
         "HTTP status distribution and technical health indicators"),
        ("Keywords Analysis",
         f"{len(Counter([kw for p in kw_pages for kw in p.get('keywords', [])]).most_common(20))} top keywords detected"),
    ]

    story: list = []

    # ── Cover page (no header/footer) ──────────────────────────────────────────
    story.extend(_cover_page(styles, site_url, crawl_status, pages, brand_name))

    # ── Table of Contents ──────────────────────────────────────────────────────
    story.extend(_toc_page(styles))

    # ── Section 1: Executive Summary ──────────────────────────────────────────
    story.extend(_executive_summary(styles, pages))
    story.append(Spacer(1, 8*mm))

    # ── Section 2: Issue Breakdown ─────────────────────────────────────────────
    story.extend(_issues_section(styles, pages))
    story.append(Spacer(1, 8*mm))

    # ── Section 3: Per-Page Audit ──────────────────────────────────────────────
    story.append(PageBreak())
    story.extend(_pages_section(styles, pages))
    story.append(Spacer(1, 8*mm))

    # ── Section 4: Technical SEO ───────────────────────────────────────────────
    story.append(PageBreak())
    story.extend(_technical_section(styles, pages))
    story.append(Spacer(1, 8*mm))

    # ── Section 5: Keywords ────────────────────────────────────────────────────
    story.extend(_keywords_section(styles, pages))

    # ── Footer note ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10*mm))
    story.append(HRFlowable(width="100%", color=_BORDER))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        f"Generated by {brand_name} — AI-powered SEO crawler · {date_str}",
        styles["small"],
    ))

    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


def generate_pdf(
    crawl_results: list[dict],
    crawl_status:  dict,
    output_path:   str,
    site_url:      str = "",
    brand_name:    str = "CrawlIQ",
) -> str:
    """Write PDF to output_path. Returns the path."""
    data = generate_pdf_bytes(crawl_results, crawl_status, site_url, brand_name=brand_name)
    with open(output_path, "wb") as f:
        f.write(data)
    logger.info("PDF written: %s (%d bytes)", output_path, len(data))
    return output_path
