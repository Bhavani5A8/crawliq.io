"""
keyword_scorer.py — Deterministic keyword scoring + structured page output.

Adds two things the pipeline was missing:

  1. score_keywords(page, suggest_hits) → list[{keyword, freq, importance}]
     Scores each keyword against page fields using strict deterministic rules.
     No LLM. No network. Instant.

  2. build_structured_page(page) → dict
     Returns a clean, export-ready dict per the spec:
       {url, title, meta, h1, h2, h3, keywords, competitor_gaps,
        issues, content_snippet}

Design rules:
  - Pure Python, zero dependencies beyond stdlib
  - No blocking calls, no async needed
  - Imported and called from keyword_pipeline.py expand_keywords()
  - Does NOT modify crawler.py, keyword_extractor.py, or any other existing file
"""

import re
import logging
from collections import Counter
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import — avoids circular dependency at module load time
def _expected_ctr(position: int, intent: str = "informational") -> float:
    """Thin wrapper so serp_engine is imported only when needed."""
    try:
        from serp_engine import expected_ctr
        return expected_ctr(position, intent)
    except Exception:
        return 0.0

def _ctr_tier(position: int) -> str:
    """Return tier label from serp_engine CTR table."""
    try:
        from serp_engine import _CTR_TABLE
        for row in _CTR_TABLE:
            if row["position"] == position:
                return row["tier"]
    except Exception:
        pass
    return "below" if position > 10 else "bronze"

# BUG-N01: pre-compiled word-boundary pattern used by _in_text().
# Python's `kw in text` is substring matching — "art" matches "startup".
# Using \b...\b ensures we only count whole-word occurrences.
#
# BUG-N40: replaced the unbounded module-level dict with lru_cache(maxsize=2048).
# In long-running deployments crawling many sites the old dict grew indefinitely;
# lru_cache evicts the least-recently-used entries once the cap is reached.

@lru_cache(maxsize=2048)
def _compile_wb(kw: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(kw)}\b")

def _in_text(kw: str, text: str) -> bool:
    """True only when kw appears as a whole word in text (case-normalised)."""
    if not kw or not text:
        return False
    return bool(_compile_wb(kw).search(text))

# ── Scoring weights ───────────────────────────────────────────────────────────
# Each condition adds points. Thresholds: ≥6 HIGH, 3-5 MEDIUM, <3 LOW.

_W_TITLE_H1   = 3   # keyword found in title or H1
_W_H2_H3      = 2   # keyword found in H2 or H3
_W_HIGH_FREQ  = 2   # keyword appears >5× in body_text
_W_SUGGEST    = 3   # keyword returned by Google Suggest for this page

_THRESH_HIGH   = 6
_THRESH_MEDIUM = 3


# ── 1. Token normaliser ───────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Lowercase + collapse whitespace. Used for substring matching."""
    return re.sub(r"\s+", " ", text.lower().strip())


# ── 2. Unigram + bigram extraction with frequency ────────────────────────────

_STOPWORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with","by",
    "is","are","was","were","be","been","have","has","had","do","does","did",
    "will","would","could","should","may","might","can","not","no","nor",
    "this","that","these","those","it","its","he","she","they","we","you",
    "from","into","about","than","more","also","just","only","very","so",
    "com","org","net","www","http","https","page","site","click","read",
    "all","any","each","few","more","most","other","some","such","only",
    "own","same","than","too","very","just","but","because","as","until",
    "while","of","at","by","for","with","about","against","between","into",
    "through","during","before","after","above","below","up","down","out",
    "off","over","under","again","further","then","once","here","there",
    "when","where","why","how","both","each","few","more","most","other",
}


def extract_keywords_with_freq(
    page: dict,
    top_n: int = 10,
) -> list[dict]:
    """
    Extract top unigrams and bigrams from the page's body_text with frequencies.

    Returns:
        [{"keyword": "coffee mug", "freq": 12}, ...]

    Combines title × 3 + h1 × 2 + body_text weighting so important-field
    terms naturally rank higher.

    Called by score_keywords() — no need to call directly.
    """
    title = _norm(page.get("title", "") or "")
    h1    = _norm(" ".join(page.get("h1", []) or []))
    body  = _norm(page.get("body_text", "") or "")

    # Weighted concatenation: title 3×, h1 2×, body 1×
    full_text = f"{title} {title} {title} {h1} {h1} {body}"

    tokens = re.findall(r"[a-z]{3,}", full_text)
    tokens = [t for t in tokens if t not in _STOPWORDS]

    # Unigrams
    unigram_counts: Counter = Counter(tokens)

    # Bigrams
    bigram_counts: Counter = Counter(
        f"{tokens[i]} {tokens[i+1]}"
        for i in range(len(tokens) - 1)
        if tokens[i] not in _STOPWORDS and tokens[i+1] not in _STOPWORDS
    )

    # Merge: bigrams that appear ≥2× are preferred over their component unigrams
    combined: dict[str, int] = {}
    strong_bigrams: set[str] = set()

    for phrase, cnt in bigram_counts.items():
        if cnt >= 2:
            combined[phrase] = cnt
            strong_bigrams.update(phrase.split())

    for word, cnt in unigram_counts.items():
        if word not in strong_bigrams:
            combined[word] = cnt

    # Sort by frequency descending, take top_n
    top = sorted(combined.items(), key=lambda x: -x[1])[:top_n]
    return [{"keyword": kw, "freq": freq} for kw, freq in top]


# ── 3. Keyword importance scorer ─────────────────────────────────────────────

def score_keywords(
    page: dict,
    suggest_hits: set[str] | None = None,
    top_n: int = 10,
    serp_positions: dict[str, int] | None = None,
) -> list[dict]:
    """
    Score each keyword and assign importance (HIGH / MEDIUM / LOW).

    Scoring rules (deterministic, no LLM):
      keyword in title or H1  → +3
      keyword in H2 or H3     → +2
      body frequency > 5      → +2
      keyword in suggest_hits → +3

    Thresholds:
      ≥ 6 → HIGH
      3–5 → MEDIUM
      < 3 → LOW

    Args:
        page:           page dict from crawl_results
        suggest_hits:   set of strings returned by Google Suggest for this page.
                        Pass None (or omit) if Suggest was not run.
        top_n:          maximum keywords to return
        serp_positions: optional dict mapping keyword → current SERP position
                        (int, 1-based). When provided, each keyword gets
                        ``expected_ctr`` (0.0–1.0) and ``ctr_tier`` fields
                        using the Sistrix 2024 benchmark curve.

    Returns:
        [{"keyword": "coffee mug", "freq": 12, "importance": "HIGH",
          "serp_position": 5, "expected_ctr": 0.072, "ctr_tier": "silver"}, ...]
    """
    if page.get("_is_error") or not page.get("body_text"):
        return []

    kw_freq_list = extract_keywords_with_freq(page, top_n=top_n)
    if not kw_freq_list:
        return []

    suggest_set   = suggest_hits or set()
    positions_map = serp_positions or {}
    # Detect page-level intent so CTR multiplier is applied consistently
    page_intent   = page.get("intent", "informational") or "informational"

    # Precompute normalised field texts for substring matching
    title_norm = _norm(page.get("title", "") or "")
    h1_norm    = _norm(" ".join(page.get("h1", []) or []))
    h2_norm    = _norm(" ".join(page.get("h2", []) or []))
    h3_norm    = _norm(" ".join(page.get("h3", []) or []))
    body_norm  = _norm(page.get("body_text", "") or "")
    # Tokenise raw body once — shared by density and prominence checks below.
    # Minimum 3-char filter matches extract_keywords_with_freq for consistency.
    body_tokens = re.findall(r"[a-z]{3,}", body_norm)
    total_words = len(body_tokens) or 1
    first_100   = " ".join(body_tokens[:100])

    scored = []
    for item in kw_freq_list:
        kw   = item["keyword"]
        freq = item["freq"]
        pts  = 0

        # +3: in title or H1 — BUG-N01: whole-word match, not substring
        if _in_text(kw, title_norm) or _in_text(kw, h1_norm):
            pts += _W_TITLE_H1

        # +2: in H2 or H3 — BUG-N01: whole-word match
        if _in_text(kw, h2_norm) or _in_text(kw, h3_norm):
            pts += _W_H2_H3

        # +2: body frequency > 5
        if freq > 5:
            pts += _W_HIGH_FREQ

        # +3: returned by Google Suggest — BUG-N01: whole-word match in suggest strings
        if kw in suggest_set or any(_in_text(kw, s) for s in suggest_set):
            pts += _W_SUGGEST

        # Map to importance label
        if pts >= _THRESH_HIGH:
            importance = "HIGH"
        elif pts >= _THRESH_MEDIUM:
            importance = "MEDIUM"
        else:
            importance = "LOW"

        # CTR enrichment — only when SERP position is known for this keyword
        position = positions_map.get(kw)
        if position:
            ctr_val  = _expected_ctr(position, page_intent)
            ctr_tier = _ctr_tier(position)
        else:
            ctr_val  = None
            ctr_tier = None

        # Density: raw body occurrences / total body tokens × 100
        # Uses _compile_wb (cached regex) — whole-word only, matches scorer logic
        raw_count = len(_compile_wb(kw).findall(body_norm))
        density   = round((raw_count / total_words) * 100, 2)
        # Prominent: keyword appears within the first 100 body tokens
        prominent = _in_text(kw, first_100)

        scored.append({
            "keyword":       kw,
            "freq":          freq,
            "importance":    importance,
            "score":         pts,
            "density":       density,
            "prominent":     prominent,
            "serp_position": position,
            "expected_ctr":  ctr_val,
            "ctr_tier":      ctr_tier,
        })

    # Sort: HIGH first, then MEDIUM, then LOW; within tier by freq desc
    tier_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    scored.sort(key=lambda x: (tier_order[x["importance"]], -x["freq"]))
    return scored


# ── 4. Structured page output ─────────────────────────────────────────────────

def build_structured_page(
    page: dict,
    scored_keywords: list[dict] | None = None,
) -> dict:
    """
    Build a clean structured dict per the pipeline spec.

    Args:
        page:            raw page dict from crawl_results
        scored_keywords: output of score_keywords(page) — pass None to skip

    Returns:
        {
          "url":              str,
          "title":            str,
          "meta":             str,
          "h1":               str,        # first H1 only
          "h2":               list[str],
          "h3":               list[str],
          "keywords":         list[{keyword, freq, importance}],
          "competitor_gaps":  dict | None,   # filled by competitor.py
          "issues":           list[str],
          "content_snippet":  str,       # first 1000 chars of body_text
        }

    This is the canonical output shape used by Gemini, the optimizer,
    and the Excel export. It does NOT replace page fields in-place —
    it returns a new dict suitable for display, AI input, or export.
    """
    h1_list = page.get("h1") or []
    body    = page.get("body_text", "") or ""

    return {
        "url":             page.get("url", ""),
        "title":           page.get("title", "") or "",
        "meta":            page.get("meta_description", "") or "",
        "h1":              h1_list[0] if h1_list else "",
        "h2":              page.get("h2") or [],
        "h3":              page.get("h3") or [],
        "keywords":        scored_keywords or [],
        "competitor_gaps": page.get("competitor_gaps"),     # None until filled
        "issues":          page.get("issues") or [],
        "content_snippet": body[:1000],
    }
