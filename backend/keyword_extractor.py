"""
keyword_extractor.py — Fast deterministic keyword extraction. Zero LLM.

Strategy (two-tier, no external model download required):
  Tier 1 — TF-IDF across all crawled pages (sklearn).
            Best when you have 5+ pages (cross-page term weighting).
  Tier 2 — Frequency + stopword filter (nltk / built-in fallback).
            Used per-page when sklearn is unavailable or corpus is tiny.

Public API:
    extract_keywords_corpus(pages, top_n=10)
        → adds page["keywords"] list to every page in-place.
          Uses TF-IDF over the full corpus for best results.

    extract_keywords_single(text, top_n=10)
        → returns keyword list for one text string (fallback / testing).
"""

import re
import logging
from collections import Counter

logger = logging.getLogger(__name__)

# ── Stopwords ─────────────────────────────────────────────────────────────────
# Built-in list so the module works even without nltk downloaded.
# Extended with common web/HTML noise words.
_STOPWORDS = {
    "a","about","above","after","again","against","all","am","an","and","any",
    "are","aren't","as","at","be","because","been","before","being","below",
    "between","both","but","by","can","can't","cannot","could","couldn't","did",
    "didn't","do","does","doesn't","doing","don't","down","during","each","few",
    "for","from","further","get","got","had","hadn't","has","hasn't","have",
    "haven't","having","he","he'd","he'll","he's","her","here","here's","hers",
    "herself","him","himself","his","how","how's","i","i'd","i'll","i'm","i've",
    "if","in","into","is","isn't","it","it's","its","itself","let's","me","more",
    "most","mustn't","my","myself","no","nor","not","of","off","on","once","only",
    "or","other","ought","our","ours","ourselves","out","over","own","same",
    "shan't","she","she'd","she'll","she's","should","shouldn't","so","some",
    "such","than","that","that's","the","their","theirs","them","themselves",
    "then","there","there's","these","they","they'd","they'll","they're",
    "they've","this","those","through","to","too","under","until","up","very",
    "was","wasn't","we","we'd","we'll","we're","we've","were","weren't","what",
    "what's","when","when's","where","where's","which","while","who","who's",
    "whom","why","why's","will","with","won't","would","wouldn't","you","you'd",
    "you'll","you're","you've","your","yours","yourself","yourselves",
    # Web noise
    "click","read","more","learn","home","page","site","website","www","http",
    "https","com","org","net","menu","nav","footer","header","skip","content",
    "search","contact","us","privacy","policy","terms","cookie","cookies",
    "copyright","all","rights","reserved","powered","by","login","sign","up",
    "subscribe","newsletter","follow","share","like","tweet","email","phone",
    "address","view","also","back","next","previous","new","use","using","used",
    "make","made","work","working","works","need","needs","want","wants","help",
}

# ── Try to import optional libs (graceful fallback if missing) ────────────────
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    import numpy as np
    _SKLEARN = True
except ImportError:
    _SKLEARN = False
    logger.info("sklearn not installed — falling back to frequency-based keyword extraction.")

try:
    import nltk
    # Download quietly if not already present
    nltk.download("stopwords", quiet=True)
    nltk.download("punkt",     quiet=True)
    from nltk.corpus import stopwords as _nltk_sw
    _NLTK_STOPS = set(_nltk_sw.words("english"))
    _STOPWORDS.update(_NLTK_STOPS)
    _NLTK = True
except Exception:
    _NLTK = False


# ── Text extraction from a page dict ─────────────────────────────────────────

def _page_to_text(page: dict) -> str:
    """
    Combine all text fields of a page into one string for NLP.
    Weights important fields by repeating them (title × 3, h1 × 2).
    """
    title = page.get("title", "") or ""
    meta  = page.get("meta_description", "") or ""
    h1    = " ".join(page.get("h1", []) or [])
    h2    = " ".join(page.get("h2", []) or [])
    body  = page.get("body_text", "") or ""   # populated by enhanced crawler

    # Weight important fields
    return f"{title} {title} {title} {h1} {h1} {meta} {h2} {body}"


def _clean_tokens(text: str) -> list[str]:
    """Lowercase, strip non-alpha, remove stopwords, min length 3."""
    tokens = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


# ── Public API ────────────────────────────────────────────────────────────────

def extract_keywords_corpus(pages: list[dict], top_n: int = 10) -> None:
    """
    Extract keywords for ALL pages using TF-IDF across the full corpus.
    Mutates each page in-place: page["keywords"] = ["seo", "audit", ...]

    TF-IDF gives each page's *distinctive* keywords — words that are
    frequent on that page but rare across the whole site.
    Falls back to per-page frequency if sklearn is unavailable.
    """
    if not pages:
        return

    texts = [_page_to_text(p) for p in pages]

    if _SKLEARN and len(pages) >= 3:
        _tfidf_extract(pages, texts, top_n)
    else:
        # Fallback: simple frequency per page
        for page, text in zip(pages, texts):
            page["keywords"] = extract_keywords_single(text, top_n)


def _tfidf_extract(pages: list[dict], texts: list[str], top_n: int) -> None:
    """TF-IDF keyword extraction across the full page corpus."""
    try:
        vectorizer = TfidfVectorizer(
            max_features=5000,
            stop_words="english",        # sklearn built-in stopwords
            token_pattern=r"[a-zA-Z]{3,}",
            sublinear_tf=True,           # dampen very frequent terms
            min_df=1,
        )
        tfidf_matrix = vectorizer.fit_transform(texts)
        feature_names = vectorizer.get_feature_names_out()
        dense = tfidf_matrix.toarray()

        for i, page in enumerate(pages):
            scores = dense[i]
            # Get indices of top_n highest TF-IDF scores
            top_indices = scores.argsort()[::-1][:top_n * 2]
            keywords = [
                feature_names[idx]
                for idx in top_indices
                if scores[idx] > 0 and feature_names[idx] not in _STOPWORDS
            ][:top_n]
            page["keywords"] = keywords

    except Exception as e:
        logger.error("TF-IDF extraction failed: %s — falling back to frequency", e)
        for page, text in zip(pages, texts):
            page["keywords"] = extract_keywords_single(text, top_n)


def extract_keywords_single(text: str, top_n: int = 10) -> list[str]:
    """
    Frequency-based keyword extraction for a single text string.
    Used as fallback and for testing.
    """
    tokens  = _clean_tokens(text)
    counter = Counter(tokens)
    return [word for word, _ in counter.most_common(top_n)]
