"""
crawler.py — Fixed async SEO crawler.

ROOT CAUSES FIXED (confirmed from Excel analysis):
===================================================

BUG 1 — VERSION MISMATCH (CRITICAL)
  The project folder had the OLD crawler.py with .run() → asyncio.run()
  but main.py (v5) called crawler.crawl_async() → AttributeError.
  Fix: This file has crawl_async() and NO .run() method.

BUG 2 — SSL CONNECTION FAILURE BLOCKS ENTIRE CRAWL
  mockers.in:443 refused the SSL connection → aiohttp.ClientError →
  _minimal_record returned (no HTML parsed) → _internal_links never called →
  queue stayed empty → crawl ended at 1 page.
  Fix: Try http:// as fallback when https:// fails. Try both in probe.

BUG 3 — ERROR RECORDS GET SENT TO GEMINI
  When page load fails, _minimal_record stores the exception message as
  "title". Then keyword extractor tokenises the error string → keywords
  like ['connect','ssl','failed'] → these get sent to Gemini as page
  keywords → irrelevant AI output.
  Fix: Skip error/timeout records in issue detection and Gemini selection.

BUG 4 — EXACT DOMAIN MATCH DROPS LINKS
  self.domain was set from the original URL in __init__ before any redirect.
  If site redirects http→https or example.com→www.example.com, ALL links
  fail the netloc == domain check.
  Fix: Resolve real domain via probe request before the BFS loop.

BUG 5 — NO www/non-www NORMALISATION
  Pages mixing https://example.com and https://www.example.com links
  would drop half their link graph.
  Fix: Compare bare domains (strip www.) on both sides.
"""

import asyncio
import logging
import re
import socket
import ssl as _ssl
import time
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from issues import detect_issues
from keyword_extractor import extract_keywords_corpus
from keyword_pipeline import run_keyword_pipeline

logger = logging.getLogger(__name__)

# ── Image format constants ────────────────────────────────────────────────────
_MODERN_IMG_EXTS  = frozenset({".webp", ".avif", ".jxl"})
_LEGACY_IMG_RE    = re.compile(r'\.(jpe?g|png|gif|bmp|tiff?)(\?|#|$)', re.I)


def _tls_version(resp) -> str:
    """
    Safely extract TLS protocol version from an aiohttp response.

    Returns a string such as "TLSv1.3", "TLSv1.2", or "" if unavailable
    (non-TLS connections, cffi paths, or aiohttp versions that don't expose it).
    """
    try:
        transport = resp.connection.transport
        ssl_obj   = transport.get_extra_info("ssl_object")
        if ssl_obj is not None:
            return ssl_obj.version() or ""
    except Exception:
        pass
    return ""

# curl_cffi — Chrome TLS fingerprint impersonation.
# Used as 5th fallback when aiohttp is blocked by Cloudflare/bot-protection.
# Must be imported AFTER logger is defined.
try:
    from curl_cffi.requests import AsyncSession as _CffiSession
    _CFFI = True
    logger.info("curl_cffi available in crawler — Cloudflare bypass enabled")
except Exception as _cffi_err:
    _CffiSession = None  # type: ignore
    _CFFI = False
    logger.debug("curl_cffi unavailable in crawler (%s) — no Cloudflare bypass", _cffi_err)

_CFFI_IMPERSONATE   = "chrome124"  # Chrome TLS fingerprint (JA3/JA4 match)
_CFFI_FETCH_TIMEOUT = 25           # seconds — cffi per-request timeout

# Bot-challenge markers — presence in HTML means we got a JS-challenge page,
# not real content. Detected even on HTTP 200 responses.
_BOT_CHALLENGE_MARKERS = (
    "cf-chl-bypass",         # Cloudflare challenge bypass token
    "__cf_chl_f_tk",         # Cloudflare fingerprint token
    "jschl_answer",          # Cloudflare legacy IUAM challenge
    "Ray ID",                # Cloudflare Ray ID (typically in 403 HTML too)
    "checking your browser", # Cloudflare "checking your browser" text
    "please wait",           # Generic bot-protection wait page
    "enable javascript",     # JS-required pages (no real content)
    "ddos-guard",            # DDoS-Guard protection
    "datadome",              # DataDome bot-protection
    "perimeterx",            # PerimeterX bot-protection
    "px-captcha",            # PerimeterX captcha
    "akamai-bot",            # Akamai Bot Manager
    "_px3",                  # PerimeterX pixel tag
)


# BUG-N33: removed _ssl_ctx_permissive() dead-code stub.
# All fetch paths use ssl=False (boolean) — no SSLContext is needed.

# ── Shared state ──────────────────────────────────────────────────────────────
crawl_results: list[dict] = []

crawl_status: dict = {
    "running":       False,
    "done":          False,
    "pages_crawled": 0,
    "pages_queued":  0,
    "errors":        0,
    "timeouts":      0,
    "ssl_fallbacks": 0,
    "current_url":   "",
    "error":         None,
    "started_at":    None,
    "elapsed_s":     0,
}

# ── Headers — real Chrome UA to avoid bot blocking ───────────────────────────
# ── Browser fingerprint headers ──────────────────────────────────────────────
# Full Chrome 124 header set — matches what a real browser sends.
# Bot-protection systems (Cloudflare, Akamai, etc.) block requests that are
# missing any of these headers or have them in the wrong order.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
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

# ── Rotating User-Agent pool ──────────────────────────────────────────────────
# Rotated per-request to avoid UA-based blocking.
# These are real, current browser strings for Chrome, Edge, Firefox on Windows/Mac.
import random as _random

_USER_AGENTS = [
    # Chrome 124 Windows (current)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.91 Safari/537.36",
    # Chrome 123 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome 124 Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 124 Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Edge 124 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Firefox 125 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox 125 Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari 17 Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    # Chrome 124 Android (mobile UA can bypass some paywalls)
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

# Per-UA consistent sec-ch-ua headers — must match the User-Agent string.
# Bot detectors cross-check these; mismatches are a strong bot signal.
_UA_HINTS: dict[str, dict] = {
    "Chrome/124": {
        "sec-ch-ua":          '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
    "Chrome/123": {
        "sec-ch-ua":          '"Chromium";v="123", "Google Chrome";v="123", "Not-A.Brand";v="24"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
    "Edg/124": {
        "sec-ch-ua":          '"Chromium";v="124", "Microsoft Edge";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
    "Firefox": {
        "sec-ch-ua":          "",   # Firefox does not send sec-ch-ua
        "sec-ch-ua-mobile":   "",
        "sec-ch-ua-platform": "",
    },
    "Safari": {
        "sec-ch-ua":          "",
        "sec-ch-ua-mobile":   "",
        "sec-ch-ua-platform": "",
    },
}

def _random_headers() -> dict:
    """
    Return a full browser-matching header set with a random User-Agent.

    Crucially: sec-ch-ua / sec-ch-ua-platform are set to match the chosen UA.
    Bot detectors cross-check these — a mismatched set is a strong bot signal.
    Firefox/Safari don't send sec-ch-ua at all, so those headers are omitted.
    """
    ua  = _random.choice(_USER_AGENTS)
    h   = dict(HEADERS)
    h["User-Agent"] = ua

    # Pick matching sec-ch-ua hints
    hint_key = next((k for k in _UA_HINTS if k in ua), None)
    hints    = _UA_HINTS.get(hint_key, {})
    if hints.get("sec-ch-ua"):
        h["sec-ch-ua"]          = hints["sec-ch-ua"]
        h["sec-ch-ua-mobile"]   = hints["sec-ch-ua-mobile"]
        h["sec-ch-ua-platform"] = hints["sec-ch-ua-platform"]
    else:
        # Firefox/Safari: remove Chromium-specific hint headers entirely
        h.pop("sec-ch-ua", None)
        h.pop("sec-ch-ua-mobile", None)
        h.pop("sec-ch-ua-platform", None)

    return h


def _is_bot_challenge(html: str, status: int) -> bool:
    """
    Return True if the HTML looks like a bot-protection challenge page.
    Checks both hard-blocked status codes and JS-challenge 200 responses.
    """
    if status in (403, 429, 503, 403):
        return True
    if not html:
        return False
    sample = html[:4096].lower()
    return any(marker in sample for marker in _BOT_CHALLENGE_MARKERS)

# ── Config ────────────────────────────────────────────────────────────────────
TIMEOUTS        = [10, 20, 30]   # outer session timeout reference
PROBE_TIMEOUT   = 12             # probe total timeout per attempt
MAX_CONCURRENCY = 3              # concurrent waves — keeps rate-limit risk low
MAX_RETRIES     = 2              # per-URL retry count (3 total attempts)

# Per-attempt timeouts: (connect_s, read_s, total_s)
# connect_s caps DNS + TLS; read_s caps stalled server; total_s is the hard cap.
# Keeping all values under 20s avoids the Windows SChannel semaphore (25s limit).
ATTEMPT_TIMEOUTS = [
    (4,  6,  10, "attempt-1"),   # fast: connect 4s, read 6s, total 10s
    (5, 10,  15, "attempt-2"),   # medium: connect 5s, read 10s, total 15s
    (6, 12,  18, "attempt-3"),   # slow: connect 6s, read 12s, total 18s
    (6, 14,  20, "attempt-4"),   # last-resort: connect 6s, read 14s, total 20s
]

# DNS cache TTL — avoids redundant DNS lookups across attempts for same host
DNS_CACHE_TTL = 300  # seconds

# Jitter delay: mimics human browsing, avoids rate-limit triggers
import random as _r
MIN_DELAY = 0.2   # seconds minimum
MAX_DELAY = 0.8   # seconds maximum

def _jitter() -> float:
    """Return a random human-like delay (0.2–0.8s)."""
    return _r.uniform(MIN_DELAY, MAX_DELAY)


class SEOCrawler:
    """
    Fully async BFS crawler. Call crawl_async() inside an async context.

    Usage (FastAPI route):
        asyncio.create_task(SEOCrawler(url, max_pages).crawl_async())
        return {"message": "started"}   # returns immediately

    NEVER call .run() or asyncio.run() inside FastAPI.
    """

    def __init__(self, root_url: str, max_pages: int = 50):
        self.root_url     = root_url.rstrip("/")
        self.max_pages    = max_pages
        self.domain       = urlparse(root_url).netloc   # refined after probe
        self._bare_domain = self.domain.lstrip("www.")
        self.visited:  set[str]  = set()
        # BFS depth tracking: queue holds (url, depth) tuples so each page
        # knows exactly how many hops it is from the root. _queued is an O(1)
        # membership set — replaces the O(n) "link not in self.queue" list scan.
        self.queue:    list[tuple[str, int]] = [(self.root_url, 0)]
        self._queued:  set[str]              = {self.root_url}
        self._fetched_ok: int    = 0   # CR-001: count only non-error pages
        self._use_cffi:   bool   = False  # True after cffi bypass succeeds once

    # ── SSL context (no cert verification, proper TLS negotiation) ────────────
    @staticmethod
    def _ssl_ctx():
        """DEAD CODE — all paths use ssl=False. Returns False if called."""
        return False

    # ── Main coroutine ────────────────────────────────────────────────────────

    async def crawl_async(self) -> None:
        """
        BFS crawl designed to run as asyncio.create_task().
        Updates crawl_status live. Never stops on slow/broken pages.
        """
        crawl_status["running"]    = True
        crawl_status["done"]       = False
        crawl_status["started_at"] = time.time()

        # Outer connector — used ONLY by _resolve_domain probe.
        # ssl=False here is a safety net; FIX 1 passes ssl= per-request
        # in session.get() which takes precedence. Both together guarantee
        # that no system SSL context is ever used for the probe.
        connector = aiohttp.TCPConnector(
            ssl=False,              # no SSLContext — Windows safe
            family=socket.AF_INET,  # force IPv4 — avoids IPv6 DNS failures
            limit=MAX_CONCURRENCY,
            limit_per_host=4,
            ttl_dns_cache=DNS_CACHE_TTL,   # cache DNS — faster retries
            enable_cleanup_closed=True,     # clean up closed connections
        )
        session_timeout = aiohttp.ClientTimeout(
            total=ATTEMPT_TIMEOUTS[-1][2] + 10,
            connect=None,  # outer session: no connect cap (probes set own)
        )

        async with aiohttp.ClientSession(
            headers=HEADERS,
            timeout=session_timeout,
            connector=connector,
        ) as session:

            # Step 1: Resolve real domain (follow redirects, try http fallback)
            await self._resolve_domain(session)
            sem = asyncio.Semaphore(MAX_CONCURRENCY)

            # Step 2: BFS loop
            # CR-001: use _fetched_ok (non-error pages) for max_pages cap.
            # Error pages still go into self.visited to avoid re-fetching, but
            # they don't consume quota — all-error sites can't loop forever
            # because the safety cap (visited < max_pages*3) prevents that.
            while (self.queue
                   and self._fetched_ok < self.max_pages
                   and len(self.visited) < self.max_pages * 3):
                remaining = self.max_pages - self._fetched_ok

                # Dequeue a wave: each item is (url, depth)
                wave: list[tuple[str, int]] = []
                while self.queue and len(wave) < min(MAX_CONCURRENCY, remaining):
                    url, depth = self.queue.pop(0)
                    self._queued.discard(url)
                    if url not in self.visited:
                        wave.append((url, depth))
                        self.visited.add(url)

                if not wave:
                    break

                crawl_status.update({
                    "current_url":  wave[0][0],
                    "pages_queued": len(self.queue),
                    "elapsed_s":    round(time.time() - crawl_status["started_at"], 1),
                })

                # Brief jitter between BFS waves — mimics human browsing pace
                # and avoids triggering rate-limit rules on protected sites
                await asyncio.sleep(_jitter())

                tasks   = [self._fetch(session, sem, url) for url, _depth in wave]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for (url, depth), result in zip(wave, results):
                    if isinstance(result, Exception):
                        # Store error record but DO NOT add to queue
                        rec = _minimal_record(url, "Error", str(result))
                        rec["_is_error"]   = True
                        rec["crawl_depth"] = depth
                        crawl_results.append(rec)
                        crawl_status["errors"] += 1
                        continue
                    if result is None:
                        continue

                    is_error = result.pop("_is_error", False)
                    links    = result.pop("_internal_links", [])
                    result["internal_links_count"] = len(links)
                    result["crawl_depth"]          = depth   # ← BFS depth stored here
                    result["_is_error"] = is_error   # restore flag so issues.py + Gemini can read it
                    crawl_results.append(result)

                    # Only enqueue links from successfully loaded pages
                    if not is_error:
                        self._fetched_ok += 1          # CR-001: count good pages only
                        child_depth = depth + 1
                        for link in links:
                            if link not in self.visited and link not in self._queued:
                                self.queue.append((link, child_depth))
                                self._queued.add(link)

                crawl_status.update({
                    "pages_crawled": len(self.visited),
                    "pages_queued":  len(self.queue),
                    "elapsed_s":     round(time.time() - crawl_status["started_at"], 1),
                })

        # Step 3: Post-crawl — skip error records in issue detection
        real_pages = [p for p in crawl_results if p.get("status_code") == 200]
        detect_issues(crawl_results)       # detects on all (broken page flag)
        extract_keywords_corpus(real_pages, top_n=10)  # keywords only from real content
        # Apply keywords back
        kw_map = {p["url"]: p.get("keywords", []) for p in real_pages}
        for page in crawl_results:
            if page["url"] in kw_map:
                page["keywords"] = kw_map[page["url"]]

        # Step 4: Keyword pipeline — n-grams + Google Suggest + competition
        # Runs async; fails silently per page; never blocks crawl results
        await run_keyword_pipeline(real_pages)

        crawl_status.update({
            "running":   False,
            "done":      True,
            "elapsed_s": round(time.time() - crawl_status["started_at"], 1),
        })

    # ── Domain resolution — follow redirects, try http fallback ──────────────

    async def _resolve_domain(self, _unused_session) -> None:
        """
        Discover real domain by following redirects on root URL.

        ROOT CAUSE FIX (ssl:default error):
        The previous version reused the outer shared ClientSession for probing.
        On Windows, even with ssl=False on both the connector AND .get(), the
        shared session's connection pool retains OS TLS state between connections
        — causing "ssl:default" errors on protected sites like mockers.in.

        FIX: Each probe attempt now gets its OWN fresh TCPConnector + ClientSession,
        identical to _fetch(). This guarantees ssl=False reaches the TLS layer
        every time with zero shared state.
        """
        urls_to_try = [self.root_url]
        if self.root_url.startswith("https://"):
            urls_to_try.append(self.root_url.replace("https://", "http://", 1))

        # Two timeout attempts per URL: fast first, then slower fallback
        probe_attempts = []
        for u in urls_to_try:
            probe_attempts.append((u,  8, f"probe-fast [{u[:40]}]"))
            probe_attempts.append((u, 15, f"probe-slow [{u[:40]}]"))

        for try_url, timeout_s, label in probe_attempts:
            # Fresh connector + session per attempt — eliminates shared TLS state
            connector = aiohttp.TCPConnector(
                ssl=False,
                family=socket.AF_INET,   # force IPv4 — avoids IPv6 NXDOMAIN
                limit=2,
                limit_per_host=2,
                force_close=True,
                ttl_dns_cache=60,
            )
            t = aiohttp.ClientTimeout(
                total=timeout_s,
                connect=min(timeout_s - 2, 5),  # cap connect: leaves time to read
                sock_read=timeout_s - 1,        # cap stalled-server hang
            )
            try:
                req_headers = {**_random_headers(), "Referer": try_url}
                async with aiohttp.ClientSession(
                    headers=req_headers, timeout=t, connector=connector
                ) as probe_sess:
                    async with probe_sess.get(
                        try_url, allow_redirects=True, ssl=False,
                        headers=req_headers,   # consistent UA — no second random call
                    ) as resp:
                        final             = str(resp.url)
                        parsed            = urlparse(final)
                        self.domain       = parsed.netloc
                        self._bare_domain = self.domain.lstrip("www.")
                        self.root_url     = final.rstrip("/")
                        self.queue        = [(self.root_url, 0)]
                        logger.info("Domain resolved [%s]: %s → %s",
                                    label, try_url, self.domain)

                        # Cache homepage — BFS won't re-fetch it
                        ctype = resp.headers.get("Content-Type", "")
                        if "text/html" in ctype:
                            try:
                                html = await resp.text(errors="replace")
                                result = _parse(
                                    self.root_url, resp.status, html,
                                    self.domain, self._bare_domain,
                                    response_headers=dict(resp.headers),
                                )
                                links = result.pop("_internal_links", [])
                                result["internal_links_count"] = len(links)
                                crawl_results.append(result)
                                self.visited.add(self.root_url)
                                self.queue = []
                                self._queued.clear()
                                for link in links:
                                    if link not in self.visited and link not in self._queued:
                                        self.queue.append((link, 1))
                                        self._queued.add(link)
                                crawl_status.update({
                                    "pages_crawled": 1,
                                    "pages_queued":  len(self.queue),
                                })
                            except Exception as parse_exc:
                                logger.warning("Probe parse failed [%s]: %s",
                                               label, parse_exc)
                        return   # probe succeeded — stop trying

            except asyncio.TimeoutError:
                logger.warning("Probe timeout [%s] after %ss", label, timeout_s)
            except aiohttp.ClientConnectorSSLError as exc:
                logger.warning("Probe SSL error [%s] — %s", label, exc)
            except aiohttp.ClientConnectorError as exc:
                logger.warning("Probe connection error [%s] — %s", label, exc)
            except Exception as exc:
                logger.warning("Probe failed [%s] — %s", label, exc)
            finally:
                if not connector.closed:
                    await connector.close()

            await asyncio.sleep(_jitter())  # brief pause before next attempt

        # ── cffi probe fallback: try Chrome TLS impersonation when aiohttp is blocked ──
        # Cloudflare and similar bot-walls check the TLS fingerprint (JA3/JA4).
        # aiohttp's TLS stack does not match Chrome — cffi does.
        # If cffi resolves the homepage, set _use_cffi=True so all BFS fetches
        # skip aiohttp entirely (faster, avoids 4 wasted blocked attempts per URL).
        if _CFFI:
            logger.info("All aiohttp probes failed — trying cffi Chrome TLS probe for %s",
                        self.root_url)
            try:
                async with _CffiSession() as _cffi_sess:
                    cffi_resp = await _cffi_sess.get(
                        self.root_url,
                        impersonate=_CFFI_IMPERSONATE,
                        timeout=_CFFI_FETCH_TIMEOUT,
                        allow_redirects=True,
                    )
                    if cffi_resp.status_code == 200:
                        final             = str(cffi_resp.url)
                        parsed            = urlparse(final)
                        self.domain       = parsed.netloc
                        self._bare_domain = self.domain.lstrip("www.")
                        self.root_url     = final.rstrip("/")
                        self._use_cffi    = True   # all BFS fetches go cffi-first
                        logger.info("cffi probe SUCCESS — domain: %s  (cffi-mode ON)",
                                    self.domain)
                        # Cache homepage exactly like the aiohttp path
                        ctype = cffi_resp.headers.get("content-type", "")
                        if "text/html" in ctype:
                            result = _parse(
                                self.root_url, 200, cffi_resp.text,
                                self.domain, self._bare_domain,
                                response_headers=dict(cffi_resp.headers),
                            )
                            links = result.pop("_internal_links", [])
                            result["internal_links_count"] = len(links)
                            crawl_results.append(result)
                            self.visited.add(self.root_url)
                            self.queue = []
                            self._queued.clear()
                            for link in links:
                                if link not in self.visited and link not in self._queued:
                                    self.queue.append((link, 1))
                                    self._queued.add(link)
                            crawl_status.update({
                                "pages_crawled": 1,
                                "pages_queued":  len(self.queue),
                            })
                        return
                    logger.warning("cffi probe got HTTP %d for %s",
                                   cffi_resp.status_code, self.root_url)
            except Exception as _cffi_probe_exc:
                logger.warning("cffi probe failed for %s: %s",
                               self.root_url, _cffi_probe_exc)

        # All probes failed — _fetch cascade handles it from the BFS queue
        logger.warning(
            "All probes failed for %s — _fetch will retry with 4-attempt cascade",
            self.root_url,
        )
        # All probes failed — ensure root is queued as (url, depth) tuple
        already = any(u == self.root_url for u, _ in self.queue) if self.queue else False
        if not already and self.root_url not in self.visited:
            self.queue = [(self.root_url, 0)]
            self._queued.add(self.root_url)

    # ── Per-URL fetch with adaptive timeout ───────────────────────────────────

    async def _fetch(
        self,
        session: aiohttp.ClientSession,
        sem:     asyncio.Semaphore,
        url:     str,
    ) -> dict | None:
        """
        Fetch one URL with a 4-attempt cascade that NEVER crashes the crawl.

        Attempt 1 — HTTPS, ssl=False, connect 4s / read 6s  / total 10s
        Attempt 2 — HTTPS, ssl=False, connect 5s / read 10s / total 15s
        Attempt 3 — HTTP,  ssl=False, connect 6s / read 12s / total 18s
        Attempt 4 — HTTP,  ssl=False, connect 6s / read 14s / total 20s

        Each attempt:
          - Fresh TCPConnector + ClientSession (no shared TLS state)
          - ssl=False throughout (no SSLContext — Windows SChannel safe)
          - socket.AF_INET (force IPv4 — no IPv6 DNS failures)
          - Separate connect/read/total timeouts (catches stalled servers)
          - Consistent single User-Agent per attempt (no UA mismatch)
          - Referer header (missing Referer is a strong bot signal)
          - Jitter on transient errors before next attempt

        Exception handling covers all failure modes:
          asyncio.TimeoutError, ServerDisconnectedError, ServerTimeoutError,
          ClientConnectorSSLError, ClientConnectorError, ClientOSError,
          ClientPayloadError, ClientResponseError (429 + backoff),
          TooManyRedirects (stops cascade), ClientError, Exception

        On total failure: returns a minimal error record (_is_error=True).
        BFS always continues — one bad page never stops the crawl.
        """
        # Unused `session` param kept for API compat — each attempt
        # creates its own session (eliminates shared TLS state issues)
        _ = session  # noqa
        async with sem:
            # ── cffi-first mode: domain was already confirmed blocked by Cloudflare ──
            # _use_cffi is set True by _resolve_domain when cffi probe succeeds.
            # Skip all aiohttp attempts — they will only get 403/429 again.
            if self._use_cffi and _CFFI:
                try:
                    async with _CffiSession() as _cffi_sess:
                        cffi_resp = await _cffi_sess.get(
                            url,
                            impersonate=_CFFI_IMPERSONATE,
                            timeout=_CFFI_FETCH_TIMEOUT,
                            allow_redirects=True,
                        )
                        if cffi_resp.status_code == 200:
                            logger.info("cffi-mode OK %s", url)
                            return _parse(url, 200, cffi_resp.text,
                                          self.domain, self._bare_domain,
                                          response_headers=dict(cffi_resp.headers))
                        logger.warning("cffi-mode got HTTP %d for %s",
                                       cffi_resp.status_code, url)
                        crawl_status["errors"] += 1
                        rec = _minimal_record(url, cffi_resp.status_code,
                                              f"HTTP {cffi_resp.status_code}")
                        rec["_is_error"] = True
                        return rec
                except Exception as _cffi_exc:
                    logger.warning("cffi-mode failed for %s: %s", url, _cffi_exc)
                    crawl_status["errors"] += 1
                    rec = _minimal_record(url, "Error", f"cffi error: {_cffi_exc}")
                    rec["_is_error"] = True
                    return rec
            # Build the 4-attempt cascade
            # Each entry: (url_to_try, ssl_setting, timeout_s, label)
            http_url = url.replace("https://", "http://", 1) if url.startswith("https://") else url

            # 4-attempt cascade. Each attempt gets:
            #   - its own TCPConnector (fresh TLS state, no shared-session issues)
            #   - ssl=False (no SSLContext object — Windows SChannel safe)
            #   - family=AF_INET (force IPv4 — avoids IPv6 DNS failures)
            #   - separate connect + read timeouts (catch stalled servers)
            # HTTPS tried twice, then HTTP twice — guaranteed fallback.
            attempts = [
                (url,      ATTEMPT_TIMEOUTS[0], "https-fast"),
                (url,      ATTEMPT_TIMEOUTS[1], "https-retry"),
                (http_url, ATTEMPT_TIMEOUTS[2], "http-fallback"),
                (http_url, ATTEMPT_TIMEOUTS[3], "http-long"),
            ]

            last_error   = ""
            got_blocked  = False   # True when aiohttp gets 403/429/503 (bot-wall, not network error)
            for try_url, (connect_s, read_s, total_s, _), label in attempts:
                t = aiohttp.ClientTimeout(
                    total=total_s,
                    connect=connect_s,  # caps DNS resolution + TLS handshake
                    sock_read=read_s,   # caps server sending response body
                )
                connector = aiohttp.TCPConnector(
                    ssl=False,                  # no SSLContext — Windows safe
                    family=socket.AF_INET,      # force IPv4 — no IPv6 NXDOMAIN
                    limit=2,
                    limit_per_host=2,
                    force_close=True,
                    ttl_dns_cache=60,
                )
                try:
                    # Build headers once per attempt — ensures consistent UA
                    # across session init and actual request (prevents UA mismatch)
                    referer    = try_url.rsplit("/", 1)[0] + "/" if "/" in try_url[8:] else try_url
                    req_hdrs   = {**_random_headers(), "Referer": referer}
                    async with aiohttp.ClientSession(
                        headers=req_hdrs,
                        timeout=t,
                        connector=connector,
                    ) as _sess:
                        async with _sess.get(
                            try_url, allow_redirects=True, ssl=False,
                            headers=req_hdrs,   # same headers — no second random call
                        ) as resp:
                            status = resp.status
                            ctype  = resp.headers.get("Content-Type", "")
                            if label != "https-fast":   # only count non-primary attempts
                                crawl_status["ssl_fallbacks"] = crawl_status.get("ssl_fallbacks", 0) + 1
                            logger.info("OK [%s] %s → HTTP %s", label, try_url, status)

                            if "text/html" not in ctype:
                                rec = _minimal_record(url, status, "Non-HTML")
                                rec["_is_error"] = True
                                return rec

                            html = await resp.text(errors="replace")

                            # Detect bot-wall: hard block (403/429/503) OR
                            # JS-challenge disguised as HTTP 200 (Cloudflare, DataDome…)
                            if _is_bot_challenge(html, status):
                                got_blocked = True
                                last_error  = f"HTTP {status} (bot-protection / JS-challenge)"
                                logger.warning("BOT-BLOCKED [%s] %s → HTTP %s (challenge detected)",
                                               label, try_url, status)
                                # Continue to next aiohttp attempt; cffi tried after all fail.
                                continue

                            return _parse(url, status, html,
                                          self.domain, self._bare_domain,
                                          response_headers=dict(resp.headers),
                                          redirect_hops=len(resp.history),
                                          tls_version=_tls_version(resp))

                except asyncio.TimeoutError:
                    # Separate TimeoutError catch here for clarity
                    last_error = f"Timeout (connect={connect_s}s read={read_s}s total={total_s}s)"
                    logger.warning("TIMEOUT [%s] %s — %s", label, try_url, last_error)
                    await asyncio.sleep(_jitter())
                    continue

                except aiohttp.ServerDisconnectedError as exc:
                    # Server closed connection mid-stream — retry on next attempt
                    last_error = f"Server disconnected: {exc}"
                    logger.warning("DISCONNECT [%s] %s — %s", label, try_url, exc)
                    await asyncio.sleep(_jitter())
                    continue

                except aiohttp.ServerTimeoutError as exc:
                    # Server accepted connection but took too long to respond
                    last_error = f"Server timeout: {exc}"
                    logger.warning("SERVER TIMEOUT [%s] %s — %s", label, try_url, exc)
                    await asyncio.sleep(_jitter())
                    continue

                except aiohttp.ClientConnectorSSLError as exc:
                    last_error = f"SSL error: {exc}"
                    logger.warning("SSL ERR [%s] %s — %s", label, try_url, exc)
                    # SSL error on HTTPS → next attempt will try HTTP fallback

                except aiohttp.ClientConnectorError as exc:
                    last_error = f"Connection error: {exc}"
                    logger.warning("CONN ERR [%s] %s — %s", label, try_url, exc)

                except aiohttp.TooManyRedirects as exc:
                    last_error = f"Redirect loop: {exc}"
                    logger.warning("REDIRECT LOOP [%s] %s — %s", label, try_url, exc)
                    break  # redirect loop is permanent — skip remaining attempts

                except aiohttp.ClientOSError as exc:
                    # OS-level socket error (ECONNRESET, ECONNREFUSED, etc.)
                    last_error = f"OS error: {exc}"
                    logger.warning("OS ERR [%s] %s — %s", label, try_url, exc)
                    await asyncio.sleep(_jitter())

                except aiohttp.ClientPayloadError as exc:
                    # Response body corrupted / truncated mid-stream
                    last_error = f"Payload error: {exc}"
                    logger.warning("PAYLOAD ERR [%s] %s — %s", label, try_url, exc)
                    await asyncio.sleep(_jitter())

                except aiohttp.ClientResponseError as exc:
                    last_error = f"HTTP {exc.status}: {exc.message}"
                    if exc.status == 429:
                        # Rate limited — back off longer before retrying
                        backoff = 5.0 + _jitter() * 3
                        logger.warning("RATE LIMITED [%s] %s — backing off %.1fs",
                                       label, try_url, backoff)
                        await asyncio.sleep(backoff)
                    else:
                        logger.warning("HTTP ERR [%s] %s — %s", label, try_url, exc)

                except aiohttp.ClientError as exc:
                    last_error = str(exc)
                    logger.warning("CLIENT ERR [%s] %s — %s", label, try_url, exc)

                except Exception as exc:
                    last_error = str(exc)
                    logger.warning("ERR [%s] %s — %s", label, try_url, exc)

                finally:
                    # Ensure connector is always closed even if session.__aexit__ skipped
                    if not connector.closed:
                        await connector.close()

            # ── Attempt 5+: curl_cffi Chrome TLS impersonation ──────────────
            # Tried when ALL aiohttp attempts hit a bot-protection wall (403/429/503
            # OR JS-challenge 200). cffi sends the exact TLS JA3/JA4 fingerprint of
            # Chrome, bypassing Cloudflare, DataDome, PerimeterX, Akamai.
            # Try two impersonation profiles: chrome124, then chrome110 (older = less
            # likely to trigger novelty-based bot filters on some WAFs).
            if _CFFI and got_blocked:
                for _cffi_profile in ("chrome124", "chrome110"):
                    try:
                        logger.info("cffi fallback [%s] for %s", _cffi_profile, url)
                        async with _CffiSession() as _cffi_sess:
                            cffi_resp = await _cffi_sess.get(
                                url,
                                impersonate=_cffi_profile,
                                timeout=_CFFI_FETCH_TIMEOUT,
                                allow_redirects=True,
                            )
                            html_cffi = cffi_resp.text or ""
                            if cffi_resp.status_code == 200 and not _is_bot_challenge(html_cffi, 200):
                                logger.info("cffi bypass SUCCESS [%s] for %s — enabling cffi-mode",
                                            _cffi_profile, url)
                                crawl_status["ssl_fallbacks"] = crawl_status.get("ssl_fallbacks", 0) + 1
                                self._use_cffi = True   # all future BFS fetches skip aiohttp
                                return _parse(url, 200, html_cffi,
                                              self.domain, self._bare_domain,
                                              response_headers=dict(cffi_resp.headers))
                            logger.warning("cffi [%s] got HTTP %d / still blocked for %s",
                                           _cffi_profile, cffi_resp.status_code, url)
                            last_error = f"cffi HTTP {cffi_resp.status_code}"
                    except Exception as _cffi_exc:
                        logger.warning("cffi [%s] failed for %s: %s", _cffi_profile, url, _cffi_exc)
                        last_error = f"cffi error: {_cffi_exc}"
                        break  # network error — no point retrying with different profile

            # All attempts failed — record error, BFS continues
            crawl_status["errors"] += 1
            logger.error("ALL ATTEMPTS FAILED for %s — last: %s", url, last_error)
            rec = _minimal_record(url, "Error", last_error)
            rec["_is_error"] = True
            return rec


# ── HTML parsing ──────────────────────────────────────────────────────────────

def _parse(url: str, status: int, html: str,
           domain: str, bare_domain: str = "",
           response_headers: dict | None = None,
           redirect_hops: int = 0,
           tls_version: str = "") -> dict:
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title    = _text(soup.find("title"))
    meta_d   = _meta_content(soup, "description")
    meta_k   = _meta_content(soup, "keywords")
    canon    = _canonical(soup, url)
    h1s      = [t.get_text(strip=True) for t in soup.find_all("h1") if t.get_text(strip=True)]
    h2s      = [t.get_text(strip=True) for t in soup.find_all("h2") if t.get_text(strip=True)]
    h3s      = [t.get_text(strip=True) for t in soup.find_all("h3") if t.get_text(strip=True)]
    # Heading sequence in DOM order — needed for skipped-level detection.
    # Extracted before _body_text() so soup is unmodified when we scan headings.
    heading_sequence = [
        {"level": int(t.name[1]), "text": t.get_text(strip=True)[:120]}
        for t in soup.find_all(re.compile(r"^h[1-6]$", re.I))
        if t.get_text(strip=True)
    ][:30]
    og_title = _og(soup, "og:title")
    og_desc  = _og(soup, "og:description")
    og_image = _og(soup, "og:image")
    og_type  = _og(soup, "og:type")
    # Twitter Card uses name= attribute (not property=) — re.I handles case variants
    _twc = soup.find("meta", attrs={"name": re.compile(r"^twitter:card$",        re.I)})
    _twt = soup.find("meta", attrs={"name": re.compile(r"^twitter:title$",       re.I)})
    _twd = soup.find("meta", attrs={"name": re.compile(r"^twitter:description$", re.I)})
    _twi = soup.find("meta", attrs={"name": re.compile(r"^twitter:image$",       re.I)})
    tw_card  = (_twc.get("content") or "").strip() if _twc else ""
    tw_title = (_twt.get("content") or "").strip() if _twt else ""
    tw_desc  = (_twd.get("content") or "").strip() if _twd else ""
    tw_image = (_twi.get("content") or "").strip() if _twi else ""

    # ── JSON-LD schemas + breadcrumbs ─────────────────────────────────────────
    # MUST run before _body_text() which calls tag.decompose() on <script> tags,
    # making soup.find_all("script") return nothing afterwards.
    import json as _json
    schema_types:        list[str]    = []
    schema_objects:      list[dict]   = []
    schema_rating:       float | None = None
    schema_review_count: int   | None = None
    schema_author:       str          = ""
    breadcrumbs_jsonld:  list[dict]   = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = (script.string or "").strip()
            if not raw:
                continue
            obj = _json.loads(raw)
            # Handle both single object and @graph arrays
            objs = obj.get("@graph", [obj]) if isinstance(obj, dict) else [obj]
            for o in objs:
                if not isinstance(o, dict):
                    continue
                # @type — collect type name and property keys for validation
                t = o.get("@type")
                if isinstance(t, str):
                    schema_types.append(t)
                    schema_objects.append({"type": t, "props": list(o.keys())})
                elif isinstance(t, list):
                    schema_types.extend(str(x) for x in t if x)
                    for _st in t:
                        if _st:
                            schema_objects.append({"type": str(_st), "props": list(o.keys())})
                # aggregateRating
                ar = o.get("aggregateRating")
                if isinstance(ar, dict):
                    try:
                        rv = ar.get("ratingValue")
                        if rv is not None and schema_rating is None:
                            schema_rating = float(rv)
                    except (ValueError, TypeError):
                        pass
                    try:
                        rc = ar.get("reviewCount") or ar.get("ratingCount")
                        if rc is not None and schema_review_count is None:
                            schema_review_count = int(rc)
                    except (ValueError, TypeError):
                        pass
                # author
                if not schema_author:
                    auth = o.get("author")
                    if isinstance(auth, dict):
                        schema_author = auth.get("name", "")
                    elif isinstance(auth, list) and auth:
                        a0 = auth[0]
                        if isinstance(a0, dict):
                            schema_author = a0.get("name", "")
                        elif isinstance(a0, str):
                            schema_author = a0
                    elif isinstance(auth, str):
                        schema_author = auth
                # BreadcrumbList — extract ordered trail
                if isinstance(t, str) and t.lower() == "breadcrumblist":
                    for _bi in (o.get("itemListElement") or []):
                        if isinstance(_bi, dict) and _bi.get("name"):
                            breadcrumbs_jsonld.append({
                                "position": int(_bi.get("position", 0)),
                                "name":     str(_bi.get("name", "")),
                                "item":     str(_bi.get("item") or ""),
                            })
                    breadcrumbs_jsonld.sort(key=lambda x: x["position"])
        except Exception:
            pass

    # ── HTML nav breadcrumb ───────────────────────────────────────────────────
    # MUST run before _body_text() which decomposes <nav> elements.
    breadcrumbs_html: list[dict] = []
    _bc_nav = soup.find("nav", attrs={"aria-label": re.compile(r"breadcrumb", re.I)})
    if _bc_nav:
        for _li in _bc_nav.find_all("li"):
            _bc_a    = _li.find("a")
            _bc_text = _bc_a.get_text(strip=True) if _bc_a else _li.get_text(strip=True)
            _bc_href = (_bc_a.get("href") or "") if _bc_a else ""
            if _bc_text:
                breadcrumbs_html.append({"name": _bc_text, "href": _bc_href})

    breadcrumbs         = breadcrumbs_jsonld if breadcrumbs_jsonld else breadcrumbs_html
    breadcrumb_detected = bool(breadcrumbs)
    breadcrumb_source   = "json_ld" if breadcrumbs_jsonld else ("html_nav" if breadcrumbs_html else "")

    # ── RESOURCE COUNT (before _body_text decomposes script/iframe tags) ──────
    # Counts all HTTP resource references — proxy for page weight/request count.
    # <script> and <iframe> are removed by _body_text(); count them here.
    _ext_scripts  = len(soup.find_all("script", src=True))
    _stylesheets  = len([t for t in soup.find_all("link", rel=True)
                         if "stylesheet" in (t.get("rel") or [])])
    _iframes_raw  = len(soup.find_all("iframe", src=True))
    _videos       = len(soup.find_all("video"))
    _audio        = len(soup.find_all("audio"))
    # img / source / picture counted below (after _body_text, they survive)
    _resource_count_partial = _ext_scripts + _stylesheets + _iframes_raw + _videos + _audio

    # ── PAGINATION (before body_txt — <link> tags survive but count early) ────
    pagination_next: str = ""
    pagination_prev: str = ""
    for _pl in soup.find_all("link", rel=True):
        _pl_rels = [r.lower() if isinstance(r, str) else r for r in (_pl.get("rel") or [])]
        _pl_href = (_pl.get("href") or "").strip()
        if not _pl_href:
            continue
        if "next" in _pl_rels and not pagination_next:
            pagination_next = urljoin(url, _pl_href)
        if "prev" in _pl_rels or "previous" in _pl_rels:
            if not pagination_prev:
                pagination_prev = urljoin(url, _pl_href)

    # ── HTML SIZE ─────────────────────────────────────────────────────────────
    html_size_kb = round(len(html.encode("utf-8")) / 1024, 1)

    body_txt     = _body_text(soup)
    link_objects = _internal_links(soup, url, domain, bare_domain)
    link_hrefs   = [d["href"] for d in link_objects]
    # Extract image alt texts — used by SEO audit and AI prompt
    img_alts = [
        img.get("alt", "").strip()
        for img in soup.find_all("img")
        if img.get("alt", "").strip()
    ][:20]   # cap at 20 to keep payload manageable

    # ── NEW: Last-Modified header (content freshness signal) ─────────────────
    _hdrs = response_headers or {}
    last_modified = (
        _hdrs.get("Last-Modified") or
        _hdrs.get("last-modified") or
        ""
    )

    # ── NEW: Viewport meta tag (mobile-friendliness signal) ──────────────────
    _vp_tag = soup.find("meta", attrs={"name": lambda n: n and n.lower() == "viewport"})
    viewport = (_vp_tag.get("content", "").strip() if _vp_tag else "")

    # ── NEW: <meta name="robots"> and X-Robots-Tag header ────────────────────
    # Both signals are equivalent per Google's spec — either can block indexing.
    _rm = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    robots_meta  = (_rm.get("content") or "").strip().lower() if _rm else ""
    x_robots_tag = (_hdrs.get("X-Robots-Tag") or _hdrs.get("x-robots-tag") or "").strip().lower()
    _robots_combined = f"{robots_meta} {x_robots_tag}"
    robots_noindex   = "noindex"  in _robots_combined
    robots_nofollow  = "nofollow" in _robots_combined
    # ── X-Robots-Tag specific booleans (header only, separate from meta robots) ──
    # "none" directive is equivalent to "noindex, nofollow" in X-Robots-Tag
    x_robots_noindex  = "noindex"  in x_robots_tag or "none" in x_robots_tag
    x_robots_nofollow = "nofollow" in x_robots_tag or "none" in x_robots_tag

    # ── NEW: Image src list (format detection: WebP/AVIF vs legacy) ──────────
    img_srcs = [
        img.get("src", "").strip()
        for img in soup.find_all("img")
        if img.get("src", "").strip()
    ][:20]

    # ── NEW: Image loading quality signals (lazy + srcset) ───────────────────
    _all_imgs        = soup.find_all("img")   # img tags survive _body_text decompose
    img_total        = len(_all_imgs)
    img_lazy_count   = sum(1 for _im in _all_imgs if (_im.get("loading") or "").lower() == "lazy")
    img_srcset_count = sum(1 for _im in _all_imgs if (_im.get("srcset") or "").strip())
    img_lazy_pct     = round(img_lazy_count   / img_total * 100, 1) if img_total else 0.0
    img_srcset_pct   = round(img_srcset_count / img_total * 100, 1) if img_total else 0.0
    # alt=None means attribute absent entirely (CLS/accessibility issue)
    # alt="" is valid for decorative images — only flag absent alt attributes
    img_missing_alt  = sum(1 for _im in _all_imgs if _im.get("alt") is None)
    # width+height both required to let browser reserve layout space before load
    img_missing_dims = sum(1 for _im in _all_imgs
                           if not (_im.get("width") and _im.get("height")))

    # ── Image format detection (modern vs legacy) ─────────────────────────────
    # Count images using legacy formats that lack modern compression efficiency.
    # "Not modern" = has a jpg/png/gif/bmp src and no .webp/.avif equivalent.
    img_non_modern_count = sum(
        1 for src in img_srcs
        if src and _LEGACY_IMG_RE.search(src)
        and not any(
            src.lower().endswith(ext) or f"{ext}?" in src.lower()
            for ext in _MODERN_IMG_EXTS
        )
    )

    # ── Finalise resource_count (post _body_text — add img + source counts) ───
    _picture_sources = len(soup.find_all("source"))   # <source> in <picture>/<video>
    resource_count = _resource_count_partial + img_total + _picture_sources

    # ── External links (for .edu/.gov/.org citation signal in E-E-A-T) ─────────
    # Extracted from actual href attributes — more reliable than body-text search.
    _ext_links: list[str] = []
    _page_bare = urlparse(url).netloc.lstrip("www.")
    for _a in soup.find_all("a", href=True):
        _href = _a["href"].strip()
        if not _href or _href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        try:
            _full = urljoin(url, _href)
            _p = urlparse(_full)
            if _p.scheme not in ("http", "https"):
                continue
            _link_bare = _p.netloc.lstrip("www.")
            if _link_bare and _link_bare != _page_bare:
                _ext_links.append(_full)
        except Exception:
            pass
    external_links: list[str] = _ext_links[:50]   # cap to keep payload small

    # ── Hreflang tags (international SEO) ───────────────────────────────────────
    # Collects all <link rel="alternate" hreflang="xx"> declarations on the page.
    hreflang_tags: list[dict] = []
    for _hl in soup.find_all("link", rel=True):
        _rel = _hl.get("rel") or []
        if "alternate" in (r.lower() if isinstance(r, str) else r for r in _rel):
            _lang = _hl.get("hreflang", "").strip()
            _href_val = _hl.get("href", "").strip()
            if _lang and _href_val:
                hreflang_tags.append({"lang": _lang, "href": _href_val})

    # ── NEW: Mixed content detection (HTTP resources on HTTPS page) ──────────
    # Scanned here — raw HTML is available; avoids storing full HTML in memory.
    mixed_resources: list[str] = []
    if url.startswith("https://"):
        _mc_seen: set[str] = set()
        for _m in re.findall(
            r'(?:src|href|action|data|poster)\s*=\s*[\'"]http://[^\'"]{4,}[\'"]',
            html, re.I
        ):
            _hit = re.search(r"http://[^'\"]+", _m)
            if _hit:
                _mc_seen.add(_hit.group(0))
        mixed_resources = sorted(_mc_seen)[:20]

    # ── Filtered response headers (keep only SEO-relevant security/caching) ──
    _KEEP_HDRS = frozenset([
        "strict-transport-security", "content-security-policy",
        "x-frame-options", "x-content-type-options", "x-xss-protection",
        "cache-control", "last-modified", "etag", "server", "content-type",
        "content-encoding",   # required for compression audit (gzip/br/deflate detection)
        "referrer-policy",    # required for referrer-policy audit
        "permissions-policy", # required for permissions-policy audit
    ])
    _stored_headers = {k: v for k, v in (_hdrs or {}).items() if k.lower() in _KEEP_HDRS}

    # Mark 4xx/5xx pages as errors — they contain no crawlable content
    # (403 = Cloudflare challenge, 404 = dead page, 5xx = server error).
    # These still appear in the results table but don't consume crawl quota
    # and don't enqueue links (CR-001 fix depends on this being correct).
    is_error_status = status >= 400

    return {
        "url": url, "status_code": status,
        "title": title, "meta_description": meta_d, "meta_keywords": meta_k,
        "canonical": canon, "h1": h1s, "h2": h2s, "h3": h3s,
        "og_title": og_title, "og_description": og_desc,
        "og_image": og_image, "og_type": og_type,
        "twitter_card": tw_card, "twitter_title": tw_title,
        "twitter_description": tw_desc, "twitter_image": tw_image,
        "robots_meta": robots_meta, "x_robots_tag": x_robots_tag,
        "robots_noindex": robots_noindex, "robots_nofollow": robots_nofollow,
        "x_robots_noindex": x_robots_noindex, "x_robots_nofollow": x_robots_nofollow,
        "body_text": body_txt,
        "img_alts": img_alts,   # image alt texts for SEO audit
        # ── NEW fields (additive — no existing field changed) ─────────────────
        "last_modified":       last_modified,      # HTTP Last-Modified header value
        "viewport":            viewport,           # <meta name="viewport"> content
        "img_srcs":            img_srcs,           # image src list for format detection
        "schema_types":        schema_types,       # JSON-LD @type values on this page
        "schema_rating":       schema_rating,      # aggregateRating.ratingValue (float|None)
        "schema_review_count": schema_review_count,# aggregateRating.reviewCount (int|None)
        "schema_author":       schema_author,      # author.name from JSON-LD (str)
        "external_links":      external_links,     # external href URLs (for citation signal)
        "hreflang_tags":       hreflang_tags,      # [{lang, href}] from <link rel=alternate>
        "redirect_hops":       redirect_hops,      # number of redirects followed to reach this page
        "response_headers":    _stored_headers,    # SEO-relevant HTTP response headers
        "mixed_resources":     mixed_resources,    # HTTP resources on HTTPS page (mixed content)
        "link_objects":        link_objects if not is_error_status else [],   # [{href, text}] for anchor analysis
        "links":               link_hrefs   if not is_error_status else [],   # hrefs only — backward-compat
        "heading_sequence":    heading_sequence,   # [{level, text}] in DOM order for flow validation
        "schema_objects":      schema_objects,     # [{type, props}] for required-property validation
        "breadcrumbs":         breadcrumbs,        # ordered trail [{position,name,item}] or [{name,href}]
        "breadcrumb_detected": breadcrumb_detected, # True if any breadcrumb found
        "breadcrumb_source":   breadcrumb_source,  # "json_ld" | "html_nav" | ""
        "img_total":           img_total,          # total <img> tags on page
        "img_lazy_count":      img_lazy_count,     # images with loading="lazy"
        "img_lazy_pct":        img_lazy_pct,       # % images using lazy loading
        "img_srcset_count":    img_srcset_count,   # images with srcset attribute
        "img_srcset_pct":      img_srcset_pct,     # % images with srcset
        "img_missing_alt":     img_missing_alt,    # images with no alt attribute (CLS/a11y)
        "img_missing_dims":    img_missing_dims,   # images missing width or height (CLS risk)
        "img_non_modern_count": img_non_modern_count,  # legacy-format images (jpg/png/gif, not WebP/AVIF)
        # ── Performance signals ───────────────────────────────────────────────
        "resource_count":     resource_count,      # total resource-loading element count
        "html_size_kb":       html_size_kb,        # HTML response size in KB
        # ── Pagination ───────────────────────────────────────────────────────
        "pagination_next":    pagination_next,     # href of <link rel="next"> or ""
        "pagination_prev":    pagination_prev,     # href of <link rel="prev"> or ""
        # ── TLS / Security ───────────────────────────────────────────────────
        "tls_version":        tls_version,         # e.g. "TLSv1.3" or "" if unavailable
        # ─────────────────────────────────────────────────────────────────────
        "internal_links_count": 0, "_internal_links": link_hrefs if not is_error_status else [],
        "issues": [], "keywords": [], "keywords_ngrams": [],
        "keywords_scored": [],   # filled by keyword_scorer.score_keywords()
        "competitor_gaps": None, # filled by competitor.run_competitor_analysis()
        "structured":      None, # filled by keyword_scorer.build_structured_page()
        "competition": "Medium", "priority": "", "gemini_fields": [],
        "_is_error": is_error_status,
    }


# ── Extraction helpers ────────────────────────────────────────────────────────

def _text(tag) -> str:
    return tag.get_text(strip=True) if tag else ""

def _meta_content(soup: BeautifulSoup, name: str) -> str:
    tag = (soup.find("meta", attrs={"name": name}) or
           soup.find("meta", attrs={"name": name.capitalize()}))
    return (tag.get("content") or "").strip() if tag else ""

def _canonical(soup: BeautifulSoup, page_url: str) -> str:
    tag = soup.find("link", attrs={"rel": "canonical"})
    return urljoin(page_url, (tag.get("href") or "").strip()) if tag else ""

def _og(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop})
    return (tag.get("content") or "").strip() if tag else ""

def _body_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "footer",
                     "header", "noscript", "iframe", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:3000]

def _internal_links(soup: BeautifulSoup, page_url: str,
                    domain: str, bare_domain: str = "") -> list[dict]:
    """
    Extract internal links with anchor text.
    Returns: [{"href": str, "text": str}]
    Deduplicates by href — first-seen anchor text wins.
    bare_domain = domain.lstrip('www.') — compared against link's bare netloc.
    """
    seen:   set[str]   = set()
    result: list[dict] = []
    bare    = bare_domain or domain.lstrip("www.")

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full      = urljoin(page_url, href)
        parsed    = urlparse(full)
        link_bare = parsed.netloc.lstrip("www.")

        if link_bare == bare and parsed.scheme in ("http", "https"):
            clean = parsed._replace(fragment="").geturl()
            if clean not in seen:
                seen.add(clean)
                result.append({"href": clean, "text": a.get_text(strip=True)[:100]})
    return result

def _minimal_record(url: str, status, note: str = "") -> dict:
    return {
        "url": url, "status_code": status,
        # note stored in title so it shows in the table
        # but Gemini/keyword logic must skip these records
        "title": note, "meta_description": "", "meta_keywords": "",
        "canonical": "", "h1": [], "h2": [], "h3": [],
        "og_title": "", "og_description": "",
        "og_image": "", "og_type": "",
        "twitter_card": "", "twitter_title": "",
        "twitter_description": "", "twitter_image": "",
        "robots_meta": "", "x_robots_tag": "",
        "robots_noindex": False, "robots_nofollow": False,
        "x_robots_noindex": False, "x_robots_nofollow": False,
        "body_text": "",
        "img_alts": [],
        # ── new fields (must match _parse() return keys) ──────────────────────
        "last_modified": "", "viewport": "", "img_srcs": [],
        "schema_types": [], "schema_rating": None, "schema_review_count": None,
        "schema_author": "", "external_links": [], "hreflang_tags": [],
        "redirect_hops": 0, "response_headers": {},
        "mixed_resources": [], "link_objects": [], "links": [],
        "heading_sequence": [], "schema_objects": [],
        "breadcrumbs": [], "breadcrumb_detected": False, "breadcrumb_source": "",
        "img_total": 0, "img_lazy_count": 0, "img_lazy_pct": 0.0,
        "img_srcset_count": 0, "img_srcset_pct": 0.0,
        "img_missing_alt": 0, "img_missing_dims": 0,
        "img_non_modern_count": 0,
        "resource_count": 0, "html_size_kb": 0.0,
        "pagination_next": "", "pagination_prev": "",
        "tls_version": "",
        # ─────────────────────────────────────────────────────────────────────
        "internal_links_count": 0, "_internal_links": [],
        "issues": [], "keywords": [], "keywords_ngrams": [],
        "keywords_scored": [], "competitor_gaps": None, "structured": None,
        "competition": "Medium", "priority": "", "gemini_fields": [],
        "_is_error": True,   # flag — skip for Gemini, skip for keywords
    }
