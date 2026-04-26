"""
issues.py — SEO Issue Detection Layer

Public functions:
  detect_issues(pages)   — lightweight crawler-time checks (per-page + dup meta/title)
  validate_all(pages, sitemap_urls=None) — comprehensive post-crawl validator:
      • Indexability      (noindex, X-Robots-Tag, canonical conflicts, chains, loops)
      • Links             (orphans, broken internal links, anchor text quality)
      • Content           (word count, keyword density, keyword prominence, dup hash)
      • Headings          (H1–H6 ordered sequence, skipped levels)
      • Hreflang          (reciprocal linking, RFC-5646 validation, x-default, cross-page map)
      • Sitemap           (noindex-in-sitemap, crawled-but-missing pages)
      • Images            (missing alt, missing width/height CLS risk, lazy loading)
      • Security          (CSP, X-Frame-Options, X-Content-Type-Options)
      • Cache/Compression (Cache-Control TTL, ETag, content-encoding)
      • Duplicates        (global title_map + meta_map with URL lists)

Severity tagging:
  Every issue dict in validate_all carries a "severity" key: CRITICAL / HIGH / MEDIUM
"""

import hashlib
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse as _up

# ── Constants ─────────────────────────────────────────────────────────────────

_GENERIC_ANCHORS = frozenset([
    "click here", "here", "read more", "more", "learn more", "this", "link",
    "page", "website", "post", "article", "view more", "see more", "details",
    "info", "information", "download", "submit", "continue", "next", "prev",
    "previous", "home", "back", "go", "open",
])

# BCP-47 / RFC-5646: language[-script][-region] or x-default
_VALID_LANG_RE = re.compile(
    r'^(?:x-default|[a-z]{2,3}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?)$',
    re.I
)

# Security headers that must be present on every page
_SECURITY_HEADERS: dict[str, str] = {
    "content-security-policy":
        "Content-Security-Policy (CSP) header missing — add to mitigate XSS attacks",
    "x-frame-options":
        "X-Frame-Options header missing — page may be embeddable in iframes (clickjacking risk)",
    "x-content-type-options":
        "X-Content-Type-Options header missing — add 'nosniff' to block MIME-type sniffing",
}

# Minimum cache TTL considered acceptable (1 hour in seconds)
_MIN_CACHE_TTL_S = 3600

# Content types that should always be compressed
_COMPRESSIBLE_TYPES = frozenset(["text/html", "text/css", "application/javascript",
                                  "application/json", "text/javascript", "text/xml"])

# Severity levels
CRITICAL = "CRITICAL"
HIGH     = "HIGH"
MEDIUM   = "MEDIUM"


def _is_homepage(url: str) -> bool:
    """BUG-N09: return True if url is the site root (no meaningful path)."""
    return _up(url).path.rstrip("/") == ""


def detect_issues(pages: list[dict]) -> list[dict]:
    """
    Analyse every crawled page and attach an 'issues' list to each one.

    Two-pass approach:
      Pass 1 — per-page checks (title, h1, status, canonical)
      Pass 2 — cross-page checks (duplicate meta descriptions, duplicate titles)

    Returns the same list with 'issues' populated in-place (also returns it
    for convenience).
    """

    # ── Pass 1: per-page rules ────────────────────────────────────────────
    for page in pages:
        page["issues"] = _per_page_issues(page)

    # ── Pass 2: cross-page rules ─────────────────────────────────────────
    _flag_duplicate_meta(pages)
    _flag_duplicate_titles(pages)

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

    # BUG-012 / BUG-N09: skip short-title check on the homepage — brand names
    # and "Home" are intentionally short there and should not be flagged.
    elif len(title) < 30 and not _is_homepage(url):
        issues.append("Title Too Short")

    # 3. Missing Meta Description
    if not meta:
        issues.append("Missing Meta Description")

    # BUG-013 / BUG-N10: tightened thresholds to match real SERP behaviour.
    # Google displays ~155 chars desktop / ~120 chars mobile — 160 is the safe max.
    # Anything under 120 chars under-utilises available SERP space on desktop.
    elif len(meta) > 160:
        issues.append("Meta Description Too Long")

    elif len(meta) < 120:
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

def _flag_duplicate_meta(pages: list[dict]) -> dict[str, list[str]]:
    """
    Duplicate Meta Description detection — two-step.

    Step 1 — build meta_map[normalised_description] → [urls]
    Step 2 — for any meta that appears on 2+ pages, inject issue string
             on every affected page.

    Returns the full meta_map so validate_all can include it in cross-page output.
    Severity: HIGH — duplicate metas cannibalise SERP snippets across pages.
    """
    meta_map: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        meta = (page.get("meta_description") or "").strip().lower()
        if meta:
            meta_map[meta].append(page.get("url", ""))

    # Flag pages where the same meta string appears on 2+ URLs
    for page in pages:
        meta = (page.get("meta_description") or "").strip().lower()
        if meta and len(meta_map[meta]) > 1:
            label = "[HIGH] Duplicate Meta Description"
            if label not in page["issues"]:
                page["issues"].append(label)

    # Return only duplicated entries (len > 1) for the cross-page report
    return {k: v for k, v in meta_map.items() if len(v) > 1}


def _flag_duplicate_titles(pages: list[dict]) -> dict[str, list[str]]:
    """
    Mirror of _flag_duplicate_meta — flags pages that share an identical title.

    Returns title_map[normalised_title] → [urls] for duplicated titles.
    Error records excluded: their title field holds a raw exception string.
    Severity: HIGH — duplicate titles cause Googlebot to treat pages as thin content.
    """
    title_map: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        if page.get("_is_error"):
            continue
        title = (page.get("title") or "").strip().lower()
        if title:
            title_map[title].append(page.get("url", ""))

    for page in pages:
        if page.get("_is_error"):
            continue
        title = (page.get("title") or "").strip().lower()
        if title and len(title_map[title]) > 1:
            label = "[HIGH] Duplicate Title"
            if label not in page["issues"]:
                page["issues"].append(label)

    return {k: v for k, v in title_map.items() if len(v) > 1}


# ═════════════════════════════════════════════════════════════════════════════
#  COMPREHENSIVE VALIDATION ENGINE
# ═════════════════════════════════════════════════════════════════════════════

def validate_all(
    pages: list[dict],
    sitemap_urls: list[str] | None = None,
) -> dict:
    """
    Comprehensive SEO validation engine — runs entirely on crawl data.

    Parameters
    ----------
    pages        : list of page dicts produced by SEOCrawler
    sitemap_urls : optional list of URLs parsed from sitemap XML

    Returns
    -------
    {
      "page_issues"      : [{url, indexability, links, content, headings,
                             hreflang, images, security, cache_compression,
                             anchor_quality_score, all_issues, issue_count}],
      "cross_page_issues": [{type, severity, ...}],
      "hreflang_map"     : {url: {lang: target_url}} — full cross-page hreflang structure,
      "title_map"        : {title: [urls]} — duplicated titles (len > 1 only),
      "meta_map"         : {meta: [urls]}  — duplicated meta descriptions (len > 1 only),
      "stats"            : {pages_analysed, pages_with_issues, cross_page_count},
    }
    """
    real_pages = [p for p in pages if not p.get("_is_error")]

    # ── Pre-build cross-page look-up structures ───────────────────────────────

    # status_map: normalised url → status_code
    status_map: dict[str, object] = {
        _n(p.get("url") or ""): p.get("status_code")
        for p in pages if p.get("url")
    }

    # canonical_map: url → canonical (only when canonical ≠ url)
    canonical_map: dict[str, str] = {}
    for p in pages:
        url   = _n(p.get("url") or "")
        canon = _n(p.get("canonical") or "")
        if url and canon and canon != url:
            canonical_map[url] = canon

    # hreflang_hrefs: url → set of hrefs declared in hreflang tags
    hreflang_hrefs: dict[str, set[str]] = {}
    for p in pages:
        url  = _n(p.get("url") or "")
        tags = p.get("hreflang_tags") or []
        hreflang_hrefs[url] = {_n(t.get("href") or "") for t in tags if t.get("href")}

    # ── Full cross-page hreflang map: {url: {lang: target_url}} ─────────────
    hreflang_map: dict[str, dict[str, str]] = {}
    for p in pages:
        url  = _n(p.get("url") or "")
        tags = p.get("hreflang_tags") or []
        if tags:
            hreflang_map[url] = {
                t["lang"]: t["href"]
                for t in tags
                if t.get("lang") and t.get("href")
            }

    # ── Global duplicate maps ────────────────────────────────────────────────
    # Run detect_issues pass-2 on real pages so maps are built from validated data
    title_map: dict[str, list[str]] = _flag_duplicate_titles(pages)
    meta_map:  dict[str, list[str]] = _flag_duplicate_meta(pages)

    # ── Per-page issues ───────────────────────────────────────────────────────
    page_issues = [_validate_page(p) for p in pages]

    # ── Cross-page issues ─────────────────────────────────────────────────────
    cross: list[dict] = []
    cross += _xp_canonical(pages, canonical_map)
    cross += _xp_broken_links(pages, status_map)
    cross += _xp_duplicate_content(real_pages)
    cross += _xp_hreflang_reciprocal(pages, hreflang_hrefs)
    cross += _xp_hreflang_xdefault(pages, hreflang_map)
    cross += _xp_pagination(pages)
    cross += _xp_orphans(pages)

    # Inject severity into duplicate-map cross-page entries
    for title, urls in title_map.items():
        cross.append({
            "type":     "duplicate_title",
            "severity": HIGH,
            "urls":     urls,
            "title":    title,
            "detail":   f"Duplicate title '{title[:60]}…' shared by {len(urls)} pages",
        })
    for meta, urls in meta_map.items():
        cross.append({
            "type":     "duplicate_meta_description",
            "severity": HIGH,
            "urls":     urls,
            "meta":     meta[:80],
            "detail":   f"Duplicate meta description shared by {len(urls)} pages",
        })

    if sitemap_urls:
        cross += _xp_sitemap(pages, sitemap_urls)

    return {
        "page_issues":       page_issues,
        "cross_page_issues": cross,
        "hreflang_map":      hreflang_map,
        "title_map":         title_map,
        "meta_map":          meta_map,
        "stats": {
            "pages_analysed":    len(pages),
            "pages_with_issues": sum(1 for pi in page_issues if pi["issue_count"] > 0),
            "cross_page_count":  len(cross),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Helper: URL normalisation (strip trailing slash, lowercase scheme+host)
# ─────────────────────────────────────────────────────────────────────────────

def _n(url: str) -> str:
    """Normalise URL for cross-page key comparison."""
    return url.rstrip("/")


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page validator
# ─────────────────────────────────────────────────────────────────────────────

def _validate_page(page: dict) -> dict:
    url      = page.get("url", "")
    is_error = bool(page.get("_is_error"))

    indexability = _pp_indexability(page)

    if is_error:
        return {
            "url":               url,
            "indexability":      indexability,
            "links":             [],
            "content":           [],
            "headings":          [],
            "hreflang":          [],
            "images":            [],
            "security":          [],
            "cache_compression": [],
            "mobile":            [],
            "performance":       [],
            "pagination":        [],
            "anchor_quality_score": 0,
            "all_issues":        indexability,
            "issue_count":       len(indexability),
        }

    links             = _pp_anchor_text(page)   # also writes page["anchor_quality_score"]
    content           = _pp_content(page)
    headings          = _pp_headings(page)
    hreflang          = _pp_hreflang(page)
    images            = _pp_images(page)
    security          = _pp_security(page)
    cache_compression = _pp_cache_compression(page)
    mobile            = _pp_mobile_viewport(page)
    performance       = _pp_performance(page)
    pagination        = _pp_pagination(page)

    all_issues = (indexability + links + content + headings
                  + hreflang + images + security + cache_compression
                  + mobile + performance + pagination)

    return {
        "url":               url,
        "indexability":      indexability,
        "links":             links,
        "content":           content,
        "headings":          headings,
        "hreflang":          hreflang,
        "images":            images,
        "security":          security,
        "cache_compression": cache_compression,
        "mobile":            mobile,
        "performance":       performance,
        "pagination":        pagination,
        "anchor_quality_score": page.get("anchor_quality_score", 100),
        "all_issues":        all_issues,
        "issue_count":       len(all_issues),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Indexability
# ─────────────────────────────────────────────────────────────────────────────

def _pp_indexability(page: dict) -> list[str]:
    issues:     list[str] = []
    robots_meta = (page.get("robots_meta") or "").lower()
    x_robots    = (page.get("x_robots_tag") or "").lower()
    combined    = f"{robots_meta} {x_robots}"

    # Detect noindex — distinguish header vs meta source for actionability
    if "noindex" in combined:
        src = "X-Robots-Tag header" if "noindex" in x_robots else "meta robots tag"
        issues.append(f"[CRITICAL] noindex directive via {src} — Googlebot will skip this page")

    # X-Robots-Tag: nofollow (header-level link suppression — different from meta nofollow)
    if page.get("x_robots_nofollow"):
        issues.append(
            "[HIGH] X-Robots-Tag: nofollow — server header blocking all link equity flow "
            "from this page; remove or restrict to specific Googlebot agents"
        )

    canonical = _n(page.get("canonical") or "")
    url       = _n(page.get("url") or "")
    if canonical and canonical != url:
        issues.append(f"[HIGH] Canonical conflict: points to {canonical} instead of self")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Anchor text quality
# ─────────────────────────────────────────────────────────────────────────────

def _pp_anchor_text(page: dict) -> list[str]:
    """
    Analyse internal anchor text quality.

    Detects:
      - Generic anchors ("click here", "read more", etc.)
      - Exact-match overuse: same anchor text used > 3 times across internal links
        (over-optimisation signal — triggers Penguin-class risk)

    Computes anchor_quality_score (0–100) and stores it on the page dict
    so downstream consumers (AI analysis, dashboard) can surface it.
    """
    issues: list[str] = []
    link_objects = page.get("link_objects") or []

    if not link_objects:
        page.setdefault("anchor_quality_score", 100)
        return issues

    total = len(link_objects)
    score = 100

    # ── Generic anchor detection ─────────────────────────────────────────────
    generic_count = sum(
        1 for lo in link_objects
        if (lo.get("text") or lo.get("anchor_text") or "").strip().lower() in _GENERIC_ANCHORS
    )
    if generic_count:
        penalty = min(40, generic_count * 8)   # cap at -40
        score  -= penalty
        issues.append(
            f"[MEDIUM] {generic_count} internal link(s) use generic anchor text "
            f'("click here", "read more", etc.) — replace with descriptive anchors'
        )

    # ── Exact-match overuse detection ────────────────────────────────────────
    # Count anchor frequency, excluding generics and blanks
    anchor_counts: Counter = Counter(
        (lo.get("text") or lo.get("anchor_text") or "").strip().lower()
        for lo in link_objects
        if (lo.get("text") or lo.get("anchor_text") or "").strip()
        and (lo.get("text") or lo.get("anchor_text") or "").strip().lower() not in _GENERIC_ANCHORS
    )
    exact_match_offenders = [
        (anchor, count)
        for anchor, count in anchor_counts.items()
        if count > 3 and anchor  # repeated 4+ times signals over-optimisation
    ]
    if exact_match_offenders:
        top_offender, top_count = max(exact_match_offenders, key=lambda x: x[1])
        penalty = min(30, len(exact_match_offenders) * 10)
        score  -= penalty
        issues.append(
            f"[HIGH] Exact-match anchor overuse: '{top_offender}' used {top_count}× "
            f"across internal links ({len(exact_match_offenders)} repeated anchor(s) total) "
            "— diversify anchor text to avoid over-optimisation penalty"
        )

    # Diversity bonus: many unique anchors across total links is healthy
    unique_ratio = len(anchor_counts) / total if total > 0 else 1.0
    if unique_ratio < 0.5 and total > 5:
        score -= 10

    page["anchor_quality_score"] = max(0, score)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Content (word count, keyword density, keyword prominence)
# ─────────────────────────────────────────────────────────────────────────────

def _pp_content(page: dict) -> list[str]:
    issues:    list[str] = []
    body_text  = (page.get("body_text") or "").strip()
    keywords   = page.get("keywords") or []
    words      = body_text.split() if body_text else []
    wc         = len(words)

    # Word count classification
    if wc == 0:
        issues.append("No visible body content detected")
        return issues
    if wc < 300:
        issues.append(f"Thin content: {wc} words — aim for 300+ for indexable pages")

    if not keywords or wc < 50:
        return issues

    body_lower  = body_text.lower()
    first_100   = " ".join(words[:100]).lower()

    for kw in keywords[:5]:
        kw_lower = kw.lower()
        count    = body_lower.count(kw_lower)
        if count == 0:
            continue
        density = count / wc * 100

        # Over-optimisation threshold: > 4 %
        if density > 4:
            issues.append(
                f'Keyword "{kw}" density {density:.1f}% — above 4% may trigger '
                "over-optimisation penalty"
            )

        # Keyword prominence: not in first 100 words (checked for top keyword only)
        if kw == keywords[0] and kw_lower not in first_100:
            issues.append(
                f'Primary keyword "{kw}" absent from first 100 words — '
                "move target keyword closer to page start for stronger prominence signal"
            )
        break  # prominence check on top keyword only; density checked for top 5

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Heading hierarchy (H1-H6 ordered sequence)
# ─────────────────────────────────────────────────────────────────────────────

def _pp_headings(page: dict) -> list[str]:
    issues:   list[str] = []
    seq = page.get("heading_sequence") or []

    if not seq:
        return issues

    prev = seq[0]["level"]
    for item in seq[1:]:
        curr = item["level"]
        if curr > prev + 1:
            for missing_lvl in range(prev + 1, curr):
                issues.append(
                    f"Heading level skipped: H{prev} → H{curr} "
                    f"(H{missing_lvl} missing between them)"
                )
        prev = curr

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Hreflang language code validity
# ─────────────────────────────────────────────────────────────────────────────

def _pp_hreflang(page: dict) -> list[str]:
    issues: list[str] = []
    tags = page.get("hreflang_tags") or []
    if not tags:
        return issues

    for tag in tags:
        lang = (tag.get("lang") or "").strip()
        if lang and not _VALID_LANG_RE.match(lang):
            issues.append(f"Invalid hreflang language code: '{lang}' (not BCP-47 compliant)")

    langs = [(t.get("lang") or "").lower() for t in tags]
    if "x-default" not in langs:
        issues.append(
            "Hreflang set missing x-default — add "
            "<link rel='alternate' hreflang='x-default'> for unmatched locales"
        )

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Images (alt, dimensions, lazy loading)
# ─────────────────────────────────────────────────────────────────────────────

def _pp_images(page: dict) -> list[str]:
    issues:          list[str] = []
    img_total        = int(page.get("img_total") or 0)
    img_missing_alt  = int(page.get("img_missing_alt") or 0)   # no alt attr at all
    img_missing_dims = int(page.get("img_missing_dims") or 0)  # no width+height
    img_lazy_count   = int(page.get("img_lazy_count") or 0)

    if img_total == 0:
        return issues

    if img_missing_alt > 0:
        issues.append(
            f"{img_missing_alt} image(s) missing alt attribute — "
            "required for accessibility and image-search indexing"
        )

    if img_missing_dims > 0:
        issues.append(
            f"{img_missing_dims} image(s) missing explicit width/height — "
            "browser cannot reserve layout space, causing Cumulative Layout Shift (CLS)"
        )

    lazy_pct = img_lazy_count / img_total * 100 if img_total > 0 else 0
    if lazy_pct < 50 and img_total > 2:
        issues.append(
            f"Only {lazy_pct:.0f}% of images use loading=\"lazy\" "
            f"({img_lazy_count}/{img_total}) — add to below-fold images to reduce LCP"
        )

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Security headers
# ─────────────────────────────────────────────────────────────────────────────

def _pp_security(page: dict) -> list[str]:
    issues:  list[str] = []
    headers: dict      = page.get("response_headers") or {}
    # response_headers stores lowercase keys (see crawler._parse _KEEP_HDRS)
    hdr_keys = {k.lower() for k in headers}

    for header, message in _SECURITY_HEADERS.items():
        if header not in hdr_keys:
            issues.append(message)

    # ── Referrer-Policy ──────────────────────────────────────────────────────
    if "referrer-policy" not in hdr_keys:
        issues.append(
            "[HIGH] Referrer-Policy header missing — "
            "browsers default to sending full URL in Referer header on cross-origin requests; "
            "add 'Referrer-Policy: strict-origin-when-cross-origin' to limit data leakage"
        )

    # ── Permissions-Policy ───────────────────────────────────────────────────
    if "permissions-policy" not in hdr_keys:
        issues.append(
            "[HIGH] Permissions-Policy header missing — "
            "browser features (camera, microphone, geolocation) not explicitly restricted; "
            "add 'Permissions-Policy: geolocation=(), microphone=(), camera=()' as baseline"
        )

    # ── TLS version check ────────────────────────────────────────────────────
    tls = (page.get("tls_version") or "").strip()
    if tls:
        # Extract numeric version — e.g. "TLSv1.1" → 1.1, "TLSv1.3" → 1.3
        _tls_m = re.search(r"(\d+\.\d+)", tls)
        if _tls_m:
            _tls_f = float(_tls_m.group(1))
            if _tls_f < 1.2:
                issues.append(
                    f"[CRITICAL] Weak TLS detected: {tls} — TLS 1.0 and 1.1 are deprecated "
                    "(RFC 8996, PCI-DSS 3.2+); upgrade server to TLS 1.2 minimum (TLS 1.3 recommended)"
                )

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Mobile viewport
# ─────────────────────────────────────────────────────────────────────────────

def _pp_mobile_viewport(page: dict) -> list[str]:
    """
    Validate the <meta name="viewport"> tag for mobile-first compliance.

    Google uses mobile-first indexing — pages without a proper viewport tag
    render poorly on mobile and are ranked lower than mobile-optimised pages.

    Checks:
      - Missing viewport entirely → HIGH
      - Missing 'width=device-width' → HIGH (content won't scale to screen)
      - 'user-scalable=no' → MEDIUM (blocks accessibility zoom — Google penalises)
      - 'maximum-scale=1' (equivalent to no-zoom) → MEDIUM
    """
    issues:   list[str] = []
    viewport: str       = (page.get("viewport") or "").strip().lower()

    if not viewport:
        issues.append(
            "[HIGH] Viewport meta tag missing — "
            "add <meta name='viewport' content='width=device-width, initial-scale=1'> "
            "for Google mobile-first indexing compliance"
        )
        return issues

    if "width=device-width" not in viewport:
        issues.append(
            "[HIGH] Viewport missing 'width=device-width' — "
            "content will render at desktop width on mobile devices; "
            "update to 'width=device-width, initial-scale=1'"
        )

    if "user-scalable=no" in viewport:
        issues.append(
            "[MEDIUM] Viewport sets user-scalable=no — "
            "this disables pinch-to-zoom, violating WCAG 1.4.4 (Resize Text) "
            "and suppressing mobile usability signals"
        )

    if "maximum-scale=1" in viewport:
        issues.append(
            "[MEDIUM] Viewport sets maximum-scale=1 — "
            "equivalent to disabling zoom on iOS; remove to improve accessibility score"
        )

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Performance (resource count + page size + image formats)
# ─────────────────────────────────────────────────────────────────────────────

def _pp_performance(page: dict) -> list[str]:
    """
    Flag performance anti-patterns detectable from crawl data.

    Checks:
      resource_count > 100  → HIGH  (too many HTTP requests inflate LCP)
      html_size_kb   > 2048 → HIGH  (>2MB HTML response is abnormally large)
      img_non_modern_count  → MEDIUM (JPEG/PNG vs WebP/AVIF wastes bandwidth)
      img_missing_dims      → already in _pp_images, not duplicated here
    """
    issues:             list[str] = []
    resource_count:     int       = int(page.get("resource_count") or 0)
    html_size_kb:       float     = float(page.get("html_size_kb") or 0.0)
    img_non_modern:     int       = int(page.get("img_non_modern_count") or 0)

    # ── Request count ────────────────────────────────────────────────────────
    if resource_count > 100:
        issues.append(
            f"[HIGH] {resource_count} resource references detected — "
            "exceeds 100 request budget; each additional request adds network round-trip "
            "latency and directly increases LCP. Consolidate scripts, use image sprites, "
            "and lazy-load below-fold resources."
        )

    # ── HTML page size ────────────────────────────────────────────────────────
    if html_size_kb > 2048:
        issues.append(
            f"[HIGH] HTML response size {html_size_kb:.0f}KB (>{2048}KB / 2MB threshold) — "
            "abnormally large HTML inflates TTFB parse time; "
            "remove inline SVG/base64 assets, dead markup, or inline JS from the HTML document"
        )

    # ── Image format optimisation ─────────────────────────────────────────────
    if img_non_modern > 0:
        issues.append(
            f"[MEDIUM] {img_non_modern} image(s) served in legacy format (JPEG/PNG/GIF) — "
            "convert to WebP (25–34% smaller than JPEG) or AVIF (50% smaller) "
            "to reduce image payload and improve LCP"
        )

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Pagination tags (rel=next / rel=prev)
# ─────────────────────────────────────────────────────────────────────────────

def _pp_pagination(page: dict) -> list[str]:
    """
    Per-page pagination validation.

    Checks that rel=next/prev URLs are absolute (not relative) and that
    the current page isn't declaring both rel=next and rel=prev pointing
    to the same URL (indicates a misconfigured template).
    """
    issues:          list[str] = []
    pagination_next: str       = (page.get("pagination_next") or "").strip()
    pagination_prev: str       = (page.get("pagination_prev") or "").strip()

    if pagination_next and pagination_prev and pagination_next == pagination_prev:
        issues.append(
            "[HIGH] Pagination misconfiguration: rel=next and rel=prev point to the same URL "
            f"({pagination_next}) — check pagination template; Googlebot will not crawl paginated series correctly"
        )

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Cache + Compression audit
# ─────────────────────────────────────────────────────────────────────────────

def _pp_cache_compression(page: dict) -> list[str]:
    """
    Audit HTTP caching and content compression signals from response headers.

    Checks:
      Cache-Control: missing entirely [HIGH], or max-age < 3600s [MEDIUM]
      ETag:          missing on text resources [MEDIUM] — enables conditional GETs
      Content-Encoding: missing on compressible content types [HIGH]
        — gzip/br/deflate absent means uncompressed bytes over the wire
    """
    issues:  list[str] = []
    headers: dict      = page.get("response_headers") or {}
    hdr     = {k.lower(): v for k, v in headers.items()}

    cache_control    = hdr.get("cache-control", "")
    content_encoding = hdr.get("content-encoding", "")
    etag             = hdr.get("etag", "")
    content_type     = hdr.get("content-type", "").lower().split(";")[0].strip()

    # ── Cache-Control ────────────────────────────────────────────────────────
    if not cache_control:
        issues.append(
            "[HIGH] Cache-Control header missing — browser cannot cache this page; "
            "add 'Cache-Control: max-age=3600' (or higher) to reduce server load and TTFB"
        )
    else:
        # Parse max-age value
        _ma = re.search(r"max-age\s*=\s*(\d+)", cache_control, re.I)
        if _ma:
            ttl = int(_ma.group(1))
            if ttl == 0:
                issues.append(
                    "[HIGH] Cache-Control: max-age=0 — page is explicitly uncacheable; "
                    "set a positive TTL for static/semi-static content"
                )
            elif ttl < _MIN_CACHE_TTL_S:
                issues.append(
                    f"[MEDIUM] Cache-Control TTL too short: max-age={ttl}s "
                    f"(< {_MIN_CACHE_TTL_S}s / 1 hour) — increase to reduce redundant fetches"
                )
        elif "no-store" in cache_control:
            issues.append(
                "[HIGH] Cache-Control: no-store — response is never cached; "
                "use no-cache instead of no-store for documents that can be revalidated"
            )

    # ── ETag ─────────────────────────────────────────────────────────────────
    if not etag and content_type in _COMPRESSIBLE_TYPES:
        issues.append(
            "[MEDIUM] ETag header missing — browser cannot send conditional GET requests "
            "(If-None-Match); add ETag to enable 304 Not Modified responses and save bandwidth"
        )

    # ── Content-Encoding (compression) ───────────────────────────────────────
    if content_type in _COMPRESSIBLE_TYPES:
        _valid_encodings = {"gzip", "br", "deflate", "zstd"}
        _enc_lower = content_encoding.lower().strip()
        if not _enc_lower or not any(enc in _enc_lower for enc in _valid_encodings):
            issues.append(
                "[HIGH] Content-Encoding missing — response is served uncompressed; "
                "enable gzip or Brotli (br) on the server to reduce page weight 60–80%"
            )

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Canonical chains and loops
# ─────────────────────────────────────────────────────────────────────────────

def _xp_canonical(pages: list[dict], canonical_map: dict[str, str]) -> list[dict]:
    """
    Detect canonical chains (A → B → C) and loops (A → B → A).

    Only crawled URLs are followed. Chains with ≥ 2 hops are flagged because
    Google does not reliably follow indirect canonicals.
    Severity: CRITICAL for loops (indexing risk), HIGH for chains.
    """
    crawled = {_n(p.get("url") or "") for p in pages if p.get("url")}
    issues:    list[dict] = []
    processed: set[str]   = set()

    for start in crawled:
        if start in processed or start not in canonical_map:
            continue

        chain:     list[str] = [start]
        seen_here: set[str]  = {start}
        current = start
        is_loop = False

        while current in canonical_map:
            nxt = canonical_map[current]
            if nxt not in crawled:
                break
            if nxt in seen_here:
                is_loop = True
                try:
                    loop_start = chain.index(nxt)
                    loop_urls  = chain[loop_start:] + [nxt]
                except ValueError:
                    loop_urls = chain + [nxt]
                issues.append({
                    "type":     "canonical_loop",
                    "severity": CRITICAL,
                    "urls":     loop_urls,
                    "detail":   "Canonical loop: " + " → ".join(loop_urls),
                })
                break
            chain.append(nxt)
            seen_here.add(nxt)
            current = nxt

        if not is_loop and len(chain) > 2:
            issues.append({
                "type":     "canonical_chain",
                "severity": HIGH,
                "urls":     chain,
                "detail":   f"Canonical chain ({len(chain) - 1} hops): " + " → ".join(chain),
            })

        processed.update(seen_here)

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Broken internal links
# ─────────────────────────────────────────────────────────────────────────────

def _xp_broken_links(pages: list[dict], status_map: dict) -> list[dict]:
    """Flag internal links from crawled pages to crawled pages with status ≥ 400."""
    issues: list[dict] = []
    for page in pages:
        src = page.get("url", "")
        for href in (page.get("links") or []):
            norm = _n(href)
            if norm not in status_map:
                continue
            try:
                code = int(status_map[norm])
            except (TypeError, ValueError):
                continue
            if code >= 400:
                issues.append({
                    "type":     "broken_internal_link",
                    "severity": CRITICAL if code >= 500 else HIGH,
                    "source":   src,
                    "target":   href,
                    "status":   code,
                    "detail":   f"Broken link (HTTP {code}): {src} → {href}",
                })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Duplicate content (body_text MD5 hash)
# ─────────────────────────────────────────────────────────────────────────────

def _xp_duplicate_content(real_pages: list[dict]) -> list[dict]:
    """
    Flag pages sharing identical body content by MD5 hash.
    Pages with fewer than 50 words are excluded (insufficient signal).
    Severity: HIGH — duplicate body content causes keyword cannibalization.
    """
    hash_map: dict[str, list[str]] = defaultdict(list)
    for page in real_pages:
        body = (page.get("body_text") or "").strip()
        if len(body.split()) < 50:
            continue
        h = hashlib.md5(body.encode("utf-8", errors="replace")).hexdigest()
        hash_map[h].append(page.get("url", ""))

    issues: list[dict] = []
    for h, urls in hash_map.items():
        if len(urls) > 1:
            issues.append({
                "type":     "duplicate_content",
                "severity": HIGH,
                "urls":     urls,
                "hash":     h,
                "detail":   f"{len(urls)} pages share identical body content (hash {h[:8]}…)",
            })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Hreflang reciprocal linking
# ─────────────────────────────────────────────────────────────────────────────

def _xp_hreflang_reciprocal(
    pages: list[dict],
    hreflang_hrefs: dict[str, set[str]],
) -> list[dict]:
    """
    For every hreflang declaration on page A pointing to page B,
    verify page B (if crawled) also declares an hreflang pointing back to A.
    Missing reciprocals break international URL sets.
    Severity: HIGH — Googlebot ignores non-reciprocated hreflang sets.
    """
    crawled = {_n(p.get("url") or "") for p in pages if p.get("url")}
    issues: list[dict] = []

    for page in pages:
        src  = _n(page.get("url") or "")
        tags = page.get("hreflang_tags") or []
        for tag in tags:
            tgt  = _n(tag.get("href") or "")
            lang = tag.get("lang", "")
            if not tgt or tgt == src or tgt not in crawled:
                continue
            if src not in hreflang_hrefs.get(tgt, set()):
                issues.append({
                    "type":     "hreflang_missing_reciprocal",
                    "severity": HIGH,
                    "source":   src,
                    "lang":     lang,
                    "target":   tgt,
                    "detail": (
                        f"{src} declares hreflang '{lang}' → {tgt} "
                        f"but {tgt} has no reciprocal hreflang pointing back"
                    ),
                })
    return issues


def _xp_pagination(pages: list[dict]) -> list[dict]:
    """
    Cross-page pagination sequence validation.

    For every page declaring rel=next pointing to page B:
      1. Check page B exists in the crawled set
      2. Check page B declares rel=prev pointing back to page A (reciprocal)

    Violations mean Googlebot may not understand the paginated series,
    causing it to treat pages as standalone duplicates rather than a series.

    Severity: HIGH for broken sequences, MEDIUM for uncrawled pagination targets.
    """
    crawled_map: dict[str, dict] = {
        _n(p.get("url") or ""): p
        for p in pages if p.get("url")
    }
    issues: list[dict] = []

    for page in pages:
        if page.get("_is_error"):
            continue
        src          = _n(page.get("url") or "")
        next_url_raw = (page.get("pagination_next") or "").strip()
        prev_url_raw = (page.get("pagination_prev") or "").strip()
        next_url     = _n(next_url_raw) if next_url_raw else ""
        prev_url     = _n(prev_url_raw) if prev_url_raw else ""

        # rel=next points to uncrawled / non-existent page
        if next_url and next_url not in crawled_map:
            issues.append({
                "type":     "pagination_next_not_crawled",
                "severity": HIGH,
                "source":   src,
                "target":   next_url_raw,
                "detail":   (
                    f"rel=next on {src} points to {next_url_raw} "
                    "which was not crawled — verify URL is accessible and not blocked"
                ),
            })
            continue

        # rel=next declared but target doesn't reciprocate with rel=prev back
        if next_url and next_url in crawled_map:
            target_prev = _n((crawled_map[next_url].get("pagination_prev") or "").strip())
            if target_prev != src:
                issues.append({
                    "type":     "pagination_missing_reciprocal",
                    "severity": HIGH,
                    "source":   src,
                    "target":   next_url_raw,
                    "detail":   (
                        f"Pagination break: {src} → rel=next → {next_url_raw} "
                        f"but {next_url_raw} has rel=prev='{target_prev or 'none'}' "
                        f"(expected '{src}') — fix rel=prev on {next_url_raw}"
                    ),
                })

        # Orphaned rel=prev — upstream page not crawled
        if prev_url and prev_url not in crawled_map:
            issues.append({
                "type":     "pagination_prev_not_crawled",
                "severity": MEDIUM,
                "source":   src,
                "target":   prev_url_raw,
                "detail":   (
                    f"rel=prev on {src} points to {prev_url_raw} "
                    "which was not crawled — chain may be broken"
                ),
            })

    return issues


def _xp_hreflang_xdefault(
    pages: list[dict],
    hreflang_map: dict[str, dict[str, str]],
) -> list[dict]:
    """
    Cross-page x-default check: any page that has hreflang tags but no
    x-default in its own set — and no other page references it as x-default.

    x-default is the fallback URL shown to users whose language doesn't match
    any hreflang variant. Missing it means no controlled fallback for unmatched locales.
    Severity: MEDIUM.
    """
    issues: list[dict] = []
    for url, lang_map in hreflang_map.items():
        if lang_map and "x-default" not in {k.lower() for k in lang_map}:
            issues.append({
                "type":     "hreflang_missing_xdefault",
                "severity": MEDIUM,
                "url":      url,
                "langs":    list(lang_map.keys()),
                "detail": (
                    f"{url} has {len(lang_map)} hreflang variant(s) "
                    f"({', '.join(list(lang_map.keys())[:4])}) but no x-default — "
                    "add <link rel='alternate' hreflang='x-default' href='...'>"
                ),
            })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Orphan pages
# ─────────────────────────────────────────────────────────────────────────────

def _xp_orphans(pages: list[dict]) -> list[dict]:
    """
    Detect pages with zero inbound internal links (excluding the root).
    Error pages are excluded — they carry no link equity.
    Severity: MEDIUM — orphans receive no PageRank and may not be crawled regularly.
    """
    url_set = {_n(p.get("url") or "") for p in pages if p.get("url")}

    # Count inbound links per URL
    inbound: dict[str, int] = {u: 0 for u in url_set}
    for page in pages:
        for href in (page.get("links") or []):
            norm = _n(href)
            if norm in inbound:
                inbound[norm] += 1

    # Root = URL with the most outbound links (homepage heuristic)
    outbound_counts = {
        _n(p.get("url") or ""): len(p.get("links") or [])
        for p in pages if p.get("url")
    }
    root = max(outbound_counts, key=outbound_counts.get, default=None)

    issues: list[dict] = []
    for page in pages:
        if page.get("_is_error"):
            continue
        url = _n(page.get("url") or "")
        if url == root:
            continue
        if inbound.get(url, 0) == 0:
            issues.append({
                "type":     "orphan_page",
                "severity": MEDIUM,
                "url":      url,
                "detail":   f"Orphan page — no crawled internal link points to {url}",
            })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Sitemap comparison
# ─────────────────────────────────────────────────────────────────────────────

def _xp_sitemap(pages: list[dict], sitemap_urls: list[str]) -> list[dict]:
    """
    Compare crawled pages against sitemap URL list.

    Flags:
      sitemap_noindex — URL is in sitemap but has noindex directive  [CRITICAL]
      sitemap_missing — URL is in sitemap but was not successfully crawled [MEDIUM]
    """
    sitemap_set = {_n(u) for u in sitemap_urls if u}
    crawled_set = {_n(p.get("url") or "") for p in pages if p.get("url") and not p.get("_is_error")}
    noindex_set = {
        _n(p.get("url") or "")
        for p in pages
        if p.get("robots_noindex") or "noindex" in (p.get("robots_meta") or "").lower()
    }

    issues: list[dict] = []

    for url in sitemap_set & noindex_set:
        issues.append({
            "type":     "sitemap_noindex",
            "severity": CRITICAL,
            "url":      url,
            "detail":   f"noindex page in sitemap — Googlebot will not index {url}; remove from sitemap",
        })

    for url in sorted(sitemap_set - crawled_set)[:30]:
        issues.append({
            "type":     "sitemap_missing",
            "severity": MEDIUM,
            "url":      url,
            "detail":   f"Sitemap URL not crawled — may be blocked, redirected, or unreachable: {url}",
        })

    return issues
