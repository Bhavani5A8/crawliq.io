"""
intent_classifier.py — Rule-based keyword intent classifier
============================================================

Classifies any keyword string into one of four search intent categories:
  informational   — user wants to learn (how, why, what, guide, tutorial)
  commercial      — user is researching before buying (best, review, vs)
  transactional   — user is ready to act (buy, price, coupon, sign up)
  navigational    — user is looking for a specific site/brand

No ML model, no training data, no external API.
Inference: < 0.1ms per keyword.

Public API
----------
  classify_intent(keyword: str) -> str
      Returns one of: "informational", "commercial", "transactional",
                      "navigational"

  classify_keywords(keywords: list) -> dict
      Classifies a list of keyword strings or {keyword:..., ...} dicts.
      Returns:
        {
          "intents":       {keyword: intent, ...},
          "distribution":  {"informational": n, "commercial": n, ...}
        }

  intent_label(intent: str) -> dict
      Returns {"color": "#hex", "short": "Info", "long": "Informational"}
      for UI badge rendering.
"""

from __future__ import annotations

import re

# ── Word sets — ordered from most-specific to least-specific ─────────────────
# Each word/phrase checked against the lowercased keyword.

_TRANSACTIONAL_WORDS = {
    "buy", "purchase", "order", "shop", "checkout", "cart", "price",
    "pricing", "cost", "costs", "fee", "fees", "coupon", "coupons",
    "discount", "discounts", "deal", "deals", "offer", "offers",
    "cheap", "cheapest", "affordable", "hire", "rent", "rental",
    "subscribe", "subscription", "sign up", "get started", "start free",
    "free trial", "trial", "demo", "quote", "estimate", "install",
    "download", "book", "booking", "reserve", "reservation",
    "register", "enroll", "apply", "apply now", "open account",
}

_COMMERCIAL_WORDS = {
    "best", "top", "review", "reviews", "reviewed", "rating", "ratings",
    "rated", "vs", "versus", "compare", "comparison", "compared",
    "alternative", "alternatives", "competitor", "competitors",
    "ranking", "rankings", "ranked", "recommended", "worth", "worth it",
    "pros", "cons", "pros and cons", "benefits", "drawbacks",
    "should i", "is it worth", "honest", "unbiased",
}

_INFORMATIONAL_WORDS = {
    "how", "why", "what", "when", "where", "who", "which",
    "guide", "guides", "tutorial", "tutorials", "tips", "tricks",
    "learn", "learning", "understand", "understanding", "explain",
    "explanation", "definition", "meaning", "example", "examples",
    "introduction", "intro", "overview", "basics", "beginner",
    "beginners", "101", "complete", "ultimate", "comprehensive",
    "step by step", "steps", "checklist", "checklists", "list of",
    "types of", "ways to", "ideas", "strategy", "strategies",
    "techniques", "methods", "approach", "difference between",
    "does", "can", "should", "will", "is", "are", "was", "were",
    "has", "have", "do", "does",
}

_NAVIGATIONAL_WORDS = {
    "login", "log in", "sign in", "sign-in", "signin",
    "account", "dashboard", "portal", "admin", "backend",
    "official", "website", "homepage", "home page", "site",
    "contact", "contact us", "support", "help center", "faq",
    "documentation", "docs", "changelog", "status page",
    "app", "mobile app", "ios", "android",
}

# Question-word patterns — fast regex check before word-set lookup
_QUESTION_PATTERN = re.compile(
    r"^(how|why|what|when|where|who|which|is|are|can|should|does|do|will|was|were)\b",
    re.IGNORECASE,
)

# Multi-word phrases to check (substring match in keyword)
_TRANSACTIONAL_PHRASES = [
    "sign up", "get started", "start free", "free trial",
    "open account", "apply now",
]
_COMMERCIAL_PHRASES = [
    "pros and cons", "is it worth", "should i buy", "vs ",
    "compared to", "pros cons",
]
_INFORMATIONAL_PHRASES = [
    "how to", "step by step", "how does", "what is", "what are",
    "difference between", "types of", "ways to", "list of",
    "complete guide", "ultimate guide",
]
_NAVIGATIONAL_PHRASES = [
    "log in", "sign in", "contact us", "help center", "mobile app",
]


def classify_intent(keyword: str) -> str:
    """
    Classify a single keyword string into one of four intent buckets.

    Priority order (most-commercial wins):
      transactional > navigational > commercial > informational
    """
    if not keyword:
        return "informational"

    kw    = keyword.lower().strip()
    words = set(kw.split())

    # ── 1. Transactional (highest commercial value) ───────────────────────────
    if words & _TRANSACTIONAL_WORDS:
        return "transactional"
    if any(phrase in kw for phrase in _TRANSACTIONAL_PHRASES):
        return "transactional"

    # ── 2. Navigational (brand/site lookups) ─────────────────────────────────
    if words & _NAVIGATIONAL_WORDS:
        return "navigational"
    if any(phrase in kw for phrase in _NAVIGATIONAL_PHRASES):
        return "navigational"

    # ── 3. Commercial (research / comparison intent) ──────────────────────────
    if words & _COMMERCIAL_WORDS:
        return "commercial"
    if any(phrase in kw for phrase in _COMMERCIAL_PHRASES):
        return "commercial"

    # ── 4. Informational — question patterns ─────────────────────────────────
    if _QUESTION_PATTERN.match(kw):
        return "informational"
    if words & _INFORMATIONAL_WORDS:
        return "informational"
    if any(phrase in kw for phrase in _INFORMATIONAL_PHRASES):
        return "informational"

    # ── Default ───────────────────────────────────────────────────────────────
    return "informational"


def classify_keywords(keywords: list) -> dict:
    """
    Classify a list of keyword strings or dicts.

    Accepts:
      - list of str: ["seo guide", "buy seo tool", ...]
      - list of dict: [{"keyword": "seo guide", ...}, ...]
      - mixed list

    Returns:
      {
        "intents":      {"seo guide": "informational", ...},
        "distribution": {"informational": 4, "commercial": 2, ...}
      }
    """
    dist: dict[str, int] = {
        "informational": 0,
        "commercial":    0,
        "transactional": 0,
        "navigational":  0,
    }
    intents: dict[str, str] = {}

    for k in keywords:
        kw = k if isinstance(k, str) else (k.get("keyword") or "")
        if not kw:
            continue
        intent = classify_intent(kw)
        intents[kw] = intent
        dist[intent] = dist.get(intent, 0) + 1

    return {"intents": intents, "distribution": dist}


def intent_label(intent: str) -> dict:
    """
    Returns UI metadata for an intent string.
    Used by frontend to render colored badges.
    """
    _MAP = {
        "informational": {
            "color": "#3b82f6",   # blue
            "bg":    "#1e3a5f",
            "short": "Info",
            "long":  "Informational",
        },
        "commercial": {
            "color": "#f59e0b",   # amber
            "bg":    "#4a2e00",
            "short": "Comm",
            "long":  "Commercial",
        },
        "transactional": {
            "color": "#10b981",   # green
            "bg":    "#0a2e1e",
            "short": "Trans",
            "long":  "Transactional",
        },
        "navigational": {
            "color": "#a78bfa",   # purple
            "bg":    "#2d1a5f",
            "short": "Nav",
            "long":  "Navigational",
        },
    }
    return _MAP.get(intent, _MAP["informational"])
