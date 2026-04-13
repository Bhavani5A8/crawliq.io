"""
serp_engine.py — SERP intelligence engine (free, no paid APIs)
==============================================================

Provides three capabilities:
  1. CTR Benchmark Table   — Sistrix 2024 industry-average CTR by SERP position
  2. Google Suggest        — keyword ideas + autocomplete via free Suggest API
  3. Featured Snippet      — heuristic detection from page signals (no SERP scrape)

Zero paid API keys needed.
Zero GPU or ML training needed.
All network calls are async with timeout + graceful fallback.

Public API
----------
  get_ctr_curve() -> list[dict]
      Returns CTR benchmark data for positions 1-20.
      [{position, ctr_pct, delta_vs_prev, tier}]

  expected_ctr(position: int) -> float
      Returns expected CTR (0.0-1.0) for a given SERP position.

  async fetch_suggestions(keyword: str, lang: str = "en") -> list[str]
      Calls Google Suggest API. Returns list of suggestion strings.
      Falls back to [] on timeout/error.

  async fetch_suggestions_with_intent(keyword: str) -> list[dict]
      Same as above but tags each suggestion with intent classification.
      Returns [{suggestion, intent, intent_color}]

  score_featured_snippet_potential(page: dict) -> dict
      Heuristic scoring of a crawled page for featured snippet eligibility.
      Returns {score, potential, signals}
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── Sistrix 2024 CTR Benchmark Data ──────────────────────────────────────────
# Source: Sistrix CTR study 2023/2024 — organic desktop averages.
# These are industry-average baselines; actual CTR varies by query type,
# rich snippets, brand vs non-brand, and mobile vs desktop.
#
# Tier labels:
#   gold   — top-3 positions (80%+ of SERP clicks shared here)
#   silver — positions 4-7
#   bronze — positions 8-10
#   below  — positions 11-20 (page 2 territory)

_CTR_TABLE: list[dict] = [
    {"position":  1,  "ctr_pct": 28.5, "tier": "gold"},
    {"position":  2,  "ctr_pct": 15.7, "tier": "gold"},
    {"position":  3,  "ctr_pct": 11.0, "tier": "gold"},
    {"position":  4,  "ctr_pct":  8.0, "tier": "silver"},
    {"position":  5,  "ctr_pct":  7.2, "tier": "silver"},
    {"position":  6,  "ctr_pct":  5.1, "tier": "silver"},
    {"position":  7,  "ctr_pct":  4.0, "tier": "silver"},
    {"position":  8,  "ctr_pct":  3.2, "tier": "bronze"},
    {"position":  9,  "ctr_pct":  2.8, "tier": "bronze"},
    {"position": 10,  "ctr_pct":  2.5, "tier": "bronze"},
    {"position": 11,  "ctr_pct":  1.2, "tier": "below"},
    {"position": 12,  "ctr_pct":  1.0, "tier": "below"},
    {"position": 13,  "ctr_pct":  0.9, "tier": "below"},
    {"position": 14,  "ctr_pct":  0.8, "tier": "below"},
    {"position": 15,  "ctr_pct":  0.7, "tier": "below"},
    {"position": 16,  "ctr_pct":  0.6, "tier": "below"},
    {"position": 17,  "ctr_pct":  0.5, "tier": "below"},
    {"position": 18,  "ctr_pct":  0.5, "tier": "below"},
    {"position": 19,  "ctr_pct":  0.4, "tier": "below"},
    {"position": 20,  "ctr_pct":  0.4, "tier": "below"},
]

# Pre-compute position → CTR for O(1) lookup
_CTR_BY_POSITION: dict[int, float] = {
    row["position"]: row["ctr_pct"] / 100
    for row in _CTR_TABLE
}

# Google Suggest API endpoint (unofficial, free, no auth required)
_SUGGEST_URL = "http://suggestqueries.google.com/complete/search"
_SUGGEST_TIMEOUT = 5  # seconds — fast or fail

# Intent multipliers for CTR adjustment
# Commercial and transactional queries show ads → real organic CTR is lower.
_INTENT_CTR_MULTIPLIER = {
    "informational": 1.0,
    "commercial":    0.75,
    "transactional": 0.65,
    "navigational":  0.90,
}


# ── Public API — CTR Benchmark ────────────────────────────────────────────────

def get_ctr_curve() -> list[dict]:
    """
    Returns full CTR benchmark curve with delta and tier for each position.

    Example entry:
      {
        "position":     1,
        "ctr_pct":      28.5,
        "ctr_frac":     0.285,
        "delta_vs_prev": None,  # position 1 has no previous
        "tier":         "gold",
        "tier_color":   "#f59e0b",
      }
    """
    tier_colors = {
        "gold":   "#f59e0b",
        "silver": "#9ca3af",
        "bronze": "#b45309",
        "below":  "#374151",
    }
    rows = []
    for i, row in enumerate(_CTR_TABLE):
        prev_ctr = _CTR_TABLE[i - 1]["ctr_pct"] if i > 0 else None
        delta    = round(row["ctr_pct"] - prev_ctr, 1) if prev_ctr is not None else None
        rows.append({
            "position":      row["position"],
            "ctr_pct":       row["ctr_pct"],
            "ctr_frac":      round(row["ctr_pct"] / 100, 4),
            "delta_vs_prev": delta,
            "tier":          row["tier"],
            "tier_color":    tier_colors[row["tier"]],
        })
    return rows


def expected_ctr(position: int, intent: str = "informational") -> float:
    """
    Return expected CTR (0.0–1.0) for a given SERP position and query intent.

    Applies intent-based multiplier to account for ad competition:
      informational → 1.0× (no ads)
      commercial    → 0.75× (shopping ads)
      transactional → 0.65× (heavy ads + PLA)
      navigational  → 0.90× (sitelinks help)

    Returns 0.002 (0.2%) for positions beyond 20.
    """
    base = _CTR_BY_POSITION.get(position, 0.002)
    multiplier = _INTENT_CTR_MULTIPLIER.get(intent, 1.0)
    return round(base * multiplier, 4)


def ctr_opportunity_score(current_position: int, target_position: int,
                           intent: str = "informational") -> dict:
    """
    Calculate the CTR uplift from moving current_position → target_position.

    Returns:
      {
        "current_ctr":  0.025,
        "target_ctr":   0.157,
        "uplift_abs":   0.132,   # absolute CTR gain
        "uplift_rel":   5.3,     # relative multiplier (5.3× more clicks)
        "tier_change":  "bronze → gold",
      }
    """
    current = expected_ctr(current_position, intent)
    target  = expected_ctr(target_position,  intent)
    uplift_abs = round(target - current, 4)
    uplift_rel = round(target / current, 1) if current > 0 else 0.0

    def _tier(pos: int) -> str:
        for row in _CTR_TABLE:
            if row["position"] == pos:
                return row["tier"]
        return "below"

    return {
        "current_position": current_position,
        "target_position":  target_position,
        "current_ctr":      current,
        "target_ctr":       target,
        "uplift_abs":       uplift_abs,
        "uplift_rel":       uplift_rel,
        "tier_change":      f"{_tier(current_position)} → {_tier(target_position)}",
    }


# ── Public API — Google Suggest ───────────────────────────────────────────────

async def fetch_suggestions(keyword: str, lang: str = "en",
                             country: str = "us") -> list[str]:
    """
    Fetch keyword suggestions from Google Suggest API.

    This is the same endpoint the Google search bar uses for autocomplete.
    It's free, requires no API key, and returns related keyword ideas.

    Rate limit: be polite — add delays between batch calls in your code.
    Falls back to [] on any error.

    Returns list of suggestion strings (typically 5-10).
    """
    if not keyword or not keyword.strip():
        return []

    params = {
        "q":      keyword.strip(),
        "client": "firefox",      # returns JSON array format
        "hl":     lang,
        "gl":     country,
    }
    query_string = urllib.parse.urlencode(params)
    url = f"{_SUGGEST_URL}?{query_string}"

    try:
        timeout = aiohttp.ClientTimeout(total=_SUGGEST_TIMEOUT)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; CrawlIQ/1.0; +https://crawliq.io)",
            "Accept": "application/json, text/plain, */*",
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
            async with sess.get(url, ssl=False) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
                # Response format: ["query", ["suggestion1", "suggestion2", ...]]
                data = json.loads(text)
                if isinstance(data, list) and len(data) >= 2:
                    suggestions = data[1]
                    if isinstance(suggestions, list):
                        return [str(s) for s in suggestions if s]
    except asyncio.TimeoutError:
        logger.debug("Google Suggest timeout for: %s", keyword)
    except Exception as exc:
        logger.debug("Google Suggest error for '%s': %s", keyword, exc)

    return []


async def fetch_suggestions_with_intent(keyword: str,
                                         lang: str = "en") -> list[dict]:
    """
    Fetch Google Suggest results and classify each by search intent.

    Returns:
      [
        {
          "suggestion":    "how to do seo for beginners",
          "intent":        "informational",
          "intent_short":  "Info",
          "intent_color":  "#3b82f6",
        },
        ...
      ]
    """
    try:
        from intent_classifier import classify_intent, intent_label
    except ImportError:
        # Fallback if intent_classifier not available
        suggestions = await fetch_suggestions(keyword, lang)
        return [{"suggestion": s, "intent": "informational",
                 "intent_short": "Info", "intent_color": "#3b82f6"}
                for s in suggestions]

    suggestions = await fetch_suggestions(keyword, lang)
    results = []
    for s in suggestions:
        intent = classify_intent(s)
        label  = intent_label(intent)
        results.append({
            "suggestion":   s,
            "intent":       intent,
            "intent_short": label["short"],
            "intent_color": label["color"],
        })
    return results


async def fetch_suggestions_batch(keywords: list[str],
                                   delay: float = 0.5) -> dict[str, list[str]]:
    """
    Fetch suggestions for multiple keywords with polite delay between calls.
    Returns {keyword: [suggestions]}.
    """
    results: dict[str, list[str]] = {}
    for kw in keywords:
        results[kw] = await fetch_suggestions(kw)
        if delay > 0:
            await asyncio.sleep(delay)
    return results


# ── Public API — Featured Snippet Potential ───────────────────────────────────

# Signals that make a page more likely to win a featured snippet
_SNIPPET_POSITIVE_PATTERNS = [
    # Definition format
    (r"\b(is|are|refers to|means|defined as|definition)\b", 8, "definition_format"),
    # List items
    (r"^[-•*]\s+\w", 10, "bullet_list"),
    (r"^\d+\.\s+\w", 10, "numbered_list"),
    # Step-by-step
    (r"\bstep\s+\d+\b", 12, "step_by_step"),
    # Table signals
    (r"\|.+\|", 8, "table_format"),
    # Short direct answers (50-300 chars after H2)
    (r"[.!?]\s+[A-Z]", 5, "clear_sentences"),
]

def score_featured_snippet_potential(page: dict) -> dict:
    """
    Heuristic scoring of a page's featured snippet eligibility.

    Google tends to pull featured snippets from pages that:
      - Have a clear definition or answer near the top
      - Use structured lists or numbered steps
      - Have matching H2/H3 headings for the target question
      - Have 40-60 word paragraphs (digestible answer size)
      - Are already in positions 1-10 organically

    Returns:
      {
        "score":     72,
        "potential": "High",   # High / Medium / Low
        "signals":   ["numbered_list", "step_by_step", ...],
        "advice":    "Add a 40-60 word definition paragraph near H1"
      }
    """
    import re

    body     = (page.get("body_text") or "")[:5000]
    h2s      = page.get("h2") or []
    h3s      = page.get("h3") or []
    title    = page.get("title") or ""
    wc       = len(body.split())

    score   = 0
    signals = []
    advice  = []

    # ── Content quality signals ───────────────────────────────────────────────
    if wc >= 800:
        score += 15
        signals.append("sufficient_word_count")
    elif wc >= 300:
        score += 8

    # Heading structure (question-style headings are snippet magnets)
    question_headings = sum(
        1 for h in (h2s + h3s)
        if re.search(r"^(how|why|what|when|where|who|which|is|are|can)\b", h, re.I)
    )
    if question_headings >= 2:
        score += 20
        signals.append("question_headings")
    elif question_headings == 1:
        score += 10

    # Answer format signals
    for pattern, pts, label in _SNIPPET_POSITIVE_PATTERNS:
        if re.search(pattern, body, re.MULTILINE | re.IGNORECASE):
            score += pts
            signals.append(label)

    # Short paragraph detection (40-60 word answer blocks — Google's sweet spot)
    sentences = [s.strip() for s in re.split(r"[.!?]", body) if s.strip()]
    paragraph_lengths = []
    for s in sentences[:30]:
        wl = len(s.split())
        if 20 <= wl <= 80:
            paragraph_lengths.append(wl)
    if len(paragraph_lengths) >= 3:
        score += 10
        signals.append("answer_length_paragraphs")

    # Title is question-format
    if re.match(r"^(how|why|what|when|where|who|which|is|are|can)\b", title, re.I):
        score += 8
        signals.append("question_title")

    # Cap at 100
    score = min(score, 100)

    # Advice
    if "question_headings" not in signals:
        advice.append("Add H2 headings phrased as questions your audience asks")
    if "numbered_list" not in signals and "bullet_list" not in signals:
        advice.append("Add a numbered steps list or bullet list near the top")
    if "answer_length_paragraphs" not in signals:
        advice.append("Write a 40-60 word direct answer paragraph after your H1")
    if "definition_format" not in signals:
        advice.append("Add a [Topic] is/means/refers to... definition sentence")

    potential = "High" if score >= 60 else "Medium" if score >= 35 else "Low"

    return {
        "score":     score,
        "potential": potential,
        "signals":   signals,
        "advice":    advice[:3],  # top 3 most actionable
    }
