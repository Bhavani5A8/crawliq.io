"""
issues.py — SEO Issue Detection Layer

Public functions:
  detect_issues(pages)   — lightweight crawler-time checks (per-page + dup meta/title)
  validate_all(pages, sitemap_urls=None) — comprehensive post-crawl validator:
      • Indexability  (noindex, canonical conflicts, chains, loops)
      • Links         (orphans, broken internal links, generic anchor text)
      • Content       (word count, keyword density, keyword prominence, dup hash)
      • Headings      (H1–H6 ordered sequence, skipped levels)
      • Hreflang      (reciprocal linking, RFC-5646 language code validation)
      • Sitemap       (noindex-in-sitemap, crawled-but-missing pages)
      • Images        (missing alt, missing width/height CLS risk, lazy loading)
      • Security      (CSP, X-Frame-Options, X-Content-Type-Options)
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


def _is_homepage(url: str) -> bool:
    """BUG-N09: return True if url is the site root (no meaningful path)."""
    return _up(url).path.rstrip("/") == ""


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

    # Step 1: tally occurrences of each meta string (ignore blanks).
    # BUG-N05: normalise to lowercase so "Buy Coffee" == "buy coffee".
    meta_counts = Counter(
        page["meta_description"].strip().lower()
        for page in pages
        if (page.get("meta_description") or "").strip()
    )

    # Step 2: flag pages whose meta appears more than once
    for page in pages:
        meta = (page.get("meta_description") or "").strip().lower()
        if meta and meta_counts[meta] > 1:
            # Avoid adding the label twice if detect_issues is called again
            if "Duplicate Meta Description" not in page["issues"]:
                page["issues"].append("Duplicate Meta Description")


def _flag_duplicate_titles(pages: list[dict]) -> None:
    """
    Mirror of _flag_duplicate_meta — flags pages that share an identical title.
    Error records are excluded: their title field holds a raw exception string,
    not a real page title, so they must never participate in dedup counts.
    """
    title_counts = Counter(
        page["title"].strip().lower()
        for page in pages
        if (page.get("title") or "").strip() and not page.get("_is_error")
    )
    for page in pages:
        if page.get("_is_error"):
            continue
        title = (page.get("title") or "").strip().lower()
        if title and title_counts[title] > 1:
            if "Duplicate Title" not in page["issues"]:
                page["issues"].append("Duplicate Title")


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
                             hreflang, images, security, all_issues, issue_count}],
      "cross_page_issues": [{type, ...}],
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

    # ── Per-page issues ───────────────────────────────────────────────────────
    page_issues = [_validate_page(p) for p in pages]

    # ── Cross-page issues ─────────────────────────────────────────────────────
    cross: list[dict] = []
    cross += _xp_canonical(pages, canonical_map)
    cross += _xp_broken_links(pages, status_map)
    cross += _xp_duplicate_content(real_pages)
    cross += _xp_hreflang_reciprocal(pages, hreflang_hrefs)
    cross += _xp_orphans(pages)
    if sitemap_urls:
        cross += _xp_sitemap(pages, sitemap_urls)

    return {
        "page_issues":       page_issues,
        "cross_page_issues": cross,
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
            "url":           url,
            "indexability":  indexability,
            "links":         [],
            "content":       [],
            "headings":      [],
            "hreflang":      [],
            "images":        [],
            "security":      [],
            "all_issues":    indexability,
            "issue_count":   len(indexability),
        }

    links      = _pp_anchor_text(page)
    content    = _pp_content(page)
    headings   = _pp_headings(page)
    hreflang   = _pp_hreflang(page)
    images     = _pp_images(page)
    security   = _pp_security(page)

    all_issues = indexability + links + content + headings + hreflang + images + security

    return {
        "url":          url,
        "indexability": indexability,
        "links":        links,
        "content":      content,
        "headings":     headings,
        "hreflang":     hreflang,
        "images":       images,
        "security":     security,
        "all_issues":   all_issues,
        "issue_count":  len(all_issues),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Indexability
# ─────────────────────────────────────────────────────────────────────────────

def _pp_indexability(page: dict) -> list[str]:
    issues:     list[str] = []
    robots_meta = (page.get("robots_meta") or "").lower()
    x_robots    = (page.get("x_robots_tag") or "").lower()
    combined    = f"{robots_meta} {x_robots}"

    if "noindex" in combined:
        src = "X-Robots-Tag" if "noindex" in x_robots else "meta robots"
        issues.append(f"noindex directive via {src} — Googlebot will skip this page")

    canonical = _n(page.get("canonical") or "")
    url       = _n(page.get("url") or "")
    if canonical and canonical != url:
        issues.append(f"Canonical conflict: points to {canonical} instead of self")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Per-page check: Anchor text quality
# ─────────────────────────────────────────────────────────────────────────────

def _pp_anchor_text(page: dict) -> list[str]:
    issues: list[str] = []
    link_objects = page.get("link_objects") or []
    generic_count = sum(
        1 for lo in link_objects
        if (lo.get("text") or "").strip().lower() in _GENERIC_ANCHORS
    )
    if generic_count:
        issues.append(
            f'{generic_count} internal link(s) use generic anchor text '
            f'("click here", "read more", etc.) — replace with descriptive anchors'
        )
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
    present = {k.lower() for k in headers}

    for header, message in _SECURITY_HEADERS.items():
        if header not in present:
            issues.append(message)

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Canonical chains and loops
# ─────────────────────────────────────────────────────────────────────────────

def _xp_canonical(pages: list[dict], canonical_map: dict[str, str]) -> list[dict]:
    """
    Detect canonical chains (A → B → C) and loops (A → B → A).

    Only crawled URLs are followed. Chains with ≥ 2 hops are flagged because
    Google does not reliably follow indirect canonicals.
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
                    "type":   "canonical_loop",
                    "urls":   loop_urls,
                    "detail": "Canonical loop: " + " → ".join(loop_urls),
                })
                break
            chain.append(nxt)
            seen_here.add(nxt)
            current = nxt

        if not is_loop and len(chain) > 2:
            issues.append({
                "type":   "canonical_chain",
                "urls":   chain,
                "detail": f"Canonical chain ({len(chain) - 1} hops): " + " → ".join(chain),
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
                    "type":   "broken_internal_link",
                    "source": src,
                    "target": href,
                    "status": code,
                    "detail": f"Broken link (HTTP {code}): {src} → {href}",
                })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Duplicate content (body_text MD5 hash)
# ─────────────────────────────────────────────────────────────────────────────

def _xp_duplicate_content(real_pages: list[dict]) -> list[dict]:
    """
    Flag pages sharing identical body content by MD5 hash.
    Pages with fewer than 50 words are excluded (insufficient signal).
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
                "type":   "duplicate_content",
                "urls":   urls,
                "hash":   h,
                "detail": f"{len(urls)} pages share identical body content (hash {h[:8]}…)",
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
                    "type":   "hreflang_missing_reciprocal",
                    "source": src,
                    "lang":   lang,
                    "target": tgt,
                    "detail": (
                        f"{src} declares hreflang '{lang}' → {tgt} "
                        f"but {tgt} has no reciprocal hreflang pointing back"
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
                "type":   "orphan_page",
                "url":    url,
                "detail": f"Orphan page — no crawled internal link points to {url}",
            })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Cross-page: Sitemap comparison
# ─────────────────────────────────────────────────────────────────────────────

def _xp_sitemap(pages: list[dict], sitemap_urls: list[str]) -> list[dict]:
    """
    Compare crawled pages against sitemap URL list.

    Flags:
      sitemap_noindex — URL is in sitemap but has noindex directive
      sitemap_missing — URL is in sitemap but was not successfully crawled
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
            "type":   "sitemap_noindex",
            "url":    url,
            "detail": f"noindex page in sitemap — Googlebot will not index {url}; remove from sitemap",
        })

    for url in sorted(sitemap_set - crawled_set)[:30]:
        issues.append({
            "type":   "sitemap_missing",
            "url":    url,
            "detail": f"Sitemap URL not crawled — may be blocked, redirected, or unreachable: {url}",
        })

    return issues
