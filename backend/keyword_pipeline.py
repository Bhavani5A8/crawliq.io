"""
keyword_pipeline.py — Extended keyword pipeline.

Adds to the existing keyword_extractor.py (which stays unchanged):
  1. N-gram extraction (bigrams + trigrams from page content)
  2. Google Suggest keyword expansion (async, fail-silent)
  3. Keyword merge + deduplication (local + suggest → top 15)
  4. Competition estimation (lightweight heuristic, no scraping)

Public API:
    async expand_keywords(page: dict) -> None
        Adds/updates page["keywords_expanded"] and page["competition"].
        Safe to call concurrently — each page is independent.

    async run_keyword_pipeline(pages: list[dict]) -> None
        Runs expand_keywords for all real pages concurrently.
        Called once after extract_keywords_corpus() in the crawl post-step.

    estimate_competition(keywords: list[str]) -> str
        Returns "Low" | "Medium" | "High" — pure heuristic, no network.

Design rules:
  - Zero blocking calls in async paths
  - Fail-silent: network errors → pipeline continues
  - No new heavy dependencies — uses stdlib + aiohttp (already required)
  - Does NOT modify keyword_extractor.py
"""

import asyncio
import json
import logging
import re
from collections import Counter
from urllib.parse import quote

import aiohttp

from keyword_scorer import score_keywords, build_structured_page
from competitor import run_competitor_analysis

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SUGGEST_URL     = "https://suggestqueries.google.com/complete/search?client=firefox&q={}"
SUGGEST_TIMEOUT = 4      # seconds — fail fast, don't block
MAX_SUGGEST     = 5      # suggestions to fetch per top keyword
MAX_KEYWORDS    = 15     # final merged keyword cap per page
CONCURRENCY     = 6      # parallel suggest requests across all pages

# BUG-N08: allow disabling Google Suggest entirely (large crawls exhaust the
# public endpoint and trigger IP bans). Set SUGGEST_ENABLED=false to skip.
# SUGGEST_DELAY adds a per-call pause to stay under implicit rate limits.
import os as _os
SUGGEST_ENABLED = _os.getenv("SUGGEST_ENABLED", "true").lower() == "true"
SUGGEST_DELAY   = float(_os.getenv("SUGGEST_DELAY", "0.2"))  # seconds between calls

# ── Stopwords (reuse from keyword_extractor without importing its private set)
_SW = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with","by",
    "is","are","was","were","be","been","have","has","had","do","does","did",
    "will","would","could","should","may","might","can","not","no","nor",
    "this","that","these","those","it","its","he","she","they","we","you",
    "from","into","about","than","more","also","just","only","very","so",
    "com","org","net","www","http","https","page","site","click","read",
}


# ── 1. N-gram extraction ──────────────────────────────────────────────────────

def extract_ngrams(text: str, top_n: int = 10) -> list[str]:
    """
    Extract top bigrams and trigrams from cleaned visible text.
    Returns a ranked list of multi-word phrases.

    Why n-grams? Single-word TF-IDF misses phrases like
    "digital marketing", "seo audit", "python async" which are
    far more useful as SEO keywords than individual words.
    """
    tokens = re.findall(r"[a-zA-Z]{3,}", text.lower())
    tokens = [t for t in tokens if t not in _SW]

    # Build bigrams and trigrams
    bigrams  = [f"{tokens[i]} {tokens[i+1]}"
                for i in range(len(tokens)-1)]
    trigrams = [f"{tokens[i]} {tokens[i+1]} {tokens[i+2]}"
                for i in range(len(tokens)-2)]

    counts = Counter(bigrams + trigrams)
    # BUG-N13: bigrams require freq ≥2 (noise filter); trigrams use ≥1 so
    # valuable long-tail phrases like "advanced seo techniques" aren't dropped
    # just because they appear once in a well-focused page.
    phrases = [
        p for p, c in counts.most_common(top_n * 2)
        if c >= 2 or len(p.split()) == 3
    ]
    return phrases[:top_n]


# ── 2. Google Suggest (async, fail-silent) ────────────────────────────────────

async def _fetch_suggestions(
    session: aiohttp.ClientSession,
    keyword: str,
) -> list[str]:
    """
    Fetch Google Suggest completions for one keyword.
    Returns empty list on any failure — never raises.
    """
    url = SUGGEST_URL.format(quote(keyword))
    try:
        timeout = aiohttp.ClientTimeout(total=SUGGEST_TIMEOUT)
        async with session.get(url, timeout=timeout, ssl=False,
                               headers={"Accept": "application/json"}) as resp:
            if resp.status != 200:
                return []
            text = await resp.text(encoding="utf-8", errors="replace")
            # Response is: ["query", ["sug1","sug2",...], ...]
            data = json.loads(text)
            suggestions = data[1] if len(data) > 1 else []
            return [s.strip() for s in suggestions if s.strip()][:MAX_SUGGEST]
    except Exception as exc:
        logger.debug("Suggest fetch failed for %r: %s", keyword, exc)
        return []


async def fetch_suggestions_for_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    keywords: list[str],
) -> list[str]:
    """
    Fetch suggestions for the top 2 keywords of a page, concurrently.
    Returns merged, deduplicated list.
    BUG-N08: returns [] immediately when SUGGEST_ENABLED=false.
    """
    if not SUGGEST_ENABLED or not keywords:
        return []

    # Only expand the top 2 — enough signal, minimal network load
    top2 = keywords[:2]
    async with sem:
        # BUG-N08: inter-request delay reduces burst pressure on Google's
        # autocomplete endpoint (implicit rate limit, no official quota).
        if SUGGEST_DELAY > 0:
            await asyncio.sleep(SUGGEST_DELAY)
        tasks = [_fetch_suggestions(session, kw) for kw in top2]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    merged = []
    for r in results:
        if isinstance(r, list):
            merged.extend(r)
    return merged


# ── 3. Keyword merge + dedup ──────────────────────────────────────────────────

def merge_keywords(
    tfidf_kws: list[str],
    ngram_kws: list[str],
    suggest_kws: list[str],
    max_total: int = MAX_KEYWORDS,
) -> list[str]:
    """
    Merge three keyword sources into a deduplicated ranked list.

    Priority order:
      1. TF-IDF single keywords (most distinctive for this page)
      2. N-gram phrases (multi-word, high-value for SEO)
      3. Google Suggest expansions (discovery/opportunity)

    Deduplication: a suggestion is dropped if any existing keyword
    is already a substring of it (avoids near-duplicates).
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(kw: str) -> None:
        kw = kw.strip().lower()
        if not kw or kw in seen:
            return
        # Drop if already covered by an existing shorter keyword
        for existing in seen:
            if existing in kw or kw in existing:
                return
        seen.add(kw)
        result.append(kw)

    for kw in tfidf_kws:
        _add(kw)
    for kw in ngram_kws:
        _add(kw)
    for kw in suggest_kws:
        _add(kw)

    return result[:max_total]


# ── 4. Competition estimation (pure heuristic, zero network) ──────────────────

# Keywords that signal high competition niches
_HIGH_COMP = {
    "insurance","loan","mortgage","credit","lawyer","attorney","casino",
    "gambling","hosting","vpn","forex","crypto","bitcoin","trading",
    "weight loss","make money","online marketing","digital marketing",
    "seo services","web design","software","saas","crm","erp",
}

# Patterns that typically signal low competition
_LOW_COMP_SIGNALS = [
    r"\b(how to|step by step|guide|tutorial|checklist|template)\b",
    r"\b(for beginners|getting started|introduction|overview)\b",
    r"\b\d{4}\b",   # year in keyword = often lower competition
    r"\b(local|near me|in \w+)\b",
]

def estimate_competition(keywords: list[str]) -> str:
    """
    Lightweight competition heuristic — no API, no scraping.

    Logic:
      HIGH   → keyword matches known high-CPC niches
      LOW    → keyword has informational / long-tail signals
      MEDIUM → everything else

    Returns "Low" | "Medium" | "High"

    Why this approach?
      Real competition data requires paid APIs (Ahrefs, SEMrush).
      This heuristic gives useful signal in ~0ms with no dependencies.
      It's clearly labelled as an estimate so users aren't misled.
    """
    if not keywords:
        return "Medium"

    kw_text = " ".join(keywords).lower()

    # Check high-competition niche keywords
    for term in _HIGH_COMP:
        if term in kw_text:
            return "High"

    # Check low-competition signals
    for pattern in _LOW_COMP_SIGNALS:
        if re.search(pattern, kw_text):
            return "Low"

    # Keyword length heuristic:
    # Long-tail (avg 3+ words per keyword) → Lower competition
    avg_words = sum(len(kw.split()) for kw in keywords) / max(len(keywords), 1)
    if avg_words >= 3:
        return "Low"
    if avg_words >= 2:
        return "Medium"

    return "Medium"


# ── 5. Per-page pipeline ──────────────────────────────────────────────────────

async def expand_keywords(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    page: dict,
) -> None:
    """
    Full keyword pipeline for one page — runs after TF-IDF corpus extraction.

    Reads:  page["keywords"]      (already set by extract_keywords_corpus)
            page["body_text"]     (set by crawler._parse)
    Writes: page["keywords"]      (upgraded — merged from 3 sources)
            page["keywords_ngrams"] (bigrams/trigrams for reference)
            page["competition"]   (Low/Medium/High)

    Does NOT touch any other page fields.
    """
    if page.get("_is_error") or not page.get("body_text"):
        page.setdefault("competition", "Medium")
        return

    tfidf_kws = page.get("keywords", [])
    body_text = page.get("body_text", "")

    # Step A: extract n-grams from body text
    ngram_kws = extract_ngrams(body_text, top_n=8)
    page["keywords_ngrams"] = ngram_kws

    # Step B: Google Suggest expansion (async, fail-silent)
    suggest_kws = await fetch_suggestions_for_page(session, sem, tfidf_kws)

    # Step C: merge all three sources
    merged = merge_keywords(tfidf_kws, ngram_kws, suggest_kws, MAX_KEYWORDS)
    if merged:
        page["keywords"] = merged   # upgrade in-place

    # Step D: competition estimation (instant, no network)
    page["competition"] = estimate_competition(page["keywords"])

    # Step E: keyword scoring (deterministic — title/H1/H2/freq/suggest)
    # Build suggest_hits set from suggestions we already fetched
    suggest_set = set(suggest_kws) if suggest_kws else set()
    page["keywords_scored"] = score_keywords(page, suggest_hits=suggest_set)

    # Step F: structured output (used by Gemini prompt + export)
    page["structured"] = build_structured_page(
        page, scored_keywords=page["keywords_scored"]
    )


# ── 6. Batch runner ───────────────────────────────────────────────────────────

async def run_keyword_pipeline(pages: list[dict]) -> None:
    """
    Run the full keyword pipeline for all real pages concurrently.

    Called in crawler.crawl_async() after extract_keywords_corpus():
        await run_keyword_pipeline(real_pages)

    Uses a shared aiohttp session and semaphore to keep
    concurrency bounded and avoid hammering Google Suggest.
    Fails silently per page — one slow/failed network call
    never stops the rest of the pipeline.
    """
    real = [p for p in pages
            if not p.get("_is_error") and p.get("status_code") == 200]

    if not real:
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)
    timeout   = aiohttp.ClientTimeout(total=SUGGEST_TIMEOUT + 2)

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
        ) as session:
            tasks = [expand_keywords(session, sem, page) for page in real]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning("keyword_pipeline failed for %s: %s",
                                   real[i].get("url", "?"), result)
                    real[i].setdefault("competition", "Medium")

        # Step 2: Competitor analysis — runs after scoring so keywords are ready
        # Fail-silent: a Google block or network error never stops the pipeline
        try:
            await run_competitor_analysis(real)
        except Exception as exc:
            logger.warning("run_competitor_analysis failed: %s", exc)

    except Exception as exc:
        logger.error("keyword_pipeline session error: %s", exc)
        # Ensure all pages have competition set even on total failure
        for page in real:
            page.setdefault("competition", "Medium")
