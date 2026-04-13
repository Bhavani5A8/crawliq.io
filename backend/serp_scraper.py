"""
serp_scraper.py — Lightweight Google SERP position scraper + keyword difficulty.

Design constraints
------------------
* Zero paid APIs — uses the public Google Search HTML endpoint.
* Rate-limit safe — per-call random delay 1-3s, max 3 parallel queries.
* Fail-silent — any scrape failure returns position=None, difficulty=None.
* Never blocks the main event loop — all I/O is async.

Public API
----------
  async get_serp_position(keyword, domain, *, lang="en", num=30) -> int | None
      Returns 1-based SERP position of `domain` for `keyword`, or None.

  async get_keyword_difficulty(keyword, *, lang="en") -> dict
      Scrapes top-10 results and computes difficulty from OPR scores.
      Returns {keyword, difficulty_score (0-100), difficulty_label,
               top_domains, avg_opr, data_source}

  async bulk_serp_check(queries, domain, *, concurrency=3) -> list[dict]
      Run get_serp_position for multiple keywords in controlled batches.
      Returns [{keyword, position, in_top_10, in_top_30}]

  async bulk_difficulty(keywords, *, concurrency=3) -> list[dict]
      Run get_keyword_difficulty for multiple keywords.

Architecture
------------
  1. Google Search HTML scrape via aiohttp (no Playwright / headless browser).
     Uses the `num` param to fetch up to 30 results per query.
  2. Domain presence detection: strip scheme/www, check if result URL contains
     the target domain substring.
  3. Keyword difficulty via OpenPageRank (OPR) API:
     - Fetch OPR scores for the top-10 result domains.
     - Difficulty = 100 − mean(OPR scores), clamped to [0, 100].
     - OPR ranges 0-10; high OPR means strong domain → high difficulty.
  4. All OPR calls are batched (10 domains per request, OPR supports bulk).

Dependencies
------------
  aiohttp (already in requirements.txt)
  No new packages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Optional
from urllib.parse import quote_plus, urlparse

import aiohttp

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

_GOOGLE_SEARCH_URL = "https://www.google.com/search"
_OPR_API_URL       = "https://openpagerank.com/api/v1.0/getPageRank"
_OPR_API_KEY       = os.getenv("OPR_API_KEY", "")   # optional — free tier key

_SCRAPE_TIMEOUT    = 10    # seconds per Google request
_OPR_TIMEOUT       = 8     # seconds per OPR request
_MIN_DELAY         = 1.2   # minimum sleep between Google calls (seconds)
_MAX_DELAY         = 3.0   # maximum sleep
_MAX_RESULTS       = 30    # num= param — fetch top-30 so we can check top-10/top-30

# Google returns different HTML per User-Agent; mimic a Chrome browser.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# Regex patterns to extract result URLs from Google's HTML.
# Google wraps organic result URLs in <a href="/url?q=..."> or <cite>.
_RE_RESULT_URL = re.compile(
    r'href="/url\?q=(https?://[^&"]+)&',
    re.IGNORECASE,
)
_RE_CITE       = re.compile(r'<cite[^>]*>(https?://[^<\s]+)</cite>', re.IGNORECASE)


# ── Domain normalisation ───────────────────────────────────────────────────────

def _normalise_domain(raw: str) -> str:
    """Strip scheme, www., trailing slashes. Returns bare hostname."""
    raw = raw.strip().lower()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        h = urlparse(raw).hostname or raw
        return h.removeprefix("www.")
    except Exception:
        return raw


def _domain_in_url(target_domain: str, result_url: str) -> bool:
    """Return True if result_url belongs to target_domain (or a subdomain)."""
    try:
        result_host = urlparse(result_url).hostname or ""
        result_host = result_host.lower().removeprefix("www.")
        return result_host == target_domain or result_host.endswith("." + target_domain)
    except Exception:
        return False


# ── SERP scrape ────────────────────────────────────────────────────────────────

async def _fetch_serp_html(
    session: aiohttp.ClientSession,
    keyword: str,
    lang:    str = "en",
    num:     int = _MAX_RESULTS,
) -> str:
    """
    Fetch Google Search HTML for `keyword`.
    Returns raw HTML string, or "" on error.
    """
    params = {
        "q":    keyword,
        "num":  num,
        "hl":   lang,
        "gl":   "us",
        "pws":  "0",          # disable personalised results
        "safe": "off",
    }
    timeout = aiohttp.ClientTimeout(total=_SCRAPE_TIMEOUT)
    try:
        async with session.get(
            _GOOGLE_SEARCH_URL,
            params=params,
            headers=_HEADERS,
            timeout=timeout,
            ssl=False,
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                logger.debug("SERP fetch returned HTTP %d for %r", resp.status, keyword)
                return ""
            return await resp.text(errors="replace")
    except Exception as exc:
        logger.debug("SERP fetch error for %r: %s", keyword, exc)
        return ""


def _extract_result_urls(html: str) -> list[str]:
    """
    Pull organic result URLs from raw Google HTML.
    Uses two regex patterns — href=/url?q= (primary) and <cite> (fallback).
    Deduplicates while preserving order.
    """
    found = _RE_RESULT_URL.findall(html)
    if not found:
        # Fallback: pull from <cite> tags
        found = _RE_CITE.findall(html)

    seen: set[str] = set()
    urls: list[str] = []
    for raw in found:
        # Decode %XX sequences
        try:
            from urllib.parse import unquote
            url = unquote(raw).split("&")[0].strip()
        except Exception:
            url = raw
        # Filter out Google's own URLs and non-HTTP results
        if not url.startswith("http"):
            continue
        host = urlparse(url).hostname or ""
        if "google." in host or "googleusercontent" in host:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


# ── OpenPageRank integration ───────────────────────────────────────────────────

async def _fetch_opr_scores(
    session: aiohttp.ClientSession,
    domains: list[str],
) -> dict[str, float]:
    """
    Fetch OpenPageRank scores for a list of domains (max 100 per request).
    Returns {domain: opr_score} mapping. Score range 0.0–10.0.
    Falls back to empty dict if OPR API key not configured or call fails.

    OPR free tier: 5 API calls/day (10 domains per call → 50 domains/day).
    Key is optional — difficulty falls back to link-count heuristic without it.
    """
    if not _OPR_API_KEY or not domains:
        return {}

    batch = domains[:100]
    params = [("domains[]", d) for d in batch]
    headers = {
        "API-OPR": _OPR_API_KEY,
        "Accept":  "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=_OPR_TIMEOUT)
    try:
        async with session.get(
            _OPR_API_URL,
            params=params,
            headers=headers,
            timeout=timeout,
            ssl=True,
        ) as resp:
            if resp.status != 200:
                logger.debug("OPR API returned HTTP %d", resp.status)
                return {}
            data = await resp.json(content_type=None)
            result: dict[str, float] = {}
            for entry in data.get("response", []):
                domain = entry.get("domain", "")
                opr    = entry.get("page_rank_decimal")
                if domain and opr is not None:
                    try:
                        result[domain] = float(opr)
                    except (ValueError, TypeError):
                        pass
            return result
    except Exception as exc:
        logger.debug("OPR fetch failed: %s", exc)
        return {}


def _difficulty_from_opr(opr_scores: dict[str, float], n_results: int) -> dict:
    """
    Compute keyword difficulty from OPR scores of top-10 domains.

    Formula:
      avg_opr  = mean(top-10 OPR scores, default 0 for missing)
      raw      = avg_opr / 10  (normalise to 0-1)
      score    = round(raw * 100)

    Difficulty labels:
      0-25  → Low
      26-50 → Medium
      51-75 → High
      76-100 → Very High
    """
    values = list(opr_scores.values())[:10]
    if not values:
        # No OPR data — use result count as a fallback heuristic.
        # More than 25 results → more established competition → higher difficulty.
        score = min(40 + (n_results // 5) * 5, 70) if n_results > 0 else 50
        return {
            "difficulty_score": score,
            "difficulty_label": _label(score),
            "avg_opr":          None,
            "top_domains":      list(opr_scores.keys())[:10],
            "data_source":      "result_count_heuristic",
        }

    avg_opr = sum(values) / len(values)
    score   = min(round(avg_opr / 10.0 * 100), 100)
    return {
        "difficulty_score": score,
        "difficulty_label": _label(score),
        "avg_opr":          round(avg_opr, 2),
        "top_domains":      list(opr_scores.keys())[:10],
        "data_source":      "opr",
    }


def _label(score: int) -> str:
    if score <= 25: return "Low"
    if score <= 50: return "Medium"
    if score <= 75: return "High"
    return "Very High"


# ── Public API ────────────────────────────────────────────────────────────────

async def get_serp_position(
    keyword: str,
    domain:  str,
    *,
    lang: str = "en",
    num:  int = _MAX_RESULTS,
    sem:  asyncio.Semaphore | None = None,
) -> int | None:
    """
    Return 1-based SERP position of `domain` for `keyword`, or None if not found.

    Args:
        keyword: search query
        domain:  target domain to look for (e.g. "example.com" or "https://example.com")
        lang:    language code for Google search
        num:     how many results to fetch (max 100, default 30)
        sem:     optional semaphore for rate-limiting concurrent calls

    Returns:
        int (1–num) if found in results, or None
    """
    target = _normalise_domain(domain)

    async def _run():
        connector = aiohttp.TCPConnector(limit=1, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Polite random delay to avoid hammering Google
            await asyncio.sleep(random.uniform(_MIN_DELAY, _MAX_DELAY))
            html = await _fetch_serp_html(session, keyword, lang=lang, num=num)
            if not html:
                return None
            urls = _extract_result_urls(html)
            for i, url in enumerate(urls, start=1):
                if _domain_in_url(target, url):
                    return i
            return None

    if sem:
        async with sem:
            return await _run()
    return await _run()


async def get_keyword_difficulty(
    keyword: str,
    *,
    lang: str = "en",
    sem:  asyncio.Semaphore | None = None,
) -> dict:
    """
    Scrape top-10 results for `keyword` and estimate difficulty via OPR scores.

    Returns:
        {
          keyword:          str,
          difficulty_score: int (0-100),
          difficulty_label: "Low" | "Medium" | "High" | "Very High",
          top_domains:      list[str],
          avg_opr:          float | None,
          data_source:      "opr" | "result_count_heuristic",
          error:            str | None,
        }
    """
    result_base = {
        "keyword":          keyword,
        "difficulty_score": None,
        "difficulty_label": None,
        "top_domains":      [],
        "avg_opr":          None,
        "data_source":      None,
        "error":            None,
    }

    async def _run():
        connector = aiohttp.TCPConnector(limit=2, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            await asyncio.sleep(random.uniform(_MIN_DELAY, _MAX_DELAY))
            html = await _fetch_serp_html(session, keyword, lang=lang, num=10)
            if not html:
                return {**result_base, "error": "SERP fetch failed"}

            urls       = _extract_result_urls(html)[:10]
            n_results  = len(urls)
            domains    = [_normalise_domain(u) for u in urls if u]
            domains    = list(dict.fromkeys(domains))   # dedup preserving order

            opr_scores = await _fetch_opr_scores(session, domains)

            diff = _difficulty_from_opr(
                {d: opr_scores[d] for d in domains if d in opr_scores},
                n_results,
            )
            return {**result_base, "keyword": keyword, **diff}

    if sem:
        async with sem:
            return await _run()
    return await _run()


async def bulk_serp_check(
    queries:     list[str],
    domain:      str,
    *,
    concurrency: int = 3,
) -> list[dict]:
    """
    Check SERP positions for multiple keywords in parallel.
    Returns [{keyword, position, in_top_10, in_top_30}]
    """
    sem = asyncio.Semaphore(concurrency)
    tasks = [get_serp_position(kw, domain, sem=sem) for kw in queries]
    positions = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for kw, pos in zip(queries, positions):
        if isinstance(pos, Exception):
            logger.warning("bulk_serp_check error for %r: %s", kw, pos)
            pos = None
        results.append({
            "keyword":    kw,
            "position":   pos,
            "in_top_10":  pos is not None and pos <= 10,
            "in_top_30":  pos is not None and pos <= 30,
        })
    return results


async def bulk_difficulty(
    keywords:    list[str],
    *,
    concurrency: int = 3,
) -> list[dict]:
    """
    Compute keyword difficulty for multiple keywords in parallel.
    Returns list of get_keyword_difficulty() dicts.
    """
    sem   = asyncio.Semaphore(concurrency)
    tasks = [get_keyword_difficulty(kw, sem=sem) for kw in keywords]
    return list(await asyncio.gather(*tasks, return_exceptions=False))
