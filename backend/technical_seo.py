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

# ── Constants ─────────────────────────────────────────────────────────────────


TITLE_MIN       = 30    # chars — shorter = too short
TITLE_MAX       = 60    # chars — longer  = too long
META_MIN        = 120   # chars — BUG-N28: aligned with issues.py threshold (Google ~155 desktop)
META_MAX        = 160   # chars
WORD_COUNT_THIN = 300   # below = thin content
WORD_COUNT_RICH = 800   # above = rich content
URL_MAX_LEN     = 115   # chars

# Approximate pixel-width per character for Google's ~580px snippet limit
TITLE_PX_PER_CHAR   = 8.5
META_PX_PER_CHAR    = 6.5
TITLE_PX_LIMIT      = 580
META_PX_LIMIT       = 920

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


def assess_indexability(url: str, status_code, canonical: str, is_error: bool) -> dict:
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
    int_links    = int(page.get("internal_links_count") or 0)
    is_error     = bool(page.get("_is_error"))
    indexability = assess_indexability(url, status_code, canonical, is_error)

    # ── Component audits ──────────────────────────────────────────────────────
    title_audit     = _audit_title(title)
    meta_audit      = _audit_meta(meta)
    canonical_audit = _audit_canonical(canonical, url)
    heading_audit   = _audit_headings(h1s, h2s, h3s)
    og_audit        = _audit_og(og_title, og_desc)
    content_audit   = _audit_content(body_text, int_links)
    url_audit       = _audit_url(url)
    image_audit     = _audit_images(img_alts)
    status_audit    = _audit_status(status_code, is_error)

    # ── Compound technical score (0 – 100) ────────────────────────────────────
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
        + status_audit["issues"]
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
        # component audits
        "title":          title_audit,
        "meta":           meta_audit,
        "canonical":      canonical_audit,
        "headings":       heading_audit,
        "open_graph":     og_audit,
        "content":        content_audit,
        "url_analysis":   url_audit,
        "images":         image_audit,
        "status":         status_audit,
    }


def analyze_all(pages: list[dict]) -> dict:
    """
    Audit every page in the list.
    Returns { pages: [...audit dicts...], summary: {...} }
    """
    audits = [analyze_page(p) for p in pages]
    return {
        "pages":   audits,
        "summary": site_summary(audits),
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
    px     = round(length * TITLE_PX_PER_CHAR, 0)

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
    px     = round(length * META_PX_PER_CHAR, 0)

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


def _audit_og(og_title: str, og_desc: str) -> dict:
    issues = []
    has_title = bool(og_title)
    has_desc  = bool(og_desc)
    has_og    = has_title or has_desc

    if not has_title:
        issues.append("og:title missing — affects social sharing previews")
    if not has_desc:
        issues.append("og:description missing — affects social sharing previews")

    if has_title and has_desc:
        completeness = "complete"
        score = 100
    elif has_og:
        completeness = "partial"
        score = 55
    else:
        completeness = "missing"
        score = 0

    return {
        "title":        og_title,
        "description":  og_desc,
        "has_og":       has_og,
        "completeness": completeness,
        "score":        score,
        "issues":       issues,
        # Note: og:image, og:type, og:url not stored by crawler — marked N/A
        "og_image":     "not_audited",
        "og_type":      "not_audited",
        "twitter_card": "not_audited",
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
