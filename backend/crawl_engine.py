"""
crawl_engine.py — Memory-bounded async SEO crawl engine.

Memory guarantees
─────────────────
  visited    set[str]       O(n)  — one normalized URL string per page
  _queued    set[str]       O(n)  — mirrors queue for O(1) membership check
  _queue     list[(url,d)]  O(n)  — bounded: never exceeds max_pages - done
  Raw HTML   str            O(1)  — local in _fetch_and_parse, GC'd on return
  BeautifulSoup              O(1)  — local in _parse_html, GC'd on return
  PageRecord objects are yielded immediately — never accumulated internally.

Streaming output
────────────────
  CrawlEngine.crawl() is an async generator.  The caller drives consumption:

      engine = CrawlEngine("https://example.com", max_pages=1000)
      async for page in engine.crawl():
          await persist(page)          # process & discard — never collect

Concurrency
───────────
  Single aiohttp.ClientSession shared across the BFS.
  asyncio.Semaphore(max_concurrency=20) hard-caps in-flight HTTP requests.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Tracking / noise query params removed during normalization ────────────────
# These carry no page-identity meaning and create phantom URL duplicates.
_STRIP_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_reader", "fbclid", "gclid", "gclsrc", "dclid",
    "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "hs_email",
    "hs_subscriber", "ref", "referrer", "source", "affiliate",
})

# ── SEO-relevant response headers retained per page ───────────────────────────
_KEEP_HEADERS: frozenset[str] = frozenset({
    "content-type", "cache-control", "last-modified", "etag",
    "x-robots-tag", "strict-transport-security", "content-security-policy",
    "x-frame-options", "x-content-type-options", "server", "location",
})

# ── Request headers — minimal Chrome fingerprint ──────────────────────────────
_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ── File-extension paths skipped by the crawler ───────────────────────────────
_SKIP_EXTENSIONS: re.Pattern = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|gz|tar|rar|7z"
    r"|jpg|jpeg|png|gif|webp|avif|svg|ico|bmp|tiff"
    r"|mp3|mp4|avi|mov|mkv|webm|ogg|flac|wav"
    r"|css|js|woff2?|ttf|eot|otf|map"
    r"|xml|json|csv|txt|rss|atom)$",
    re.IGNORECASE,
)


# ── URL normalization ─────────────────────────────────────────────────────────

def normalize_url(raw: str, trailing_slash: bool = False) -> str | None:
    """
    Normalize a URL into a canonical string for deduplication.

    Steps applied in order:
      1. Strip whitespace, parse
      2. Reject non-HTTP/S schemes (mailto:, tel:, javascript:, data:, …)
      3. Lowercase scheme + host
      4. Drop default ports (:80 on http, :443 on https)
      5. Remove fragment (#...)
      6. Filter tracking/noise query params
      7. Sort remaining params — produces a stable identity key
      8. Apply trailing-slash policy (paths without file extensions only)

    Returns None for any URL that cannot be normalized to a valid http/https URL.
    """
    try:
        p = urlparse(raw.strip())
    except Exception:
        return None

    if p.scheme not in ("http", "https"):
        return None
    if not p.netloc:
        return None

    scheme = p.scheme.lower()
    host   = p.netloc.lower()

    # Drop default ports
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]

    # Filter + sort query params
    raw_qs = parse_qs(p.query, keep_blank_values=False)
    clean  = {k: v for k, v in raw_qs.items() if k.lower() not in _STRIP_PARAMS}
    query  = urlencode(sorted(clean.items()), doseq=True)

    path = p.path or "/"

    # Trailing-slash policy — skip paths whose last segment has a file extension.
    # We check the segment without its trailing slash so that /file.pdf/ is
    # correctly identified as having an extension even though it ends with '/'.
    if path != "/":
        last_seg = path.rstrip("/").rsplit("/", 1)[-1]
        if not re.search(r"\.[a-zA-Z0-9]{1,6}$", last_seg):
            if trailing_slash and not path.endswith("/"):
                path += "/"
            elif not trailing_slash and path.endswith("/"):
                path = path.rstrip("/") or "/"

    # fragment is intentionally dropped (never part of page identity)
    return urlunparse((scheme, host, path, "", query, ""))


# ── Page record ───────────────────────────────────────────────────────────────

@dataclass
class PageRecord:
    """
    All data extracted from one crawled page.

    Raw HTML is never stored here — only the parsed fields below.
    Instantiated by _parse_html(); yielded directly from CrawlEngine.crawl().
    """
    url:              str
    status_code:      int
    response_time_ms: float
    headers:          dict[str, str]   # SEO-relevant response headers only
    crawl_depth:      int

    # ── SEO fields ────────────────────────────────────────────────────────────
    title:            str        = ""
    meta_description: str        = ""
    headings:         list[dict] = field(default_factory=list)  # [{level, text}]
    body_text:        str        = ""
    links:            list[dict] = field(default_factory=list)  # [{href, anchor_text}]
    images:           list[dict] = field(default_factory=list)  # [{src, alt, width, height}]
    schema:           list[dict] = field(default_factory=list)  # JSON-LD objects
    hreflang:         list[dict] = field(default_factory=list)  # [{lang, href}]
    meta_robots:      str        = ""
    canonical:        str        = ""

    # ── Error state ───────────────────────────────────────────────────────────
    error:    str | None = None
    is_error: bool       = False

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)


# ── Crawl engine ──────────────────────────────────────────────────────────────

class CrawlEngine:
    """
    Async BFS crawl engine.  Instantiate once per crawl job.

    Parameters
    ──────────
    root_url          Starting URL (scheme + host required)
    max_pages         Hard limit on total pages yielded          (default 5000)
    max_depth         BFS depth limit from root                  (default 5)
    max_concurrency   Semaphore cap on simultaneous HTTP requests (default 20)
    per_domain_limit  Max pages queued per unique netloc; None = unlimited
    timeout_s         Per-request total timeout in seconds       (default 5.0)
    max_retries       Retry attempts on transient errors         (default 2)
    trailing_slash    Trailing-slash normalization policy        (default False)

    Usage
    ─────
    engine = CrawlEngine("https://example.com", max_pages=5000, max_depth=5)
    async for page in engine.crawl():
        await downstream(page)          # don't collect — process & discard
    """

    def __init__(
        self,
        root_url:         str,
        max_pages:        int        = 5000,
        max_depth:        int        = 5,
        max_concurrency:  int        = 20,
        per_domain_limit: int | None = None,
        timeout_s:        float      = 5.0,
        max_retries:      int        = 2,
        trailing_slash:   bool       = False,
    ) -> None:
        norm = normalize_url(root_url, trailing_slash)
        if not norm:
            raise ValueError(f"Invalid root URL: {root_url!r}")

        self.root_norm        = norm
        self.max_pages        = max_pages
        self.max_depth        = max_depth
        self.max_concurrency  = max_concurrency
        self.per_domain_limit = per_domain_limit
        self.timeout_s        = timeout_s
        self.max_retries      = max_retries
        self.trailing_slash   = trailing_slash

        parsed               = urlparse(norm)
        self._domain         = parsed.netloc
        self._bare_domain    = self._domain.lstrip("www.")

        # ── Crawl state (O(n) total) ──────────────────────────────────────────
        self.visited:        set[str]             = set()
        self._queued:        set[str]             = set()   # O(1) membership
        self._queue:         list[tuple[str,int]] = []      # BFS queue
        self._domain_counts: dict[str, int]       = {}      # per-domain quota
        self._pages_done:    int                  = 0       # pages yielded

    # ── Public ────────────────────────────────────────────────────────────────

    async def crawl(self) -> AsyncGenerator[PageRecord, None]:
        """
        Async generator — yields one PageRecord per crawled page.

        - Does not accumulate records internally.
        - Stops automatically when max_pages is reached.
        - Caller can stop early by breaking out of the async for loop.
        - Enqueues child links of successful pages only (is_error=False).
        - Canonical URLs: if a page's canonical differs from its URL, child
          links are still enqueued so we don't silently skip crawlable content;
          but callers can inspect page.canonical to detect duplicates.
        """
        timeout = aiohttp.ClientTimeout(
            total=self.timeout_s,
            connect=max(2.0, self.timeout_s * 0.4),
            sock_read=self.timeout_s,
        )
        connector = aiohttp.TCPConnector(
            limit=self.max_concurrency,
            limit_per_host=max(4, self.max_concurrency // 5),
            ssl=False,                    # no SSLContext — avoids Windows SChannel issues
            enable_cleanup_closed=True,
            ttl_dns_cache=300,            # 5-minute DNS cache — avoids redundant lookups
        )
        sem = asyncio.Semaphore(self.max_concurrency)

        self._enqueue(self.root_norm, depth=0)

        async with aiohttp.ClientSession(
            headers=_REQUEST_HEADERS,
            timeout=timeout,
            connector=connector,
        ) as session:
            while self._queue and self._pages_done < self.max_pages:
                wave = self._dequeue_wave()
                if not wave:
                    break

                tasks = [
                    self._fetch_and_parse(session, sem, url, depth)
                    for url, depth in wave
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for (url, depth), result in zip(wave, results):
                    if isinstance(result, Exception):
                        record = PageRecord(
                            url=url, status_code=0, response_time_ms=0.0,
                            headers={}, crawl_depth=depth,
                            error=str(result), is_error=True,
                        )
                    else:
                        record = result  # type: ignore[assignment]

                    # Enqueue child links — only from pages that loaded successfully
                    if not record.is_error and depth < self.max_depth:
                        for link_obj in record.links:
                            child = normalize_url(link_obj["href"], self.trailing_slash)
                            if child:
                                self._enqueue(child, depth + 1)

                    self._pages_done += 1
                    yield record

                    if self._pages_done >= self.max_pages:
                        return   # hard stop — leaves remaining queue intact

    # ── BFS queue helpers ─────────────────────────────────────────────────────

    def _enqueue(self, url: str, depth: int) -> None:
        """
        Conditionally add url to the BFS queue.

        Rejects if:
          - already visited or queued  (dedup)
          - depth > max_depth          (depth limit)
          - would exceed max_pages     (page-count limit)
          - domain quota exhausted     (per_domain_limit)
          - not the target domain      (keeps crawl internal)
          - path has a binary/asset extension (no SEO value)
        """
        if url in self.visited or url in self._queued:
            return
        if depth > self.max_depth:
            return
        if self._pages_done + len(self._queued) >= self.max_pages:
            return

        parsed = urlparse(url)
        netloc = parsed.netloc

        # Only crawl same bare domain (strips www. on both sides)
        if netloc.lstrip("www.") != self._bare_domain:
            return

        # Skip non-HTML asset paths
        if _SKIP_EXTENSIONS.search(parsed.path):
            return

        # Per-domain page quota
        if self.per_domain_limit is not None:
            if self._domain_counts.get(netloc, 0) >= self.per_domain_limit:
                return
        self._domain_counts[netloc] = self._domain_counts.get(netloc, 0) + 1

        self._queue.append((url, depth))
        self._queued.add(url)

    def _dequeue_wave(self) -> list[tuple[str, int]]:
        """
        Pull up to max_concurrency items from the front of the BFS queue.
        Skips URLs already visited (race-condition guard).
        """
        remaining = self.max_pages - self._pages_done
        wave: list[tuple[str, int]] = []

        while self._queue and len(wave) < min(self.max_concurrency, remaining):
            url, depth = self._queue.pop(0)
            self._queued.discard(url)
            if url not in self.visited:
                self.visited.add(url)
                wave.append((url, depth))

        return wave

    # ── Fetch ─────────────────────────────────────────────────────────────────

    async def _fetch_and_parse(
        self,
        session: aiohttp.ClientSession,
        sem:     asyncio.Semaphore,
        url:     str,
        depth:   int,
    ) -> PageRecord:
        """
        Acquire semaphore slot, then fetch url with up to max_retries+1 attempts.
        On success, parse HTML and return PageRecord.
        On total failure, return an error PageRecord.

        html string is local — never stored on self or in any list.
        """
        async with sem:
            t0       = time.monotonic()
            last_err = "unknown error"

            for attempt in range(self.max_retries + 1):
                try:
                    async with session.get(url, allow_redirects=True, ssl=False) as resp:
                        rt_ms   = (time.monotonic() - t0) * 1000
                        headers = {
                            k.lower(): v
                            for k, v in resp.headers.items()
                            if k.lower() in _KEEP_HEADERS
                        }
                        status  = resp.status
                        ctype   = resp.headers.get("Content-Type", "")

                        if "text/html" not in ctype:
                            return PageRecord(
                                url=url, status_code=status,
                                response_time_ms=rt_ms, headers=headers,
                                crawl_depth=depth, is_error=True,
                                error=f"Non-HTML content-type: {ctype[:80]}",
                            )

                        # html is a local variable — exits scope when function returns
                        html   = await resp.text(errors="replace")
                        record = _parse_html(
                            url, status, html, headers, depth,
                            self._domain, self._bare_domain,
                        )
                        record.response_time_ms = rt_ms
                        return record   # html ref drops here

                except asyncio.TimeoutError:
                    last_err = f"timeout ({self.timeout_s}s)"
                    logger.debug("timeout [%d/%d] %s", attempt + 1,
                                 self.max_retries + 1, url)
                except aiohttp.TooManyRedirects:
                    last_err = "redirect loop"
                    break   # permanent — no point retrying
                except aiohttp.ClientConnectorError as exc:
                    last_err = f"connection error: {exc}"
                    logger.debug("connect [%d/%d] %s — %s",
                                 attempt + 1, self.max_retries + 1, url, exc)
                except aiohttp.ClientError as exc:
                    last_err = str(exc)
                    logger.debug("client [%d/%d] %s — %s",
                                 attempt + 1, self.max_retries + 1, url, exc)
                except Exception as exc:
                    last_err = str(exc)
                    logger.warning("unexpected [%d/%d] %s — %s",
                                   attempt + 1, self.max_retries + 1, url, exc)

                # Back off before next attempt (no sleep after final attempt)
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

            rt_ms = (time.monotonic() - t0) * 1000
            return PageRecord(
                url=url, status_code=0, response_time_ms=rt_ms,
                headers={}, crawl_depth=depth,
                is_error=True, error=last_err,
            )


# ── HTML parsing (module-level, stateless) ────────────────────────────────────

def _parse_html(
    url:         str,
    status:      int,
    html:        str,
    headers:     dict[str, str],
    depth:       int,
    domain:      str,
    bare_domain: str,
) -> PageRecord:
    """
    Parse html string into a PageRecord.

    html and the BeautifulSoup object are both local here.
    After this function returns, no reference to either is retained —
    CPython's reference counting frees them immediately.

    Extraction order matters:
      schema / hreflang / images must run BEFORE _body_text(),
      which calls tag.decompose() on <script>, <style>, etc.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title        = _extract_title(soup)
    meta_desc    = _extract_meta(soup, "description")
    canonical    = _extract_canonical(soup, url)
    meta_robots  = _extract_meta_robots(soup, headers)
    hreflang     = _extract_hreflang(soup)
    schema       = _extract_schema_jsonld(soup)   # before _body_text decomposes <script>
    images       = _extract_images(soup)
    links        = _extract_links(soup, url, domain, bare_domain)
    headings     = _extract_headings(soup)
    body_text    = _extract_body_text(soup)       # decomposes noise tags — run last

    # soup, html go out of scope; GC reclaims the memory
    is_err = status >= 400
    return PageRecord(
        url=url, status_code=status, response_time_ms=0.0,
        headers=headers, crawl_depth=depth,
        title=title, meta_description=meta_desc,
        headings=headings, body_text=body_text,
        links=links, images=images, schema=schema,
        hreflang=hreflang, meta_robots=meta_robots,
        canonical=canonical,
        is_error=is_err,
        error=f"HTTP {status}" if is_err else None,
    )


# ── Field extractors ──────────────────────────────────────────────────────────

def _extract_title(soup: BeautifulSoup) -> str:
    tag = soup.find("title")
    return tag.get_text(strip=True) if tag else ""


def _extract_meta(soup: BeautifulSoup, name: str) -> str:
    tag = (
        soup.find("meta", attrs={"name": name}) or
        soup.find("meta", attrs={"name": name.capitalize()}) or
        soup.find("meta", attrs={"property": f"og:{name}"})
    )
    return (tag.get("content") or "").strip() if tag else ""


def _extract_canonical(soup: BeautifulSoup, page_url: str) -> str:
    tag  = soup.find("link", attrs={"rel": "canonical"})
    href = (tag.get("href") or "").strip() if tag else ""
    return urljoin(page_url, href) if href else ""


def _extract_meta_robots(soup: BeautifulSoup, headers: dict[str, str]) -> str:
    """Merge <meta name=robots> and X-Robots-Tag header into one string."""
    tag      = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    meta_val = (tag.get("content") or "").strip() if tag else ""
    hdr_val  = headers.get("x-robots-tag", "").strip()
    return ", ".join(v for v in (meta_val, hdr_val) if v)


def _extract_hreflang(soup: BeautifulSoup) -> list[dict]:
    """Collect all <link rel="alternate" hreflang="…"> declarations."""
    results: list[dict] = []
    for tag in soup.find_all("link", rel=True):
        rel = tag.get("rel") or []
        if "alternate" not in [r.lower() if isinstance(r, str) else r for r in rel]:
            continue
        lang = (tag.get("hreflang") or "").strip()
        href = (tag.get("href") or "").strip()
        if lang and href:
            results.append({"lang": lang, "href": href})
    return results


def _extract_schema_jsonld(soup: BeautifulSoup) -> list[dict]:
    """
    Extract JSON-LD objects from all <script type="application/ld+json"> tags.
    Handles both single objects and @graph arrays.
    Must run before _extract_body_text(), which decomposes <script> tags.
    """
    results: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = (script.string or "").strip()
            if not raw:
                continue
            obj = json.loads(raw)
            if isinstance(obj, dict):
                items = obj.get("@graph", [obj])
            elif isinstance(obj, list):
                items = obj
            else:
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type")
                entry: dict = {
                    "type":  t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else None),
                    "props": [k for k in item if not k.startswith("@")],
                }
                if "name" in item:
                    entry["name"] = str(item["name"])[:200]
                if "description" in item:
                    entry["description"] = str(item["description"])[:200]
                # Hoist aggregateRating for quick access
                ar = item.get("aggregateRating")
                if isinstance(ar, dict):
                    try:
                        entry["rating_value"] = float(ar.get("ratingValue") or 0)
                        entry["review_count"]  = int(
                            ar.get("reviewCount") or ar.get("ratingCount") or 0
                        )
                    except (ValueError, TypeError):
                        pass
                results.append(entry)
        except (json.JSONDecodeError, Exception):
            pass
    return results


def _extract_images(soup: BeautifulSoup) -> list[dict]:
    """
    Extract image metadata: src, alt, width, height.
    Also checks data-src / data-lazy-src for lazy-loaded images.
    Capped at 50 images per page.
    """
    results: list[dict] = []
    for img in soup.find_all("img"):
        src = (
            (img.get("src") or "").strip() or
            (img.get("data-src") or "").strip() or
            (img.get("data-lazy-src") or "").strip()
        )
        if not src or src.startswith("data:"):
            continue
        results.append({
            "src":    src,
            "alt":    (img.get("alt")    or "").strip(),
            "width":  (img.get("width")  or "").strip(),
            "height": (img.get("height") or "").strip(),
        })
        if len(results) >= 50:
            break
    return results


def _extract_links(
    soup:        BeautifulSoup,
    page_url:    str,
    domain:      str,
    bare_domain: str,
) -> list[dict]:
    """
    Extract all hyperlinks with anchor text.

    Returns both internal and external links so the SEO pipeline has full
    link-graph data.  BFS enqueueing is handled separately by CrawlEngine.

    Deduplicates by normalized href (fragment stripped).
    First-seen anchor text wins per href.
    Capped at 500 links per page.
    """
    seen:    set[str]   = set()
    results: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        try:
            full   = urljoin(page_url, href)
            parsed = urlparse(full)
            if parsed.scheme not in ("http", "https"):
                continue
            clean = parsed._replace(fragment="").geturl()
            if clean in seen:
                continue
            seen.add(clean)
            results.append({
                "href":        clean,
                "anchor_text": a.get_text(strip=True)[:200],
            })
        except Exception:
            pass

        if len(results) >= 500:
            break

    return results


def _extract_headings(soup: BeautifulSoup) -> list[dict]:
    """
    Extract H1–H6 in DOM order.
    Ordered list preserves document outline for heading-flow analysis.
    """
    results: list[dict] = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$", re.I)):
        text = tag.get_text(strip=True)
        if text:
            results.append({"level": int(tag.name[1]), "text": text[:300]})
    return results


def _extract_body_text(soup: BeautifulSoup) -> str:
    """
    Extract visible body text (max 5000 chars).

    Decomposes noise tags before extraction.  Must run LAST — decompose()
    mutates the soup tree and would break any extractor that runs after it.
    """
    for tag in soup(["script", "style", "nav", "footer",
                     "header", "noscript", "iframe", "svg", "aside"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:5000]
