"""
technical_seo.py — Technical SEO Post-Processing Pipeline
==========================================================

ARCHITECTURE RULES (strict):
  ✓  Reads from crawl_results dicts already in memory
  ✓  Zero HTTP requests — no aiohttp, no requests, no urllib.request
  ✓  Zero crawler imports — does NOT touch crawler.py / issues.py
  ✓  Pure stdlib: re, urllib.parse, collections, math
  ✓  Called only AFTER crawl completes (via /technical-seo endpoint)
  ✗  Never modifies crawl_results in-place — returns new dicts

Public API
----------
  analyze_page(page: dict) -> dict
      Full technical SEO audit for one already-crawled page dict.

  analyze_all(pages: list[dict]) -> list[dict]
      Batch version — returns list of audit dicts + site-wide summary.

  site_summary(audit_list: list[dict]) -> dict
      Aggregate metrics across all audited pages.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from urllib.parse import urlparse

# ── Optional: textstat for Flesch-Kincaid readability ────────────────────────
try:
    import textstat as _textstat
    _TEXTSTAT = True
except ImportError:
    _textstat = None  # type: ignore
    _TEXTSTAT = False

# ── Constants ─────────────────────────────────────────────────────────────────


TITLE_MIN       = 30    # chars — shorter = too short
TITLE_MAX       = 60    # chars — longer  = too long
META_MIN        = 120   # chars — BUG-N28: aligned with issues.py threshold (Google ~155 desktop)
META_MAX        = 160   # chars
WORD_COUNT_THIN = 300   # below = thin content
WORD_COUNT_RICH = 800   # above = rich content
URL_MAX_LEN     = 115   # chars

# Pixel limits for Google SERP truncation
TITLE_PX_LIMIT  = 580
META_PX_LIMIT   = 920

# BUG-014: replace flat per-character ratio with a 3-bucket lookup.
# Narrow characters (i, l, 1, punctuation) are ~4.5px wide in Arial 18px.
# Wide characters  (M, W, uppercase) are ~11px.
# Everything else is ~7.5px.
# This cuts false-positive "will be truncated" flags by >50% vs a flat 8.5px ratio.
_PX_NARROW = frozenset("iIl|!.,;:'\"-()[]{}1 /\\")
_PX_WIDE   = frozenset("MmWwABCDEFGHJKLOPQRSTUVXYZ@%")

def _text_px_width(text: str) -> float:
    """Estimate rendered pixel width using character-class buckets."""
    total = 0.0
    for ch in text:
        if ch in _PX_NARROW:
            total += 4.5
        elif ch in _PX_WIDE:
            total += 10.5
        else:
            total += 7.5
    return total

# Scoring weights (must sum to 100)
WEIGHTS = {
    "title":        18,
    "meta":         14,
    "canonical":    10,
    "headings":     12,
    "og":            8,
    "content":      10,
    "url":           8,
    "images":        8,
    "links":         6,
    "status":        6,
}


# ── Indexability assessment ────────────────────────────────────────────────────

# Indexability status constants
INDEX_OK         = "indexable"
INDEX_LIKELY     = "likely_indexable"
INDEX_REDIRECT   = "not_indexable_redirect"
INDEX_CANON      = "canonical_mismatch"
INDEX_ERROR      = "not_indexable_error"
INDEX_UNKNOWN    = "unknown"
INDEX_NOINDEX    = "not_indexable_noindex"

# Required JSON-LD properties per schema type (Google Rich Results spec)
_SCHEMA_REQUIRED: dict[str, list[str]] = {
    "Article":  ["headline", "author", "datePublished"],
    "Product":  ["name", "offers"],
    "FAQPage":  ["mainEntity"],
}

# Human-readable heading level names (1-6)
_LEVEL_NAME = {1: "H1", 2: "H2", 3: "H3", 4: "H4", 5: "H5", 6: "H6"}


def assess_indexability(url: str, status_code, canonical: str, is_error: bool,
                        robots_noindex: bool = False) -> dict:
    """
    Determine the likely indexability of a page from crawl-data signals only.

    No network call is made.  The assessment is based on:
      • HTTP status code  (4xx/5xx → blocked)
      • Canonical URL     (pointing elsewhere → may not be indexed at this URL)
      • is_error flag     (crawler-level timeout / connection failure)

    Limitation: meta robots noindex/nofollow directives are NOT checked here
    because the crawler does not preserve the raw <head> HTML. This assessment
    is therefore a *best-effort* signal, not a definitive index status.

    Returns a dict with keys:
      status  — one of the INDEX_* constants above
      label   — short human-readable string for the UI
      reason  — one-sentence explanation
    """
    # noindex directive takes precedence — checked before status code because
    # a 200 page with noindex must not be reported as indexable.
    if robots_noindex:
        return {
            "status": INDEX_NOINDEX,
            "label":  "No Index",
            "reason": "noindex directive in meta robots or X-Robots-Tag — Googlebot will not index this page",
        }

    code = str(status_code)

    # ── Crawler-level errors ──────────────────────────────────────────────────
    if is_error or code in ("Error", "Timeout"):
        return {
            "status": INDEX_ERROR,
            "label":  "Load Error",
            "reason": f"Page failed to load ({code}) — not accessible to crawlers",
        }

    # ── HTTP 3xx redirects ────────────────────────────────────────────────────
    if code.startswith("3"):
        return {
            "status": INDEX_REDIRECT,
            "label":  "Redirect",
            "reason": f"HTTP {code} redirect — destination URL is indexed, not this one",
        }

    # ── HTTP 4xx / 5xx ────────────────────────────────────────────────────────
    if code.startswith("4"):
        return {
            "status": INDEX_ERROR,
            "label":  f"{code} Error",
            "reason": f"Client error ({code}) — page not accessible to Googlebot",
        }
    if code.startswith("5"):
        return {
            "status": INDEX_ERROR,
            "label":  f"{code} Error",
            "reason": f"Server error ({code}) — page unreachable to crawlers",
        }

    # ── HTTP 2xx: check canonical signals ────────────────────────────────────
    if code.startswith("2"):
        canon_clean = (canonical or "").rstrip("/")
        url_clean   = (url or "").rstrip("/")

        if canon_clean and canon_clean != url_clean:
            return {
                "status": INDEX_CANON,
                "label":  "Canonical",
                "reason": f"Canonical points to a different URL — Google will index that URL instead",
            }

        if canon_clean and canon_clean == url_clean:
            return {
                "status": INDEX_OK,
                "label":  "Indexable",
                "reason": "200 OK with self-referencing canonical — likely indexed",
            }

        # 200 but no canonical tag present
        return {
            "status": INDEX_LIKELY,
            "label":  "Likely",
            "reason": "200 OK but no canonical tag — indexability inferred, not confirmed",
        }

    return {
        "status": INDEX_UNKNOWN,
        "label":  "Unknown",
        "reason": f"Unexpected status code: {code}",
    }


# ── Main entry points ─────────────────────────────────────────────────────────

def analyze_page(page: dict) -> dict:
    """
    Return a technical SEO audit dict for one already-crawled page.
    All inputs come from page dict fields — zero network calls.
    """
    url          = page.get("url", "")
    status_code  = page.get("status_code", "")
    title        = (page.get("title") or "").strip()
    meta         = (page.get("meta_description") or "").strip()
    meta_kw      = (page.get("meta_keywords") or "").strip()
    canonical    = (page.get("canonical") or "").strip()
    h1s          = page.get("h1") or []
    h2s          = page.get("h2") or []
    h3s          = page.get("h3") or []
    og_title     = (page.get("og_title") or "").strip()
    og_desc      = (page.get("og_description") or "").strip()
    body_text    = (page.get("body_text") or "")
    img_alts     = page.get("img_alts") or []
    img_srcs     = page.get("img_srcs") or []
    int_links    = int(page.get("internal_links_count") or 0)
    is_error       = bool(page.get("_is_error"))
    robots_noindex = bool(page.get("robots_noindex"))
    last_modified  = (page.get("last_modified") or "")
    viewport       = (page.get("viewport") or "")
    schema_types   = page.get("schema_types") or []
    hreflang_tags  = page.get("hreflang_tags") or []
    og_image         = (page.get("og_image") or "").strip()
    og_type          = (page.get("og_type") or "").strip()
    tw_card          = (page.get("twitter_card") or "").strip()
    tw_title         = (page.get("twitter_title") or "").strip()
    tw_desc          = (page.get("twitter_description") or "").strip()
    tw_image         = (page.get("twitter_image") or "").strip()
    heading_sequence = page.get("heading_sequence") or []
    schema_objects   = page.get("schema_objects") or []
    breadcrumbs          = page.get("breadcrumbs") or []
    breadcrumb_detected  = bool(page.get("breadcrumb_detected"))
    breadcrumb_source    = (page.get("breadcrumb_source") or "")
    img_total            = int(page.get("img_total") or 0)
    img_lazy_count       = int(page.get("img_lazy_count") or 0)
    img_lazy_pct         = float(page.get("img_lazy_pct") or 0.0)
    img_srcset_count     = int(page.get("img_srcset_count") or 0)
    img_srcset_pct       = float(page.get("img_srcset_pct") or 0.0)
    indexability     = assess_indexability(url, status_code, canonical, is_error, robots_noindex)

    # ── Component audits ──────────────────────────────────────────────────────
    title_audit       = _audit_title(title)
    meta_audit        = _audit_meta(meta)
    canonical_audit   = _audit_canonical(canonical, url)
    heading_audit     = _audit_headings(h1s, h2s, h3s)
    og_audit          = _audit_og(og_title, og_desc, og_image, og_type,
                                  tw_card, tw_title, tw_desc, tw_image)
    content_audit     = _audit_content(body_text, int_links)
    url_audit         = _audit_url(url)
    image_audit       = _audit_images(img_alts)
    image_fmt_audit   = _audit_image_formats(img_srcs)
    status_audit      = _audit_status(status_code, is_error)
    # ── NEW supplementary audits (informational — do not change existing score) ──
    readability_audit  = _audit_readability(body_text)
    freshness_audit    = _audit_freshness(last_modified)
    viewport_audit     = _audit_viewport(viewport)
    schema_type_audit  = _audit_schema_types(schema_types)
    hreflang_audit     = _audit_hreflang(hreflang_tags)
    heading_flow_audit   = _audit_heading_flow(heading_sequence)
    schema_val_audit     = _audit_schema_validation(schema_objects)
    breadcrumb_audit     = _audit_breadcrumbs(breadcrumbs, breadcrumb_detected, breadcrumb_source)
    image_loading_audit  = _audit_image_loading(
        img_total, img_lazy_count, img_lazy_pct, img_srcset_count, img_srcset_pct
    )

    # ── Compound technical score (0 – 100) ────────────────────────────────────
    # Score uses only the original 9 components — new audits are additive/informational.
    score = _compute_score(
        title_audit, meta_audit, canonical_audit,
        heading_audit, og_audit, content_audit,
        url_audit, image_audit, status_audit,
    )

    # ── All issues flat list ──────────────────────────────────────────────────
    all_issues = (
        title_audit["issues"]
        + meta_audit["issues"]
        + canonical_audit["issues"]
        + heading_audit["issues"]
        + og_audit["issues"]
        + content_audit["issues"]
        + url_audit["issues"]
        + image_audit["issues"]
        + image_fmt_audit["issues"]
        + status_audit["issues"]
        + viewport_audit["issues"]
        + heading_flow_audit["issues"]
        + schema_val_audit["schema_errors"]
    )

    return {
        "url":            url,
        "tech_score":     score,
        "tech_grade":     _grade(score),
        "issue_count":    len(all_issues),
        "all_issues":     all_issues,
        "status_code":    status_code,
        "is_error":       is_error,
        "indexability":   indexability,
        # ── original component audits ─────────────────────────────────────────
        "title":          title_audit,
        "meta":           meta_audit,
        "canonical":      canonical_audit,
        "headings":       heading_audit,
        "open_graph":     og_audit,
        "content":        content_audit,
        "url_analysis":   url_audit,
        "images":         image_audit,
        "status":         status_audit,
        # ── NEW supplementary audits ──────────────────────────────────────────
        "readability":    readability_audit,   # Flesch-Kincaid / sentence-length proxy
        "freshness":      freshness_audit,     # Last-Modified age signal
        "viewport":       viewport_audit,      # mobile viewport presence
        "schema_types":      schema_type_audit,   # JSON-LD @type detection
        "image_formats":     image_fmt_audit,    # WebP/AVIF vs legacy format ratio
        "hreflang":          hreflang_audit,      # international SEO hreflang tags
        "heading_flow":      heading_flow_audit,  # skipped heading levels
        "schema_validation": schema_val_audit,    # required-property check per @type
        "breadcrumb":        breadcrumb_audit,    # breadcrumb presence + source
        "image_loading":     image_loading_audit, # lazy loading + srcset coverage
    }


def analyze_all(pages: list[dict]) -> dict:
    """
    Audit every page in the list.
    Returns { pages: [...audit dicts...], summary: {...} }
    """
    audits = [analyze_page(p) for p in pages]

    # ── Inbound link map (O(pages × avg_links)) ───────────────────────────────
    # Build {url: [referring_urls]} from the `links` field stored per page.
    # `links` is a list of href strings (backward-compat field set in crawler._parse).
    url_set = {p["url"] for p in pages if p.get("url")}
    inbound: dict[str, list[str]] = {u: [] for u in url_set}
    for page in pages:
        src = page.get("url", "")
        for href in (page.get("links") or []):
            if href in inbound:
                inbound[href].append(src)

    # Inject inbound_count, inbound_urls, and orphan flag into each audit dict
    for audit in audits:
        url  = audit.get("url", "")
        refs = inbound.get(url, [])
        audit["inbound_count"] = len(refs)
        audit["inbound_urls"]  = refs[:10]   # cap to keep payload manageable
        # Orphan: no other crawled page links here.
        # Error pages are excluded — they have no content to link to.
        audit["orphan"] = (len(refs) == 0 and not audit.get("is_error"))

    orphan_pages = [a["url"] for a in audits if a.get("orphan")]

    summary = site_summary(audits)
    summary["link_graph"] = {
        "orphan_count": len(orphan_pages),
        "orphan_urls":  orphan_pages[:20],   # cap list for API payload size
    }

    return {
        "pages":   audits,
        "summary": summary,
    }


def site_summary(audit_list: list[dict]) -> dict:
    """
    Aggregate site-wide technical SEO metrics across all page audits.
    """
    if not audit_list:
        return {}

    total   = len(audit_list)
    real    = [a for a in audit_list if not a["is_error"]]
    n_real  = len(real) or 1  # avoid /0

    scores  = [a["tech_score"] for a in real]
    avg_score = round(sum(scores) / n_real, 1) if scores else 0

    # Coverage metrics
    def _pct(n): return round((n / n_real) * 100, 1)

    has_title      = sum(1 for a in real if a["title"]["present"])
    has_meta       = sum(1 for a in real if a["meta"]["present"])
    has_canonical  = sum(1 for a in real if a["canonical"]["present"])
    has_og         = sum(1 for a in real if a["open_graph"]["has_og"])
    has_h1         = sum(1 for a in real if a["headings"]["h1_count"] == 1)
    thin_content   = sum(1 for a in real if a["content"]["depth"] == "thin")
    url_https      = sum(1 for a in real if a["url_analysis"]["is_https"])

    # Indexability breakdown
    idx_counts = Counter(
        a.get("indexability", {}).get("status", INDEX_UNKNOWN)
        for a in real
    )
    indexable_count = idx_counts.get(INDEX_OK, 0) + idx_counts.get(INDEX_LIKELY, 0)

    # Grade distribution
    grade_dist = Counter(a["tech_grade"] for a in real)

    # Top issues across site
    all_issues = [i for a in audit_list for i in a["all_issues"]]
    top_issues = [{"issue": k, "count": v}
                  for k, v in Counter(all_issues).most_common(10)]

    # Score distribution buckets
    buckets = {"A (85-100)": 0, "B (70-84)": 0, "C (55-69)": 0,
               "D (40-54)": 0, "F (0-39)": 0}
    for s in scores:
        if s >= 85:  buckets["A (85-100)"] += 1
        elif s >= 70: buckets["B (70-84)"] += 1
        elif s >= 55: buckets["C (55-69)"] += 1
        elif s >= 40: buckets["D (40-54)"] += 1
        else:         buckets["F (0-39)"] += 1

    return {
        "total_pages":       total,
        "real_pages":        n_real,
        "avg_tech_score":    avg_score,
        "site_grade":        _grade(avg_score),
        "grade_distribution": dict(grade_dist),
        "score_distribution": buckets,
        "top_issues":        top_issues,
        "indexability": {
            "indexable":            idx_counts.get(INDEX_OK, 0),
            "likely_indexable":     idx_counts.get(INDEX_LIKELY, 0),
            "canonical_mismatch":   idx_counts.get(INDEX_CANON, 0),
            "redirect":             idx_counts.get(INDEX_REDIRECT, 0),
            "error":                idx_counts.get(INDEX_ERROR, 0),
            "unknown":              idx_counts.get(INDEX_UNKNOWN, 0),
            "indexable_total":      indexable_count,
            "indexable_pct":        _pct(indexable_count),
            "blocked_total":        n_real - indexable_count,
        },
        "coverage": {
            "title_pct":      _pct(has_title),
            "meta_pct":       _pct(has_meta),
            "canonical_pct":  _pct(has_canonical),
            "og_pct":         _pct(has_og),
            "h1_pct":         _pct(has_h1),
            "https_pct":      _pct(url_https),
        },
        "content": {
            "thin_pages":  thin_content,
            "thin_pct":    _pct(thin_content),
        },
    }


# ── Component audit functions ─────────────────────────────────────────────────

def _audit_title(title: str) -> dict:
    issues = []
    length = len(title)
    # BUG-014: use per-character-class width; old flat 8.5px ratio had ±15% error.
    px     = round(_text_px_width(title), 0)

    if not title:
        status = "missing"
        issues.append("Title tag missing")
        score  = 0
    elif length < TITLE_MIN:
        status = "too_short"
        issues.append(f"Title too short ({length} chars, min {TITLE_MIN})")
        score  = 55
    elif length > TITLE_MAX:
        status = "too_long"
        issues.append(f"Title too long ({length} chars, max {TITLE_MAX})")
        score  = 65
    elif px > TITLE_PX_LIMIT:
        status = "pixel_overflow"
        issues.append(f"Title may truncate in SERP (~{int(px)}px > {TITLE_PX_LIMIT}px limit)")
        score  = 75
    else:
        status = "ok"
        score  = 100

    return {
        "value":   title,
        "length":  length,
        "px_est":  int(px),
        "status":  status,
        "score":   score,
        "present": bool(title),
        "issues":  issues,
    }


def _audit_meta(meta: str) -> dict:
    issues = []
    length = len(meta)
    # BUG-014: character-class buckets instead of flat 6.5px ratio.
    px     = round(_text_px_width(meta), 0)

    if not meta:
        status = "missing"
        issues.append("Meta description missing")
        score  = 0
    elif length < META_MIN:
        status = "too_short"
        issues.append(f"Meta too short ({length} chars, min {META_MIN})")
        score  = 55
    elif length > META_MAX:
        status = "too_long"
        issues.append(f"Meta too long ({length} chars, max {META_MAX})")
        score  = 65
    elif px > META_PX_LIMIT:
        status = "pixel_overflow"
        issues.append(f"Meta may truncate in SERP (~{int(px)}px > {META_PX_LIMIT}px limit)")
        score  = 80
    else:
        status = "ok"
        score  = 100

    return {
        "value":   meta,
        "length":  length,
        "px_est":  int(px),
        "status":  status,
        "score":   score,
        "present": bool(meta),
        "issues":  issues,
    }


def _audit_canonical(canonical: str, page_url: str) -> dict:
    issues = []

    if not canonical:
        status = "missing"
        issues.append("Canonical tag missing")
        score  = 0
    elif canonical.rstrip("/") == page_url.rstrip("/"):
        status = "self"
        score  = 100
    else:
        status = "points_elsewhere"
        issues.append(f"Canonical points to different URL: {canonical}")
        score  = 60

    return {
        "value":   canonical,
        "status":  status,
        "score":   score,
        "present": bool(canonical),
        "issues":  issues,
    }


def _audit_headings(h1s: list, h2s: list, h3s: list) -> dict:
    issues = []
    h1c, h2c, h3c = len(h1s), len(h2s), len(h3s)

    # H1 checks
    if h1c == 0:
        issues.append("Missing H1 tag")
        h1_status = "missing"
    elif h1c > 1:
        issues.append(f"Multiple H1 tags found ({h1c})")
        h1_status = "multiple"
    else:
        h1_status = "ok"

    # H2 checks
    if h2c == 0:
        issues.append("No H2 tags — page lacks content structure")
        h2_status = "missing"
    else:
        h2_status = "ok"

    # Heading hierarchy check (H3 without H2)
    if h3c > 0 and h2c == 0:
        issues.append("H3 tags used without H2 — skipped heading level")

    # Depth (deepest heading level used)
    depth = 3 if h3c else (2 if h2c else (1 if h1c else 0))

    # Score
    if h1c == 0:       score = 20
    elif h1c > 1:      score = 55
    elif h2c == 0:     score = 70
    else:              score = 100

    return {
        "h1":         h1s[:3],
        "h2":         h2s[:5],
        "h3":         h3s[:3],
        "h1_count":   h1c,
        "h2_count":   h2c,
        "h3_count":   h3c,
        "depth":      depth,
        "h1_status":  h1_status,
        "h2_status":  h2_status,
        "score":      score,
        "issues":     issues,
    }


def _audit_heading_flow(heading_sequence: list[dict]) -> dict:
    """
    Detect skipped heading levels in DOM order.

    A skip is when heading level increases by more than one step
    (e.g. H1 → H3 without an H2, or H2 → H4 without an H3).
    The first heading on the page is the reference; subsequent ones
    are compared only to the immediately preceding heading.

    Returns: {sequence_length, skipped_levels, issues}
    """
    if not heading_sequence:
        return {"sequence_length": 0, "skipped_levels": [], "issues": []}

    issues:  list[str] = []
    skipped: list[str] = []
    prev = heading_sequence[0]["level"]

    for item in heading_sequence[1:]:
        curr = item["level"]
        if curr > prev + 1:
            # Every missing intermediate level is a separate issue
            for missing in range(prev + 1, curr):
                label = (f"{_LEVEL_NAME[prev]}→{_LEVEL_NAME[curr]} "
                         f"(missing {_LEVEL_NAME[missing]})")
                skipped.append(label)
                issues.append(f"Skipped heading level: {label}")
        prev = curr

    return {
        "sequence_length": len(heading_sequence),
        "skipped_levels":  skipped,
        "issues":          issues,
    }


def _audit_schema_validation(schema_objects: list[dict]) -> dict:
    """
    Validate JSON-LD schema objects against required-property rules.

    Checks only the types listed in _SCHEMA_REQUIRED — unrecognised types
    are passed through without errors. Property keys come from the crawled
    JSON-LD object (already parsed by the crawler); values are not inspected.

    Returns: {schema_errors, validated_types, has_errors}
    """
    errors:    list[str] = []
    validated: list[str] = []

    for obj in schema_objects:
        schema_type = obj.get("type", "")
        if schema_type not in _SCHEMA_REQUIRED:
            continue
        validated.append(schema_type)
        present = set(obj.get("props", []))
        for req in _SCHEMA_REQUIRED[schema_type]:
            if req not in present:
                errors.append(f"{schema_type}: missing required property '{req}'")

    return {
        "schema_errors":   errors,
        "validated_types": validated,
        "has_errors":      bool(errors),
    }


def _audit_og(og_title: str, og_desc: str,
              og_image: str = "", og_type: str = "",
              tw_card: str = "", tw_title: str = "",
              tw_desc: str = "", tw_image: str = "") -> dict:
    issues = []
    has_title = bool(og_title)
    has_desc  = bool(og_desc)
    has_image = bool(og_image)
    has_og    = has_title or has_desc

    if not has_title:
        issues.append("og:title missing — affects social sharing previews")
    if not has_desc:
        issues.append("og:description missing — affects social sharing previews")
    if not has_image:
        issues.append("og:image missing — no thumbnail when page is shared on social networks")

    # Full score requires title + description + image (image drives click-through on social)
    if has_title and has_desc and has_image:
        completeness = "complete"
        score = 100
    elif has_title and has_desc:
        completeness = "partial"
        score = 75
    elif has_og:
        completeness = "partial"
        score = 55
    else:
        completeness = "missing"
        score = 0

    return {
        "title":               og_title,
        "description":         og_desc,
        "og_image":            og_image,
        "og_type":             og_type,
        "has_og":              has_og,
        "completeness":        completeness,
        "score":               score,
        "issues":              issues,
        "twitter_card":        tw_card,
        "twitter_title":       tw_title,
        "twitter_description": tw_desc,
        "twitter_image":       tw_image,
        "has_twitter":         bool(tw_card),
    }


def _audit_content(body_text: str, int_links: int) -> dict:
    issues = []
    words  = body_text.split() if body_text else []
    wc     = len(words)

    if wc < WORD_COUNT_THIN:
        depth = "thin"
        issues.append(f"Thin content ({wc} words) — aim for {WORD_COUNT_THIN}+ words")
        score = 30 if wc < 100 else 55
    elif wc < WORD_COUNT_RICH:
        depth = "medium"
        score = 80
    else:
        depth = "rich"
        score = 100

    # Link density (links per 100 words)
    density = round((int_links / max(wc, 1)) * 100, 1) if wc else 0
    if density > 10 and int_links > 5:
        issues.append(f"High link density ({density:.1f} links/100 words)")

    return {
        "word_count":     wc,
        "depth":          depth,
        "score":          score,
        "internal_links": int_links,
        "link_density":   density,
        "issues":         issues,
    }


def _audit_url(url: str) -> dict:
    issues    = []
    parsed    = urlparse(url)
    path      = parsed.path or "/"
    segments  = [s for s in path.split("/") if s]
    depth     = len(segments)
    length    = len(url)
    is_https  = parsed.scheme == "https"
    has_query = bool(parsed.query)
    has_underscore = "_" in path
    has_uppercase  = any(c.isupper() for c in path)
    has_spaces     = "%20" in url or "+" in url

    if not is_https:
        issues.append("URL uses HTTP — HTTPS required for ranking signal")
    if length > URL_MAX_LEN:
        issues.append(f"URL too long ({length} chars, recommended < {URL_MAX_LEN})")
    if has_underscore:
        issues.append("URL contains underscores — use hyphens for word separation")
    if has_uppercase:
        issues.append("URL contains uppercase characters — may cause duplicate content")
    if has_spaces:
        issues.append("URL contains encoded spaces — use hyphens instead")
    if depth > 4:
        issues.append(f"URL depth {depth} — deep paths reduce crawl priority")

    if not issues:
        score = 100
    elif len(issues) == 1:
        score = 75
    else:
        score = max(30, 100 - len(issues) * 18)

    return {
        "is_https":      is_https,
        "length":        length,
        "depth":         depth,
        "has_query":     has_query,
        "has_underscore": has_underscore,
        "has_uppercase": has_uppercase,
        "scheme":        parsed.scheme,
        "path":          path,
        "score":         score,
        "issues":        issues,
    }


def _audit_images(img_alts: list) -> dict:
    """
    Audit image alt text coverage from the img_alts list already in crawl data.
    Note: crawler stores up to 20 alt texts; total image count is unavailable.
    """
    issues = []
    captured     = len(img_alts)
    with_text    = sum(1 for a in img_alts if a.strip())
    empty_alts   = captured - with_text

    if empty_alts > 0:
        issues.append(f"{empty_alts} image(s) with empty alt text detected")

    if captured == 0:
        status = "no_images"
        score  = 100   # no images = no alt text issue
    elif with_text == captured:
        status = "all_have_alt"
        score  = 100
    elif with_text > 0:
        pct    = round((with_text / captured) * 100)
        status = "partial"
        score  = max(40, pct)
    else:
        status = "none_have_alt"
        issues.append("All captured images missing alt text")
        score  = 0

    return {
        "alts_captured":  captured,
        "alts_with_text": with_text,
        "alts_empty":     empty_alts,
        "status":         status,
        "score":          score,
        "issues":         issues,
        "note":           "Crawler captures up to 20 alt texts per page",
    }


def _audit_status(status_code, is_error: bool) -> dict:
    issues = []
    code   = str(status_code)

    if is_error or code in ("Error", "Timeout"):
        issues.append(f"Page failed to load ({code})")
        score = 0
    elif code.startswith("2"):
        score = 100
    elif code.startswith("3"):
        issues.append(f"Redirect ({code}) — check redirect chain length")
        score = 70
    elif code.startswith("4"):
        issues.append(f"Client error ({code}) — page not accessible")
        score = 0
    elif code.startswith("5"):
        issues.append(f"Server error ({code}) — page unreachable")
        score = 0
    else:
        score = 50

    return {
        "code":   code,
        "score":  score,
        "issues": issues,
    }


# ── NEW supplementary audit functions ────────────────────────────────────────
# These are purely informational — they extend the output dict but do NOT
# change the existing weighted score. Existing API consumers are unaffected.

def _audit_readability(body_text: str) -> dict:
    """
    Measure content readability.

    Primary: Flesch Reading Ease (textstat library, 0-100, higher=easier).
      90-100 = Very Easy (5th grade)
      60-70  = Standard (8th/9th grade — target for most web content)
      30-50  = Difficult (college level)
      0-30   = Very Confusing (professional/academic)

    Fallback (if textstat not installed): average sentence length heuristic.
      ≤18 words/sentence = Good (readable)
      ≤25 words/sentence = Fair
      >25 words/sentence = Difficult
    """
    if not body_text or len(body_text.split()) < 30:
        return {
            "method":       "insufficient_content",
            "score":        None,
            "label":        "N/A",
            "grade_level":  None,
            "issues":       [],
        }

    if _TEXTSTAT:
        try:
            fk_ease        = round(_textstat.flesch_reading_ease(body_text), 1)
            fk_grade       = round(_textstat.flesch_kincaid_grade(body_text), 1)
            gunning        = round(_textstat.gunning_fog(body_text), 1)
            if fk_ease >= 70:
                label = "Easy"
            elif fk_ease >= 50:
                label = "Standard"
            elif fk_ease >= 30:
                label = "Difficult"
            else:
                label = "Very Difficult"
            issues = []
            if fk_ease < 40:
                issues.append(
                    f"Low readability (Flesch {fk_ease}) — simplify sentences "
                    f"and use shorter words"
                )
            return {
                "method":        "flesch_kincaid",
                "score":         fk_ease,
                "label":         label,
                "grade_level":   fk_grade,
                "gunning_fog":   gunning,
                "issues":        issues,
            }
        except Exception:
            pass  # fall through to heuristic

    # Fallback heuristic — average sentence length
    sentences = [s.strip() for s in re.split(r"[.!?]", body_text) if len(s.strip()) > 10]
    if not sentences:
        return {"method": "insufficient_content", "score": None,
                "label": "N/A", "grade_level": None, "issues": []}

    avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
    avg_len = round(avg_len, 1)
    if avg_len <= 18:
        label, score = "Easy", 75
    elif avg_len <= 25:
        label, score = "Standard", 55
    else:
        label, score = "Difficult", 30

    issues = []
    if avg_len > 25:
        issues.append(
            f"High avg sentence length ({avg_len} words) — aim for ≤18 words/sentence"
        )
    return {
        "method":       "sentence_length_heuristic",
        "score":        score,
        "label":        label,
        "avg_sentence_len": avg_len,
        "grade_level":  None,
        "issues":       issues,
    }


def _audit_freshness(last_modified: str) -> dict:
    """
    Evaluate content freshness from the HTTP Last-Modified header.

    Google uses freshness as a ranking signal for time-sensitive queries.
    Pages not updated in 12+ months may lose SERP positions to fresher content.
    """
    if not last_modified:
        return {
            "last_modified": None,
            "age_days":      None,
            "status":        "unknown",
            "label":         "No Last-Modified header",
            "issues":        ["Last-Modified header missing — server should send this"],
        }

    import email.utils
    import datetime

    try:
        # HTTP-date format: "Wed, 21 Oct 2015 07:28:00 GMT"
        parsed_time = email.utils.parsedate_to_datetime(last_modified)
        now         = datetime.datetime.now(datetime.timezone.utc)
        age_days    = (now - parsed_time).days

        if age_days < 30:
            status, label = "fresh",   "Fresh (< 30 days)"
        elif age_days < 180:
            status, label = "recent",  f"Recent ({age_days} days old)"
        elif age_days < 365:
            status, label = "ageing",  f"Ageing ({age_days} days old)"
        else:
            status, label = "stale",   f"Stale ({age_days // 30} months old)"

        issues = []
        if age_days > 365:
            issues.append(
                f"Content last modified {age_days // 30} months ago — "
                "update or add new content to signal freshness"
            )
        elif age_days > 180:
            issues.append(
                f"Content is {age_days} days old — consider a content refresh"
            )

        return {
            "last_modified": last_modified,
            "age_days":      age_days,
            "status":        status,
            "label":         label,
            "issues":        issues,
        }
    except Exception:
        return {
            "last_modified": last_modified,
            "age_days":      None,
            "status":        "parse_error",
            "label":         "Could not parse date",
            "issues":        [],
        }


def _audit_viewport(viewport: str) -> dict:
    """
    Check for mobile-viewport meta tag — required for mobile-friendliness.

    Google uses mobile-first indexing. Pages missing this tag are
    treated as desktop-only and may rank lower on mobile searches.

    Best practice: content="width=device-width, initial-scale=1"
    """
    issues = []
    if not viewport:
        issues.append(
            'Mobile viewport meta tag missing — add '
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
        )
        return {
            "present":       False,
            "value":         "",
            "has_width":     False,
            "has_scale":     False,
            "status":        "missing",
            "issues":        issues,
        }

    vp_lower    = viewport.lower()
    has_width   = "width=device-width" in vp_lower
    has_scale   = "initial-scale=1" in vp_lower
    has_shrink  = "user-scalable=no" in vp_lower

    if not has_width:
        issues.append(
            'Viewport missing "width=device-width" — may not render correctly on mobile'
        )
    if has_shrink:
        issues.append(
            '"user-scalable=no" in viewport disables zoom — accessibility issue'
        )

    status = "ok" if has_width and not issues else "warning" if viewport else "missing"
    return {
        "present":      True,
        "value":        viewport,
        "has_width":    has_width,
        "has_scale":    has_scale,
        "user_scalable_disabled": has_shrink,
        "status":       status,
        "issues":       issues,
    }


def _audit_schema_types(schema_types: list) -> dict:
    """
    Detect and evaluate JSON-LD schema markup types on the page.

    Rich result eligible types (direct SERP feature triggers):
      Article, NewsArticle, BlogPosting → article rich result
      FAQPage                           → FAQ accordion in SERP
      HowTo                             → HowTo steps in SERP
      Product + AggregateRating         → star ratings + price
      BreadcrumbList                    → breadcrumb path in SERP
      Event                             → event rich result
      Recipe                            → recipe rich result
      Review, AggregateRating           → star ratings
      Organization, LocalBusiness       → knowledge panel signals
    """
    _RICH_RESULT_TYPES = {
        "article", "newsarticle", "blogposting",
        "faqpage",
        "howto",
        "product",
        "breadcrumblist",
        "event",
        "recipe",
        "review", "aggregaterating",
        "organization", "localbusiness",
        "person",
        "video", "videoobject",
    }
    _RICH_RESULT_LABELS = {
        "faqpage":        "FAQ accordion",
        "howto":          "HowTo steps",
        "product":        "Product rich result",
        "breadcrumblist": "Breadcrumb path",
        "event":          "Event rich result",
        "recipe":         "Recipe rich result",
        "article":        "Article rich result",
        "newsarticle":    "News Article",
        "blogposting":    "Blog Article",
        "aggregaterating":"Star ratings",
        "organization":   "Organization KP",
        "localbusiness":  "Local Business",
        "video":          "Video result",
        "videoobject":    "Video result",
    }

    if not schema_types:
        return {
            "present":         False,
            "types":           [],
            "rich_results":    [],
            "missing_types":   ["FAQPage", "BreadcrumbList", "Article"],
            "status":          "missing",
            "issues":          ["No JSON-LD structured data — add schema markup for rich results"],
        }

    types_lower     = [t.lower() for t in schema_types]
    rich_eligible   = [
        _RICH_RESULT_LABELS.get(t, t.title())
        for t in types_lower
        if t in _RICH_RESULT_TYPES
    ]
    suggested = []
    if "faqpage" not in types_lower:
        suggested.append("FAQPage")
    if "breadcrumblist" not in types_lower:
        suggested.append("BreadcrumbList")
    if not any(t in types_lower for t in ("article", "newsarticle", "blogposting", "product")):
        suggested.append("Article or Product")

    issues = []
    if not rich_eligible:
        issues.append(
            f"Schema types {schema_types} not rich-result eligible — "
            "add FAQPage, HowTo, or Article markup"
        )

    return {
        "present":       True,
        "types":         schema_types,
        "rich_results":  rich_eligible,
        "suggested":     suggested[:3],
        "status":        "ok" if rich_eligible else "non_eligible",
        "issues":        issues,
    }


def _audit_image_formats(img_srcs: list) -> dict:
    """
    Check image format distribution — next-gen formats (WebP, AVIF) vs legacy.

    Next-gen formats (WebP, AVIF) are 25-50% smaller than JPEG/PNG at same
    quality. Google PageSpeed Insights flags legacy images as an opportunity.
    """
    issues = []
    if not img_srcs:
        return {
            "total":          0,
            "next_gen":       0,
            "legacy":         0,
            "next_gen_pct":   None,
            "status":         "no_images",
            "issues":         [],
        }

    next_gen_exts = {".webp", ".avif"}
    legacy_exts   = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff"}

    next_gen_count = 0
    legacy_count   = 0
    for src in img_srcs:
        src_lower = src.lower().split("?")[0]   # strip query strings
        ext = "." + src_lower.rsplit(".", 1)[-1] if "." in src_lower else ""
        if ext in next_gen_exts:
            next_gen_count += 1
        elif ext in legacy_exts:
            legacy_count += 1

    total = next_gen_count + legacy_count
    pct   = round((next_gen_count / total) * 100, 1) if total > 0 else 0.0

    if legacy_count > 0 and next_gen_count == 0:
        status = "legacy_only"
        issues.append(
            f"All {legacy_count} images use legacy formats (JPG/PNG) — "
            "convert to WebP or AVIF for 25-50% smaller file sizes"
        )
    elif legacy_count > next_gen_count:
        status = "mostly_legacy"
        issues.append(
            f"{legacy_count}/{total} images use legacy formats — "
            "prioritise converting to WebP"
        )
    else:
        status = "ok"

    return {
        "total":        total,
        "next_gen":     next_gen_count,
        "legacy":       legacy_count,
        "next_gen_pct": pct,
        "status":       status,
        "issues":       issues,
    }


def _audit_breadcrumbs(breadcrumbs: list, detected: bool, source: str) -> dict:
    """
    Audit breadcrumb implementation.

    Best practice (per Google): use BreadcrumbList JSON-LD — it triggers
    the breadcrumb path display in SERP snippets without relying on HTML parsing.
    HTML nav[aria-label=breadcrumb] is a valid fallback.
    """
    issues = []
    if not detected:
        issues.append(
            "No breadcrumb markup detected — add BreadcrumbList JSON-LD "
            "or <nav aria-label=\"breadcrumb\"> for SERP breadcrumb display"
        )
        return {
            "detected":    False,
            "source":      "",
            "item_count":  0,
            "items":       [],
            "status":      "missing",
            "issues":      issues,
        }

    if source == "html_nav":
        issues.append(
            "Breadcrumb uses HTML nav only — add BreadcrumbList JSON-LD "
            "for richer SERP integration and Googlebot parsing"
        )

    return {
        "detected":    True,
        "source":      source,       # "json_ld" | "html_nav"
        "item_count":  len(breadcrumbs),
        "items":       breadcrumbs[:8],
        "status":      "ok" if source == "json_ld" else "partial",
        "issues":      issues,
    }


def _audit_image_loading(
    img_total: int,
    img_lazy_count: int,
    img_lazy_pct: float,
    img_srcset_count: int,
    img_srcset_pct: float,
) -> dict:
    """
    Audit image lazy-loading and srcset (responsive images) coverage.

    lazy loading: loading="lazy" defers off-screen images — reduces initial
    page weight and improves LCP / CLS scores on image-heavy pages.

    srcset: lets the browser pick the right resolution for the viewport —
    avoids serving desktop-sized images to mobile devices.
    """
    issues = []

    if img_total == 0:
        return {
            "total":         0,
            "lazy_count":    0,
            "lazy_pct":      None,
            "srcset_count":  0,
            "srcset_pct":    None,
            "status":        "no_images",
            "issues":        [],
        }

    if img_lazy_pct < 50 and img_total > 2:
        issues.append(
            f"Only {img_lazy_pct}% of images use loading=\"lazy\" "
            f"({img_lazy_count}/{img_total}) — add it to below-fold images"
        )
    if img_srcset_pct < 30 and img_total > 2:
        issues.append(
            f"Only {img_srcset_pct}% of images have srcset "
            f"({img_srcset_count}/{img_total}) — add srcset for responsive delivery"
        )

    if not issues:
        status = "ok"
    elif len(issues) == 1:
        status = "partial"
    else:
        status = "needs_work"

    return {
        "total":        img_total,
        "lazy_count":   img_lazy_count,
        "lazy_pct":     img_lazy_pct,
        "srcset_count": img_srcset_count,
        "srcset_pct":   img_srcset_pct,
        "status":       status,
        "issues":       issues,
    }


def _audit_hreflang(hreflang_tags: list) -> dict:
    """
    Audit international SEO hreflang tag implementation.

    Checks for:
      - Presence of any hreflang declarations
      - x-default fallback tag
      - Duplicate language codes (configuration error)
      - Self-referencing hreflang (best practice)

    Returns:
      {
        "present":       bool,
        "count":         int,
        "langs":         list[str],   # language codes declared
        "has_x_default": bool,
        "issues":        list[str],
      }
    """
    issues = []
    if not hreflang_tags:
        return {
            "present":       False,
            "count":         0,
            "langs":         [],
            "has_x_default": False,
            "status":        "not_implemented",
            "issues":        [],   # informational only — not every site needs hreflang
        }

    langs = [tag.get("lang", "").lower() for tag in hreflang_tags if tag.get("lang")]
    has_x_default = "x-default" in langs

    # Duplicate language codes are a misconfiguration
    seen: set[str] = set()
    dupes: set[str] = set()
    for lang in langs:
        if lang in seen:
            dupes.add(lang)
        seen.add(lang)
    if dupes:
        issues.append(f"Duplicate hreflang codes detected: {sorted(dupes)} — each language must appear once")

    if not has_x_default:
        issues.append("Missing hreflang x-default — add <link rel='alternate' hreflang='x-default'> for unmatched locales")

    return {
        "present":       True,
        "count":         len(hreflang_tags),
        "langs":         sorted(set(langs)),
        "has_x_default": has_x_default,
        "status":        "ok" if not issues else "issues",
        "issues":        issues,
    }


# ── Scoring ───────────────────────────────────────────────────────────────────

def _compute_score(title, meta, canonical, headings, og, content, url, images, status) -> int:
    """
    Weighted average of component scores → overall technical score 0-100.
    """
    weighted = (
        title["score"]     * WEIGHTS["title"]     +
        meta["score"]      * WEIGHTS["meta"]       +
        canonical["score"] * WEIGHTS["canonical"]  +
        headings["score"]  * WEIGHTS["headings"]   +
        og["score"]        * WEIGHTS["og"]         +
        content["score"]   * WEIGHTS["content"]    +
        url["score"]       * WEIGHTS["url"]        +
        images["score"]    * WEIGHTS["images"]     +
        status["score"]    * WEIGHTS["status"]
    )
    total_weight = sum(WEIGHTS.values())
    return max(0, min(100, round(weighted / total_weight)))


def _grade(score: int) -> str:
    if score >= 85: return "A"
    if score >= 70: return "B"
    if score >= 55: return "C"
    if score >= 40: return "D"
    return "F"
