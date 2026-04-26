"""
full_audit_engine.py — Zero-gap SEO audit engine for CrawlIQ.

Audits every crawled page across all five clusters with exhaustive signal
coverage. No signal is skipped. No data is assumed — only validated.

Clusters
────────
  1. INDEXABILITY  — robots.txt, meta/X-Robots, canonical, status, redirects,
                     sitemap conflicts, orphan pages
  2. ON-PAGE SEO   — title, meta, headings, keywords, content quality,
                     internal linking, images, OG/Twitter, structured data
  3. TECHNICAL SEO — crawl depth, link graph, canonical system, hreflang,
                     pagination, mobile, URL structure
  4. PERFORMANCE   — CWV, page size, resource budget, compression, caching,
                     image optimisation, render-blocking
  5. SECURITY      — HTTPS, HSTS, CSP, X-Frame, X-Content-Type,
                     Referrer-Policy, Permissions-Policy, TLS, mixed content

Cross-cluster validation
────────────────────────
  • Sitemap ↔ robots.txt ↔ indexability
  • Canonical ↔ hreflang
  • Internal links ↔ status codes
  • CWV ↔ page size ↔ resource count
  • Security headers ↔ performance (CSP blocking assets)

Entry point
───────────
    from full_audit_engine import run_exhaustive_audit

    result = run_exhaustive_audit(
        pages,                        # list[dict] — crawler output
        sitemap_urls=None,            # list[str]  — parsed from sitemap XML
        robots_txt_content=None,      # str        — raw robots.txt text
        cwv_data=None,                # dict[url → {lcp_s, cls, inp_s}]
    )

Output
──────
    {
      "pages": [
        {
          "url": str,
          "issues": [{"type", "cluster", "severity", "explanation", "fix"}],
          "scores": {"indexability": int, "on_page": int, "technical": int,
                     "performance": int, "security": int}
        }
      ],
      "site_summary": {
        "total_issues": int,
        "critical": int, "high": int, "medium": int, "low": int,
        "by_cluster": {cluster: int},
        "top_issues": [most-frequent issue types],
        "site_score": int,             # 0-100 weighted average
        "site_grade": str              # A+ / A / B / Needs Fix
      }
    }
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import urlparse, parse_qs

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

CRITICAL = "CRITICAL"
HIGH     = "HIGH"
MEDIUM   = "MEDIUM"
LOW      = "LOW"

_SEVERITY_PTS = {CRITICAL: 20, HIGH: 12, MEDIUM: 5, LOW: 2}

_CLUSTERS = ("indexability", "on_page", "technical", "performance", "security")

_REQUIRED_SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
)

_GENERIC_ANCHORS = frozenset({
    "click here", "here", "read more", "learn more", "more", "link",
    "this", "page", "website", "visit", "view", "see", "details",
    "info", "information", "source", "article",
})

_MODERN_IMG_EXTS = frozenset({".webp", ".avif", ".jxl"})
_LEGACY_IMG_RE   = re.compile(r'\.(jpe?g|png|gif|bmp|tiff?)(\?|#|$)', re.I)
_CLEAN_URL_RE    = re.compile(r'^https?://[^/]+(/[a-z0-9/_\-\.~%]*)?(\?[^#]*)?$', re.I)
_EXCESS_PARAM_RE = re.compile(r'[?&][^=]+=', re.I)

# BCP-47 language code validator
_LANG_RE = re.compile(
    r'^(x-default|[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2}|[0-9]{3})?(-[a-z0-9]{5,8})*)$'
)

# ─────────────────────────────────────────────────────────────────────────────
#  Issue builder
# ─────────────────────────────────────────────────────────────────────────────

def _issue(
    itype:       str,
    cluster:     str,
    severity:    str,
    explanation: str,
    fix:         str,
) -> dict:
    return {
        "type":        itype,
        "cluster":     cluster,
        "severity":    severity,
        "explanation": explanation,
        "fix":         fix,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm(url: str) -> str:
    return url.rstrip("/").lower()


def _hdrs(page: dict) -> dict:
    """Return response_headers normalised to lowercase keys."""
    raw = page.get("response_headers") or {}
    return {k.lower(): v for k, v in raw.items()}


def _url_depth(url: str) -> int:
    """Count path segments (slashes) after the domain."""
    path = urlparse(url).path.rstrip("/")
    return len([s for s in path.split("/") if s])


def _url_param_count(url: str) -> int:
    qs = urlparse(url).query
    return len(parse_qs(qs)) if qs else 0


def _keyword_density(text: str, keyword: str) -> float:
    if not text or not keyword:
        return 0.0
    words = text.lower().split()
    if not words:
        return 0.0
    count = sum(1 for w in words if keyword.lower() in w)
    return round(count / len(words) * 100, 2)


def _score_from_issues(issues: list[dict], cluster: str) -> int:
    score = 100
    for iss in issues:
        if iss.get("cluster") != cluster:
            continue
        score -= _SEVERITY_PTS.get(iss.get("severity", LOW), 2)
    return max(0, score)


# ─────────────────────────────────────────────────────────────────────────────
#  CLUSTER 1 — INDEXABILITY
# ─────────────────────────────────────────────────────────────────────────────

def _check_robots_block(page: dict, disallowed_paths: list[str]) -> list[dict]:
    """Check if the page URL is blocked by robots.txt Disallow rules."""
    issues = []
    url    = page.get("url", "")
    path   = urlparse(url).path or "/"

    for rule in disallowed_paths:
        if not rule:
            continue
        # Simple prefix matching (covers most real-world robots rules)
        if path.startswith(rule) or rule == "/":
            issues.append(_issue(
                "robots_blocked",
                "indexability",
                CRITICAL,
                f"Path '{path}' matches Disallow: {rule} in robots.txt — Googlebot cannot crawl this page.",
                "Remove the Disallow rule from robots.txt or restrict it to paths that should be blocked.",
            ))
            break
    return issues


def _check_meta_robots(page: dict) -> list[dict]:
    """Validate meta robots directives."""
    issues = []
    meta   = (page.get("robots_meta") or "").lower()
    url    = page.get("url", "")

    if not meta or meta in ("index,follow", "index, follow", "all"):
        return issues  # healthy defaults

    if "noindex" in meta:
        issues.append(_issue(
            "meta_noindex",
            "indexability",
            CRITICAL,
            f"Page has meta robots 'noindex' — Googlebot will crawl but not index this page.",
            "Remove 'noindex' from the meta robots tag if this page should appear in search results.",
        ))

    if "nofollow" in meta:
        issues.append(_issue(
            "meta_nofollow",
            "indexability",
            HIGH,
            "Page has meta robots 'nofollow' — all outbound links on this page are ignored for PageRank.",
            "Remove 'nofollow' unless you intentionally want to suppress link equity on this page.",
        ))

    if "none" in meta:
        issues.append(_issue(
            "meta_robots_none",
            "indexability",
            CRITICAL,
            "Meta robots is set to 'none' — equivalent to noindex + nofollow.",
            "Replace 'none' with 'index,follow' if this page should be indexed.",
        ))

    # Conflict: noindex + canonical pointing elsewhere
    canonical = (page.get("canonical") or "").rstrip("/")
    norm_url  = url.rstrip("/")
    if "noindex" in meta and canonical and canonical != norm_url:
        issues.append(_issue(
            "noindex_with_foreign_canonical",
            "indexability",
            CRITICAL,
            f"Page has noindex AND a canonical pointing to '{canonical}'. "
            "These are contradictory — noindex suppresses the page while canonical signals it should be consolidated.",
            "Choose one: either remove noindex (to let the page rank) or use a self-canonical (to exclude it cleanly).",
        ))

    return issues


def _check_x_robots(page: dict) -> list[dict]:
    """Check X-Robots-Tag header directives."""
    issues   = []
    x_robots = (page.get("x_robots_tag") or "").lower()
    hdrs     = _hdrs(page)
    x_hdr    = hdrs.get("x-robots-tag", "").lower()
    combined = f"{x_robots} {x_hdr}".strip()

    if not combined:
        return issues

    if "noindex" in combined:
        issues.append(_issue(
            "x_robots_noindex",
            "indexability",
            CRITICAL,
            "Server sends X-Robots-Tag: noindex header — this takes precedence over meta robots and blocks indexing.",
            "Remove the noindex directive from the X-Robots-Tag response header in your server or CDN configuration.",
        ))

    if "nofollow" in combined:
        issues.append(_issue(
            "x_robots_nofollow",
            "indexability",
            HIGH,
            "Server sends X-Robots-Tag: nofollow — all links on this page are suppressed at the server level.",
            "Remove 'nofollow' from the X-Robots-Tag header unless intentional.",
        ))

    if "none" in combined:
        issues.append(_issue(
            "x_robots_none",
            "indexability",
            CRITICAL,
            "X-Robots-Tag: none — server-level noindex + nofollow combination blocking all crawl signals.",
            "Remove 'none' from the X-Robots-Tag header.",
        ))

    return issues


def _check_status_code(page: dict) -> list[dict]:
    """Validate HTTP status code and detect soft 404s."""
    issues      = []
    status      = page.get("status_code")
    url         = page.get("url", "")
    body_text   = (page.get("body_text") or "")
    title       = (page.get("title") or "").lower()
    word_count  = len(body_text.split())

    if not isinstance(status, int):
        return issues

    if status in (301, 302, 303, 307, 308):
        issues.append(_issue(
            "redirect_status",
            "indexability",
            HIGH,
            f"Page returns HTTP {status} redirect. Crawlers follow this but it costs a hop and dilutes link signals.",
            f"{'Update internal links to point directly to the final destination.' if status in (302, 307) else '301 is correct; ensure internal links target the final URL directly.'}",
        ))
    elif status == 404:
        issues.append(_issue(
            "page_not_found",
            "indexability",
            CRITICAL,
            "Page returns HTTP 404 — it cannot be indexed and any inbound links lose their value.",
            "Restore the page, create a 301 redirect to the correct URL, or remove all internal links to it.",
        ))
    elif status == 410:
        issues.append(_issue(
            "page_gone",
            "indexability",
            HIGH,
            "Page returns HTTP 410 Gone — signals intentional permanent removal to Googlebot.",
            "Ensure this is intentional. If the content should exist, restore it and return 200.",
        ))
    elif status >= 500:
        issues.append(_issue(
            "server_error",
            "indexability",
            CRITICAL,
            f"Page returns HTTP {status} server error — blocks indexing and signals instability to Google.",
            "Fix the server-side error. Check application logs for the root cause.",
        ))
    elif status == 200:
        # Soft 404 detection: 200 status but suspiciously thin content with error-like title
        soft_404_title_signals = ("not found", "404", "page not found", "error", "doesn't exist")
        title_signals = any(s in title for s in soft_404_title_signals)
        if word_count < 50 and title_signals:
            issues.append(_issue(
                "soft_404",
                "indexability",
                HIGH,
                f"Page returns 200 but appears to be a soft 404 — title suggests 'not found' and body has only {word_count} words.",
                "Return a proper 404 or 410 status, or add meaningful content if the page should exist.",
            ))
        # Soft 404 by thin content alone (no real content = likely generated error page)
        elif word_count < 20 and not page.get("h1") and not page.get("title"):
            issues.append(_issue(
                "empty_200_page",
                "indexability",
                HIGH,
                "Page returns 200 with virtually no content, no title, and no H1 — likely a misconfigured empty page.",
                "Add real content or return a 404 status if the page has no useful content.",
            ))

    return issues


def _check_redirect_chain(page: dict) -> list[dict]:
    """Flag redirect chains longer than 1 hop."""
    issues = []
    hops   = page.get("redirect_hops", 0)

    if hops == 1:
        issues.append(_issue(
            "redirect_single_hop",
            "indexability",
            MEDIUM,
            f"Page is reached via 1 redirect hop — a single redirect is acceptable but internal links should target the final URL.",
            "Update internal links to point directly to the final destination URL.",
        ))
    elif hops >= 2:
        issues.append(_issue(
            "redirect_chain",
            "indexability",
            HIGH,
            f"Page is reached via {hops} redirect hops — each hop adds latency and leaks PageRank.",
            "Flatten the redirect chain so all links resolve in ≤1 hop to the final destination.",
        ))

    return issues


def _check_canonical_indexability(page: dict, pages_map: dict) -> list[dict]:
    """Validate canonical against indexability of the target."""
    issues    = []
    url       = _norm(page.get("url", ""))
    canonical = _norm(page.get("canonical") or "")

    if not canonical or canonical == url:
        return issues  # self-canonical or not set

    target = pages_map.get(canonical)
    if target is None:
        return issues  # target not crawled — can't validate

    target_noindex = (
        target.get("noindex")
        or "noindex" in (target.get("robots_meta") or "").lower()
        or "noindex" in (target.get("x_robots_tag") or "").lower()
    )
    target_status = target.get("status_code", 200)

    if target_noindex:
        issues.append(_issue(
            "canonical_to_noindex",
            "indexability",
            CRITICAL,
            f"Canonical points to '{canonical}' which has a noindex directive — "
            "Google cannot index the canonical target, effectively de-indexing this page too.",
            "Fix the canonical: either point it to an indexable page or remove noindex from the target.",
        ))

    if isinstance(target_status, int) and target_status >= 400:
        issues.append(_issue(
            "canonical_to_error_page",
            "indexability",
            CRITICAL,
            f"Canonical points to '{canonical}' which returns HTTP {target_status} — "
            "Google will discard the canonical signal and may not index either page.",
            "Update the canonical to point to a live 200-OK page.",
        ))

    return issues


def _audit_indexability(
    page:             dict,
    disallowed_paths: list[str],
    sitemap_set:      set[str],
    pages_map:        dict,
    internally_linked_urls: set[str],
) -> list[dict]:
    """Run all indexability checks for a single page."""
    issues: list[dict] = []

    issues += _check_robots_block(page, disallowed_paths)
    issues += _check_meta_robots(page)
    issues += _check_x_robots(page)
    issues += _check_status_code(page)
    issues += _check_redirect_chain(page)
    issues += _check_canonical_indexability(page, pages_map)

    url      = _norm(page.get("url", ""))
    noindex  = (
        page.get("noindex")
        or "noindex" in (page.get("robots_meta") or "").lower()
        or "noindex" in (page.get("x_robots_tag") or "").lower()
    )
    status   = page.get("status_code", 200)
    in_sitemap = url in sitemap_set

    # Sitemap conflicts
    if in_sitemap:
        if noindex:
            issues.append(_issue(
                "sitemap_noindex_conflict",
                "indexability",
                CRITICAL,
                "Page is listed in the sitemap but has a noindex directive — contradictory signals waste crawl budget.",
                "Remove the URL from the sitemap, or remove the noindex directive if the page should be indexed.",
            ))
        if isinstance(status, int) and status >= 400:
            issues.append(_issue(
                "sitemap_error_page",
                "indexability",
                CRITICAL,
                f"Sitemap lists this URL but it returns HTTP {status} — crawlers waste budget fetching dead pages.",
                "Remove the URL from sitemap.xml and fix or 301-redirect it.",
            ))

    # Orphan page: in sitemap but not internally linked
    if in_sitemap and url not in internally_linked_urls:
        issues.append(_issue(
            "orphan_page",
            "indexability",
            HIGH,
            "Page is in the sitemap but has no inbound internal links — Googlebot relies on links for discovery beyond sitemaps.",
            "Add at least one contextual internal link from a related page to improve crawl accessibility.",
        ))

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CLUSTER 2 — ON-PAGE SEO
# ─────────────────────────────────────────────────────────────────────────────

def _check_title(page: dict, title_counts: Counter) -> list[dict]:
    """Full title validation."""
    issues = []
    title  = (page.get("title") or "").strip()
    url    = page.get("url", "")
    kws    = page.get("keywords") or []
    kw1    = kws[0] if kws else ""

    if not title:
        issues.append(_issue(
            "missing_title",
            "on_page",
            CRITICAL,
            "Page has no <title> tag — Google auto-generates a title, often poorly, reducing SERP click-through.",
            "Add a unique, descriptive <title> tag (30–60 characters) containing the primary keyword.",
        ))
        return issues

    length = len(title)
    if length < 30:
        issues.append(_issue(
            "title_too_short",
            "on_page",
            MEDIUM,
            f"Title is {length} characters — too short to fully describe the page or include keyword context.",
            "Expand title to 30–60 characters; include the primary keyword naturally.",
        ))
    elif length > 60:
        issues.append(_issue(
            "title_too_long",
            "on_page",
            MEDIUM,
            f"Title is {length} characters — truncated in SERP at ~580px (≈60 chars), hiding keywords at the end.",
            "Rewrite to ≤60 characters; place the primary keyword at the beginning.",
        ))

    if title_counts[title.lower()] > 1:
        issues.append(_issue(
            "duplicate_title",
            "on_page",
            HIGH,
            f"Title '{title[:50]}' is shared by multiple pages — Google cannot distinguish which to rank for a given query.",
            "Write a unique title for each page reflecting its specific topic and primary keyword.",
        ))

    if kw1 and kw1.lower() not in title.lower():
        issues.append(_issue(
            "keyword_absent_from_title",
            "on_page",
            HIGH,
            f"Primary keyword '{kw1}' is not present in the page title — misses the strongest on-page ranking signal.",
            f"Include '{kw1}' naturally in the title, ideally near the beginning.",
        ))

    # Over-optimisation: keyword repeated >2× in title
    if kw1 and title.lower().count(kw1.lower()) > 2:
        issues.append(_issue(
            "title_keyword_stuffing",
            "on_page",
            HIGH,
            f"Keyword '{kw1}' appears {title.lower().count(kw1.lower())}× in the title — Google treats this as spammy.",
            "Use the keyword once naturally; vary with synonyms if needed.",
        ))

    return issues


def _check_meta_description(page: dict, meta_counts: Counter) -> list[dict]:
    """Full meta description validation."""
    issues = []
    meta   = (page.get("meta_description") or "").strip()

    if not meta:
        issues.append(_issue(
            "missing_meta_description",
            "on_page",
            HIGH,
            "No meta description — Google generates one automatically, often with poor click-through messaging.",
            "Write a unique meta description of 120–160 characters with a clear value proposition and CTA.",
        ))
        return issues

    length = len(meta)
    if length < 120:
        issues.append(_issue(
            "meta_description_too_short",
            "on_page",
            MEDIUM,
            f"Meta description is {length} characters — under-utilises available SERP space (120–160 recommended).",
            "Expand to 120–160 characters; include the primary keyword and a call to action.",
        ))
    elif length > 160:
        issues.append(_issue(
            "meta_description_too_long",
            "on_page",
            MEDIUM,
            f"Meta description is {length} characters — truncated in SERP, cutting off the CTA.",
            "Trim to ≤160 characters; place the most important content first.",
        ))

    if meta_counts[meta.lower()] > 1:
        issues.append(_issue(
            "duplicate_meta_description",
            "on_page",
            HIGH,
            "Meta description is identical on multiple pages — reduces click diversity and signals low content quality.",
            "Write a unique meta description for every page.",
        ))

    return issues


def _check_headings(page: dict) -> list[dict]:
    """Validate H1–H6 hierarchy."""
    issues = []
    h1s    = page.get("h1") or []
    h2s    = page.get("h2") or []
    h3s    = page.get("h3") or []
    kws    = page.get("keywords") or []
    kw1    = kws[0] if kws else ""

    if not h1s:
        issues.append(_issue(
            "missing_h1",
            "on_page",
            HIGH,
            "Page has no H1 heading — H1 is the primary topic signal that Google uses to understand page content.",
            "Add exactly one H1 that clearly describes the page topic and includes the primary keyword.",
        ))
    elif len(h1s) > 1:
        issues.append(_issue(
            "multiple_h1",
            "on_page",
            MEDIUM,
            f"Page has {len(h1s)} H1 tags — multiple H1s dilute the topic signal.",
            "Consolidate to a single H1; demote additional headings to H2 or H3.",
        ))
    elif kw1 and kw1.lower() not in " ".join(h1s).lower():
        issues.append(_issue(
            "keyword_absent_from_h1",
            "on_page",
            HIGH,
            f"Primary keyword '{kw1}' is not in the H1 — Google treats H1 as the second-strongest on-page signal after title.",
            f"Include '{kw1}' naturally in the H1 heading.",
        ))

    if not h2s:
        issues.append(_issue(
            "missing_h2",
            "on_page",
            LOW,
            "Page has no H2 headings — content lacks structural hierarchy, making it harder to scan and rank for long-tail queries.",
            "Add H2 headings to break content into logical sections; include secondary keywords.",
        ))

    # H3 without H2 = broken hierarchy
    if h3s and not h2s:
        issues.append(_issue(
            "broken_heading_hierarchy",
            "on_page",
            MEDIUM,
            "Page has H3 tags but no H2 — heading hierarchy skips a level, confusing both users and crawlers.",
            "Add H2 headings between the H1 and H3 tags to maintain proper hierarchy.",
        ))

    return issues


def _check_keyword_signals(page: dict) -> list[dict]:
    """Keyword presence in title, H1, URL and over-optimisation detection."""
    issues = []
    kws    = page.get("keywords") or []
    if not kws:
        return issues

    kw1       = kws[0]
    body_text = (page.get("body_text") or "").lower()
    url       = page.get("url", "")
    path      = urlparse(url).path.lower()

    # Keyword in URL
    kw_slug = kw1.lower().replace(" ", "-").replace("_", "-")
    if kw_slug not in path and kw1.lower().replace(" ", "") not in path:
        issues.append(_issue(
            "keyword_absent_from_url",
            "on_page",
            MEDIUM,
            f"Primary keyword '{kw1}' is not reflected in the URL path — URLs with keywords get slightly higher CTR.",
            f"Include '{kw_slug}' in the URL path if practical (e.g., /category/{kw_slug}/).",
        ))

    # Over-optimisation: keyword density > 4%
    density = _keyword_density(body_text, kw1)
    if density > 4.0:
        issues.append(_issue(
            "keyword_stuffing_body",
            "on_page",
            HIGH,
            f"Keyword '{kw1}' appears at {density}% density in body text — above 4% is a spam signal for Google.",
            "Reduce keyword frequency; use semantic variants and LSI terms instead of exact-match repetition.",
        ))

    return issues


def _check_content_quality(page: dict) -> list[dict]:
    """Word count and duplicate content checks."""
    issues     = []
    body_text  = (page.get("body_text") or "")
    word_count = len(body_text.split())

    if word_count == 0:
        issues.append(_issue(
            "no_content",
            "on_page",
            CRITICAL,
            "Page has zero words of visible body text — Google has nothing to rank this page for.",
            "Add meaningful content that addresses user intent. Minimum 300 words recommended.",
        ))
    elif word_count < 300:
        sev = HIGH if word_count < 100 else MEDIUM
        issues.append(_issue(
            "thin_content",
            "on_page",
            sev,
            f"Page has only {word_count} words — thin content is a primary cause of poor rankings and Google quality penalties.",
            "Expand content to ≥300 words covering the topic thoroughly with subheadings and supporting details.",
        ))

    return issues


def _check_internal_linking(page: dict) -> list[dict]:
    """Anchor text quality, generic anchors, exact-match overuse."""
    issues     = []
    anchors    = page.get("anchor_texts") or []   # list of anchor strings if available
    score      = page.get("anchor_quality_score")

    if not anchors:
        return issues

    # Generic anchor detection
    generic = [a for a in anchors if a.strip().lower() in _GENERIC_ANCHORS]
    if len(generic) > 3:
        issues.append(_issue(
            "generic_anchor_text",
            "on_page",
            MEDIUM,
            f"{len(generic)} internal links use generic anchor text (e.g., 'click here', 'read more') — "
            "no keyword signal is passed to linked pages.",
            "Replace generic anchors with descriptive keyword-rich text describing the destination page topic.",
        ))

    # Exact-match overuse (same anchor text >3 times)
    anchor_counts = Counter(a.strip().lower() for a in anchors if a.strip())
    for anchor, count in anchor_counts.items():
        if count > 3 and anchor not in _GENERIC_ANCHORS:
            issues.append(_issue(
                "exact_match_anchor_overuse",
                "on_page",
                HIGH,
                f"Anchor text '{anchor[:40]}' is used {count}× — over-optimised anchor patterns are a Penguin penalty signal.",
                "Vary anchor text naturally; use synonyms and partial-match variants.",
            ))

    if isinstance(score, (int, float)) and score < 40:
        issues.append(_issue(
            "low_anchor_quality_score",
            "on_page",
            MEDIUM,
            f"Overall internal link anchor quality score is {score}/100 — many links lack descriptive anchors.",
            "Audit internal links and replace vague anchors with keyword-relevant descriptions.",
        ))

    return issues


def _check_images(page: dict) -> list[dict]:
    """Alt text coverage, keyword stuffing in alt, image format & dimensions."""
    issues       = []
    total        = page.get("img_total") or 0
    missing_alt  = page.get("img_missing_alt") or 0
    missing_dims = page.get("img_missing_dims") or 0
    alts         = page.get("img_alts") or []
    non_modern   = page.get("img_non_modern_count") or 0

    if total == 0:
        return issues

    if missing_alt > 0:
        pct = round(missing_alt / total * 100)
        sev = CRITICAL if pct > 50 else (HIGH if pct > 20 else MEDIUM)
        issues.append(_issue(
            "images_missing_alt",
            "on_page",
            sev,
            f"{missing_alt}/{total} images ({pct}%) lack alt text — blind users cannot understand them, "
            "and Google cannot index the image content.",
            "Add descriptive alt attributes to every informational image. Use alt='' for purely decorative images.",
        ))

    # Keyword stuffing in alt text
    stuffed_alts = [
        a for a in alts
        if isinstance(a, str) and len(a.split()) > 15
    ]
    if stuffed_alts:
        issues.append(_issue(
            "alt_text_keyword_stuffing",
            "on_page",
            MEDIUM,
            f"{len(stuffed_alts)} alt attributes exceed 15 words — over-long alts are treated as keyword stuffing.",
            "Keep alt text concise (5–15 words); describe what is shown, not keywords you want to rank for.",
        ))

    if missing_dims > 0:
        issues.append(_issue(
            "images_missing_dimensions",
            "on_page",
            MEDIUM,
            f"{missing_dims} images missing width/height attributes — causes Cumulative Layout Shift (CLS) as page loads.",
            "Add explicit width and height attributes to every <img> tag (CSS can still override display size).",
        ))

    if non_modern > 0:
        issues.append(_issue(
            "legacy_image_formats",
            "on_page",
            MEDIUM,
            f"{non_modern} images use JPEG/PNG/GIF — 25–50% larger than WebP/AVIF equivalents.",
            "Convert images to WebP or AVIF. Use <picture> with srcset for progressive adoption.",
        ))

    return issues


def _check_open_graph(page: dict) -> list[dict]:
    """OG and Twitter Card completeness."""
    issues  = []
    og_t    = page.get("og_title")    or ""
    og_d    = page.get("og_description") or ""
    og_img  = page.get("og_image")    or ""
    tw_card = page.get("twitter_card") or ""

    missing_og = []
    if not og_t:   missing_og.append("og:title")
    if not og_d:   missing_og.append("og:description")
    if not og_img: missing_og.append("og:image")

    if missing_og:
        sev = HIGH if len(missing_og) == 3 else MEDIUM
        issues.append(_issue(
            "missing_og_tags",
            "on_page",
            sev,
            f"Missing Open Graph tags: {', '.join(missing_og)} — social shares produce broken or generic previews.",
            f"Add the missing OG tags to the <head>: {', '.join(missing_og)}.",
        ))

    if not tw_card:
        issues.append(_issue(
            "missing_twitter_card",
            "on_page",
            LOW,
            "No twitter:card meta tag — Twitter falls back to generic link preview.",
            "Add <meta name='twitter:card' content='summary_large_image'> and twitter:title/description/image.",
        ))

    return issues


def _check_structured_data(page: dict) -> list[dict]:
    """JSON-LD presence and basic schema type validation."""
    issues      = []
    schema_types = page.get("schema_types") or []

    if not schema_types:
        issues.append(_issue(
            "no_structured_data",
            "on_page",
            MEDIUM,
            "No JSON-LD structured data detected — missing out on rich result eligibility "
            "(star ratings, FAQs, breadcrumbs, product snippets).",
            "Implement JSON-LD: start with BreadcrumbList on all pages, then add Article/Product/FAQPage on relevant pages.",
        ))
        return issues

    # Validate types are real schema.org types (basic check)
    known_types = {
        "Article", "BlogPosting", "BreadcrumbList", "FAQPage", "HowTo",
        "ItemList", "LocalBusiness", "NewsArticle", "Organization",
        "Person", "Product", "Recipe", "Review", "VideoObject", "WebPage",
        "WebSite", "Event", "Course", "JobPosting", "SoftwareApplication",
    }
    unknown = [t for t in schema_types if t not in known_types]
    if unknown:
        issues.append(_issue(
            "invalid_schema_type",
            "on_page",
            MEDIUM,
            f"Unknown schema.org types detected: {', '.join(unknown[:5])} — "
            "unrecognised types are ignored by Google.",
            "Use only schema.org-approved types. Check https://schema.org/docs/full.html for valid types.",
        ))

    return issues


def _audit_on_page(
    page:         dict,
    title_counts: Counter,
    meta_counts:  Counter,
) -> list[dict]:
    issues: list[dict] = []
    issues += _check_title(page, title_counts)
    issues += _check_meta_description(page, meta_counts)
    issues += _check_headings(page)
    issues += _check_keyword_signals(page)
    issues += _check_content_quality(page)
    issues += _check_internal_linking(page)
    issues += _check_images(page)
    issues += _check_open_graph(page)
    issues += _check_structured_data(page)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CLUSTER 3 — TECHNICAL SEO
# ─────────────────────────────────────────────────────────────────────────────

def _check_crawl_depth(page: dict) -> list[dict]:
    """Flag pages buried more than 3 levels deep."""
    issues = []
    depth  = _url_depth(page.get("url", ""))

    if depth > 3:
        issues.append(_issue(
            "deep_crawl_depth",
            "technical",
            MEDIUM if depth <= 5 else HIGH,
            f"Page is {depth} levels deep from the root — pages beyond 3 levels receive less PageRank "
            "and are crawled less frequently.",
            "Flatten site architecture: link to important pages from higher-level pages or the homepage.",
        ))

    return issues


def _check_canonical_system(page: dict, pages_map: dict) -> list[dict]:
    """Detect canonical loops and multi-hop chains."""
    issues    = []
    url       = _norm(page.get("url", ""))
    canonical = _norm(page.get("canonical") or "")

    if not canonical or canonical == url:
        return issues

    # Detect chain/loop by traversal (up to 6 hops)
    chain   = [url, canonical]
    visited = {url, canonical}
    cursor  = canonical
    loop    = False

    for _ in range(6):
        target_info = pages_map.get(cursor)
        if not target_info:
            break
        next_canon = _norm(target_info.get("canonical") or "")
        if not next_canon or next_canon == cursor:
            break
        if next_canon in visited:
            loop = True
            chain.append(next_canon)
            break
        chain.append(next_canon)
        visited.add(next_canon)
        cursor = next_canon

    if loop:
        issues.append(_issue(
            "canonical_loop",
            "technical",
            CRITICAL,
            f"Canonical loop detected: {' → '.join(chain[:5])} — "
            "Google cannot determine the authoritative URL; both pages may be de-indexed.",
            "Break the loop by making one page self-canonical (canonical = its own URL).",
        ))
    elif len(chain) > 2:
        issues.append(_issue(
            "canonical_chain",
            "technical",
            HIGH,
            f"Multi-hop canonical chain ({len(chain)-1} hops): {' → '.join(chain[:5])} — "
            "Google does not reliably follow chains longer than 1 hop.",
            "Update the canonical on this page to point directly to the final target URL.",
        ))

    return issues


def _check_hreflang(page: dict, hreflang_map: dict) -> list[dict]:
    """Hreflang extraction, reciprocal validation, x-default check."""
    issues = []
    url    = _norm(page.get("url", ""))
    tags   = page.get("hreflang_tags") or []

    # Support both list[{lang, href}] and dict{lang: href} formats
    if isinstance(tags, dict):
        tag_pairs = [(k, v) for k, v in tags.items()]
    else:
        tag_pairs = [(t.get("lang", ""), t.get("href", "")) for t in tags if isinstance(t, dict)]

    if not tag_pairs:
        return issues

    langs = [lang for lang, _ in tag_pairs]

    # x-default check
    if "x-default" not in langs:
        issues.append(_issue(
            "hreflang_missing_x_default",
            "technical",
            MEDIUM,
            "Hreflang set lacks x-default — users from unmatched locales may land on the wrong language version.",
            "Add <link rel='alternate' hreflang='x-default' href='...'> pointing to your canonical fallback URL.",
        ))

    # BCP-47 language code validation
    invalid_langs = [l for l in langs if l != "x-default" and not _LANG_RE.match(l)]
    if invalid_langs:
        issues.append(_issue(
            "hreflang_invalid_lang_code",
            "technical",
            HIGH,
            f"Invalid hreflang language codes: {', '.join(invalid_langs[:5])} — "
            "Google ignores unrecognised codes, breaking the alternate set.",
            "Use valid BCP-47 codes (e.g., 'en', 'en-US', 'zh-Hans-CN'). See https://tools.ietf.org/html/bcp47.",
        ))

    # Reciprocal validation
    for lang, href in tag_pairs:
        if not href:
            continue
        target_url  = _norm(href)
        target_tags = hreflang_map.get(target_url)
        if target_tags is None:
            continue  # target not crawled

        # target must declare hreflang pointing back to this page
        if isinstance(target_tags, dict):
            target_hrefs = {_norm(v) for v in target_tags.values()}
        else:
            target_hrefs = {_norm(t.get("href", "")) for t in target_tags if isinstance(t, dict)}

        if url not in target_hrefs:
            issues.append(_issue(
                "hreflang_missing_reciprocal",
                "technical",
                HIGH,
                f"Hreflang points to '{href}' ({lang}) but that page does not declare a reciprocal hreflang back — "
                "Google ignores the entire hreflang set if reciprocals are missing.",
                f"On '{href}', add <link rel='alternate' hreflang='...' href='{page.get('url', '')}'>.",
            ))

    return issues


def _check_pagination(page: dict, pages_map: dict) -> list[dict]:
    """Validate rel=next/prev presence and sequence consistency."""
    issues = []
    url    = _norm(page.get("url", ""))
    pnext  = _norm(page.get("pagination_next") or "")
    pprev  = _norm(page.get("pagination_prev") or "")

    if pnext and pprev and pnext == pprev:
        issues.append(_issue(
            "pagination_next_prev_same",
            "technical",
            HIGH,
            "rel=next and rel=prev point to the same URL — contradictory pagination signals.",
            "Fix pagination links: rel=prev should point to the previous page, rel=next to the next.",
        ))

    # Reciprocal check: if this page declares next=B, B should declare prev=this
    if pnext:
        target = pages_map.get(pnext)
        if target:
            target_prev = _norm(target.get("pagination_prev") or "")
            if target_prev and target_prev != url:
                issues.append(_issue(
                    "pagination_non_reciprocal",
                    "technical",
                    MEDIUM,
                    f"This page declares rel=next pointing to '{pnext}', but '{pnext}' has rel=prev pointing elsewhere — "
                    "broken pagination sequence.",
                    "Ensure every page's rel=next matches the following page's rel=prev.",
                ))

    return issues


def _check_mobile(page: dict) -> list[dict]:
    """Mobile viewport validation."""
    issues   = []
    viewport = (page.get("viewport") or "").lower()

    if not viewport:
        issues.append(_issue(
            "missing_viewport",
            "technical",
            CRITICAL,
            "Missing <meta name='viewport'> tag — page will render at desktop width on mobile, "
            "causing poor UX. Google's mobile-first indexing penalises this.",
            "Add <meta name='viewport' content='width=device-width, initial-scale=1'> to every page <head>.",
        ))
        return issues

    if "width=device-width" not in viewport:
        issues.append(_issue(
            "viewport_missing_device_width",
            "technical",
            HIGH,
            "Viewport tag exists but lacks 'width=device-width' — page may not scale correctly on mobile.",
            "Set viewport to: content='width=device-width, initial-scale=1'.",
        ))

    if "user-scalable=no" in viewport or "maximum-scale=1" in viewport:
        issues.append(_issue(
            "viewport_disables_zoom",
            "technical",
            HIGH,
            "Viewport disables user zoom (user-scalable=no or maximum-scale=1) — "
            "accessibility violation and a mobile UX quality signal for Google.",
            "Remove 'user-scalable=no' and 'maximum-scale=1' from the viewport meta tag.",
        ))

    return issues


def _check_url_structure(page: dict) -> list[dict]:
    """Clean URL validation, excessive parameters."""
    issues     = []
    url        = page.get("url", "")
    param_count = _url_param_count(url)
    path       = urlparse(url).path

    # Excessive query parameters
    if param_count > 3:
        issues.append(_issue(
            "url_excessive_parameters",
            "technical",
            MEDIUM,
            f"URL has {param_count} query parameters — complex URLs are harder to crawl efficiently "
            "and less likely to accumulate backlinks.",
            "Reduce URL parameters. Use URL path segments for important dimensions (e.g., /category/page/ not ?cat=1&p=2).",
        ))

    # Uppercase in path (case-sensitive duplicate risk)
    if re.search(r'[A-Z]', path):
        issues.append(_issue(
            "url_mixed_case",
            "technical",
            LOW,
            "URL path contains uppercase letters — different capitalisation creates duplicate URL risk "
            "(e.g., /Page and /page are treated as different URLs).",
            "Lowercase all URL paths and set up 301 redirects from mixed-case variants.",
        ))

    # Double slashes
    if "//" in path:
        issues.append(_issue(
            "url_double_slash",
            "technical",
            LOW,
            "URL path contains double slashes (//) — may cause crawl issues and creates duplicate content risk.",
            "Fix URL generation to avoid double slashes; add redirects for existing doubled-slash URLs.",
        ))

    # Underscore in path (hyphens preferred)
    if "_" in path:
        issues.append(_issue(
            "url_underscores",
            "technical",
            LOW,
            "URL uses underscores instead of hyphens — Google treats hyphens as word separators, underscores as joins.",
            "Replace underscores with hyphens in URL paths.",
        ))

    return issues


def _check_broken_internal_links(page: dict, pages_map: dict) -> list[dict]:
    """Flag internal links pointing to 4xx pages or redirect chains."""
    issues        = []
    links         = page.get("internal_links") or []
    broken        = []
    redirecting   = []

    for link in links:
        target = _norm(link) if isinstance(link, str) else ""
        if not target or target not in pages_map:
            continue
        target_page   = pages_map[target]
        target_status = target_page.get("status_code", 200)
        target_hops   = target_page.get("redirect_hops", 0)

        if isinstance(target_status, int) and target_status >= 400:
            broken.append(target)
        elif target_hops > 1:
            redirecting.append(target)

    if broken:
        issues.append(_issue(
            "broken_internal_link",
            "technical",
            HIGH,
            f"{len(broken)} internal link(s) point to error pages (4xx/5xx) — "
            "wastes crawl budget and leaks PageRank through dead-ends.",
            "Fix or 301-redirect the broken target pages. Alternatively, remove the dead links.",
        ))

    if redirecting:
        issues.append(_issue(
            "internal_link_redirect_chain",
            "technical",
            MEDIUM,
            f"{len(redirecting)} internal link(s) pass through redirect chains (>1 hop) — "
            "each hop dilutes PageRank passed to the destination.",
            "Update internal links to point directly to the final destination URL, bypassing all redirect hops.",
        ))

    return issues


def _audit_technical(
    page:          dict,
    pages_map:     dict,
    hreflang_map:  dict,
) -> list[dict]:
    issues: list[dict] = []
    issues += _check_crawl_depth(page)
    issues += _check_canonical_system(page, pages_map)
    issues += _check_hreflang(page, hreflang_map)
    issues += _check_pagination(page, pages_map)
    issues += _check_mobile(page)
    issues += _check_url_structure(page)
    issues += _check_broken_internal_links(page, pages_map)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CLUSTER 4 — PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────

def _check_cwv(page: dict, cwv_data: dict) -> list[dict]:
    """Core Web Vitals — real data if available, proxy estimates otherwise."""
    issues  = []
    url     = page.get("url", "")
    cwv     = (cwv_data or {}).get(url) or (cwv_data or {}).get(_norm(url))

    if cwv:
        lcp = cwv.get("lcp_s")
        cls = cwv.get("cls")
        inp = cwv.get("inp_s")

        if isinstance(lcp, (int, float)):
            if lcp > 4.0:
                issues.append(_issue("cwv_lcp_poor", "performance", CRITICAL,
                    f"LCP is {lcp}s (poor threshold: >4s) — users experience very slow page loads.",
                    "Optimise server response time, eliminate render-blocking resources, and lazy-load below-fold images."))
            elif lcp > 2.5:
                issues.append(_issue("cwv_lcp_needs_improvement", "performance", HIGH,
                    f"LCP is {lcp}s (needs improvement: 2.5–4s) — fails Google's Core Web Vitals threshold.",
                    "Compress images, preload LCP element, reduce Time to First Byte (TTFB)."))

        if isinstance(cls, (int, float)):
            if cls > 0.25:
                issues.append(_issue("cwv_cls_poor", "performance", CRITICAL,
                    f"CLS is {cls} (poor threshold: >0.25) — severe layout shifts degrade user experience.",
                    "Add explicit width/height to images/embeds; avoid inserting DOM above existing content."))
            elif cls > 0.1:
                issues.append(_issue("cwv_cls_needs_improvement", "performance", HIGH,
                    f"CLS is {cls} (needs improvement: 0.1–0.25).",
                    "Reserve space for ads/embeds, use CSS aspect-ratio, avoid late-injected content."))

        if isinstance(inp, (int, float)):
            if inp > 0.5:
                issues.append(_issue("cwv_inp_poor", "performance", CRITICAL,
                    f"INP is {inp}s (poor threshold: >500ms) — severely unresponsive to user interactions.",
                    "Reduce JavaScript execution time, break up long tasks, use a web worker."))
            elif inp > 0.2:
                issues.append(_issue("cwv_inp_needs_improvement", "performance", HIGH,
                    f"INP is {inp}s (needs improvement: 200–500ms).",
                    "Defer non-critical JS, minimise event handler work, use requestAnimationFrame for visual updates."))
    else:
        # Proxy estimate: high response time → likely poor LCP
        rt = page.get("response_time_ms") or 0
        if rt > 2500:
            issues.append(_issue("slow_ttfb_proxy_lcp", "performance", HIGH,
                f"Server response time is {rt}ms — a high TTFB is the strongest proxy for poor LCP when CWV data is unavailable.",
                "Add server-side caching (Redis, Varnish), use a CDN, and optimise slow database queries."))
        elif rt > 800:
            issues.append(_issue("elevated_ttfb", "performance", MEDIUM,
                f"Server response time is {rt}ms — Google recommends TTFB < 800ms for good LCP.",
                "Investigate TTFB bottlenecks: slow DB queries, no caching, or absent CDN."))

    return issues


def _check_page_size(page: dict) -> list[dict]:
    """HTML response size check."""
    issues   = []
    size_kb  = page.get("html_size_kb") or 0

    if size_kb > 2048:
        issues.append(_issue(
            "oversized_html",
            "performance",
            HIGH,
            f"HTML response is {size_kb:.0f} KB (>{2048} KB threshold) — large HTML payloads delay "
            "Time-to-First-Byte and browser parse time.",
            "Enable GZIP/Brotli compression. Move large inline scripts/data to external files or lazy-load them.",
        ))
    elif size_kb > 500:
        issues.append(_issue(
            "large_html",
            "performance",
            MEDIUM,
            f"HTML response is {size_kb:.0f} KB — larger than recommended for fast mobile rendering.",
            "Review inline scripts/styles and large embedded JSON/data. Consider server-side partial rendering.",
        ))

    return issues


def _check_resource_count(page: dict) -> list[dict]:
    """HTTP request budget."""
    issues = []
    count  = page.get("resource_count") or 0

    if count > 100:
        issues.append(_issue(
            "excessive_resource_requests",
            "performance",
            HIGH,
            f"Page loads {count} resource elements — each request adds network round-trip latency and is a key LCP risk factor.",
            "Bundle CSS/JS files, remove unused third-party scripts, lazy-load below-fold resources.",
        ))
    elif count > 60:
        issues.append(_issue(
            "high_resource_requests",
            "performance",
            MEDIUM,
            f"Page loads {count} resource elements — above 60 increases likelihood of LCP degradation on mobile.",
            "Audit third-party scripts, defer non-critical resources, use resource hints (preload/preconnect).",
        ))

    return issues


def _check_compression(page: dict) -> list[dict]:
    """Gzip/Brotli compression detection."""
    issues   = []
    hdrs     = _hdrs(page)
    encoding = hdrs.get("content-encoding", "").lower()
    ctype    = hdrs.get("content-type", "").lower()

    _COMPRESSIBLE = ("text/html", "text/css", "application/javascript",
                     "application/json", "text/javascript", "text/xml", "image/svg")
    is_compressible = any(ct in ctype for ct in _COMPRESSIBLE)

    if is_compressible and not encoding:
        issues.append(_issue(
            "compression_missing",
            "performance",
            HIGH,
            "Response is a compressible text type but no Content-Encoding header was returned — "
            "uncompressed responses are 60–80% larger than gzip-compressed equivalents.",
            "Enable gzip or Brotli compression on your web server/CDN for all text-based content types.",
        ))

    return issues


def _check_caching(page: dict) -> list[dict]:
    """Cache-Control policy validation."""
    issues = []
    hdrs   = _hdrs(page)
    cc     = hdrs.get("cache-control", "").lower()
    etag   = hdrs.get("etag", "")

    if not cc:
        issues.append(_issue(
            "missing_cache_control",
            "performance",
            HIGH,
            "No Cache-Control header — browsers and CDNs cannot cache this response, "
            "forcing a network request on every visit.",
            "Add Cache-Control: max-age=<seconds>, public for static content; use stale-while-revalidate for dynamic pages.",
        ))
        return issues

    # Extract max-age
    m = re.search(r'max-age\s*=\s*(\d+)', cc)
    if m:
        ttl = int(m.group(1))
        if ttl < 3600:
            issues.append(_issue(
                "low_cache_ttl",
                "performance",
                MEDIUM,
                f"Cache-Control max-age is {ttl}s — too low for static resources. Short TTLs increase origin load.",
                "Set max-age to ≥86400 (1 day) for static assets; use cache-busting via file hashes instead of short TTLs.",
            ))

    if not etag and "no-store" not in cc:
        issues.append(_issue(
            "missing_etag",
            "performance",
            LOW,
            "No ETag response header — conditional requests (304 Not Modified) cannot be used to avoid full re-downloads.",
            "Enable ETag generation on your server to allow efficient cache validation.",
        ))

    return issues


def _check_render_blocking(page: dict) -> list[dict]:
    """Heuristic render-blocking detection from resource counts."""
    issues      = []
    resource_count = page.get("resource_count") or 0
    # We use resource_count as a proxy; crawler doesn't separate head/body resources
    # Flag when both resource count is high AND response time is high
    rt          = page.get("response_time_ms") or 0

    if resource_count > 80 and rt > 1500:
        issues.append(_issue(
            "render_blocking_risk",
            "performance",
            HIGH,
            f"Page has {resource_count} resources and {rt}ms response time — "
            "high probability of render-blocking CSS/JS delaying First Contentful Paint.",
            "Audit scripts/stylesheets in <head>: add defer/async to JS, inline critical CSS, "
            "and preload key resources with <link rel='preload'>.",
        ))

    return issues


def _audit_performance(page: dict, cwv_data: dict) -> list[dict]:
    issues: list[dict] = []
    issues += _check_cwv(page, cwv_data)
    issues += _check_page_size(page)
    issues += _check_resource_count(page)
    issues += _check_compression(page)
    issues += _check_caching(page)
    issues += _check_images(page)   # image performance overlap is intentional
    issues += _check_render_blocking(page)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CLUSTER 5 — SECURITY
# ─────────────────────────────────────────────────────────────────────────────

_SECURITY_HEADER_GUIDANCE = {
    "strict-transport-security": (
        CRITICAL,
        "HSTS header missing — browsers will not enforce HTTPS-only access, enabling downgrade attacks.",
        "Add: Strict-Transport-Security: max-age=31536000; includeSubDomains; preload",
    ),
    "content-security-policy": (
        HIGH,
        "CSP header missing — no XSS mitigation policy declared; injected scripts run unchallenged.",
        "Define a Content-Security-Policy. Start with a report-only policy to identify violations before enforcing.",
    ),
    "x-frame-options": (
        HIGH,
        "X-Frame-Options missing — page can be embedded in an iframe, enabling clickjacking attacks.",
        "Add: X-Frame-Options: DENY (or SAMEORIGIN if you need same-origin embeds).",
    ),
    "x-content-type-options": (
        HIGH,
        "X-Content-Type-Options missing — browsers may MIME-sniff responses, executing unexpected content types.",
        "Add: X-Content-Type-Options: nosniff",
    ),
    "referrer-policy": (
        HIGH,
        "Referrer-Policy missing — full URL (including sensitive query params) is leaked in the Referer header on navigation.",
        "Add: Referrer-Policy: strict-origin-when-cross-origin",
    ),
    "permissions-policy": (
        MEDIUM,
        "Permissions-Policy missing — browser features (camera, microphone, geolocation) are not explicitly restricted.",
        "Add: Permissions-Policy: camera=(), microphone=(), geolocation=()",
    ),
}


def _check_https(page: dict) -> list[dict]:
    issues = []
    url    = page.get("url", "")
    if url.startswith("http://"):
        issues.append(_issue(
            "http_page",
            "security",
            CRITICAL,
            "Page is served over unencrypted HTTP — user data is exposed and Google applies a ranking penalty.",
            "Redirect all HTTP traffic to HTTPS with 301 redirects. Update internal links to use HTTPS.",
        ))
    return issues


def _check_security_headers(page: dict) -> list[dict]:
    issues = []
    hdrs   = _hdrs(page)

    for header, (sev, explanation, fix) in _SECURITY_HEADER_GUIDANCE.items():
        if header not in hdrs:
            issues.append(_issue(
                f"missing_{header.replace('-', '_')}",
                "security",
                sev,
                explanation,
                fix,
            ))

    # HSTS quality check (when present)
    hsts = hdrs.get("strict-transport-security", "")
    if hsts:
        m = re.search(r'max-age\s*=\s*(\d+)', hsts, re.I)
        if m and int(m.group(1)) < 31536000:
            issues.append(_issue(
                "hsts_weak_max_age",
                "security",
                HIGH,
                f"HSTS max-age is {m.group(1)}s — below the recommended 1 year (31536000). "
                "Browsers only enforce HSTS for the declared duration.",
                "Set HSTS max-age to at least 31536000 (1 year).",
            ))
        if "includesubdomains" not in hsts.lower():
            issues.append(_issue(
                "hsts_missing_include_subdomains",
                "security",
                MEDIUM,
                "HSTS missing includeSubDomains directive — subdomains are not protected by HTTPS enforcement.",
                "Add 'includeSubDomains' to the Strict-Transport-Security header.",
            ))

    # CSP quality check
    csp = hdrs.get("content-security-policy", "")
    if csp and "unsafe-inline" in csp.lower():
        issues.append(_issue(
            "csp_unsafe_inline",
            "security",
            HIGH,
            "CSP contains 'unsafe-inline' — negates XSS protection for inline scripts and styles.",
            "Remove 'unsafe-inline'. Use nonces or hashes for inline scripts instead.",
        ))
    if csp and "unsafe-eval" in csp.lower():
        issues.append(_issue(
            "csp_unsafe_eval",
            "security",
            HIGH,
            "CSP contains 'unsafe-eval' — allows dynamic code execution (eval(), setTimeout(string)) which bypasses CSP.",
            "Remove 'unsafe-eval'. Refactor code that uses eval() to use safer alternatives.",
        ))

    return issues


def _check_tls_version(page: dict) -> list[dict]:
    issues  = []
    tls_ver = (page.get("tls_version") or "").strip()
    if tls_ver and tls_ver in ("SSLv2", "SSLv3", "TLSv1", "TLSv1.1"):
        issues.append(_issue(
            "legacy_tls",
            "security",
            CRITICAL,
            f"Connection negotiated {tls_ver} — this version has known cryptographic weaknesses "
            "(POODLE, BEAST, DROWN) and fails PCI-DSS compliance.",
            "Disable TLS 1.0/1.1 and SSL on the server. Enforce TLS 1.2 minimum (TLS 1.3 preferred).",
        ))
    return issues


def _check_mixed_content(page: dict) -> list[dict]:
    issues   = []
    mixed    = page.get("mixed_resources") or []
    url      = page.get("url", "")

    if mixed and url.startswith("https://"):
        issues.append(_issue(
            "mixed_content",
            "security",
            HIGH,
            f"{len(mixed)} HTTP resource(s) loaded on an HTTPS page — browsers block active mixed content "
            "and warn on passive mixed content, degrading trust and UX.",
            "Update all resource URLs to HTTPS. Search for hardcoded 'http://' in templates and CMS settings.",
        ))

    return issues


def _audit_security(page: dict) -> list[dict]:
    issues: list[dict] = []
    issues += _check_https(page)
    issues += _check_security_headers(page)
    issues += _check_tls_version(page)
    issues += _check_mixed_content(page)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CROSS-CLUSTER VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def _cross_sitemap_robots_indexability(
    pages:            list[dict],
    sitemap_set:      set[str],
    disallowed_paths: list[str],
) -> list[dict]:
    """
    Conflict: page is in the sitemap AND blocked by robots.txt.
    This is the worst-case indexability conflict — Google can find it via sitemap
    but cannot crawl it due to robots.txt block.
    """
    issues = []
    for page in pages:
        url  = _norm(page.get("url", ""))
        path = urlparse(page.get("url", "")).path or "/"
        in_sitemap = url in sitemap_set
        blocked    = any(path.startswith(r) or r == "/" for r in disallowed_paths if r)

        if in_sitemap and blocked:
            issues.append(_issue(
                "sitemap_robots_conflict",
                "indexability",
                CRITICAL,
                f"'{page.get('url')}' is listed in the sitemap but blocked by robots.txt — "
                "Googlebot will see the URL in the sitemap but cannot crawl it, wasting crawl budget.",
                "Either remove the URL from the sitemap or remove the Disallow rule from robots.txt.",
            ))

    return issues


def _cross_canonical_hreflang(pages: list[dict]) -> list[dict]:
    """
    Hreflang targets must be self-canonicalized.
    If page A's hreflang points to page B, B's canonical must equal B (not redirect to C).
    """
    issues     = []
    canon_map  = {_norm(p.get("url", "")): _norm(p.get("canonical") or "") for p in pages}

    for page in pages:
        url  = page.get("url", "")
        tags = page.get("hreflang_tags") or []

        if isinstance(tags, dict):
            tag_pairs = list(tags.items())
        else:
            tag_pairs = [(t.get("lang", ""), t.get("href", "")) for t in tags if isinstance(t, dict)]

        for lang, href in tag_pairs:
            if not href:
                continue
            target_norm  = _norm(href)
            target_canon = canon_map.get(target_norm, "")

            if target_canon and target_canon != target_norm:
                issues.append(_issue(
                    "hreflang_non_canonical_target",
                    "technical",
                    HIGH,
                    f"Hreflang ({lang}) on '{url}' points to '{href}', but that page's canonical "
                    f"is '{target_canon}' — Google requires hreflang targets to be self-canonicalized.",
                    f"Update the hreflang href to point to the canonical URL: '{target_canon}'.",
                ))

    return issues


def _cross_links_status(pages: list[dict], pages_map: dict) -> list[dict]:
    """
    Aggregate internal-link-to-error-page violations at the site level.
    (Per-page broken links are already detected in technical; this builds the
    site-wide picture for the cross-cluster section.)
    """
    issues         = []
    broken_sources = defaultdict(list)

    for page in pages:
        src   = page.get("url", "")
        links = page.get("internal_links") or []
        for link in links:
            target = _norm(link) if isinstance(link, str) else ""
            if not target or target not in pages_map:
                continue
            ts = pages_map[target].get("status_code", 200)
            if isinstance(ts, int) and ts >= 400:
                broken_sources[target].append(src)

    for target, sources in broken_sources.items():
        if len(sources) > 5:
            issues.append(_issue(
                "widely_linked_dead_page",
                "technical",
                CRITICAL,
                f"'{target}' returns an error status and is linked from {len(sources)} pages — "
                "high PageRank loss and significant crawl budget waste.",
                f"Restore '{target}', 301-redirect it, or remove the link from all {len(sources)} source pages.",
            ))

    return issues


def _cross_cwv_size_resources(pages: list[dict]) -> list[dict]:
    """
    Flag pages where BOTH oversized HTML AND excessive resources are present —
    a compounding performance failure that makes CWV improvement very difficult.
    """
    issues = []
    for page in pages:
        size_kb = page.get("html_size_kb") or 0
        count   = page.get("resource_count") or 0
        rt      = page.get("response_time_ms") or 0

        if size_kb > 500 and count > 60 and rt > 1000:
            issues.append(_issue(
                "compounding_performance_failure",
                "performance",
                CRITICAL,
                f"Page has large HTML ({size_kb:.0f}KB) + {count} resources + {rt}ms response time — "
                "all three performance factors are poor simultaneously, making good CWV scores nearly impossible.",
                "Tackle in order: (1) Enable compression. (2) Eliminate unused resources. (3) Add server-side caching.",
            ))

    return issues


def _cross_security_performance(pages: list[dict]) -> list[dict]:
    """
    Detect cases where CSP blocks same-origin or CDN assets,
    evidenced by a strict CSP AND high response time / resource count
    (CSP violations cause failed resource loads which add network latency).
    """
    issues = []
    for page in pages:
        hdrs = _hdrs(page)
        csp  = hdrs.get("content-security-policy", "")
        rt   = page.get("response_time_ms") or 0

        if csp and "default-src 'none'" in csp.lower() and rt > 2000:
            issues.append(_issue(
                "csp_blocking_assets",
                "security",
                HIGH,
                f"CSP uses 'default-src none' and page response time is {rt}ms — "
                "overly strict CSP may be blocking legitimate resources and causing load failures.",
                "Audit the browser console for CSP violations. Whitelist required sources explicitly.",
            ))

    return issues


def _run_cross_validation(
    pages:            list[dict],
    pages_map:        dict,
    sitemap_set:      set[str],
    disallowed_paths: list[str],
) -> list[dict]:
    issues: list[dict] = []
    issues += _cross_sitemap_robots_indexability(pages, sitemap_set, disallowed_paths)
    issues += _cross_canonical_hreflang(pages)
    issues += _cross_links_status(pages, pages_map)
    issues += _cross_cwv_size_resources(pages)
    issues += _cross_security_performance(pages)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  robots.txt parser (minimal — avoids external dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_disallowed(robots_txt: str, target_agent: str = "*") -> list[str]:
    """Extract Disallow paths for the target user-agent from robots.txt content."""
    if not robots_txt:
        return []
    disallowed: list[str] = []
    in_block = False

    for raw_line in robots_txt.splitlines():
        line  = raw_line.strip()
        lower = line.lower()
        if not line or line.startswith("#"):
            continue
        if lower.startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip().lower()
            in_block = agent in (target_agent.lower(), "*")
        elif lower.startswith("disallow:") and in_block:
            path = line.split(":", 1)[1].strip()
            if path:
                disallowed.append(path)

    return disallowed


# ─────────────────────────────────────────────────────────────────────────────
#  Scoring
# ─────────────────────────────────────────────────────────────────────────────

def _compute_scores(all_issues: list[dict]) -> dict[str, int]:
    scores = {}
    for cluster in _CLUSTERS:
        score = 100
        for iss in all_issues:
            if iss.get("cluster") == cluster:
                score -= _SEVERITY_PTS.get(iss.get("severity", LOW), 2)
        scores[cluster] = max(0, score)
    return scores


def _site_grade(score: int) -> str:
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B"
    return "Needs Fix"


# ─────────────────────────────────────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_exhaustive_audit(
    pages:              list[dict],
    sitemap_urls:       list[str]  | None = None,
    robots_txt_content: str        | None = None,
    cwv_data:           dict       | None = None,
) -> dict:
    """
    Run all five cluster audits + cross-cluster validation on every page.

    Returns the canonical output format:
    {
      "pages": [{url, issues, scores}],
      "site_summary": {total_issues, critical, high, medium, low,
                       by_cluster, top_issues, site_score, site_grade}
    }
    """
    if not pages:
        return {
            "pages": [],
            "site_summary": {
                "total_issues": 0, "critical": 0, "high": 0, "medium": 0, "low": 0,
                "by_cluster": {c: 0 for c in _CLUSTERS},
                "top_issues": [], "site_score": 0, "site_grade": "Needs Fix",
                "error": "No pages provided",
            },
        }

    real_pages = [p for p in pages if not p.get("_is_error")]

    # ── Pre-compute lookup structures ─────────────────────────────────────────
    pages_map: dict[str, dict] = {_norm(p.get("url", "")): p for p in real_pages}

    sitemap_set: set[str] = {_norm(u) for u in (sitemap_urls or [])}

    disallowed_paths: list[str] = _parse_disallowed(robots_txt_content or "")

    # All URLs that are internally linked to (for orphan detection)
    internally_linked: set[str] = set()
    for p in real_pages:
        for link in (p.get("internal_links") or []):
            if isinstance(link, str):
                internally_linked.add(_norm(link))

    # Hreflang map: url → hreflang_tags
    hreflang_map: dict[str, list | dict] = {
        _norm(p.get("url", "")): (p.get("hreflang_tags") or [])
        for p in real_pages
    }

    # Duplicate title/meta counters
    title_counts = Counter(
        (p.get("title") or "").strip().lower()
        for p in real_pages
        if (p.get("title") or "").strip()
    )
    meta_counts = Counter(
        (p.get("meta_description") or "").strip().lower()
        for p in real_pages
        if (p.get("meta_description") or "").strip()
    )

    # ── Cross-cluster issues (site-level, assigned to the page they first appear on)
    cross_issues = _run_cross_validation(
        real_pages, pages_map, sitemap_set, disallowed_paths
    )
    # Build a lookup: url → list of cross issues for that page
    cross_by_url: dict[str, list[dict]] = defaultdict(list)
    for iss in cross_issues:
        # Cross issues don't have a specific page URL — we'll attach them to site_summary
        pass

    # ── Per-page audit ────────────────────────────────────────────────────────
    page_results = []
    all_issue_counter: Counter = Counter()
    severity_totals = Counter()
    cluster_totals  = Counter()

    for page in real_pages:
        url       = page.get("url", "")
        pg_issues: list[dict] = []

        pg_issues += _audit_indexability(
            page, disallowed_paths, sitemap_set, pages_map, internally_linked
        )
        pg_issues += _audit_on_page(page, title_counts, meta_counts)
        pg_issues += _audit_technical(page, pages_map, hreflang_map)
        pg_issues += _audit_performance(page, cwv_data)
        pg_issues += _audit_security(page)

        scores = _compute_scores(pg_issues)

        # Tally
        for iss in pg_issues:
            all_issue_counter[iss["type"]] += 1
            severity_totals[iss["severity"]] += 1
            cluster_totals[iss["cluster"]] += 1

        page_results.append({
            "url":    url,
            "issues": pg_issues,
            "scores": scores,
        })

    # Tally cross-cluster issues
    for iss in cross_issues:
        all_issue_counter[iss["type"]] += 1
        severity_totals[iss["severity"]] += 1
        cluster_totals[iss["cluster"]] += 1

    # ── Site score (average of all cluster averages) ──────────────────────────
    if page_results:
        cluster_avgs = {}
        for cluster in _CLUSTERS:
            scores_for_cluster = [r["scores"][cluster] for r in page_results]
            cluster_avgs[cluster] = round(sum(scores_for_cluster) / len(scores_for_cluster))
        site_score = round(sum(cluster_avgs.values()) / len(cluster_avgs))
    else:
        site_score = 0

    # ── Site summary ──────────────────────────────────────────────────────────
    total = sum(severity_totals.values())
    site_summary = {
        "total_issues": total,
        "critical":     severity_totals.get(CRITICAL, 0),
        "high":         severity_totals.get(HIGH, 0),
        "medium":       severity_totals.get(MEDIUM, 0),
        "low":          severity_totals.get(LOW, 0),
        "by_cluster":   dict(cluster_totals),
        "top_issues":   [
            {"type": itype, "count": count}
            for itype, count in all_issue_counter.most_common(10)
        ],
        "site_score":   site_score,
        "site_grade":   _site_grade(site_score),
        "cross_cluster_issues": cross_issues,
    }

    return {"pages": page_results, "site_summary": site_summary}
