"""
openai_adapter.py — Drop-in OpenAI replacement for gemini_analysis.py AI calls.

Usage: set USE_OPENAI=true in environment, or call generate_with_openai() directly.

Requires: pip install openai
Set: OPENAI_API_KEY=sk-...
"""

import os, json, re, logging
logger = logging.getLogger(__name__)

def _get_openai_client():
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Run: pip install openai")
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    return OpenAI(api_key=key)

def generate_with_openai(prompt: str, max_tokens: int = 800) -> str:
    """
    Call OpenAI GPT-4o-mini with the same prompt used for Gemini.
    Returns raw text response.
    Falls back gracefully on any error.
    """
    client = _get_openai_client()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",     # cheapest capable model: $0.15/1M tokens
            messages=[
                {"role": "system",
                 "content": "You are a professional SEO content optimizer. "
                             "Return ONLY valid JSON — no markdown, no explanation."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.15,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},  # forces valid JSON output
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.error("OpenAI call failed: %s", exc)
        return ""
