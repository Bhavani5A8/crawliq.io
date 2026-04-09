"""
competitor_analysis.py — CrawlIQ Competitor Analysis Engine (Phase 1 → 4)

════════════════════════════════════════════════════════════════════════════════
PHASE 1  (today, no paid APIs)
  ✓ Parallel multi-URL crawl (reuses existing SEOCrawler)
  ✓ PSI API — Core Web Vitals per URL (free, 25 k/day)
  ✓ On-page SEO scoring (title, meta, headings, canonical, schema)
  ✓ Technical SEO scoring (reuses technical_seo.py)
  ✓ Content depth scoring (word count, readability proxy, H-tag density)
  ✓ E-E-A-T rule-based scorer (12 signals → 0-100)
  ✓ CTR potential scorer (title/meta heuristics → 0-100)
  ✓ Keyword gap detection (TF-IDF cosine similarity)
  ✓ Composite weighted score (8 dimensions → 0-100)
  ✓ Radar chart data (all 8 dims normalised per-competitor)
  ✓ Action priority list (top 5 quick-win recommendations)
  ✓ SQLite persistence via competitor_db.py

PHASE 2  (scaffold — SERP + CTR intelligence)
  ○ Live SERP scrape for keyword ranking positions
  ○ CTR prediction model (XGBoost / rule-based on title features)
  ○ Keyword ROI scoring (volume × difficulty inverse)
  ○ Featured snippet / PAA detection

PHASE 3  (scaffold — persistence + scheduling)
  ○ Scheduled monitoring (APScheduler)
  ○ Ranking velocity (position Δ over 7/30/90 days)
  ○ CTR decay detection from GSC API
  ○ Automated alert webhooks

PHASE 4  (scaffold — production hardening)
  ○ Proxy rotation for SERP scraping
  ○ Redis TTL cache for PSI results
  ○ PDF report export (WeasyPrint)
  ○ Webhook push on ranking change

Public API
──────────
  run_competitor_analysis(task_id, target_url, competitor_urls)
      → Async entry point. Orchestrates full pipeline, persists to DB.
        Call from FastAPI background task.

  get_analysis_result(task_id)
      → Returns DB snapshot dict (status + metrics).
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from collections import Counter
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# ── Optional deps (graceful fallback) ────────────────────────────────────────
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    _SKLEARN = True
except ImportError:
    _SKLEARN = False
    logger.info("sklearn not installed — keyword gap uses frequency fallback")

# curl_cffi — Chrome TLS fingerprint impersonation, bypasses Cloudflare/Akamai
try:
    from curl_cffi.requests import AsyncSession as _CffiSession
    _CFFI = True
    logger.info("curl_cffi available — Cloudflare bypass enabled")
except Exception as _cffi_err:
    _CffiSession = None  # type: ignore
    _CFFI = False
    logger.warning("curl_cffi unavailable — Cloudflare bypass disabled (%s: %s)",
                   type(_cffi_err).__name__, _cffi_err)

# ── Internal project imports ──────────────────────────────────────────────────
from competitor_db import (
    save_snapshot, update_snapshot, get_snapshot,
    save_cwv, save_keyword_rankings,
)
from crawler import SEOCrawler, crawl_results as _shared_crawl_results
from technical_seo import analyze_page as _tseo_page
from issues import detect_issues
from keyword_extractor import extract_keywords_corpus

# Serialise all competitor crawls so they don't clobber the shared crawl_results.
# Each crawl: clear → run → snapshot → clear.
# Concurrent dashboard crawls are blocked during this window (same as normal crawl).
_comp_crawl_lock = asyncio.Lock()


# ── Constants ─────────────────────────────────────────────────────────────────

PSI_API_URL   = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
PSI_API_KEY   = os.getenv("PSI_API_KEY", "")          # optional; free without key
PSI_TIMEOUT   = 55   # seconds per PSI call — Lighthouse can take 40-50s on slow sites
CRAWL_TIMEOUT = 60                                      # seconds per site crawl
MAX_CRAWL_PAGES = int(os.getenv("COMP_MAX_PAGES", "15"))  # per competitor

# Dimension weights for composite score
_WEIGHTS = {
    "technical":    0.20,
    "on_page":      0.20,
    "content":      0.15,
    "eeat":         0.15,
    "ctr":          0.10,
    "keywords":     0.10,
    "page_speed":   0.10,
}

# CTR power words
_POWER_WORDS = {
    "best", "top", "free", "ultimate", "guide", "complete", "expert",
    "proven", "easy", "fast", "simple", "new", "official", "trusted",
    "review", "tips", "how", "why", "what", "vs",
}
_CTA_WORDS = {
    "learn", "get", "find", "discover", "explore", "try", "start",
    "download", "buy", "shop", "see", "read", "view", "compare",
}

# E-E-A-T trusted domains (for citation signal)
_AUTHORITY_TLDS = {".edu", ".gov", ".org"}
_AUTHORITY_DOMAINS = {
    "wikipedia.org", "scholar.google.com", "pubmed.ncbi.nlm.nih.gov",
    "bbc.com", "reuters.com", "nytimes.com", "forbes.com",
}

# Stopwords (minimal, same as keyword_extractor)
_SW = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "by","is","are","was","were","be","been","have","has","had","do","does",
    "did","will","would","could","should","may","might","can","not","no",
    "this","that","these","those","it","its","he","she","they","we","you",
    "from","into","about","than","more","also","just","only","very","so",
    "com","org","net","www","http","https","page","site","click","read",
}


# ════════════════════════════════════════════════════════════════════════════
# ── SECTION 1: PSI API (Core Web Vitals) ────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

async def _fetch_psi(url: str, strategy: str = "mobile") -> dict:
    """
    Call Google PageSpeed Insights API for one URL.
    Requests BOTH performance + seo categories so we can score blocked sites
    from Lighthouse audit data alone (Google's Chrome bypasses Cloudflare).

    Returns normalised CWV dict + raw SEO audits. Never raises.
    Free quota: 25,000 requests/day without key, 100 k/day with PSI_API_KEY.
    """
    # Use list-of-tuples so aiohttp sends two separate category= params
    params_list: list[tuple] = [
        ("url",      url),
        ("strategy", strategy),
        ("category", "performance"),
        ("category", "seo"),         # ← gives us full Lighthouse SEO audits
    ]
    if PSI_API_KEY:
        params_list.append(("key", PSI_API_KEY))

    timeout = aiohttp.ClientTimeout(total=PSI_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(PSI_API_URL, params=params_list) as resp:
                if resp.status == 429:
                    logger.warning(
                        "PSI API rate-limited (429) for %s. "
                        "Set PSI_API_KEY env var for higher quota.", url)
                    return {}
                if resp.status != 200:
                    logger.warning("PSI API HTTP %d for %s", resp.status, url)
                    return {}
                data = await resp.json()

        lhr    = data.get("lighthouseResult", {})
        cats   = lhr.get("categories", {})
        audits = lhr.get("audits", {})
        le     = data.get("loadingExperience", {})

        def _num(key: str) -> float | None:
            v = audits.get(key, {}).get("numericValue")
            return round(float(v), 1) if v is not None else None

        def _pass(key: str) -> bool:
            """True if Lighthouse audit passed (score == 1)."""
            return audits.get(key, {}).get("score") == 1

        # ── Core SEO audit IDs we use for fallback scoring ────────────────
        _SEO_AUDIT_IDS = [
            "document-title", "meta-description", "heading-order",
            "link-text", "crawlable-anchors", "is-crawlable",
            "robots-txt", "canonical", "structured-data", "hreflang",
            "image-alt", "font-size", "tap-targets", "plugins",
        ]
        seo_audits: dict[str, dict] = {}
        for aid in _SEO_AUDIT_IDS:
            a = audits.get(aid, {})
            seo_audits[aid] = {
                "score":        a.get("score"),          # 1=pass 0=fail None=n/a
                "numericValue": a.get("numericValue"),
                "displayValue": a.get("displayValue", ""),
            }
        # DOM size from performance audits (node count ≈ content richness)
        dom_audit = audits.get("dom-size", {})
        seo_audits["dom-size"] = {
            "score":        dom_audit.get("score"),
            "numericValue": dom_audit.get("numericValue"),  # total DOM nodes
            "displayValue": dom_audit.get("displayValue", ""),
        }

        result = {
            "perf_score":      round((cats.get("performance", {}).get("score") or 0) * 100, 1),
            "seo_score":       round((cats.get("seo",         {}).get("score") or 0) * 100, 1),
            "lcp_ms":          _num("largest-contentful-paint"),
            "inp_ms":          _num("total-blocking-time"),
            "cls":             _num("cumulative-layout-shift"),
            "fcp_ms":          _num("first-contentful-paint"),
            "ttfb_ms":         _num("server-response-time"),
            "speed_index_ms":  _num("speed-index"),
            "tti_ms":          _num("interactive"),
            "field_data":      le.get("overall_category", "UNKNOWN"),
            "strategy":        strategy,
            "_seo_audits":     seo_audits,   # raw audits for fallback scoring
        }

        lcp = result["lcp_ms"]
        result["lcp_status"] = (
            "Good" if lcp and lcp < 2500 else
            "Needs Improvement" if lcp and lcp < 4000 else
            "Poor" if lcp else "Unknown"
        )
        cls_ = result["cls"]
        result["cls_status"] = (
            "Good" if cls_ is not None and cls_ < 0.1 else
            "Needs Improvement" if cls_ is not None and cls_ < 0.25 else
            "Poor" if cls_ is not None else "Unknown"
        )

        logger.info("PSI done: %s  perf=%.0f  seo=%.0f  lcp=%.0fms",
                    url, result["perf_score"], result["seo_score"], lcp or 0)
        return result

    except asyncio.TimeoutError:
        logger.warning("PSI timeout for %s", url)
        return {}
    except Exception as exc:
        logger.warning("PSI error for %s: %s", url, exc)
        return {}


async def fetch_psi_all(urls: list[str], strategy: str = "mobile") -> dict[str, dict]:
    """
    Fetch PSI data for all URLs concurrently (max 3 at a time to stay polite).
    Returns {url: cwv_dict}.
    """
    sem = asyncio.Semaphore(3)

    async def _bounded(url: str) -> tuple[str, dict]:
        async with sem:
            return url, await _fetch_psi(url, strategy)

    tasks = [_bounded(u) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, dict] = {}
    for item in results:
        if isinstance(item, tuple):
            out[item[0]] = item[1]
        else:
            logger.debug("PSI gather error: %s", item)
    return out


# ════════════════════════════════════════════════════════════════════════════
# ── SECTION 2: SITE CRAWLING ─────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

def _normalize_url(url: str) -> str:
    """
    Normalize any URL to its root domain homepage.
    Strips subpaths so we always crawl/score the site root.
    e.g. https://proprofs.com/mock-test-series-online → https://proprofs.com/
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


async def _crawl_site(url: str) -> list[dict]:
    """
    Crawl one site up to MAX_CRAWL_PAGES using the existing SEOCrawler.

    The crawler writes to the module-level crawl_results list (shared state).
    We serialise all competitor crawls through _comp_crawl_lock so they
    never interleave, then snapshot and clear after each run.

    Returns a private list of page dicts. Never raises.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    async with _comp_crawl_lock:
        # Clear shared store before this crawl
        _shared_crawl_results.clear()
        try:
            await asyncio.wait_for(
                SEOCrawler(url, max_pages=MAX_CRAWL_PAGES).crawl_async(),
                timeout=CRAWL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Comp crawl timeout for %s (got %d pages)",
                           url, len(_shared_crawl_results))
        except Exception as exc:
            logger.warning("Comp crawl failed for %s: %s", url, exc)

        # Snapshot and clear so the next crawl starts clean
        pages = list(_shared_crawl_results)
        _shared_crawl_results.clear()

    return pages


async def _crawl_site_safe(url: str) -> tuple[str, list[dict]]:
    """Wrapper that always returns (url, pages) regardless of errors."""
    pages = await _crawl_site(url)
    return url, pages


# ── curl_cffi Cloudflare-bypass crawler ──────────────────────────────────────

_CFFI_IMPERSONATE = "chrome120"   # TLS fingerprint to impersonate
_CFFI_MAX_PAGES   = 8             # pages to fetch per blocked site
_CFFI_TIMEOUT     = 20            # seconds per request

# Priority paths to try after homepage — improves E-E-A-T scoring
_EEAT_PATHS = ["/about", "/about-us", "/contact", "/team", "/privacy-policy",
               "/privacy", "/blog", "/authors", "/staff"]


def _parse_html_to_page(url: str, html: str) -> dict:
    """
    Parse raw HTML into the standard page-dict format expected by all scorers.
    Matches the fields produced by SEOCrawler so all scoring functions work
    without modification.
    """
    soup = BeautifulSoup(html, "lxml")

    # Title
    title_tag = soup.find("title")
    title = title_tag.get_text().strip() if title_tag else ""

    # Meta description
    meta_desc = ""
    for attr in ({"name": "description"}, {"property": "og:description"}):
        tag = soup.find("meta", attrs=attr)
        if tag and tag.get("content"):
            meta_desc = tag["content"].strip()
            break

    # Headings
    h1 = [h.get_text().strip() for h in soup.find_all("h1")]
    h2 = [h.get_text().strip() for h in soup.find_all("h2")]
    h3 = [h.get_text().strip() for h in soup.find_all("h3")]

    # Canonical
    canonical = ""
    can_tag = soup.find("link", rel="canonical")
    if can_tag:
        canonical = can_tag.get("href", "")

    # Schema markup
    schema_tags  = soup.find_all("script", type="application/ld+json")
    schema_types = []
    for st in schema_tags:
        try:
            import json as _json
            obj = _json.loads(st.string or "{}")
            t = obj.get("@type")
            if t:
                schema_types.append(t if isinstance(t, str) else str(t))
        except Exception:
            pass
    has_schema = bool(schema_tags)

    # Body text — strip nav/footer/script/style noise
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    body_text = soup.get_text(" ", strip=True)

    # Images without alt
    images_without_alt = sum(
        1 for img in soup.find_all("img")
        if not img.get("alt", "").strip()
    )

    # Internal links (for multi-page crawl)
    parsed_base = urlparse(url)
    base_domain = parsed_base.netloc
    internal_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(url, href)
        p = urlparse(full)
        if p.netloc == base_domain and p.path not in ("", "/"):
            internal_links.append(full)

    return {
        "url":                url,
        "status_code":        200,
        "title":              title,
        "meta_description":   meta_desc,
        "h1":                 h1,
        "h2":                 h2,
        "h3":                 h3,
        "canonical":          canonical,
        "schema_types":       schema_types,
        "has_schema":         has_schema,
        "body_text":          body_text[:8000],
        "word_count":         len(body_text.split()),
        "images_without_alt": images_without_alt,
        "internal_links":     list(dict.fromkeys(internal_links))[:60],
        "keywords":           [],
        "issues":             [],
    }


async def _cffi_get(session, url: str) -> str | None:
    """
    Fetch one URL using the shared cffi session.
    Returns HTML string or None on failure.
    """
    try:
        resp = await session.get(
            url,
            impersonate=_CFFI_IMPERSONATE,
            timeout=_CFFI_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            return resp.text
        logger.debug("cffi %d for %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("cffi error for %s: %s", url, exc)
    return None


async def _crawl_site_cffi(url: str) -> list[dict]:
    """
    Crawl a Cloudflare-protected site using Chrome TLS impersonation.

    Strategy:
      1. Fetch homepage
      2. Try known E-E-A-T paths (/about, /contact, /privacy, /blog …)
      3. Follow up to _CFFI_MAX_PAGES internal links from the homepage

    Returns page-dicts compatible with all scoring functions.
    Never raises.
    """
    if not _CFFI:
        return []

    pages: list[dict] = []
    visited: set[str] = set()

    try:
        async with _CffiSession() as sess:
            # ── 1. Homepage ───────────────────────────────────────────────
            html = await _cffi_get(sess, url)
            if not html:
                logger.warning("cffi: homepage fetch failed for %s", url)
                return []

            home_page = _parse_html_to_page(url, html)
            pages.append(home_page)
            visited.add(url)
            logger.info("cffi: homepage OK for %s (%d words)",
                        url, home_page.get("word_count", 0))

            # ── 2. Priority E-E-A-T paths ─────────────────────────────────
            base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            priority_urls = [base + path for path in _EEAT_PATHS]
            for purl in priority_urls:
                if purl in visited:
                    continue
                await asyncio.sleep(0.3)   # polite delay
                ph = await _cffi_get(sess, purl)
                if ph:
                    pages.append(_parse_html_to_page(purl, ph))
                    visited.add(purl)
                if len(pages) >= _CFFI_MAX_PAGES:
                    break

            # ── 3. Internal links from homepage ───────────────────────────
            if len(pages) < _CFFI_MAX_PAGES:
                for link in home_page.get("internal_links", []):
                    if link in visited:
                        continue
                    await asyncio.sleep(0.3)
                    lh = await _cffi_get(sess, link)
                    if lh:
                        pages.append(_parse_html_to_page(link, lh))
                        visited.add(link)
                    if len(pages) >= _CFFI_MAX_PAGES:
                        break

    except Exception as exc:
        logger.warning("cffi crawl error for %s: %s", url, exc)

    logger.info("cffi crawl done for %s: %d pages", url, len(pages))
    return pages


# ════════════════════════════════════════════════════════════════════════════
# ── SECTION 3: SCORING MODELS ────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

def score_on_page(pages: list[dict]) -> float:
    """
    On-page SEO score for a site (0–100) based on the home/best page.
    Signals: title, meta, H1, H2, canonical, schema, URL quality.

    Uses the home page (first 200-status page) as representative.
    Falls back to best-scoring page if home is unavailable.
    """
    real = [p for p in pages if p.get("status_code") == 200]
    if not real:
        return 0.0

    # Pick the best-scoring page as representative
    def _pg_score(p: dict) -> int:
        s = 0
        if p.get("title"):             s += 15
        t = p.get("title", "") or ""
        if 50 <= len(t) <= 60:         s += 10
        if p.get("meta_description"):  s += 15
        m = p.get("meta_description", "") or ""
        if 120 <= len(m) <= 155:       s += 10
        if p.get("h1"):                s += 15
        if p.get("h2"):                s += 10
        if p.get("canonical"):         s += 10
        if not p.get("issues"):        s += 15
        return s

    best = max(real, key=_pg_score)
    return float(_pg_score(best))


def score_content(pages: list[dict]) -> float:
    """
    Content depth score (0–100).
    Aggregates word count, heading structure, and body richness.
    """
    real = [p for p in pages if p.get("status_code") == 200]
    if not real:
        return 0.0

    scores = []
    for p in real[:10]:   # evaluate up to 10 pages
        s = 0
        wc = _word_count(p)
        if wc >= 1500:   s += 30
        elif wc >= 800:  s += 20
        elif wc >= 300:  s += 10
        elif wc > 0:     s += 5

        h2 = p.get("h2") or []
        if len(h2) >= 4:   s += 20
        elif len(h2) >= 2: s += 12
        elif len(h2) >= 1: s += 6

        h3 = p.get("h3") or []
        if len(h3) >= 3:   s += 15
        elif len(h3) >= 1: s += 8

        # Readability proxy: avg sentence length (shorter = more readable)
        body = (p.get("body_text") or "")[:3000]
        if body:
            sents = [x for x in re.split(r"[.!?]", body) if x.strip()]
            avg_len = sum(len(s_.split()) for s_ in sents) / max(len(sents), 1)
            if avg_len <= 18:  s += 20
            elif avg_len <= 25: s += 12
            else:               s += 5

        if p.get("body_text"):  s += 15
        scores.append(min(s, 100))

    return round(sum(scores) / len(scores), 1) if scores else 0.0


def _word_count(page: dict) -> int:
    body = page.get("body_text") or ""
    if not body:
        # Estimate from title + meta + headings
        parts = [
            page.get("title") or "",
            page.get("meta_description") or "",
            " ".join(page.get("h1") or []),
            " ".join(page.get("h2") or []),
        ]
        body = " ".join(parts)
    return len(body.split())


def score_eeat(pages: list[dict], site_url: str) -> float:
    """
    E-E-A-T rule-based score (0–100) using 12 signals extracted from crawled pages.

    Signal                          Weight
    ─────────────────────────────────────
    Has author bio / byline          +10
    Has About page                   +10
    Has Contact page                 +10
    Has Privacy Policy page           +8
    Has schema:author                +10
    External links to .edu/.gov       +8
    Has review schema (rating)        +8
    Avg rating ≥ 4.0                  +5
    HTTPS enforced                    +8
    Word count ≥ 1000 (avg)           +8
    Citation links to authorities     +7
    Has team / people page            +8
    ─────────────────────────────────────
    Total possible                   100
    """
    if not pages:
        return 0.0

    real = [p for p in pages if p.get("status_code") == 200]
    if not real:
        return 0.0

    score = 0.0
    all_urls_lower = [p.get("url", "").lower() for p in real]

    # About page
    if any("about" in u for u in all_urls_lower):
        score += 10

    # Contact page
    if any("contact" in u for u in all_urls_lower):
        score += 10

    # Privacy policy
    if any("privacy" in u or "policy" in u for u in all_urls_lower):
        score += 8

    # Team / people page
    if any("team" in u or "people" in u or "staff" in u or "author" in u
           for u in all_urls_lower):
        score += 8

    # HTTPS
    if site_url.startswith("https://"):
        score += 8

    # Check home page body text for signals
    home = next(
        (p for p in real if _strip_path(p.get("url", "")) == _strip_path(site_url)),
        real[0]
    )

    body_all = " ".join(
        (p.get("body_text") or p.get("title") or "") for p in real[:5]
    ).lower()

    # Author bio signals
    if any(kw in body_all for kw in ["author", "written by", "contributor", "byline"]):
        score += 10

    # Schema: author (look in body_text for JSON-LD markers)
    if '"author"' in body_all or 'itemprop="author"' in body_all:
        score += 10

    # Review schema
    has_review = any(
        '"ratingvalue"' in (p.get("body_text") or "").lower() or
        'itemprop="ratingvalue"' in (p.get("body_text") or "").lower()
        for p in real[:5]
    )
    if has_review:
        score += 8

    # Citation links to authoritative sources
    # (heuristic: look for known authority domain mentions in body)
    if any(d in body_all for d in _AUTHORITY_DOMAINS):
        score += 7

    # External edu/gov links (heuristic: look for .edu/.gov in body text)
    if any(tld in body_all for tld in _AUTHORITY_TLDS):
        score += 8

    # Average word count ≥ 1000
    avg_wc = sum(_word_count(p) for p in real) / len(real)
    if avg_wc >= 1000:
        score += 8

    return min(round(score, 1), 100.0)


def _strip_path(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def score_ctr_potential(pages: list[dict]) -> float:
    """
    CTR potential score (0–100) based on title and meta description quality.
    Evaluated on the best-optimised page found.

    Signal                         Max
    ──────────────────────────────────
    Title length 50-60 chars        20
    Title has power word            15
    Title has number                15
    Title has current year          10
    Meta has CTA verb               20
    Meta length 120-155 chars       20
    ──────────────────────────────────
    Total                          100
    """
    real = [p for p in pages if p.get("status_code") == 200 and p.get("title")]
    if not real:
        return 0.0

    import datetime
    this_year = str(datetime.datetime.now().year)

    best = 0.0
    for p in real[:10]:
        s = 0.0
        title = (p.get("title") or "").strip()
        meta  = (p.get("meta_description") or "").strip()
        tl    = title.lower()

        # Title length
        tlen = len(title)
        if 50 <= tlen <= 60:    s += 20
        elif 40 <= tlen <= 70:  s += 12
        elif title:             s += 5

        # Power word
        if any(w in tl.split() for w in _POWER_WORDS):
            s += 15

        # Number in title
        if re.search(r"\d", title):
            s += 15

        # Year in title
        if this_year in title or str(int(this_year) - 1) in title:
            s += 10

        # Meta CTA
        ml = meta.lower()
        if any(w in ml for w in _CTA_WORDS):
            s += 20

        # Meta length
        mlen = len(meta)
        if 120 <= mlen <= 155:   s += 20
        elif 100 <= mlen <= 170: s += 12
        elif meta:               s += 5

        best = max(best, s)

    return min(round(best, 1), 100.0)


def score_keywords(pages: list[dict]) -> float:
    """
    Keyword coverage score (0–100).
    Measures breadth and density of meaningful keywords across the site.
    """
    real = [p for p in pages if p.get("status_code") == 200]
    if not real:
        return 0.0

    # Count unique keywords across site
    all_kws: set[str] = set()
    for p in real:
        for k in (p.get("keywords") or []):
            kw = k if isinstance(k, str) else k.get("keyword", "")
            if kw:
                all_kws.add(kw.lower())

    # Keyword coverage scoring
    kw_count = len(all_kws)
    s = 0.0
    if kw_count >= 50:    s += 40
    elif kw_count >= 20:  s += 30
    elif kw_count >= 10:  s += 20
    elif kw_count >= 5:   s += 10

    # Pages with keywords
    pages_with_kw = sum(1 for p in real if p.get("keywords"))
    ratio = pages_with_kw / len(real)
    s += ratio * 30

    # Average keyword importance (HIGH = more weight)
    high_kw_pages = sum(
        1 for p in real
        if any(
            k.get("importance") == "HIGH"
            for k in (p.get("keywords_scored") or [])
            if isinstance(k, dict)
        )
    )
    s += (high_kw_pages / max(len(real), 1)) * 30

    return min(round(s, 1), 100.0)


def score_page_speed(cwv: dict) -> float:
    """
    Page speed score (0–100) from PSI data.
    Uses Lighthouse performance score as primary signal.
    Supplements with LCP and CLS if performance score is missing.

    NOTE: cwv={} (PSI failed / rate-limited) must NOT return 0 — that would
    falsely label the site as having terrible performance.  We fall through to
    the LCP/CLS fallback which returns 50 (neutral/unknown) so the composite
    score and chart are not corrupted by a missing API response.
    Only a hard None (caller passed no dict at all) returns 0.
    """
    if cwv is None:
        return 0.0

    perf = cwv.get("perf_score")
    if perf is not None:
        return float(perf)

    # Fallback: derive from LCP + CLS (also covers PSI-failed empty-dict case)
    s = 50.0  # neutral baseline — we don't know speed, so don't penalise
    lcp = cwv.get("lcp_ms")
    if lcp is not None:
        if lcp < 2500:    s += 20
        elif lcp < 4000:  s += 5
        else:             s -= 20

    cls_ = cwv.get("cls")
    if cls_ is not None:
        if cls_ < 0.1:   s += 10
        elif cls_ < 0.25: s += 0
        else:             s -= 10

    return min(max(round(s, 1), 0.0), 100.0)


def score_technical(pages: list[dict]) -> float:
    """
    Technical SEO score (0–100) using existing technical_seo.py analyzer.
    Returns average tech_score across all real pages.
    """
    real = [p for p in pages if p.get("status_code") == 200]
    if not real:
        return 0.0
    scores = []
    for p in real[:15]:  # cap at 15 pages for speed
        try:
            result = _tseo_page(p)
            scores.append(result.get("tech_score", 0))
        except Exception:
            pass
    return round(sum(scores) / len(scores), 1) if scores else 0.0


# ════════════════════════════════════════════════════════════════════════════
# ── SECTION 4: KEYWORD GAP ANALYSIS ─────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

def compute_keyword_gap(
    target_pages: list[dict],
    competitor_pages_map: dict[str, list[dict]],
) -> list[dict]:
    """
    Identify keywords that 2+ competitors rank for but target site doesn't.

    Returns sorted list of gap opportunities:
    [
      {
        "keyword": str,
        "found_in": [domain1, domain2],
        "competitor_count": int,
        "opportunity_score": float,  # 0-100
      }
    ]

    Strategy:
      - Extract keywords from all pages using TF-IDF corpus method
      - Target keywords = union across all target site pages
      - Gap keywords = in competitors but NOT in target (≥2 competitors)
      - Opportunity score = competitor_count × keyword_length_bonus
    """
    # Collect target keywords
    target_kws: set[str] = set()
    for p in target_pages:
        for k in (p.get("keywords") or []):
            kw = k if isinstance(k, str) else k.get("keyword", "")
            if kw:
                target_kws.add(kw.lower())

    # Collect competitor keywords per domain
    comp_kw_map: dict[str, set[str]] = {}
    for domain, pages in competitor_pages_map.items():
        kws: set[str] = set()
        for p in pages:
            for k in (p.get("keywords") or []):
                kw = k if isinstance(k, str) else k.get("keyword", "")
                if kw:
                    kws.add(kw.lower())
        comp_kw_map[domain] = kws

    # Find gaps: keywords in competitors but not in target
    kw_domain_count: dict[str, list[str]] = {}
    for domain, kws in comp_kw_map.items():
        for kw in kws:
            if kw not in target_kws and len(kw) > 3:
                kw_domain_count.setdefault(kw, []).append(domain)

    # Score by how many competitors have it + keyword quality
    # Only include keywords found in 2+ competitors (as stated in docstring).
    # With 1 competitor submitted, lower threshold to 1 so the report isn't empty.
    _min_comp = 2 if len(comp_kw_map) >= 2 else 1
    gaps = []
    for kw, domains in kw_domain_count.items():
        if len(domains) < _min_comp:
            continue
        # Longer multi-word phrases = higher opportunity (more specific).
        # Coverage = fraction of competitors that have this keyword (0-1).
        # Normalise relative to the minimum required so a keyword in all
        # competitors always scores the full 70 base regardless of pool size.
        words = kw.split()
        length_bonus = min(len(words) * 10, 30)
        coverage = len(domains) / max(len(comp_kw_map), 1)
        opp_score = min(coverage * 70 + length_bonus, 100)
        gaps.append({
            "keyword":          kw,
            "found_in":         domains,
            "competitor_count": len(domains),
            "opportunity_score": round(opp_score, 1),
        })

    # Sort: most competitors first, then by opportunity score
    gaps.sort(key=lambda x: (-x["competitor_count"], -x["opportunity_score"]))
    return gaps[:50]  # return top 50 gaps


def compute_semantic_similarity(
    target_pages: list[dict],
    competitor_pages_map: dict[str, list[dict]],
) -> dict[str, float]:
    """
    Compute TF-IDF cosine similarity between target and each competitor.
    Returns {domain: similarity_score (0-100)}.
    Used as content depth comparison proxy.
    """
    if not _SKLEARN:
        return {}

    def _site_text(pages: list[dict]) -> str:
        parts = []
        for p in pages[:10]:
            parts.extend([
                p.get("title") or "",
                p.get("meta_description") or "",
                " ".join(p.get("h1") or []),
                " ".join(p.get("h2") or []),
                (p.get("body_text") or "")[:1000],
            ])
        return " ".join(parts)

    if not competitor_pages_map:
        return {}

    target_text = _site_text(target_pages)
    if not target_text.strip():
        return {}

    docs = [target_text] + [
        _site_text(pages) for pages in competitor_pages_map.values()
    ]
    domains = ["__target__"] + list(competitor_pages_map.keys())

    if len(docs) < 2:
        # No competitor documents to compare against
        return {}

    try:
        vec = TfidfVectorizer(
            max_features=3000,
            stop_words="english",
            token_pattern=r"[a-zA-Z]{3,}",
            sublinear_tf=True,
        )
        matrix = vec.fit_transform(docs)
        target_vec = matrix[0]
        comp_matrix = matrix[1:]
        if comp_matrix.shape[0] == 0:
            return {}
        similarities = cosine_similarity(target_vec, comp_matrix)[0]
        return {
            domain: round(float(sim) * 100, 1)
            for domain, sim in zip(domains[1:], similarities)
        }
    except Exception as exc:
        logger.warning("Cosine similarity failed: %s", exc)
        return {}


# ════════════════════════════════════════════════════════════════════════════
# ── SECTION 5: COMPOSITE SCORE + RADAR DATA ──────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

def compute_composite(dims: dict[str, float]) -> float:
    """
    Weighted composite score (0–100) from 7 dimension scores.
    Dimensions: technical, on_page, content, eeat, ctr, keywords, page_speed
    """
    total = sum(
        dims.get(k, 0) * w
        for k, w in _WEIGHTS.items()
    )
    return round(total, 1)


def build_radar_data(site_scores: dict[str, dict[str, float]]) -> dict:
    """
    Build ECharts radar chart data from per-site dimension scores.

    site_scores = {
        "https://target.com":    {"technical": 72, "on_page": 65, ...},
        "https://competitor.com": {...},
    }

    Returns:
    {
      "indicators": [{"name": "Technical SEO", "max": 100}, ...],
      "series": [
        {"name": "target.com", "value": [72, 65, ...]},
        ...
      ]
    }
    """
    dim_labels = {
        "technical":  "Technical SEO",
        "on_page":    "On-Page SEO",
        "content":    "Content Depth",
        "eeat":       "E-E-A-T",
        "ctr":        "CTR Potential",
        "keywords":   "Keyword Coverage",
        "page_speed": "Page Speed",
    }
    dim_order = list(dim_labels.keys())

    indicators = [{"name": dim_labels[d], "max": 100} for d in dim_order]
    series = []
    for url, scores in site_scores.items():
        domain = urlparse(url).netloc or url
        values = [round(float(scores.get(d, 0)), 1) for d in dim_order]
        series.append({
            "name":   domain,
            "value":  values,
            "source": scores.get("score_source", "crawl"),
        })

    return {"indicators": indicators, "series": series}


def build_action_list(
    target_scores: dict[str, float],
    competitor_scores_map: dict[str, dict[str, float]],
) -> list[dict]:
    """
    Generate top-5 quick-win recommendations based on the biggest gaps
    between target scores and average competitor scores.

    Returns sorted list of action items:
    [
      {
        "dimension": str,
        "target_score": float,
        "avg_competitor_score": float,
        "gap": float,
        "priority": "High"|"Medium"|"Low",
        "action": str,  # human-readable recommendation
      }
    ]
    """
    _ACTION_TEMPLATES = {
        "technical":  "Fix technical SEO issues: improve robots.txt, canonicals, and indexability.",
        "on_page":    "Optimise title tags (50-60 chars), meta descriptions (120-155 chars), and H1 tags.",
        "content":    "Increase content depth: aim for 1,500+ words, add subheadings (H2/H3), improve readability.",
        "eeat":       "Build E-E-A-T: add author bios, About/Contact pages, cite authoritative sources.",
        "ctr":        "Improve CTR: add power words, numbers, and CTAs to titles and meta descriptions.",
        "keywords":   "Expand keyword coverage: target keyword gaps competitors rank for.",
        "page_speed": "Improve page speed: optimise LCP < 2.5s, reduce CLS < 0.1 (use PageSpeed Insights).",
    }

    if not competitor_scores_map:
        return []

    actions = []
    for dim, template in _ACTION_TEMPLATES.items():
        target = target_scores.get(dim, 0)
        comp_scores = [s.get(dim, 0) for s in competitor_scores_map.values()]
        avg_comp = sum(comp_scores) / len(comp_scores) if comp_scores else 0
        gap = avg_comp - target

        if gap > 5:  # only report meaningful gaps
            priority = "High" if gap >= 20 else "Medium" if gap >= 10 else "Low"
            actions.append({
                "dimension":            dim,
                "label":                dim.replace("_", " ").title(),
                "target_score":         round(target, 1),
                "avg_competitor_score": round(avg_comp, 1),
                "gap":                  round(gap, 1),
                "priority":             priority,
                "action":               template,
            })

    actions.sort(key=lambda x: -x["gap"])
    return actions[:5]


# ════════════════════════════════════════════════════════════════════════════
# ── SECTION 5b: PSI-DERIVED FALLBACK SCORING ─────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

def _score_from_psi(psi: dict, url: str) -> dict:
    """
    Derive all 7 dimension scores from PSI / Lighthouse data alone.

    Used when a site blocks our aiohttp crawler (Cloudflare, JS walls) but
    Google's own Chrome infrastructure (PSI) can still fetch it.

    Coverage per dimension:
      technical   — Lighthouse SEO audits: is-crawlable, robots-txt, canonical,
                    structured-data, hreflang, plugins
      on_page     — Lighthouse SEO audits: document-title, meta-description,
                    heading-order, link-text, image-alt, crawlable-anchors
      content     — DOM node count (dom-size audit) + font-size + tap-targets
      eeat        — TLD authority + structured-data + overall SEO score
      ctr         — title presence + meta presence + Lighthouse SEO score
      keywords    — title + meta signals (limited without body text)
      page_speed  — Lighthouse performance score (direct)
    """
    if not psi:
        return {}

    audits    = psi.get("_seo_audits", {})
    seo_score = psi.get("seo_score", 0) or 0  # Lighthouse SEO category 0-100

    def _pass(aid: str) -> bool:
        return audits.get(aid, {}).get("score") == 1

    # ── Technical SEO ─────────────────────────────────────────────────────
    tech = 0.0
    if _pass("is-crawlable"):    tech += 25   # not noindexed — biggest signal
    if _pass("robots-txt"):      tech += 20   # robots.txt valid
    if _pass("canonical"):       tech += 20   # canonical tag present
    if _pass("structured-data"): tech += 20   # schema markup present
    if _pass("hreflang"):        tech += 10   # hreflang (optional)
    if _pass("plugins"):         tech += 5    # no Flash/Java plugins
    tech = min(tech, 100.0)

    # ── On-Page SEO ───────────────────────────────────────────────────────
    on_page = 0.0
    if _pass("document-title"):     on_page += 25
    if _pass("meta-description"):   on_page += 25
    if _pass("heading-order"):      on_page += 20
    if _pass("link-text"):          on_page += 15
    if _pass("image-alt"):          on_page += 10
    if _pass("crawlable-anchors"):  on_page += 5
    on_page = min(on_page, 100.0)

    # ── Content Depth ─────────────────────────────────────────────────────
    # DOM node count is the best content-richness proxy available via PSI.
    # Lighthouse "good" threshold is ≤ 1500 nodes, but rich content pages
    # commonly have 2000–5000 nodes.
    dom_nodes = audits.get("dom-size", {}).get("numericValue") or 0
    content = 15.0   # baseline — page loads at all
    if dom_nodes >= 3000:   content += 40
    elif dom_nodes >= 1500: content += 30
    elif dom_nodes >= 600:  content += 15
    elif dom_nodes >= 200:  content += 5
    if _pass("font-size"):   content += 15   # legible text = real content
    if _pass("tap-targets"): content += 10   # structured layout
    if _pass("heading-order"): content += 10 # heading hierarchy = structured content
    content = min(content, 100.0)

    # ── E-E-A-T ───────────────────────────────────────────────────────────
    # Limited without crawl — use domain authority signals + SEO quality.
    parsed = urlparse(url)
    tld    = "." + (parsed.netloc.split(".")[-1] if parsed.netloc else "")
    eeat   = 15.0   # baseline
    if tld in _AUTHORITY_TLDS:       eeat += 35   # .edu/.gov/.org
    if _pass("structured-data"):     eeat += 20   # schema = trust signals
    if _pass("document-title"):      eeat += 10
    if _pass("meta-description"):    eeat += 10
    eeat += min(seo_score * 0.10, 10)  # overall SEO quality bonus (max 10)
    eeat = min(eeat, 100.0)

    # ── CTR Potential ─────────────────────────────────────────────────────
    ctr = 0.0
    if _pass("document-title"):    ctr += 35
    if _pass("meta-description"):  ctr += 35
    ctr += min(seo_score * 0.30, 30)  # Lighthouse SEO score bonus (max 30)
    ctr = min(ctr, 100.0)

    # ── Keywords ─────────────────────────────────────────────────────────
    # Very limited without body text — only title + meta signals available.
    kw = 0.0
    if _pass("document-title"):    kw += 25
    if _pass("meta-description"):  kw += 25
    kw += min(seo_score * 0.20, 20)   # SEO score proxy (max 20)
    kw = min(kw, 70.0)  # cap at 70 — we can't verify body keywords without crawl

    # ── Page Speed ────────────────────────────────────────────────────────
    page_speed = float(psi.get("perf_score") or 0)

    scores = {
        "technical":  round(tech, 1),
        "on_page":    round(on_page, 1),
        "content":    round(content, 1),
        "eeat":       round(eeat, 1),
        "ctr":        round(ctr, 1),
        "keywords":   round(kw, 1),
        "page_speed": round(page_speed, 1),
    }
    logger.info("PSI-derived scores for %s: %s", url, scores)
    return scores


# ════════════════════════════════════════════════════════════════════════════
# ── SECTION 6: MAIN ORCHESTRATOR ────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

async def run_competitor_analysis(
    task_id: str,
    target_url: str,
    competitor_urls: list[str],
) -> None:
    """
    Full Phase 1 competitor analysis pipeline.

    Steps:
      1. Mark task as running in DB
      2. Crawl all sites in parallel
      3. Run keyword extraction on all crawled pages
      4. Fetch PSI (CWV) for all homepage URLs in parallel
      5. Score all 7 dimensions per site
      6. Compute composite scores
      7. Compute keyword gaps
      8. Compute semantic similarity
      9. Build radar chart data + action list
     10. Persist full metrics to DB
     11. Mark task as done

    Never raises — all errors are caught and stored in DB.
    """
    # Normalize all URLs to root domains before anything else.
    # Strips subpaths like /mock-test-series-online so we crawl the homepage.
    target_url = _normalize_url(target_url)

    # Deduplicate competitors after normalization; also drop any that match target.
    _seen: set[str] = set()
    _deduped: list[str] = []
    for _u in competitor_urls:
        _n = _normalize_url(_u)
        if _n not in _seen and _n != target_url:
            _seen.add(_n)
            _deduped.append(_n)
    competitor_urls = _deduped

    update_snapshot(task_id, status="running")
    logger.info("[%s] Competitor analysis started: %s vs %d competitors",
                task_id, target_url, len(competitor_urls))

    t0 = time.time()

    try:
        all_urls = [target_url] + competitor_urls

        # ── Step 1: Sequential crawl (serialised through _comp_crawl_lock) ──
        # asyncio.gather submits all tasks, but _comp_crawl_lock inside
        # _crawl_site ensures only one site is crawled at a time — safe
        # against shared crawl_results corruption.
        crawl_tasks = [_crawl_site_safe(u) for u in all_urls]
        crawl_results = await asyncio.gather(*crawl_tasks, return_exceptions=True)

        pages_map: dict[str, list[dict]] = {}
        for item in crawl_results:
            if isinstance(item, tuple):
                url, pages = item
                pages_map[url] = pages
                logger.info("[%s] Crawled %s → %d pages", task_id, url, len(pages))
            else:
                logger.warning("[%s] Crawl gather error: %s", task_id, item)

        # Ensure we have at least empty lists for all URLs
        for u in all_urls:
            pages_map.setdefault(u, [])

        # Track which sites returned no usable pages (blocked / timed out)
        crawl_errors: dict[str, str] = {}
        for u in all_urls:
            real = [p for p in pages_map[u] if p.get("status_code") == 200]
            if not real:
                total = len(pages_map[u])
                msg = (
                    "0 pages crawled — site is blocking bots (Cloudflare/JS wall)"
                    if total == 0 else
                    f"{total} pages fetched but none returned HTTP 200"
                )
                crawl_errors[u] = msg
                logger.warning("[%s] Crawl blocked for %s: %s", task_id, u, msg)

        # ── Step 1b: curl_cffi bypass for Cloudflare-blocked sites ────────
        # curl_cffi impersonates Chrome's exact TLS fingerprint (JA3/JA4).
        # Cloudflare whitelists Chrome — this bypasses the bot-detection wall.
        if _CFFI and crawl_errors:
            logger.info("[%s] Trying curl_cffi bypass for %d blocked sites",
                        task_id, len(crawl_errors))
            for u in list(crawl_errors.keys()):
                cffi_pages = await _crawl_site_cffi(u)
                real_cffi  = [p for p in cffi_pages if p.get("status_code") == 200]
                if real_cffi:
                    pages_map[u] = cffi_pages
                    del crawl_errors[u]
                    logger.info("[%s] cffi bypass SUCCESS for %s — %d pages",
                                task_id, u, len(real_cffi))
                else:
                    logger.warning("[%s] cffi bypass FAILED for %s — PSI fallback next",
                                   task_id, u)

        # ── Step 2: Keyword extraction per site ───────────────────────────
        for url in all_urls:
            real = [p for p in pages_map[url] if p.get("status_code") == 200]
            if real:
                try:
                    detect_issues(real)
                    extract_keywords_corpus(real, top_n=10)
                except Exception as exc:
                    logger.warning("[%s] Keyword extraction failed for %s: %s",
                                   task_id, url, exc)
                    crawl_errors[url] = (
                        crawl_errors.get(url, "") +
                        f"; keyword extraction error: {exc}"
                    ).lstrip("; ")

        # ── Step 3: PSI API for all homepages ─────────────────────────────
        # Use the first 200-status page per site as the measured URL
        def _home_url(url: str, pages: list[dict]) -> str:
            real = [p for p in pages if p.get("status_code") == 200]
            return real[0]["url"] if real else url

        measure_urls = {u: _home_url(u, pages_map[u]) for u in all_urls}
        cwv_map = await fetch_psi_all(list(measure_urls.values()))
        # Re-key by original URL
        cwv_by_site: dict[str, dict] = {}
        for site_url, home in measure_urls.items():
            cwv_by_site[site_url] = cwv_map.get(home, {})

        # ── Step 4: Score all dimensions per site ─────────────────────────
        # Strategy:
        #   crawl succeeded → score from crawled pages (full accuracy)
        #   crawl blocked but PSI succeeded → derive all 7 dims from
        #     Lighthouse SEO audits (Google's Chrome bypasses Cloudflare)
        #   both failed → zeros with score_source="no_data"
        site_scores: dict[str, dict[str, float]] = {}
        for url in all_urls:
            pages = pages_map[url]
            cwv   = cwv_by_site.get(url, {})
            blocked = url in crawl_errors

            if not blocked:
                # Normal path — site was crawled successfully
                dims = {
                    "technical":  score_technical(pages),
                    "on_page":    score_on_page(pages),
                    "content":    score_content(pages),
                    "eeat":       score_eeat(pages, url),
                    "ctr":        score_ctr_potential(pages),
                    "keywords":   score_keywords(pages),
                    "page_speed": score_page_speed(cwv),
                }
                source = "crawl"
            elif cwv:
                # Crawl blocked but PSI succeeded — use Lighthouse SEO audits
                # PSI uses Google's own Chrome → bypasses Cloudflare/JS walls
                dims = _score_from_psi(cwv, url)
                source = "psi_derived"
                logger.info("[%s] Using PSI-derived scores for blocked site %s", task_id, url)
            else:
                # Both crawl and PSI failed — return zeros
                dims = {k: 0.0 for k in _WEIGHTS}
                source = "no_data"
                logger.warning("[%s] No data at all for %s (crawl + PSI both failed)", task_id, url)
                psi_fail_msg = "PSI API returned no data (rate-limited or timeout)"
                if url in crawl_errors:
                    crawl_errors[url] += f"; {psi_fail_msg}"
                else:
                    crawl_errors[url] = psi_fail_msg

            site_scores[url] = dims
            site_scores[url]["composite"] = compute_composite(dims)
            site_scores[url]["score_source"] = source

        # ── Step 5: Keyword gap analysis ──────────────────────────────────
        competitor_pages_map = {
            u: pages_map[u] for u in competitor_urls
        }
        keyword_gaps = compute_keyword_gap(
            pages_map[target_url],
            competitor_pages_map,
        )

        # ── Step 6: Semantic similarity ───────────────────────────────────
        similarities = compute_semantic_similarity(
            pages_map[target_url],
            competitor_pages_map,
        )

        # ── Step 7: Radar + action list ───────────────────────────────────
        radar = build_radar_data(site_scores)
        actions = build_action_list(
            site_scores[target_url],
            {u: site_scores[u] for u in competitor_urls},
        )

        # ── Step 8: Build per-site page summaries ─────────────────────────
        def _site_summary(url: str) -> dict:
            pages   = pages_map[url]
            real    = [p for p in pages if p.get("status_code") == 200]
            blocked = url in crawl_errors
            sc      = site_scores[url]
            source  = sc.get("score_source", "crawl")
            # Strip internal metadata key from the scores dict shown in the UI
            clean_scores = {k: v for k, v in sc.items() if k != "score_source"}
            # For PSI-derived sites, extract title/meta from PSI audit displayValues
            cwv = cwv_by_site.get(url, {})
            psi_title = ""
            psi_meta  = ""
            if source == "psi_derived" and cwv:
                psi_title = cwv.get("_seo_audits", {}).get("document-title", {}).get("displayValue", "")
                psi_meta  = cwv.get("_seo_audits", {}).get("meta-description", {}).get("displayValue", "")
            return {
                "url":           url,
                "domain":        urlparse(url).netloc,
                "pages_crawled": len(pages),
                "real_pages":    len(real),
                "crawl_blocked": blocked,
                "crawl_error":   crawl_errors.get(url, ""),
                "score_source":  source,   # "crawl" | "psi_derived" | "no_data"
                "psi_title":     psi_title,
                "psi_meta":      psi_meta,
                "issues_count":  sum(1 for p in real if p.get("issues")),
                "scores":        clean_scores,
                "cwv":           {k: v for k, v in cwv.items() if not k.startswith("_")},
                "top_keywords":  list({
                    k if isinstance(k, str) else k.get("keyword", "")
                    for p in real
                    for k in (p.get("keywords") or [])
                    if k
                })[:20],
            }

        sites_summary = [_site_summary(u) for u in all_urls]

        # ── Step 9: Full metrics payload ──────────────────────────────────
        elapsed = round(time.time() - t0, 1)
        metrics = {
            "task_id":           task_id,
            "target_url":        target_url,
            "competitor_urls":   competitor_urls,
            "elapsed_s":         elapsed,
            "sites":             sites_summary,
            "scores":            site_scores,
            "radar":             radar,
            "keyword_gaps":      keyword_gaps,
            "similarities":      similarities,
            "actions":           actions,
            "crawl_errors":      crawl_errors,
            "phase":             1,
        }

        # Lightweight summary for list view
        summary = {
            "target_composite":       site_scores[target_url]["composite"],
            "competitor_composites":  {
                urlparse(u).netloc: site_scores[u]["composite"]
                for u in competitor_urls
            },
            "gap_count":              len(keyword_gaps),
            "top_action":             actions[0]["action"] if actions else "",
        }

        # ── Step 10: Persist to DB ────────────────────────────────────────
        update_snapshot(
            task_id,
            status="done",
            metrics=metrics,
            summary=summary,
            completed=True,
        )

        logger.info(
            "[%s] Competitor analysis done in %.1fs | target=%s composite=%.0f",
            task_id, elapsed, target_url,
            site_scores[target_url]["composite"],
        )

    except Exception as exc:
        logger.error("[%s] Competitor analysis failed: %s", task_id, exc, exc_info=True)
        update_snapshot(
            task_id,
            status="error",
            error_msg=str(exc),
            completed=True,
        )


def get_analysis_result(task_id: str) -> dict | None:
    """Retrieve analysis result from DB by task_id. Returns None if not found."""
    return get_snapshot(task_id)


def generate_task_id() -> str:
    """Generate a unique task ID for a new analysis run."""
    return f"comp_{uuid.uuid4().hex[:12]}"


# ════════════════════════════════════════════════════════════════════════════
# ── PHASE 2 SCAFFOLD: SERP + CTR Intelligence ───────────────────────────────
# ════════════════════════════════════════════════════════════════════════════
# TODO Phase 2:
#
#   async def fetch_serp_positions(keywords, domains) -> dict:
#       """Scrape Google top-10 for each keyword, return {kw: {domain: pos}}."""
#       # Use SERP_BASE = "https://www.google.com/search?q={kw}&num=10"
#       # Respect rate limits: asyncio.sleep(2) between queries
#       # Fail-silent per keyword (CAPTCHA = empty result)
#       pass
#
#   def predict_ctr(position, title_features) -> float:
#       """XGBoost CTR model or industry benchmark curve fallback."""
#       # CTR curve: pos1=28%, pos2=15%, pos3=11%, pos4=8%, pos5=7%...
#       # Multiply by title attractiveness multiplier from score_ctr_potential()
#       pass
#
#   def compute_keyword_roi(kw, difficulty, volume, position) -> float:
#       """ROI = (volume × (1 - difficulty/100)) / max(position, 1)"""
#       pass
#
#   def detect_featured_snippet(kw, domain, serp_html) -> bool:
#       """Check if domain owns position-0 for this keyword."""
#       pass


# ════════════════════════════════════════════════════════════════════════════
# ── PHASE 3 SCAFFOLD: Persistence + Scheduling ──────────────────────────────
# ════════════════════════════════════════════════════════════════════════════
# TODO Phase 3:
#
#   from apscheduler.schedulers.asyncio import AsyncIOScheduler
#
#   def schedule_competitor_monitoring(target_url, competitor_urls, interval_h=24):
#       """Register recurring analysis job in APScheduler."""
#       scheduler = AsyncIOScheduler()
#       scheduler.add_job(
#           run_competitor_analysis,
#           trigger="interval", hours=interval_h,
#           args=[generate_task_id(), target_url, competitor_urls],
#           id=f"monitor_{urlparse(target_url).netloc}",
#           replace_existing=True,
#       )
#       scheduler.start()
#
#   def compute_ranking_velocity(domain, keyword, days=30) -> float:
#       """Calculate Δ position over last N days using kwranking history."""
#       history = get_keyword_history(domain, keyword, days)
#       if len(history) < 2:
#           return 0.0
#       start_pos = history[0]["position"] or 100
#       end_pos   = history[-1]["position"] or 100
#       return start_pos - end_pos  # positive = improved


# ════════════════════════════════════════════════════════════════════════════
# ── PHASE 4 SCAFFOLD: Production Hardening ───────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════
# TODO Phase 4:
#
#   _psi_cache: dict[str, tuple[float, dict]] = {}  # {url: (timestamp, data)}
#   PSI_CACHE_TTL = 3600  # 1 hour
#
#   async def fetch_psi_cached(url: str) -> dict:
#       """TTL-based in-memory cache to avoid redundant PSI calls."""
#       now = time.time()
#       if url in _psi_cache and now - _psi_cache[url][0] < PSI_CACHE_TTL:
#           return _psi_cache[url][1]
#       data = await _fetch_psi(url)
#       _psi_cache[url] = (now, data)
#       return data
#
#   def export_pdf_report(task_id: str) -> bytes:
#       """Generate PDF report using WeasyPrint or Playwright PDF."""
#       # from weasyprint import HTML
#       # html_content = render_report_html(get_analysis_result(task_id))
#       # return HTML(string=html_content).write_pdf()
#       pass
#
#   async def send_alert_webhook(webhook_url: str, payload: dict) -> None:
#       """Push ranking change alert to external webhook (Slack, Teams, etc)."""
#       async with aiohttp.ClientSession() as sess:
#           await sess.post(webhook_url, json=payload)
