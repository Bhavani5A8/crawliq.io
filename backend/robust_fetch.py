"""
robust_fetch.py — Multi-strategy async fetch layer for the SEO crawler.

Drop-in addition to crawler.py.  Import and call robust_fetch() anywhere
_fetch() currently reads HTML.  Does NOT modify BFS logic, queue, visited
set, or any existing file.

Strategy cascade (in order):
  S1 — aiohttp, fast timeout (8s), HTTPS, ssl=False
  S2 — aiohttp, browser headers + rotated UA (12s), HTTPS, ssl=False
  S3 — aiohttp, HTTP fallback, ssl=False (15s)
  S4 — Playwright headless (15s) — last resort for JS-heavy / bot-blocked

Returns (html: str, status: int, strategy_used: str) on success.
Returns (None, 0, "all_failed") when every strategy fails — caller must skip.

Design rules (same as crawler.py):
  - Fully async — no blocking, no time.sleep
  - Fail-silent at every step — one strategy failure never raises
  - Playwright imported lazily — zero overhead when not needed
  - Concurrency guarded by the caller's asyncio.Semaphore
  - No changes to BFS, queue, visited, max_pages
"""

from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

# ── Timeouts per strategy ─────────────────────────────────────────────────────
_T_S1 = 8    # S1 fast aiohttp
_T_S2 = 12   # S2 browser headers
_T_S3 = 15   # S3 HTTP fallback
_T_S4 = 15   # S4 Playwright

# ── Browser User-Agent pool (same as crawler.py) ─────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

# Full Chrome header block — matches crawler.py HEADERS
_BASE_HEADERS = {
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate, br",
    "Cache-Control":             "max-age=0",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "sec-ch-ua":                 '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile":          "?0",
    "sec-ch-ua-platform":        '"Windows"',
    "DNT":                       "1",
}


def _headers(referer: str = "") -> dict:
    """Build headers with a random UA and optional Referer."""
    h = {**_BASE_HEADERS, "User-Agent": random.choice(_USER_AGENTS)}
    if referer:
        h["Referer"] = referer
    return h


def _http_url(url: str) -> str:
    """Convert https:// → http:// for the HTTP fallback strategy."""
    return url.replace("https://", "http://", 1) if url.startswith("https://") else url


def _new_connector() -> aiohttp.TCPConnector:
    """Fresh connector per attempt — avoids Windows SChannel TLS state sharing."""
    return aiohttp.TCPConnector(ssl=False, limit=2, limit_per_host=2, force_close=True)


# ── Strategy 1: fast aiohttp, HTTPS, ssl=False ───────────────────────────────

async def _s1_aiohttp_fast(url: str) -> tuple[str, int] | None:
    """
    S1 — baseline fast fetch.
    ssl=False skips cert verification without creating an SSLContext object,
    which avoids the Windows SChannel semaphore timeout.
    """
    connector = _new_connector()
    timeout   = aiohttp.ClientTimeout(total=_T_S1)
    try:
        async with aiohttp.ClientSession(
            headers=_headers(), timeout=timeout, connector=connector
        ) as sess:
            async with sess.get(url, allow_redirects=True, ssl=False) as resp:
                if "text/html" not in resp.headers.get("Content-Type", ""):
                    return None
                html = await resp.text(errors="replace")
                logger.info("[FETCH] S1 aiohttp-fast success → %s (HTTP %s)", url, resp.status)
                return html, resp.status
    except asyncio.TimeoutError:
        logger.warning("[FETCH] S1 timeout (%ss) → %s", _T_S1, url)
        return None
    except (aiohttp.ClientConnectorSSLError, aiohttp.ClientConnectorError) as exc:
        logger.warning("[FETCH] S1 connection error → %s — %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("[FETCH] S1 failed → %s — %s", url, exc)
        return None
    finally:
        if not connector.closed:
            await connector.close()


# ── Strategy 2: aiohttp with full browser headers + rotated UA ───────────────

async def _s2_browser_headers(url: str) -> tuple[str, int] | None:
    """
    S2 — adds Referer + full browser fingerprint headers.
    Unblocks Cloudflare / Akamai bot-filters that check header completeness.
    """
    referer   = url.rsplit("/", 1)[0] + "/" if "/" in url[8:] else url
    connector = _new_connector()
    timeout   = aiohttp.ClientTimeout(total=_T_S2)
    try:
        async with aiohttp.ClientSession(
            headers=_headers(referer=referer), timeout=timeout, connector=connector
        ) as sess:
            async with sess.get(url, allow_redirects=True, ssl=False) as resp:
                if "text/html" not in resp.headers.get("Content-Type", ""):
                    return None
                html = await resp.text(errors="replace")
                logger.info("[FETCH] S2 browser-headers success → %s (HTTP %s)", url, resp.status)
                return html, resp.status
    except asyncio.TimeoutError:
        logger.warning("[FETCH] S2 timeout (%ss) → %s", _T_S2, url)
        return None
    except (aiohttp.ClientConnectorSSLError, aiohttp.ClientConnectorError) as exc:
        logger.warning("[FETCH] S2 connection error → %s — %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("[FETCH] S2 failed → %s — %s", url, exc)
        return None
    finally:
        if not connector.closed:
            await connector.close()


# ── Strategy 3: HTTP fallback (ssl=False, longer timeout) ────────────────────

async def _s3_http_fallback(url: str) -> tuple[str, int] | None:
    """
    S3 — tries plain HTTP when HTTPS is broken/misconfigured.
    Many low-budget sites have expired TLS certs but working HTTP.
    """
    target    = _http_url(url)
    connector = _new_connector()
    timeout   = aiohttp.ClientTimeout(total=_T_S3)
    try:
        async with aiohttp.ClientSession(
            headers=_headers(), timeout=timeout, connector=connector
        ) as sess:
            async with sess.get(target, allow_redirects=True, ssl=False) as resp:
                if "text/html" not in resp.headers.get("Content-Type", ""):
                    return None
                html = await resp.text(errors="replace")
                logger.info("[FETCH] S3 http-fallback success → %s (HTTP %s)", target, resp.status)
                return html, resp.status
    except asyncio.TimeoutError:
        logger.warning("[FETCH] S3 timeout (%ss) → %s", _T_S3, target)
        return None
    except (aiohttp.ClientConnectorSSLError, aiohttp.ClientConnectorError) as exc:
        logger.warning("[FETCH] S3 connection error → %s — %s", target, exc)
        return None
    except Exception as exc:
        logger.warning("[FETCH] S3 failed → %s — %s", target, exc)
        return None
    finally:
        if not connector.closed:
            await connector.close()


# ── Strategy 4: Playwright headless (last resort) ────────────────────────────

async def _playwright_available() -> bool:
    """Check if playwright is installed without importing at module level."""
    try:
        import importlib
        spec = importlib.util.find_spec("playwright")
        return spec is not None
    except Exception:
        return False


async def _s4_playwright(url: str) -> tuple[str, int] | None:
    """
    S4 — real Chromium browser via Playwright.
    Bypasses JS-rendered pages, Cloudflare challenges, and aggressive bot filters.
    Runs headless, fully async, does NOT block the event loop.

    Install: pip install playwright && playwright install chromium
    """
    if not await _playwright_available():
        logger.debug("[FETCH] S4 skipped — playwright not installed (pip install playwright && playwright install chromium)")
        return None

    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent=random.choice(_USER_AGENTS),
                java_script_enabled=True,
                ignore_https_errors=True,   # handles expired/self-signed certs
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                },
            )
            page = await context.new_page()

            try:
                response = await page.goto(
                    url,
                    timeout=_T_S4 * 1000,          # Playwright uses milliseconds
                    wait_until="domcontentloaded",  # faster than "networkidle"
                )
                status = response.status if response else 200
                html   = await page.content()
                logger.info("[FETCH] S4 playwright success → %s (HTTP %s)", url, status)
                return html, status

            except PWTimeout:
                logger.warning("[FETCH] S4 playwright timeout (%ss) → %s", _T_S4, url)
                return None
            except Exception as exc:
                logger.warning("[FETCH] S4 playwright error → %s — %s", url, exc)
                return None
            finally:
                await context.close()
                await browser.close()

    except ImportError:
        logger.debug("[FETCH] S4 playwright import failed")
        return None
    except Exception as exc:
        logger.warning("[FETCH] S4 unexpected playwright error → %s — %s", url, exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

async def robust_fetch(
    url: str,
    sem: asyncio.Semaphore | None = None,
) -> tuple[str | None, int, str]:
    """
    Try 4 strategies in order and return the first that succeeds.

    Args:
        url:  Absolute URL to fetch.
        sem:  Optional semaphore — if supplied, the entire cascade runs
              inside one semaphore slot (same behaviour as crawler._fetch).

    Returns:
        (html, status_code, strategy_label)   — on success
        (None, 0, "all_failed")               — when every strategy fails

    Caller must handle None html:
        html, status, strategy = await robust_fetch(url, sem)
        if html is None:
            logger.error("[FETCH] All strategies failed for %s — skipping", url)
            return _minimal_record(url, "Error", "all strategies failed")
    """

    async def _run() -> tuple[str | None, int, str]:
        # S1 — fast baseline
        result = await _s1_aiohttp_fast(url)
        if result:
            return result[0], result[1], "s1_aiohttp_fast"

        logger.info("[FETCH] S1 failed → trying S2 (browser headers) for %s", url)

        # S2 — full browser headers
        result = await _s2_browser_headers(url)
        if result:
            return result[0], result[1], "s2_browser_headers"

        logger.info("[FETCH] S2 failed → trying S3 (HTTP fallback) for %s", url)

        # S3 — HTTP fallback
        result = await _s3_http_fallback(url)
        if result:
            return result[0], result[1], "s3_http_fallback"

        logger.info("[FETCH] S3 failed → trying S4 (Playwright) for %s", url)

        # S4 — Playwright last resort
        result = await _s4_playwright(url)
        if result:
            return result[0], result[1], "s4_playwright"

        logger.error("[FETCH] ALL 4 strategies failed for %s", url)
        return None, 0, "all_failed"

    # Honour the caller's semaphore if provided
    if sem is not None:
        async with sem:
            return await _run()
    else:
        return await _run()
