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

_CFFI_IMPERSONATE   = "chrome124"  # current Chrome TLS fingerprint
_CFFI_FETCH_TIMEOUT = 25           # seconds — cffi per-request timeout


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
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

def _random_headers() -> dict:
    """Return HEADERS with a randomly selected User-Agent string."""
    ua = _random.choice(_USER_AGENTS)
    h  = dict(HEADERS)
    h["User-Agent"] = ua
    return h

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
        self.queue:    list[str] = [self.root_url]
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

                wave: list[str] = []
                while self.queue and len(wave) < min(MAX_CONCURRENCY, remaining):
                    url = self.queue.pop(0)
                    if url not in self.visited:
                        wave.append(url)
                        self.visited.add(url)

                if not wave:
                    break

                crawl_status.update({
                    "current_url":  wave[0],
                    "pages_queued": len(self.queue),
                    "elapsed_s":    round(time.time() - crawl_status["started_at"], 1),
                })

                # Brief jitter between BFS waves — mimics human browsing pace
                # and avoids triggering rate-limit rules on protected sites
                await asyncio.sleep(_jitter())

                tasks   = [self._fetch(session, sem, url) for url in wave]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for url, result in zip(wave, results):
                    if isinstance(result, Exception):
                        # Store error record but DO NOT add to queue
                        rec = _minimal_record(url, "Error", str(result))
                        rec["_is_error"] = True
                        crawl_results.append(rec)
                        crawl_status["errors"] += 1
                        continue
                    if result is None:
                        continue

                    is_error = result.pop("_is_error", False)
                    links    = result.pop("_internal_links", [])
                    result["internal_links_count"] = len(links)
                    result["_is_error"] = is_error   # restore flag so issues.py + Gemini can read it
                    crawl_results.append(result)

                    # Only enqueue links from successfully loaded pages
                    if not is_error:
                        self._fetched_ok += 1          # CR-001: count good pages only
                        for link in links:
                            if link not in self.visited and link not in self.queue:
                                self.queue.append(link)

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
                        self.queue        = [self.root_url]
                        logger.info("Domain resolved [%s]: %s → %s",
                                    label, try_url, self.domain)

                        # Cache homepage — BFS won't re-fetch it
                        ctype = resp.headers.get("Content-Type", "")
                        if "text/html" in ctype:
                            try:
                                html = await resp.text(errors="replace")
                                result = _parse(
                                    self.root_url, resp.status, html,
                                    self.domain, self._bare_domain
                                )
                                links = result.pop("_internal_links", [])
                                result["internal_links_count"] = len(links)
                                crawl_results.append(result)
                                self.visited.add(self.root_url)
                                self.queue = []
                                for link in links:
                                    if link not in self.visited:
                                        self.queue.append(link)
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
                                self.domain, self._bare_domain
                            )
                            links = result.pop("_internal_links", [])
                            result["internal_links_count"] = len(links)
                            crawl_results.append(result)
                            self.visited.add(self.root_url)
                            self.queue = []
                            for link in links:
                                if link not in self.visited:
                                    self.queue.append(link)
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
        if self.root_url not in self.queue and self.root_url not in self.visited:
            self.queue = [self.root_url]

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
                                          self.domain, self._bare_domain)
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

                            # Detect bot-wall responses before parsing:
                            # 403/429/503 from real servers = bot-blocked, not network error.
                            # Flag for cffi retry; still try to parse in case cffi unavailable.
                            if status in (403, 429, 503):
                                got_blocked = True
                                last_error  = f"HTTP {status} (bot-protection wall)"
                                logger.warning("BOT-BLOCKED [%s] %s → HTTP %s",
                                               label, try_url, status)
                                # Don't return yet — try remaining aiohttp attempts,
                                # then cffi below if all aiohttp attempts are blocked.
                                continue

                            html = await resp.text(errors="replace")
                            return _parse(url, status, html,
                                          self.domain, self._bare_domain)

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

            # ── Attempt 5: curl_cffi Chrome TLS impersonation ────────────────
            # Only tried when ALL aiohttp attempts hit a bot-protection wall
            # (403/429/503). cffi sends the exact TLS fingerprint of Chrome 124,
            # bypassing Cloudflare JA3/JA4 checks. Network errors go straight to
            # the error record below — no point retrying with a different TLS stack.
            if _CFFI and got_blocked:
                try:
                    logger.info("cffi fallback attempt for %s", url)
                    async with _CffiSession() as _cffi_sess:
                        cffi_resp = await _cffi_sess.get(
                            url,
                            impersonate=_CFFI_IMPERSONATE,
                            timeout=_CFFI_FETCH_TIMEOUT,
                            allow_redirects=True,
                        )
                        if cffi_resp.status_code == 200:
                            logger.info("cffi bypass SUCCESS for %s — enabling cffi-mode", url)
                            crawl_status["ssl_fallbacks"] = crawl_status.get("ssl_fallbacks", 0) + 1
                            self._use_cffi = True   # all future BFS fetches skip aiohttp
                            return _parse(url, 200, cffi_resp.text,
                                          self.domain, self._bare_domain)
                        logger.warning("cffi bypass got HTTP %d for %s",
                                       cffi_resp.status_code, url)
                        last_error = f"cffi HTTP {cffi_resp.status_code}"
                except Exception as _cffi_exc:
                    logger.warning("cffi fallback failed for %s: %s", url, _cffi_exc)
                    last_error = f"cffi error: {_cffi_exc}"

            # All attempts failed — record error, BFS continues
            crawl_status["errors"] += 1
            logger.error("ALL ATTEMPTS FAILED for %s — last: %s", url, last_error)
            rec = _minimal_record(url, "Error", last_error)
            rec["_is_error"] = True
            return rec


# ── HTML parsing ──────────────────────────────────────────────────────────────

def _parse(url: str, status: int, html: str,
           domain: str, bare_domain: str = "") -> dict:
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
    og_title = _og(soup, "og:title")
    og_desc  = _og(soup, "og:description")
    body_txt = _body_text(soup)
    links    = _internal_links(soup, url, domain, bare_domain)
    # Extract image alt texts — used by SEO audit and AI prompt
    img_alts = [
        img.get("alt", "").strip()
        for img in soup.find_all("img")
        if img.get("alt", "").strip()
    ][:20]   # cap at 20 to keep payload manageable

    # Mark 4xx/5xx pages as errors — they contain no crawlable content
    # (403 = Cloudflare challenge, 404 = dead page, 5xx = server error).
    # These still appear in the results table but don't consume crawl quota
    # and don't enqueue links (CR-001 fix depends on this being correct).
    is_error_status = status >= 400

    return {
        "url": url, "status_code": status,
        "title": title, "meta_description": meta_d, "meta_keywords": meta_k,
        "canonical": canon, "h1": h1s, "h2": h2s, "h3": h3s,
        "og_title": og_title, "og_description": og_desc, "body_text": body_txt,
        "img_alts": img_alts,   # image alt texts for SEO audit
        "internal_links_count": 0, "_internal_links": links if not is_error_status else [],
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
                    domain: str, bare_domain: str = "") -> list[str]:
    """
    Extract internal links. Accepts both www and non-www variants.
    bare_domain = domain.lstrip('www.') — compared against link's bare netloc.
    """
    links = set()
    bare  = bare_domain or domain.lstrip("www.")

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full      = urljoin(page_url, href)
        parsed    = urlparse(full)
        link_bare = parsed.netloc.lstrip("www.")

        if link_bare == bare and parsed.scheme in ("http", "https"):
            links.add(parsed._replace(fragment="").geturl())
    return list(links)

def _minimal_record(url: str, status, note: str = "") -> dict:
    return {
        "url": url, "status_code": status,
        # note stored in title so it shows in the table
        # but Gemini/keyword logic must skip these records
        "title": note, "meta_description": "", "meta_keywords": "",
        "canonical": "", "h1": [], "h2": [], "h3": [],
        "og_title": "", "og_description": "", "body_text": "",
        "internal_links_count": 0, "_internal_links": [],
        "issues": [], "keywords": [], "keywords_ngrams": [],
        "keywords_scored": [], "competitor_gaps": None, "structured": None,
        "img_alts": [],
        "competition": "Medium", "priority": "", "gemini_fields": [],
        "_is_error": True,   # flag — skip for Gemini, skip for keywords
    }
