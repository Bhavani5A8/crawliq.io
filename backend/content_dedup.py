"""
content_dedup.py — SimHash-based near-duplicate content detection
Detects pages with near-identical body text using 64-bit SimHash.
No external dependencies — pure Python.
"""
from __future__ import annotations

import re
import hashlib
from itertools import combinations

# ---------------------------------------------------------------------------
# SimHash
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into word n-grams (unigrams here)."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [w for w in text.split() if len(w) > 2]


def _word_hash(word: str) -> int:
    """64-bit hash of a word."""
    digest = hashlib.md5(word.encode("utf-8", errors="ignore")).digest()
    # Take first 8 bytes as a 64-bit int
    return int.from_bytes(digest[:8], "big")


def simhash(text: str, bits: int = 64) -> int:
    """
    Compute 64-bit SimHash fingerprint for text.
    Returns integer fingerprint.
    """
    if not text or not text.strip():
        return 0

    tokens = _tokenise(text)
    if not tokens:
        return 0

    v = [0] * bits
    for token in tokens:
        h = _word_hash(token)
        for i in range(bits):
            bit = (h >> i) & 1
            v[i] += 1 if bit else -1

    fingerprint = 0
    for i in range(bits):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming_distance(a: int, b: int, bits: int = 64) -> int:
    """Bit-count of XOR — number of differing bits."""
    x = (a ^ b) & ((1 << bits) - 1)
    count = 0
    while x:
        count += x & 1
        x >>= 1
    return count


def similarity(a: int, b: int, bits: int = 64) -> float:
    """Similarity score in [0, 1]. 1 = identical, 0 = completely different."""
    dist = hamming_distance(a, b, bits)
    return round(1.0 - dist / bits, 4)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

# Threshold: ≤ 3 differing bits out of 64 = near-duplicate (~95% similar)
_DUPLICATE_THRESHOLD = 3
# Similarity score for human display
_SIMILARITY_FLOOR = 0.85


def detect_duplicates(
    pages: list[dict],
    threshold: int = _DUPLICATE_THRESHOLD,
) -> list[dict]:
    """
    Find near-duplicate page pairs using SimHash.

    Each page dict must have:
        url  (str)
        body_text (str) — plain text body content

    Returns list of duplicate groups sorted by similarity desc:
    [
      {
        "url_a": str,
        "url_b": str,
        "similarity": float,       # 0-1
        "hamming_distance": int,   # differing bits
        "risk_level": str,         # "exact" | "near_duplicate" | "similar"
        "recommendation": str,
      }
    ]
    """
    if not pages:
        return []

    # Compute fingerprints
    fp_map: dict[str, int] = {}
    for page in pages:
        url = page.get("url", "")
        text = page.get("body_text", "") or ""
        if url and text.strip():
            fp_map[url] = simhash(text)

    urls = list(fp_map.keys())
    duplicates = []

    for url_a, url_b in combinations(urls, 2):
        dist = hamming_distance(fp_map[url_a], fp_map[url_b])
        if dist <= threshold:
            sim = similarity(fp_map[url_a], fp_map[url_b])
            if dist == 0:
                risk = "exact"
                rec = "Add canonical tag pointing to the preferred URL, or 301-redirect the duplicate."
            elif dist <= 1:
                risk = "exact"
                rec = "Near-exact duplicate. Consolidate with canonical or redirect."
            elif dist <= 3:
                risk = "near_duplicate"
                rec = "Substantially similar. Consider consolidating or differentiating content."
            else:
                risk = "similar"
                rec = "Similar content. Review for thin content or unintentional overlap."
            duplicates.append({
                "url_a": url_a,
                "url_b": url_b,
                "similarity": sim,
                "hamming_distance": dist,
                "risk_level": risk,
                "recommendation": rec,
            })

    duplicates.sort(key=lambda x: (x["hamming_distance"], x["url_a"]))
    return duplicates


def duplicate_summary(pages: list[dict], threshold: int = _DUPLICATE_THRESHOLD) -> dict:
    """
    Full duplicate analysis pipeline entry point.

    Returns:
    {
      "total_pages_analysed": int,
      "duplicate_pairs": int,
      "exact_duplicates": int,
      "near_duplicates": int,
      "pairs": [...],           # full list from detect_duplicates
    }
    """
    pairs = detect_duplicates(pages, threshold)
    exact = sum(1 for p in pairs if p["risk_level"] == "exact")
    near = sum(1 for p in pairs if p["risk_level"] == "near_duplicate")

    return {
        "total_pages_analysed": len([p for p in pages if p.get("body_text", "").strip()]),
        "duplicate_pairs": len(pairs),
        "exact_duplicates": exact,
        "near_duplicates": near,
        "pairs": pairs,
    }
