"""
ai_fallback.py — CrawlIQ AI provider fallback chain

Tries each configured AI provider in priority order:
  1. Gemini  (GEMINI_API_KEY)
  2. Groq    (GROQ_API_KEY)
  3. OpenAI  (OPENAI_API_KEY)
  4. Claude  (ANTHROPIC_API_KEY)
  5. Ollama  (local, no key)
  6. Rule-based fallback (always available)

Returns the AI-generated result plus which provider was used.

Usage:
    from ai_fallback import run_with_fallback
    result = await run_with_fallback(prompt, page_data)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Provider priority list ────────────────────────────────────────────────────
# Ordered: try each in sequence, skip if no key, stop on first success.
_PROVIDER_CHAIN = ["gemini", "groq", "openai", "claude", "ollama"]

_KEY_ENV_VARS = {
    "gemini":  "GEMINI_API_KEY",
    "groq":    "GROQ_API_KEY",
    "openai":  "OPENAI_API_KEY",
    "claude":  "ANTHROPIC_API_KEY",
    "ollama":  None,   # no key needed
}


def _provider_available(provider: str) -> bool:
    """Return True if the provider has a key configured (or needs none)."""
    key_var = _KEY_ENV_VARS.get(provider)
    if key_var is None:
        return True   # Ollama — always try
    return bool(os.getenv(key_var, "").strip())


def _available_providers() -> list[str]:
    """Return providers in chain order that have keys configured."""
    return [p for p in _PROVIDER_CHAIN if _provider_available(p)]


# ── Adapter wrappers ──────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai
    api_key = os.getenv("GEMINI_API_KEY", "")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
    )
    response = model.generate_content(prompt)
    return response.text


def _call_groq(prompt: str) -> str:
    from groq_adapter import generate_with_groq
    return generate_with_groq(prompt)


def _call_openai(prompt: str) -> str:
    from openai_adapter import generate_with_openai
    return generate_with_openai(prompt)


def _call_claude(prompt: str) -> str:
    from claude_adapter import generate_with_claude
    return generate_with_claude(prompt)


def _call_ollama(prompt: str) -> str:
    from ollama_adapter import generate_with_ollama
    return generate_with_ollama(prompt)


_PROVIDER_CALLERS = {
    "gemini": _call_gemini,
    "groq":   _call_groq,
    "openai": _call_openai,
    "claude": _call_claude,
    "ollama": _call_ollama,
}


# ── Rule-based fallback ───────────────────────────────────────────────────────

def _rule_based(page: dict) -> dict:
    """
    Pure-Python heuristic fixes — always available, no API calls.
    Mirrors the logic in gemini_analysis._rule_based_fallback().
    """
    try:
        from gemini_analysis import _rule_based_fallback
        return _rule_based_fallback(page)
    except ImportError:
        pass

    title = page.get("title", "")
    meta  = page.get("meta_description", "")
    h1    = page.get("h1", "")
    url   = page.get("url", "")

    new_title = title if len(title) >= 30 else f"{h1 or url} | CrawlIQ"
    new_meta  = meta  if len(meta)  >= 50 else (
        f"Learn more about {h1 or url}. Technical SEO reference and evaluation guide."
    )

    return {
        "new_title":       new_title[:70],
        "new_meta":        new_meta[:160],
        "new_h1":          h1 or title,
        "reasoning":       "Rule-based heuristic (no AI provider configured)",
        "provider_used":   "rules",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def call_with_fallback(prompt: str, page: dict) -> dict:
    """
    Synchronous fallback chain.
    Tries each available AI provider in order; falls back to rule-based.

    Returns:
        dict with at least {"raw_text": str, "provider_used": str}
        or a rule-based dict on full failure.
    """
    chain = _available_providers()

    for provider in chain:
        caller = _PROVIDER_CALLERS.get(provider)
        if not caller:
            continue
        try:
            raw_text = caller(prompt)
            if raw_text and raw_text.strip():
                logger.info("AI provider '%s' succeeded", provider)
                return {"raw_text": raw_text, "provider_used": provider}
        except Exception as exc:
            logger.warning("AI provider '%s' failed (%s), trying next…", provider, exc)
            continue

    # All providers failed — rule-based fallback
    logger.warning("All AI providers failed — using rule-based fallback")
    result = _rule_based(page)
    result["provider_used"] = "rules"
    return result


async def call_with_fallback_async(prompt: str, page: dict) -> dict:
    """Async wrapper — runs the synchronous fallback chain in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, call_with_fallback, prompt, page)


def get_provider_status() -> dict:
    """
    Return which providers are configured and which would be tried.
    Useful for /healthz and AI setup UI.
    """
    status = {}
    for provider in _PROVIDER_CHAIN:
        key_var = _KEY_ENV_VARS.get(provider)
        if key_var is None:
            status[provider] = {"available": True, "key_required": False}
        else:
            has_key = bool(os.getenv(key_var, "").strip())
            status[provider] = {
                "available":     has_key,
                "key_required":  True,
                "env_var":       key_var,
            }
    available = _available_providers()
    return {
        "chain":              _PROVIDER_CHAIN,
        "available":          available,
        "active_provider":    available[0] if available else "rules",
        "fallback_to_rules":  len(available) == 0,
        "providers":          status,
    }
