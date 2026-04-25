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


async def run_site_audit(site_url: str, pages: list[dict]) -> dict:
    """
    Run all site-level audits.

    Returns combined result dict with keys:
      robots, hsts, mixed_content, redirect_chains
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

    return {
        "robots": robots,
        "hsts": hsts,
        "mixed_content": mixed,
        "redirect_chains": {
            "checked": len(redir_pages),
            "problematic": len(problematic_redirects),
            "issues": problematic_redirects[:20],
        },
    }
