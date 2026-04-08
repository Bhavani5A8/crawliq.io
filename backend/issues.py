"""
issues.py — SEO Issue Detection Layer

Provides a single public function: detect_issues(pages)
Call this AFTER all pages have been crawled so that cross-page
checks (e.g. duplicate meta descriptions) work correctly.
"""

from collections import Counter


def detect_issues(pages: list[dict]) -> list[dict]:
    """
    Analyse every crawled page and attach an 'issues' list to each one.

    Two-pass approach:
      Pass 1 — per-page checks (title, h1, status, canonical)
      Pass 2 — cross-page checks (duplicate meta descriptions)

    Returns the same list with 'issues' populated in-place (also returns it
    for convenience).
    """

    # ── Pass 1: per-page rules ────────────────────────────────────────────
    for page in pages:
        page["issues"] = _per_page_issues(page)

    # ── Pass 2: cross-page rules ─────────────────────────────────────────
    _flag_duplicate_meta(pages)

    return pages


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page checks
# ─────────────────────────────────────────────────────────────────────────────

def _per_page_issues(page: dict) -> list[str]:
    """Return a list of issue labels for a single page."""
    # Skip all SEO field checks for error/timeout records.
    # Only flag them as Broken Page — their "title" field holds a raw
    # exception string, which would falsely trigger Title Too Long etc.
    if page.get("_is_error"):
        return ["Broken Page"]

    issues = []

    title       = (page.get("title") or "").strip()
    meta        = (page.get("meta_description") or "").strip()
    keywords    = (page.get("meta_keywords") or "").strip()
    h1_list     = page.get("h1") or []
    h2_list     = page.get("h2") or []
    canonical   = (page.get("canonical") or "").strip()
    status_code = page.get("status_code")
    url         = page.get("url", "")

    # 1. Missing Title
    if not title:
        issues.append("Missing Title")

    # 2. Title Too Long (> 60 characters)
    elif len(title) > 60:
        issues.append("Title Too Long")

    # BUG-012: Title Too Short (< 30 characters) — e.g. "Home", "Contact"
    elif len(title) < 30:
        issues.append("Title Too Short")

    # 3. Missing Meta Description
    if not meta:
        issues.append("Missing Meta Description")

    # BUG-013: Meta Description Too Long (> 160 chars) — truncated in SERPs
    elif len(meta) > 160:
        issues.append("Meta Description Too Long")

    # BUG-013: Meta Description Too Short (< 70 chars) — under-utilises SERP space
    elif len(meta) < 70:
        issues.append("Meta Description Too Short")

    # 4. Missing H1
    if not h1_list:
        issues.append("Missing H1")

    # 5. Multiple H1 tags
    elif len(h1_list) > 1:
        issues.append("Multiple H1 Tags")

    # 6. Missing H2 tags
    if not h2_list:
        issues.append("Missing H2")

    # 7. Broken Page  (meta keywords intentionally removed — Google ignores this tag) — any non-200 status (including Timeout / Error strings)
    if str(status_code) != "200":
        issues.append("Broken Page")

    # 9a. Missing Canonical tag
    if not canonical:
        issues.append("Missing Canonical")
    # 9b. Canonical mismatch — canonical exists but points elsewhere
    elif canonical.rstrip("/") != url.rstrip("/"):
        issues.append("Canonical Mismatch")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page checks
# ─────────────────────────────────────────────────────────────────────────────

def _flag_duplicate_meta(pages: list[dict]) -> None:
    """
    Duplicate Meta Description detection — two-step:

    Step 1 — count how many pages share each non-empty meta string.
    Step 2 — for any meta that appears on 2+ pages, add the issue label
             to every affected page (avoiding duplicates with a set check).

    Why post-crawl? Because we need the full dataset to know whether a
    meta description is duplicated — we can't tell on a per-page basis
    while crawling.
    """

    # Step 1: tally occurrences of each meta string (ignore blanks)
    meta_counts = Counter(
        page["meta_description"].strip()
        for page in pages
        if (page.get("meta_description") or "").strip()
    )

    # Step 2: flag pages whose meta appears more than once
    for page in pages:
        meta = (page.get("meta_description") or "").strip()
        if meta and meta_counts[meta] > 1:
            # Avoid adding the label twice if detect_issues is called again
            if "Duplicate Meta Description" not in page["issues"]:
                page["issues"].append("Duplicate Meta Description")
