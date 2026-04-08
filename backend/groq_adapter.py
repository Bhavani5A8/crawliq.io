"""
groq_adapter.py — Use Groq API (free tier: 30 req/min, Llama3 model).

Free tier is much more generous than Gemini for short requests.
Sign up: https://console.groq.com  (free account)
Set: GROQ_API_KEY=gsk_...

Requires: pip install groq
"""

import os, logging
logger = logging.getLogger(__name__)

GROQ_MODEL = "llama-3.1-8b-instant"  # fast, free, good SEO quality


def generate_with_groq(prompt: str) -> str:
    """Call Groq API, return raw text. Falls back to empty string on error."""
    try:
        from groq import Groq
    except ImportError:
        raise ImportError("Run: pip install groq")

    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        raise ValueError("GROQ_API_KEY not set — get free key at console.groq.com")

    client = Groq(api_key=key)
    try:
        # BUG-003: hard 15-second timeout prevents hanging the thread pool worker.
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system",
                 "content": "You are a professional SEO content optimizer. "
                             "Return ONLY valid JSON — no markdown, no explanation."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.15,
            max_tokens=800,
            timeout=15,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.error("Groq call failed: %s", exc)
        return ""
