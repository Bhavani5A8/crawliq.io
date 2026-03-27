"""
claude_adapter.py — Anthropic Claude as the AI provider for SEO content generation.

Claude is excellent for structured SEO content:
- Very precise JSON output
- Follows instructions strictly
- Low hallucination rate
- claude-3-haiku-20240307  → fastest + cheapest ($0.25/1M tokens input)
- claude-3-5-haiku-20241022 → better quality, still affordable
- claude-sonnet-4-5         → best quality

Setup:
  1. pip install anthropic
  2. Get API key: https://console.anthropic.com
  3. set ANTHROPIC_API_KEY=sk-ant-...
  4. set AI_PROVIDER=claude
"""

import os
import logging

logger = logging.getLogger(__name__)

# Model options — change CLAUDE_MODEL to switch
# claude-haiku-4-5-20251001          fastest, cheapest
# claude-sonnet-4-6                  best quality
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


def generate_with_claude(prompt: str, max_tokens: int = 800) -> str:
    """
    Call Anthropic Claude with the SEO content prompt.
    Returns raw text response (JSON string).
    Falls back to empty string on any failure — never raises.

    Claude is instructed via the system prompt to return ONLY valid JSON
    so _parse_content_response() can parse it directly.
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("Run: pip install anthropic")

    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. "
            "Get your key at https://console.anthropic.com"
        )

    client = anthropic.Anthropic(api_key=key)

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            temperature=0.15,   # low = deterministic, no hallucination
            system=(
                "You are a professional SEO content optimizer. "
                "You ONLY output valid JSON — no markdown fences, "
                "no explanation, no text outside the JSON object. "
                "Every value you generate must be based strictly on "
                "the page data provided. Never hallucinate or invent context."
            ),
            messages=[
                {"role": "user", "content": prompt}
            ],
        )
        # Claude returns a list of content blocks
        return message.content[0].text if message.content else ""

    except anthropic.RateLimitError as exc:
        logger.warning("Claude rate limit: %s", exc)
        return ""
    except anthropic.AuthenticationError:
        logger.error("Claude: invalid ANTHROPIC_API_KEY")
        return ""
    except Exception as exc:
        logger.error("Claude call failed: %s", exc)
        return ""
