"""
site_auditor.py — Technical site-level audits
Covers:
  - robots.txt rule parsing & directive extraction
  - HSTS header check (Strict-Transport-Security)
  - Mixed content scanner (HTTP resources on HTTPS pages)
  - Redirect chain tracer (up to 10 hops)
No paid APIs. Uses aiohttp for network calls where needed.
"""
from __future__ import annotations

import re
import asyncio
from urllib.parse import urlparse, urljoin

try:
    import aiohttp
    _AIOHTTP = True
except ImportError:
    _AIOHTTP = False

try:
    from curl_cffi.requests import AsyncSession as _CffiSession
    _CFFI = True
except ImportError:
    _CFFI = False

# ---------------------------------------------------------------------------
# robots.txt parser
# ---------------------------------------------------------------------------

def parse_robots_txt(content: str, target_agent: str = "*") -> dict:
    """
    Parse robots.txt content and return structured directives.

    Returns:
    {
      "user_agents": [str],
      "disallowed": [str],          # paths blocked for target_agent
      "allowed": [str],             # explicit allows for target_agent
      "sitemaps": [str],
      "crawl_delay": float | None,
      "has_googlebot_rules": bool,
      "issues": [str],
    }
    """
    if not content:
        return {
            "user_agents": [],
            "disallowed": [],
            "allowed": [],
            "sitemaps": [],
            "crawl_delay": None,
            "has_googlebot_rules": False,
            "issues": ["robots.txt not found or empty"],
        }

    lines = [l.strip() for l in content.splitlines()]
    agents_seen: list[str] = []
    disallowed: list[str] = []
    allowed: list[str] = []
    sitemaps: list[str] = []
    crawl_delay: float | None = None
    current_agents: list[str] = []
    in_target_block = False
    has_googlebot = False

    for line in lines:
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if lower.startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip()
            agents_seen.append(agent)
            current_agents = [a.strip() for a in agent.split(",")]
            in_target_block = any(
                a.lower() in (target_agent.lower(), "*") for a in current_agents
            )
            if any(a.lower() == "googlebot" for a in current_agents):
                has_googlebot = True
        elif lower.startswith("disallow:") and in_target_block:
            path = line.split(":", 1)[1].strip()
            if path:
                disallowed.append(path)
        elif lower.startswith("allow:") and in_target_block:
            path = line.split(":", 1)[1].strip()
            if path:
                allowed.append(path)
        elif lower.startswith("crawl-delay:") and in_target_block:
            try:
                crawl_delay = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif lower.startswith("sitemap:"):
            # Preserve the full URL including https:// prefix
            sitemaps.append(line[len("sitemap:"):].strip())

    issues = []
    if "/" in disallowed:
        issues.append("Entire site blocked by Disallow: /")
    if not sitemaps:
        issues.append("No Sitemap directive found in robots.txt")
    if crawl_delay and crawl_delay > 10:
        issues.append(f"High crawl-delay: {crawl_delay}s may slow indexing")

    return {
        "user_agents": list(dict.fromkeys(agents_seen)),
        "disallowed": disallowed,
        "allowed": allowed,
        "sitemaps": sitemaps,
        "crawl_delay": crawl_delay,
        "has_googlebot_rules": has_googlebot,
        "issues": issues,
    }


async def fetch_robots_txt(site_url: str, timeout: int = 8) -> dict:
    """Fetch and parse /robots.txt for the given site."""
    parsed = urlparse(site_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    content = ""
    error = None

    if _AIOHTTP:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    robots_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers={"User-Agent": "CrawlIQ-SEO-Bot/1.0"},
                    allow_redirects=True,
                ) as resp:
                    if resp.status == 200:
                        content = await resp.text(errors="replace")
        except Exception as e:
            error = str(e)

    result = parse_robots_txt(content)
    result["url"] = robots_url
    if error:
        result["issues"].append(f"Fetch error: {error}")
    return result


# ---------------------------------------------------------------------------
# HSTS header check
# ---------------------------------------------------------------------------

def check_hsts(headers: dict) -> dict:
    """
    Evaluate Strict-Transport-Security header.

    Returns:
    {
      "present": bool,
      "value": str,
      "max_age": int | None,
      "includes_subdomains": bool,
      "preload": bool,
      "status": "good" | "weak" | "missing",
      "issues": [str],
    }
    """
    hsts = (
        headers.get("Strict-Transport-Security")
        or headers.get("strict-transport-security")
        or ""
    )
    if not hsts:
        return {
            "present": False,
            "value": "",
            "max_age": None,
            "includes_subdomains": False,
            "preload": False,
            "status": "missing",
            "issues": ["Strict-Transport-Security header not present"],
        }

    max_age = None
    m = re.search(r"max-age\s*=\s*(\d+)", hsts, re.I)
    if m:
        max_age = int(m.group(1))

    includes_sub = bool(re.search(r"includeSubDomains", hsts, re.I))
    preload = bool(re.search(r"preload", hsts, re.I))

    issues = []
    if max_age is None:
        issues.append("HSTS max-age directive missing")
    elif max_age < 31536000:  # < 1 year
        issues.append(f"HSTS max-age {max_age}s is below recommended 1 year (31536000)")
    if not includes_sub:
        issues.append("HSTS missing includeSubDomains")
    if not preload:
        issues.append("HSTS missing preload directive (optional but recommended)")

    if not issues:
        status = "good"
    elif max_age and max_age >= 31536000:
        status = "weak"
    else:
        status = "weak"

    return {
        "present": True,
        "value": hsts,
        "max_age": max_age,
        "includes_subdomains": includes_sub,
        "preload": preload,
        "status": status,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Mixed content scanner
# ---------------------------------------------------------------------------

_HTTP_RE = re.compile(r"""(?:src|href|action|data|poster)\s*=\s*['"]http://[^'"]+['"]""", re.I)
_SRCSET_RE = re.compile(r"""srcset\s*=\s*['"][^'"]*http://[^'"]+['"]""", re.I)
_CSS_HTTP_RE = re.compile(r"""url\s*\(\s*['"]?http://[^)'"]+['"]?\s*\)""", re.I)


def scan_mixed_content(page_url: str, raw_html: str) -> dict:
    """
    Scan raw HTML for HTTP resources loaded on an HTTPS page.

    Returns:
    {
      "page_url": str,
      "is_https": bool,
      "mixed_resources": [str],
      "count": int,
      "status": "clean" | "has_mixed_content",
      "issues": [str],
    }
    """
    is_https = page_url.startswith("https://")

    if not is_https:
        return {
            "page_url": page_url,
            "is_https": False,
            "mixed_resources": [],
            "count": 0,
            "status": "clean",
            "issues": ["Page is served over HTTP — upgrade to HTTPS"],
        }

    mixed = set()
    for match in _HTTP_RE.findall(raw_html):
        # Extract just the URL
        m = re.search(r"http://[^'\"]+", match)
        if m:
            mixed.add(m.group(0))
    for match in _SRCSET_RE.findall(raw_html):
        for m in re.finditer(r"http://\S+", match):
            mixed.add(m.group(0).rstrip(","))
    for match in _CSS_HTTP_RE.findall(raw_html):
        m = re.search(r"http://[^)'\"]+", match)
        if m:
            mixed.add(m.group(0))

    mixed_list = sorted(mixed)
    issues = [f"Mixed content: {u}" for u in mixed_list[:10]]

    return {
        "page_url": page_url,
        "is_https": True,
        "mixed_resources": mixed_list,
        "count": len(mixed_list),
        "status": "has_mixed_content" if mixed_list else "clean",
        "issues": issues,
    }


def scan_mixed_content_all(pages: list[dict]) -> dict:
    """
    Report mixed content using pre-computed mixed_resources field from crawler.
    The crawler scans HTML inline during _parse() and stores results per page.
    No raw_html storage needed — avoids duplicating HTML in memory.
    """
    results = []
    total_mixed = 0
    for page in pages:
        url = page.get("url", "")
        # Use pre-computed list from crawler._parse() (not raw_html)
        mixed = page.get("mixed_resources") or []
        r = {
            "page_url": url,
            "is_https": url.startswith("https://"),
            "mixed_resources": mixed,
            "count": len(mixed),
            "status": "has_mixed_content" if mixed else "clean",
            "issues": [f"Mixed content: {u}" for u in mixed[:5]],
        }
        results.append(r)
        total_mixed += len(mixed)

    affected_pages = [r for r in results if r["status"] == "has_mixed_content"]
    return {
        "pages_scanned": len(results),
        "pages_with_mixed_content": len(affected_pages),
        "total_mixed_resources": total_mixed,
        "affected": affected_pages,
    }


# ---------------------------------------------------------------------------
# Redirect chain tracer
# ---------------------------------------------------------------------------

async def trace_redirect_chain(url: str, max_hops: int = 10, timeout: int = 6) -> dict:
    """
    Follow redirect chain for a URL, recording each hop.

    Returns:
    {
      "original_url": str,
      "final_url": str,
      "hops": int,
      "chain": [{"url": str, "status": int}],
      "status": "ok" | "redirect_chain" | "redirect_loop" | "error",
      "issues": [str],
    }
    """
    chain = []
    visited = set()
    current = url
    issues = []

    for hop in range(max_hops + 1):
        if current in visited:
            issues.append(f"Redirect loop detected at {current}")
            return {
                "original_url": url,
                "final_url": current,
                "hops": hop,
                "chain": chain,
                "status": "redirect_loop",
                "issues": issues,
            }
        visited.add(current)

        status_code = 0
        next_url = None

        if _AIOHTTP:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        current,
                        allow_redirects=False,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                        headers={"User-Agent": "CrawlIQ-SEO-Bot/1.0"},
                    ) as resp:
                        status_code = resp.status
                        if status_code in (301, 302, 303, 307, 308):
                            next_url = resp.headers.get("Location", "")
                            if next_url and not next_url.startswith("http"):
                                next_url = urljoin(current, next_url)
            except Exception as e:
                issues.append(f"Error fetching {current}: {e}")
                chain.append({"url": current, "status": 0})
                break
        else:
            break

        chain.append({"url": current, "status": status_code})

        if next_url:
            current = next_url
        else:
            break

    final_url = chain[-1]["url"] if chain else url
    hop_count = len(chain) - 1

    if hop_count >= max_hops:
        issues.append(f"Redirect chain exceeds {max_hops} hops")

    # Flag non-HTTPS final destination
    if final_url.startswith("http://"):
        issues.append("Final redirect destination is HTTP, not HTTPS")

    # Flag 302 used where 301 preferred
    for hop in chain[:-1]:
        if hop["status"] == 302:
            issues.append(f"Temporary redirect (302) used at {hop['url']} — prefer 301 for SEO")

    if not issues and hop_count == 0:
        status = "ok"
    elif issues and any("loop" in i for i in issues):
        status = "redirect_loop"
    elif hop_count > 2:
        status = "redirect_chain"
        issues.insert(0, f"Long redirect chain: {hop_count} hops")
    else:
        status = "ok"

    return {
        "original_url": url,
        "final_url": final_url,
        "hops": hop_count,
        "chain": chain,
        "status": status,
        "issues": issues,
    }


async def trace_redirect_chains_batch(urls: list[str], concurrency: int = 5) -> list[dict]:
    """Trace redirect chains for multiple URLs concurrently."""
    sem = asyncio.Semaphore(concurrency)

    async def _trace(url: str) -> dict:
        async with sem:
            return await trace_redirect_chain(url)

    tasks = [_trace(u) for u in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)


# ---------------------------------------------------------------------------
# Full site audit entry point
# ---------------------------------------------------------------------------

def parse_sitemap_xml(content: str) -> list[str]:
    """
    Extract <loc> URLs from a sitemap XML string.
    Handles both <urlset> and <sitemapindex> formats without external XML libraries.
    Returns a deduplicated, order-preserving list of URL strings.
    """
    # Regex extraction tolerates whitespace and attribute variations in <loc> tags.
    raw = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", content, re.I)
    seen:   set[str]   = set()
    result: list[str]  = []
    for u in raw:
        u = u.strip().rstrip("/")
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


# ---------------------------------------------------------------------------
# Cross-validation layer
# ---------------------------------------------------------------------------

def _cv_sitemap_indexability(pages: list[dict], sitemap_urls: list[str]) -> list[dict]:
    """
    1. SITEMAP vs INDEXABILITY
    Flag pages that are in the sitemap but:
      - have noindex (meta robots or X-Robots-Tag)
      - returned 4xx or 5xx status
    """
    if not sitemap_urls:
        return []

    sitemap_set = {u.rstrip("/") for u in sitemap_urls}
    issues: list[dict] = []

    for page in pages:
        url = (page.get("url") or "").rstrip("/")
        if url not in sitemap_set:
            continue

        status = page.get("status_code", 200)

        # 4xx / 5xx in sitemap
        if isinstance(status, int) and status >= 400:
            issues.append({
                "type":          "sitemap_error_page",
                "severity":      "CRITICAL" if status >= 500 else "HIGH",
                "category":      "sitemap_indexability",
                "detail":        f"Sitemap lists URL that returns HTTP {status}",
                "affected_urls": [url],
                "source_url":    url,
                "count":         1,
            })

        # noindex in sitemap
        noindex_meta   = page.get("noindex", False)
        noindex_header = page.get("x_robots_noindex", False)
        if noindex_meta or noindex_header:
            source = "X-Robots-Tag header" if noindex_header else "meta robots"
            issues.append({
                "type":          "sitemap_noindex_conflict",
                "severity":      "HIGH",
                "category":      "sitemap_indexability",
                "detail":        f"Sitemap lists URL that has noindex ({source})",
                "affected_urls": [url],
                "source_url":    url,
                "count":         1,
            })

    return issues


def _cv_internal_links_status(pages: list[dict]) -> list[dict]:
    """
    2. INTERNAL LINKS vs STATUS
    Flag pages that have internal links pointing to:
      - 4xx pages
      - pages reached via redirect chains (redirect_hops > 1)
    """
    # Build lookup: url → {status_code, redirect_hops}
    url_info: dict[str, dict] = {}
    for page in pages:
        url = (page.get("url") or "").rstrip("/")
        if url:
            url_info[url] = {
                "status": page.get("status_code", 200),
                "hops":   page.get("redirect_hops", 0),
            }

    issues: list[dict] = []

    for page in pages:
        src = page.get("url", "")
        internal_links = page.get("internal_links") or []

        broken_targets:   list[str] = []
        redirect_targets: list[str] = []

        for link in internal_links:
            target = link.rstrip("/") if isinstance(link, str) else ""
            if not target or target not in url_info:
                continue
            info = url_info[target]
            if isinstance(info["status"], int) and info["status"] >= 400:
                broken_targets.append(target)
            elif info["hops"] > 1:
                redirect_targets.append(target)

        if broken_targets:
            issues.append({
                "type":          "internal_link_to_4xx",
                "severity":      "HIGH",
                "category":      "internal_links",
                "detail":        f"{len(broken_targets)} internal link(s) point to error pages",
                "affected_urls": broken_targets[:20],
                "source_url":    src,
                "count":         len(broken_targets),
            })

        if redirect_targets:
            issues.append({
                "type":          "internal_link_redirect_chain",
                "severity":      "MEDIUM",
                "category":      "internal_links",
                "detail":        f"{len(redirect_targets)} internal link(s) pass through redirect chains (>1 hop)",
                "affected_urls": redirect_targets[:20],
                "source_url":    src,
                "count":         len(redirect_targets),
            })

    return issues


def _cv_canonical_consistency(pages: list[dict]) -> list[dict]:
    """
    3. CANONICAL CONSISTENCY
    Detect:
      - Canonical loops (A → B → A)
      - Multi-hop chains (A → B → C)
      - Canonical pointing to non-indexable pages (noindex / 4xx)
    """
    # Build lookup: url → {canonical, noindex, x_robots_noindex, status_code}
    page_info: dict[str, dict] = {}
    for page in pages:
        url = (page.get("url") or "").rstrip("/")
        if url:
            page_info[url] = {
                "canonical":        (page.get("canonical") or "").rstrip("/"),
                "noindex":          page.get("noindex", False),
                "x_robots_noindex": page.get("x_robots_noindex", False),
                "status":           page.get("status_code", 200),
            }

    issues: list[dict] = []

    for url, info in page_info.items():
        canon = info["canonical"]
        if not canon or canon == url:
            continue  # self-canonical or missing — no cross-page problem

        # Multi-hop: canonical target itself has a different canonical
        target_info = page_info.get(canon)
        if target_info:
            target_canon = target_info["canonical"]
            if target_canon and target_canon != canon:
                # Could be loop or extended chain — trace up to 5 hops
                chain = [url, canon]
                visited = {url, canon}
                cursor = target_canon
                loop = False
                while cursor and cursor not in visited and len(chain) < 6:
                    chain.append(cursor)
                    visited.add(cursor)
                    next_info = page_info.get(cursor)
                    cursor = (next_info["canonical"] if next_info else "") or ""
                if cursor in visited:
                    loop = True

                if loop:
                    issues.append({
                        "type":          "canonical_loop",
                        "severity":      "HIGH",
                        "category":      "canonical",
                        "detail":        f"Canonical loop detected: {' → '.join(chain[:5])} → …",
                        "affected_urls": chain[:5],
                        "source_url":    url,
                        "count":         len(chain),
                    })
                else:
                    issues.append({
                        "type":          "canonical_chain",
                        "severity":      "MEDIUM",
                        "category":      "canonical",
                        "detail":        f"Multi-hop canonical chain ({len(chain) - 1} hops): {url} → … → {chain[-1]}",
                        "affected_urls": chain,
                        "source_url":    url,
                        "count":         len(chain) - 1,
                    })
                continue  # already reported, skip noindex check for this URL

            # Canonical target is noindex or error page
            target_noindex = target_info["noindex"] or target_info["x_robots_noindex"]
            target_status  = target_info["status"]
            if target_noindex:
                issues.append({
                    "type":          "canonical_to_noindex",
                    "severity":      "HIGH",
                    "category":      "canonical",
                    "detail":        f"Canonical points to a noindex page: {canon}",
                    "affected_urls": [url, canon],
                    "source_url":    url,
                    "count":         1,
                })
            elif isinstance(target_status, int) and target_status >= 400:
                issues.append({
                    "type":          "canonical_to_error",
                    "severity":      "CRITICAL",
                    "category":      "canonical",
                    "detail":        f"Canonical points to HTTP {target_status} page: {canon}",
                    "affected_urls": [url, canon],
                    "source_url":    url,
                    "count":         1,
                })

    return issues


def _cv_hreflang_canonical(pages: list[dict]) -> list[dict]:
    """
    4. HREFLANG vs CANONICAL
    Ensure hreflang targets are self-canonicalized:
      - hreflang target's canonical must equal the target URL itself
    """
    # Build lookup: url → canonical
    canon_map: dict[str, str] = {}
    for page in pages:
        url = (page.get("url") or "").rstrip("/")
        if url:
            canon_map[url] = (page.get("canonical") or "").rstrip("/")

    issues: list[dict] = []

    for page in pages:
        src = page.get("url", "")
        hreflang_tags = page.get("hreflang_tags") or {}  # {lang: target_url}

        bad_targets: list[str] = []

        for lang, target in hreflang_tags.items():
            if not target:
                continue
            target_norm = target.rstrip("/")
            # Only validate targets we actually crawled
            if target_norm not in canon_map:
                continue
            target_canon = canon_map[target_norm]
            # self-canonical = canonical is empty (not set) or equals the URL
            if target_canon and target_canon != target_norm:
                bad_targets.append(
                    f"{lang}: {target} → canonical={target_canon}"
                )

        if bad_targets:
            issues.append({
                "type":          "hreflang_non_canonical_target",
                "severity":      "HIGH",
                "category":      "hreflang_canonical",
                "detail":        (
                    f"{len(bad_targets)} hreflang target(s) are not self-canonicalized"
                ),
                "affected_urls": [src],
                "source_url":    src,
                "count":         len(bad_targets),
                "details":       bad_targets[:10],
            })

    return issues


def cross_validate(pages: list[dict], sitemap_urls: list[str] | None = None) -> dict:
    """
    Run all cross-validation checks across crawled pages.

    Checks performed:
      1. Sitemap vs Indexability  — noindex / error pages listed in sitemap
      2. Internal Links vs Status — links to 4xx pages or redirect chains
      3. Canonical Consistency    — loops, multi-hop chains, canonical → bad target
      4. Hreflang vs Canonical   — hreflang targets must be self-canonicalized

    Returns:
    {
      "consistency_issues": [
        {
          "type":          str,
          "severity":      "CRITICAL" | "HIGH" | "MEDIUM",
          "category":      str,
          "detail":        str,
          "affected_urls": [str],
          "source_url":    str,
          "count":         int,
        }
      ],
      "summary": {
        "total_issues": int,
        "critical":     int,
        "high":         int,
        "medium":       int,
        "by_category":  {str: int},
      }
    }
    """
    all_issues: list[dict] = []

    all_issues.extend(_cv_sitemap_indexability(pages, sitemap_urls or []))
    all_issues.extend(_cv_internal_links_status(pages))
    all_issues.extend(_cv_canonical_consistency(pages))
    all_issues.extend(_cv_hreflang_canonical(pages))

    # Build summary
    by_cat: dict[str, int] = {}
    n_crit = n_high = n_med = 0
    for issue in all_issues:
        sev = issue.get("severity", "MEDIUM")
        cat = issue.get("category", "other")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        if sev == "CRITICAL":
            n_crit += 1
        elif sev == "HIGH":
            n_high += 1
        else:
            n_med += 1

    return {
        "consistency_issues": all_issues,
        "summary": {
            "total_issues": len(all_issues),
            "critical":     n_crit,
            "high":         n_high,
            "medium":       n_med,
            "by_category":  by_cat,
        },
    }


async def run_site_audit(site_url: str, pages: list[dict], sitemap_urls: list[str] | None = None) -> dict:
    """
    Run all site-level audits.

    Returns combined result dict with keys:
      robots, hsts, mixed_content, redirect_chains, consistency
    """
    # robots.txt
    robots = await fetch_robots_txt(site_url)

    # HSTS — use headers from first crawled page if available
    hsts_headers = {}
    if pages:
        hsts_headers = pages[0].get("response_headers", {}) or {}
    hsts = check_hsts(hsts_headers)

    # Mixed content
    mixed = scan_mixed_content_all(pages)

    # Redirect chains — use hop counts already collected by crawler (zero extra HTTP requests).
    # trace_redirect_chains_batch() is available for on-demand use but too slow for the
    # full site audit (50 URLs × up to 10 hops × 6s each would cause request timeouts).
    redir_pages = [
        {
            "url":    p["url"],
            "hops":   p.get("redirect_hops", 0),
            "status": "redirect_chain" if p.get("redirect_hops", 0) > 1 else "ok",
            "issues": [f"Redirect chain: {p.get('redirect_hops', 0)} hops to reach {p['url']}"]
                      if p.get("redirect_hops", 0) > 1 else [],
        }
        for p in pages if p.get("url")
    ]
    problematic_redirects = [r for r in redir_pages if r["status"] != "ok"]

    # Cross-validation — consistency checks across pages, sitemap, canonicals, hreflang
    consistency = cross_validate(pages, sitemap_urls)

    return {
        "robots": robots,
        "hsts": hsts,
        "mixed_content": mixed,
        "redirect_chains": {
            "checked": len(redir_pages),
            "problematic": len(problematic_redirects),
            "issues": problematic_redirects[:20],
        },
        "consistency": consistency,
    }
