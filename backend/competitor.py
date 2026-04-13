"""
competitor.py — Lightweight async competitor analysis.

For each page's top keyword, fetches up to 2 competitor pages from
Google Search results, extracts their title / H1 / top-5 keywords,
and compares against the source page.

Design rules:
  - Async throughout — no blocking, no time.sleep
  - Fail-silent at every level — one failure never affects other pages
  - Max 2 competitor URLs per page to stay fast and polite
  - Uses the same SSL-permissive approach as crawler._fetch
  - Does NOT modify crawler.py, BFS, or any other existing file
  - Called from keyword_pipeline.run_keyword_pipeline() after scoring

Public API:
    async run_competitor_analysis(pages, session, sem) -> None
        Mutates page["competitor_gaps"] in-place for each page.
        Pages without keywords or with _is_error are skipped silently.
"""

import asyncio
import json
import logging
import re
import ssl as _ssl
from urllib.parse import quote, urlparse

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
# BUG-016: raised from 2 → 3 for richer competitive signal without excessive
# load. Full competitor analysis (competitor_analysis.py) already accepts up
# to 5 URLs entered by the user; this setting governs the lightweight
# per-page BFS analysis only.
MAX_COMPETITORS   = 3      # max competitor pages to fetch per source page
FETCH_TIMEOUT     = 8      # seconds per competitor fetch
SEARCH_TIMEOUT    = 6      # seconds for Google Search scrape
CONCURRENCY_COMP  = 3      # parallel competitor fetches across all pages

# ── Stopwords (same minimal set as keyword_pipeline) ─────────────────────────
_SW = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with","by",
    "is","are","was","were","be","been","have","has","had","do","does","did",
    "will","would","could","should","may","might","can","not","no","nor",
    "this","that","these","those","it","its","he","she","they","we","you",
    "from","into","about","than","more","also","just","only","very","so",
    "com","org","net","www","http","https","page","site","click","read",
}

# Browser headers — same as crawler
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── SSL helper ────────────────────────────────────────────────────────────────

def _permissive_ssl() -> _ssl.SSLContext:
    """Kept for API compat — no longer called in hot paths (ssl=False used instead)."""
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = _ssl.CERT_NONE
    return ctx


# ── 1. Token extraction (self-contained, no import from keyword_scorer) ───────

def _top_keywords(text: str, n: int = 5) -> list[str]:
    """Fast frequency-based top-n unigrams from plain text."""
    from collections import Counter
    tokens = re.findall(r"[a-z]{3,}", text.lower())
    tokens = [t for t in tokens if t not in _SW]
    return [w for w, _ in Counter(tokens).most_common(n)]


# ── 2. Fetch + parse one competitor page ─────────────────────────────────────

async def _fetch_competitor_page(
    url: str,
    sem: asyncio.Semaphore,
) -> dict | None:
    """
    Fetch one URL and extract {url, title, h1, keywords}.
    Returns None on any failure — never raises.

    Uses its own short-lived session (same pattern as crawler._fetch)
    so SSL settings don't leak into the main crawl session.
    """
    async with sem:
        # ssl=False avoids Windows SChannel semaphore timeout
        connector = aiohttp.TCPConnector(
            ssl=False, limit=2, limit_per_host=2, force_close=True
        )
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        try:
            async with aiohttp.ClientSession(
                headers=_HEADERS, timeout=timeout, connector=connector
            ) as sess:
                async with sess.get(url, allow_redirects=True, ssl=False) as resp:
                    if resp.status != 200:
                        return None
                    ctype = resp.headers.get("Content-Type", "")
                    if "text/html" not in ctype:
                        return None
                    html = await resp.text(errors="replace")

            # Parse outside the response context (connection already closed)
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception:
                soup = BeautifulSoup(html, "html.parser")

            # Strip noise tags
            for tag in soup(["script","style","nav","footer","header","noscript"]):
                tag.decompose()

            title = (soup.find("title") or {}).get_text(strip=True) if soup.find("title") else ""
            h1_tag = soup.find("h1")
            h1    = h1_tag.get_text(strip=True) if h1_tag else ""
            body  = " ".join(soup.get_text(" ", strip=True).split())[:2000]
            kws   = _top_keywords(body, n=5)

            logger.debug("Competitor fetched: %s", url)
            return {"url": url, "title": title, "h1": h1, "keywords": kws}

        except asyncio.TimeoutError:
            logger.debug("Competitor timeout: %s", url)
            return None
        except Exception as exc:
            logger.debug("Competitor fetch failed: %s — %s", url, exc)
            return None
        finally:
            if not connector.closed:
                await connector.close()


# ── 3. Find competitor URLs via Google Search (scrape, fail-silent) ───────────

async def _search_competitor_urls(
    keyword: str,
    source_domain: str,
    sem: asyncio.Semaphore,
    max_urls: int = MAX_COMPETITORS,
) -> list[str]:
    """
    Scrape Google Search results for `keyword` and return up to `max_urls`
    URLs that are NOT from `source_domain`.

    Completely fail-silent — returns [] on any error or block.
    Google may return a CAPTCHA for repeated scraping; this is expected
    behaviour and is handled by returning an empty list gracefully.
    """
    query = quote(keyword)
    search_url = f"https://www.google.com/search?q={query}&num=10&hl=en"

    async with sem:
        connector = aiohttp.TCPConnector(
            ssl=False, limit=2, limit_per_host=2, force_close=True
        )
        timeout = aiohttp.ClientTimeout(total=SEARCH_TIMEOUT)
        try:
            async with aiohttp.ClientSession(
                headers=_HEADERS, timeout=timeout, connector=connector
            ) as sess:
                async with sess.get(search_url, allow_redirects=True, ssl=False) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text(errors="replace")

            soup  = BeautifulSoup(html, "html.parser")
            urls  = []
            bare_src = source_domain.lstrip("www.")

            # Google renders result URLs in <a href="/url?q=..."> or <a href="https://...">
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Handle Google redirect links
                if href.startswith("/url?q="):
                    href = href[7:].split("&")[0]
                if not href.startswith("http"):
                    continue
                parsed = urlparse(href)
                # Skip Google's own pages, ads, and source domain
                if parsed.netloc.lstrip("www.") in (bare_src, "google.com",
                                                     "webcache.googleusercontent.com"):
                    continue
                # Skip non-useful paths
                if any(x in href for x in ["/search?", "accounts.google", "support.google"]):
                    continue
                if href not in urls:
                    urls.append(href)
                if len(urls) >= max_urls:
                    break

            logger.debug("Found %d competitors for %r", len(urls), keyword)
            return urls[:max_urls]

        except Exception as exc:
            logger.debug("Competitor search failed for %r: %s", keyword, exc)
            return []
        finally:
            if not connector.closed:
                await connector.close()


# ── 4. Gap analysis ───────────────────────────────────────────────────────────

def _compute_gaps(
    source_page: dict,
    competitor_pages: list[dict],
) -> dict:
    """
    Compare source page against competitor pages and return gap analysis.

    Returns:
        {
          "missing_keywords": list[str],   # in competitors, not in source
          "h1_gap":           str,          # competitor H1 if source H1 is empty
          "title_gap":        str,          # competitor title if source title is empty
          "competitor_urls":  list[str],    # pages analysed
          "competitor_count": int,
        }
    """
    if not competitor_pages:
        return {
            "missing_keywords": [],
            "h1_gap": "",
            "title_gap": "",
            "competitor_urls": [],
            "competitor_count": 0,
        }

    # BUG-N06: keywords can be list[str] (from TF-IDF) or list[dict] (from
    # scorer). Normalise both to lowercase strings before set operations to
    # avoid TypeError: unhashable type on dict and silent wrong gap results.
    def _norm_kws(kws) -> set[str]:
        result: set[str] = set()
        for k in (kws or []):
            if isinstance(k, str):
                result.add(k.lower())
            elif isinstance(k, dict):
                kw = k.get("keyword", "")
                if kw:
                    result.add(kw.lower())
        return result

    src_kws    = _norm_kws(source_page.get("keywords"))
    src_title  = (source_page.get("title") or "").strip()
    src_h1     = " ".join(source_page.get("h1") or []).strip()

    # Collect all competitor keywords (also normalised)
    comp_kws: list[str] = []
    for cp in competitor_pages:
        comp_kws.extend(_norm_kws(cp.get("keywords")))

    # Keywords that appear in ≥1 competitor but NOT in source
    from collections import Counter
    comp_counts   = Counter(comp_kws)
    missing       = [
        kw for kw, _ in comp_counts.most_common(10)
        if kw not in src_kws and len(kw) > 3
    ][:5]

    # H1 gap: if source has no H1, show competitor's first H1
    h1_gap = ""
    if not src_h1:
        for cp in competitor_pages:
            if cp.get("h1"):
                h1_gap = cp["h1"]
                break

    # Title gap: if source title is empty, show competitor title
    title_gap = ""
    if not src_title:
        for cp in competitor_pages:
            if cp.get("title"):
                title_gap = cp["title"]
                break

    return {
        "missing_keywords": missing,
        "h1_gap":           h1_gap,
        "title_gap":        title_gap,
        "competitor_urls":  [cp["url"] for cp in competitor_pages],
        "competitor_count": len(competitor_pages),
    }


# ── 5. Per-page analysis ──────────────────────────────────────────────────────

async def _analyse_page(
    page: dict,
    sem: asyncio.Semaphore,
) -> None:
    """
    Run competitor analysis for one page. Mutates page["competitor_gaps"].
    Completely fail-silent — on any error, page["competitor_gaps"] is set to
    an empty gap dict so downstream code always gets a consistent structure.
    """
    empty_gaps = {
        "missing_keywords": [], "h1_gap": "", "title_gap": "",
        "competitor_urls": [], "competitor_count": 0,
    }

    try:
        keywords = page.get("keywords") or []
        if not keywords:
            page["competitor_gaps"] = empty_gaps
            return

        # Use the top keyword for search
        top_kw      = keywords[0] if isinstance(keywords[0], str) else keywords[0].get("keyword", "")
        if not top_kw:
            page["competitor_gaps"] = empty_gaps
            return

        source_domain = urlparse(page.get("url", "")).netloc

        # Step 1: find competitor URLs
        comp_urls = await _search_competitor_urls(top_kw, source_domain, sem)
        if not comp_urls:
            page["competitor_gaps"] = empty_gaps
            return

        # Step 2: fetch up to MAX_COMPETITORS competitor pages concurrently
        tasks   = [_fetch_competitor_page(u, sem) for u in comp_urls[:MAX_COMPETITORS]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        comp_pages = [r for r in results if isinstance(r, dict) and r is not None]

        # Step 3: gap analysis
        page["competitor_gaps"] = _compute_gaps(page, comp_pages)
        logger.info("Competitor gaps for %s: %d missing kws",
                    page.get("url", "?"),
                    len(page["competitor_gaps"]["missing_keywords"]))

    except Exception as exc:
        logger.warning("Competitor analysis failed for %s: %s",
                       page.get("url", "?"), exc)
        page["competitor_gaps"] = empty_gaps


# ── 6. Batch runner ───────────────────────────────────────────────────────────

async def run_competitor_analysis(pages: list[dict]) -> None:
    """
    Run competitor analysis for all real pages.

    Called from keyword_pipeline.run_keyword_pipeline() after scoring.
    Uses its own semaphore and session lifecycle — completely independent
    from the main crawl session.

    Fails silently per page. All pages always get competitor_gaps set.
    """
    real = [
        p for p in pages
        if not p.get("_is_error") and p.get("status_code") == 200
           and p.get("keywords")
    ]

    if not real:
        return

    sem = asyncio.Semaphore(CONCURRENCY_COMP)
    tasks = [_analyse_page(page, sem) for page in real]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("Competitor batch error for %s: %s",
                           real[i].get("url", "?"), result)
            real[i].setdefault("competitor_gaps", {
                "missing_keywords": [], "h1_gap": "", "title_gap": "",
                "competitor_urls": [], "competitor_count": 0,
            })
