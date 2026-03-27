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

logger = logging.getLogger(__name__)

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
        page:         page dict from crawl_results
        suggest_hits: set of strings returned by Google Suggest for this page.
                      Pass None (or omit) if Suggest was not run.
        top_n:        maximum keywords to return

    Returns:
        [{"keyword": "coffee mug", "freq": 12, "importance": "HIGH"}, ...]
    """
    if page.get("_is_error") or not page.get("body_text"):
        return []

    kw_freq_list = extract_keywords_with_freq(page, top_n=top_n)
    if not kw_freq_list:
        return []

    suggest_set = suggest_hits or set()

    # Precompute normalised field texts for substring matching
    title_norm = _norm(page.get("title", "") or "")
    h1_norm    = _norm(" ".join(page.get("h1", []) or []))
    h2_norm    = _norm(" ".join(page.get("h2", []) or []))
    h3_norm    = _norm(" ".join(page.get("h3", []) or []))
    body_norm  = _norm(page.get("body_text", "") or "")

    scored = []
    for item in kw_freq_list:
        kw   = item["keyword"]
        freq = item["freq"]
        pts  = 0

        # +3: in title or H1
        if kw in title_norm or kw in h1_norm:
            pts += _W_TITLE_H1

        # +2: in H2 or H3
        if kw in h2_norm or kw in h3_norm:
            pts += _W_H2_H3

        # +2: body frequency > 5
        if freq > 5:
            pts += _W_HIGH_FREQ

        # +3: returned by Google Suggest
        if kw in suggest_set or any(kw in s for s in suggest_set):
            pts += _W_SUGGEST

        # Map to importance label
        if pts >= _THRESH_HIGH:
            importance = "HIGH"
        elif pts >= _THRESH_MEDIUM:
            importance = "MEDIUM"
        else:
            importance = "LOW"

        scored.append({
            "keyword":    kw,
            "freq":       freq,
            "importance": importance,
            "score":      pts,
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
