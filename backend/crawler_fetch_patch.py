"""
crawler_fetch_patch.py
======================
This file shows the EXACT MINIMAL changes needed in crawler.py.

DO NOT copy this whole file into crawler.py.
Apply the three changes described below.

──────────────────────────────────────────────────────────────────────────────
CHANGE 1 — Add import at the top of crawler.py (after existing imports)
──────────────────────────────────────────────────────────────────────────────

ADD this line after the existing imports block (around line 48):

    from robust_fetch import robust_fetch as _robust_fetch

──────────────────────────────────────────────────────────────────────────────
CHANGE 2 — Replace the entire _fetch() method body in SEOCrawler
──────────────────────────────────────────────────────────────────────────────

FIND the method signature (line ~390):

    async def _fetch(
        self,
        session: aiohttp.ClientSession,
        sem:     asyncio.Semaphore,
        url:     str,
    ) -> dict | None:

REPLACE the entire method body (everything indented under it) with:

    async def _fetch(
        self,
        session: aiohttp.ClientSession,   # kept for API compat — not used by robust_fetch
        sem:     asyncio.Semaphore,
        url:     str,
    ) -> dict | None:
        \"\"\"
        Multi-strategy fetch via robust_fetch.py.

        Cascade:
          S1 — aiohttp fast (8s)
          S2 — aiohttp + full browser headers (12s)
          S3 — HTTP fallback (15s)
          S4 — Playwright headless (15s, last resort)

        Returns a parsed page dict, or a minimal error record.
        Never raises — BFS always continues.
        \"\"\"
        html, status, strategy = await _robust_fetch(url, sem)

        if html is None:
            # All 4 strategies failed — log, count, continue BFS
            crawl_status["errors"] += 1
            logger.error("[FETCH] Skipping %s — all strategies failed", url)
            rec = _minimal_record(url, "Error", f"all strategies failed (last tried: {strategy})")
            rec["_is_error"] = True
            return rec

        logger.info("[FETCH] %s succeeded via %s", url, strategy)

        # Track SSL/HTTP fallbacks in crawl_status
        if strategy in ("s3_http_fallback", "s4_playwright"):
            crawl_status["ssl_fallbacks"] = crawl_status.get("ssl_fallbacks", 0) + 1

        return _parse(url, status, html, self.domain, self._bare_domain)

──────────────────────────────────────────────────────────────────────────────
CHANGE 3 — (Optional but recommended) Update crawl_status initialiser
──────────────────────────────────────────────────────────────────────────────

In the crawl_status dict (around line 70), add a "playwright_fallbacks" counter:

    crawl_status: dict = {
        ...existing keys...,
        "playwright_fallbacks": 0,   # ADD THIS LINE
    }

Then in _fetch(), update the fallback counter for Playwright:

    if strategy == "s4_playwright":
        crawl_status["playwright_fallbacks"] = crawl_status.get("playwright_fallbacks", 0) + 1

──────────────────────────────────────────────────────────────────────────────
NOTHING ELSE CHANGES.  BFS loop, queue, visited, max_pages — all untouched.
──────────────────────────────────────────────────────────────────────────────
"""

# ── Full replacement _fetch() method for copy-paste into SEOCrawler ──────────

REPLACEMENT_FETCH_METHOD = '''
    async def _fetch(
        self,
        session: aiohttp.ClientSession,   # kept for API compat — not used by robust_fetch
        sem:     asyncio.Semaphore,
        url:     str,
    ) -> dict | None:
        """
        Multi-strategy fetch via robust_fetch.py.

        Cascade:
          S1 — aiohttp fast (8s)
          S2 — aiohttp + full browser headers (12s)
          S3 — HTTP fallback (15s)
          S4 — Playwright headless (15s, last resort)

        Returns a parsed page dict, or a minimal error record.
        Never raises — BFS always continues.
        """
        html, status, strategy = await _robust_fetch(url, sem)

        if html is None:
            # All 4 strategies failed — log, count, continue BFS
            crawl_status["errors"] += 1
            logger.error("[FETCH] Skipping %s — all strategies failed", url)
            rec = _minimal_record(url, "Error", f"all strategies failed (last tried: {strategy})")
            rec["_is_error"] = True
            return rec

        logger.info("[FETCH] %s succeeded via %s", url, strategy)

        # Track fallbacks in crawl_status
        if strategy in ("s3_http_fallback", "s4_playwright"):
            crawl_status["ssl_fallbacks"] = crawl_status.get("ssl_fallbacks", 0) + 1
        if strategy == "s4_playwright":
            crawl_status["playwright_fallbacks"] = crawl_status.get("playwright_fallbacks", 0) + 1

        return _parse(url, status, html, self.domain, self._bare_domain)
'''
