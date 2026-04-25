"""
pdf_export.py — CrawlIQ PDF Report Generator (Phase 4)

Generates a styled PDF audit report from crawl results using ReportLab
(pure-Python, no system dependencies — works on HuggingFace Spaces).

Public API
──────────
  generate_pdf(crawl_results, crawl_status, output_path) → str
      Write PDF to output_path. Returns the path on success.

  generate_pdf_bytes(crawl_results, crawl_status) → bytes
      Return PDF as bytes (for FastAPI StreamingResponse).
"""

from __future__ import annotations

import io
import logging
import os
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


# ── Document builder ──────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "title", parent=base["Heading1"],
            fontSize=22, textColor=_WHITE,
            spaceAfter=6, fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"],
            fontSize=10, textColor=_DIM, spaceAfter=4,
        ),
        "section": ParagraphStyle(
            "section", parent=base["Heading2"],
            fontSize=13, textColor=_CYAN,
            spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold",
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


def _header_table(styles, url: str, crawl_status: dict, brand_name: str = "CrawlIQ") -> Table:
    """Top banner with site URL + key stats. brand_name supports white-labelling."""
    elapsed = crawl_status.get("elapsed_s", 0)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pages    = crawl_status.get("pages_crawled", 0)
    errors   = crawl_status.get("errors", 0)

    data = [[
        Paragraph(f"<b>{brand_name} — SEO Audit Report</b>", styles["title"]),
        Paragraph(f"Generated: {date_str}", styles["small"]),
    ], [
        Paragraph(_truncate(url, 80), styles["mono"]),
        Paragraph(f"{pages} pages · {errors} errors · {elapsed}s", styles["small"]),
    ]]
    t = Table(data, colWidths=[120*mm, 60*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), _SURF),
        ("ROWPADDING",  (0, 0), (-1, -1), 8),
        ("LINEBELOW",   (0, -1), (-1, -1), 1, _BORDER),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",       (1, 0), (1, -1), "RIGHT"),
    ]))
    return t


def _summary_table(styles, pages: list[dict]) -> Table:
    """4-card summary row."""
    real    = [p for p in pages if not p.get("_is_error")]
    issues  = sum(1 for p in real if p.get("issues"))
    high    = sum(1 for p in real if p.get("priority") == "High")
    ok      = len(real) - issues

    cards = [
        (str(len(real)),   "Pages Crawled",  _WHITE),
        (str(issues),      "With Issues",    _RED if issues else _GREEN),
        (str(ok),          "Clean Pages",    _GREEN),
        (str(high),        "High Priority",  _RED if high else _GREEN),
    ]
    row_vals  = [[Paragraph(v, ParagraphStyle("cv", fontSize=20, textColor=c, fontName="Helvetica-Bold", alignment=TA_CENTER)) for v, _, c in cards]]
    row_lbls  = [[Paragraph(l, styles["metric_lbl"]) for _, l, _ in cards]]
    data = row_vals + row_lbls

    col_w = 44*mm
    t = Table(data, colWidths=[col_w]*4, rowHeights=[28, 14])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), _DARK),
        ("TOPPADDING",  (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LINEBELOW",   (0, -1), (-1, -1), 1, _BORDER),
        ("LINEBEFORE",  (1, 0), (-1, -1), 1, _BORDER),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
    ]))
    return t


def _issues_section(styles, pages: list[dict]) -> list:
    """Issue breakdown section."""
    from collections import Counter
    all_issues: list[str] = []
    for p in pages:
        all_issues.extend(p.get("issues") or [])
    counts = Counter(all_issues).most_common(15)
    if not counts:
        return []

    flowables = [
        Paragraph("Issue Breakdown", styles["section"]),
    ]
    data = [["Issue", "Count"]]
    for issue, cnt in counts:
        data.append([_truncate(issue, 55), str(cnt)])

    t = Table(data, colWidths=[140*mm, 30*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  _SURF),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  _DIM),
        ("FONTSIZE",     (0, 0), (-1, 0),  8),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 1), (-1, -1), 8),
        ("TEXTCOLOR",    (0, 1), (-1, -1), _WHITE),
        ("TEXTCOLOR",    (1, 1), (1, -1),  _YELLOW),
        ("ROWPADDING",   (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_DARK, _SURF]),
        ("LINEBELOW",    (0, 0), (-1, 0),  1, _BORDER),
        ("ALIGN",        (1, 0), (1, -1),  "CENTER"),
        ("FONTNAME",     (1, 1), (1, -1),  "Courier-Bold"),
    ]))
    flowables.append(t)
    return flowables


def _pages_section(styles, pages: list[dict]) -> list:
    """Per-page audit table (max 100 rows for PDF size)."""
    real = [p for p in pages if not p.get("_is_error") and p.get("status_code") == 200]
    real.sort(key=lambda p: (p.get("priority", "Low") != "High",
                             p.get("priority", "Low") != "Medium"))
    real = real[:100]

    if not real:
        return []

    flowables = [
        Paragraph("Page-Level Audit", styles["section"]),
    ]

    # Header
    headers = ["URL", "Title", "Priority", "Score", "Issues"]
    col_w   = [58*mm, 48*mm, 20*mm, 16*mm, 38*mm]
    data    = [headers]

    for p in real:
        url      = _truncate(p.get("url", ""), 50)
        title    = _truncate(p.get("title", "—"), 40)
        priority = p.get("priority", "—")
        issues   = "; ".join((p.get("issues") or [])[:3])
        issues   = _truncate(issues, 35)

        # Compute score
        try:
            from gemini_analysis import compute_ranking_score
            sc = compute_ranking_score(p)
            score = str(int(sc.get("score", 0)))
        except Exception:
            score = "—"

        data.append([url, title, priority, score, issues])

    t = Table(data, colWidths=col_w, repeatRows=1)

    # Build per-row styles
    row_styles: list = [
        ("BACKGROUND",   (0, 0), (-1, 0),  _SURF),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  _DIM),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  8),
        ("FONTSIZE",     (0, 1), (-1, -1), 7.5),
        ("TEXTCOLOR",    (0, 1), (-1, -1), _WHITE),
        ("TEXTCOLOR",    (0, 1), (0, -1),  _CYAN),    # URL col
        ("FONTNAME",     (0, 1), (0, -1),  "Courier"),
        ("ROWPADDING",   (0, 0), (-1, -1), 4),
        ("LINEBELOW",    (0, 0), (-1, 0),  1, _BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_DARK, _SURF]),
        ("WORDWRAP",     (0, 0), (-1, -1), True),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]
    # Colour priority column
    for i, p in enumerate(real, start=1):
        pri = p.get("priority", "")
        c   = _priority_color(pri)
        row_styles.append(("TEXTCOLOR", (2, i), (2, i), c))
        row_styles.append(("FONTNAME",  (2, i), (2, i), "Helvetica-Bold"))

    t.setStyle(TableStyle(row_styles))
    flowables.append(t)
    return flowables


def _keywords_section(styles, pages: list[dict]) -> list:
    """Top keywords across the crawl."""
    from collections import Counter
    all_kw: list[str] = []
    for p in pages:
        all_kw.extend(p.get("keywords") or [])
    counts = Counter(all_kw).most_common(20)
    if not counts:
        return []

    flowables = [Paragraph("Top Keywords", styles["section"])]
    # 4-column layout
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
    flowables.append(t)
    return flowables


# ── Main entry points ─────────────────────────────────────────────────────────

def generate_pdf_bytes(
    crawl_results: list[dict],
    crawl_status:  dict,
    site_url:      str = "",
    brand_name:    str = "CrawlIQ",
) -> bytes:
    """
    Build a full PDF audit report and return raw bytes.
    brand_name: override the header title for white-label reports.
    Raises RuntimeError if reportlab is not installed.
    """
    if not _REPORTLAB:
        raise RuntimeError(
            "reportlab is not installed. Add 'reportlab>=4.0.0' to requirements.txt."
        )

    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(
        buf,
        pagesize    = A4,
        leftMargin  = 15*mm,
        rightMargin = 15*mm,
        topMargin   = 15*mm,
        bottomMargin= 15*mm,
        title       = f"{brand_name} SEO Audit Report",
        author      = brand_name,
    )
    styles = _build_styles()
    pages  = list(crawl_results)

    story: list = []

    # ── Cover / header ──────────────────────────────────────────────────────
    story.append(_header_table(styles, site_url, crawl_status, brand_name=brand_name))
    story.append(Spacer(1, 6*mm))
    story.append(_summary_table(styles, pages))
    story.append(Spacer(1, 8*mm))

    # ── Issue breakdown ─────────────────────────────────────────────────────
    story.extend(_issues_section(styles, pages))
    story.append(Spacer(1, 8*mm))

    # ── Per-page table ──────────────────────────────────────────────────────
    story.extend(_pages_section(styles, pages))
    story.append(Spacer(1, 8*mm))

    # ── Keywords ────────────────────────────────────────────────────────────
    story.extend(_keywords_section(styles, pages))

    # ── Footer note ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 10*mm))
    story.append(HRFlowable(width="100%", color=_BORDER))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        f"Generated by {brand_name} — AI-powered SEO crawler",
        styles["small"],
    ))

    doc.build(story)
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
