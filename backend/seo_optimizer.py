"""
seo_optimizer.py — Live Optimization Table Generator.

Generates a precise, actionable "Live Optimization Table" for each page
using structured page data and Groq AI (Llama3).

AI provider is selected via AI_PROVIDER environment variable:
  AI_PROVIDER=groq    → Groq Llama3 (DEFAULT — free tier, needs GROQ_API_KEY)
  AI_PROVIDER=gemini  → Google Gemini (needs GEMINI_API_KEY)
  AI_PROVIDER=openai  → OpenAI GPT-4o-mini (needs OPENAI_API_KEY)
  AI_PROVIDER=claude  → Anthropic Claude (needs ANTHROPIC_API_KEY)
  AI_PROVIDER=rules   → Rule-based only (no API, always works)

Rules (non-negotiable):
  - NO placeholders: [brand], [keyword], [topic] etc.
  - Every optimized_value must be real and paste-ready.
  - Context is derived ONLY from URL + content snippet + existing tags.
  - If data is genuinely insufficient → "Insufficient Data" (not generic advice).
  - Only include fields that have actual issues — skip already-optimized fields.
  - Supports: Title, Meta Description, H1, H2, Canonical, URL Slug

Output per row:
  {url, field, status, current_value, optimized_value, seo_logic}
"""

import os
import json
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama3-70b-8192")

# Reject AI values with placeholder brackets/braces/angles: [keyword], {brand}, <insert>
_PLACEHOLDER_RE = re.compile(r"\[.*?\]|\{.*?\}|<[^>]+>")

# BUG-N11: also reject common English word-based placeholders that AI uses
# when it lacks real page data: "your brand", "insert keyword", "example.com".
_PLACEHOLDER_WORDS = re.compile(
    r"\b(insert|your[\s_-]?brand|your[\s_-]?keyword|example\.com|"
    r"company[\s_-]?name|placeholder|todo|tbd)\b",
    re.IGNORECASE,
)
BATCH_SIZE   = 2    # pages per API call — keep low for free tier
MAX_WORKERS  = 1    # sequential batches — avoids rate limit bursts
MAX_RETRIES  = 2
BATCH_DELAY  = 10   # seconds between batches — respects free-tier rate limits

# AI provider (mirrors gemini_analysis.py)
AI_PROVIDER  = os.getenv("AI_PROVIDER", "groq").lower()

# ── Shared status (polled by /optimize-status) ────────────────────────────────
optimizer_status = {
    "running":   False,
    "done":      False,
    "error":     None,
    "processed": 0,
    "total":     0,
}

# ── In-memory store for optimization results ─────────────────────────────────
_optimization_store: dict[str, list[dict]] = {}


def get_optimization_table(urls: list[str] | None = None) -> list[dict]:
    """
    Return all optimization rows, optionally filtered to specific URLs.
    Each row: {url, field, status, current_value, optimized_value, seo_logic}
    """
    if urls:
        url_set = set(urls)
        return [row for url, rows in _optimization_store.items()
                if url in url_set for row in rows]
    return [row for rows in _optimization_store.values() for row in rows]


def clear_optimization_store() -> None:
    _optimization_store.clear()


# ── Page validation ───────────────────────────────────────────────────────────

def _is_optimizable(page: dict) -> bool:
    """Only optimize pages that loaded successfully and have real content."""
    if page.get("_is_error"):
        return False
    if str(page.get("status_code", "")) != "200":
        return False
    if not (page.get("body_text") or "").strip():
        return False
    if not page.get("issues"):
        return False
    return True


# ── Main entry point ──────────────────────────────────────────────────────────

def run_optimization(pages: list[dict], urls: list[str] | None = None) -> None:
    """
    Generate the Live Optimization Table for the given pages.
    If urls is provided, only those pages are processed.
    Mutates _optimization_store in-place.

    Called via loop.run_in_executor() from main.py — never blocks event loop.
    """
    candidates = [p for p in pages if _is_optimizable(p)]
    if urls:
        url_set    = set(urls)
        candidates = [p for p in candidates if p["url"] in url_set]

    optimizer_status.update({
        "running": True, "done": False, "error": None,
        "processed": 0, "total": len(candidates),
    })

    if not candidates:
        optimizer_status.update({"running": False, "done": True})
        return

    # If rules-only mode or no API key available, use rule-based fallback
    if AI_PROVIDER == "rules" or not _has_api_key():
        for page in candidates:
            result = _rule_based_rows(page)
            if result["rows"]:
                _optimization_store[result["url"]] = result["rows"]
            optimizer_status["processed"] += 1
        optimizer_status.update({"running": False, "done": True})
        return

    batches = [candidates[i:i + BATCH_SIZE]
               for i in range(0, len(candidates), BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for batch_idx, batch in enumerate(batches):
            if batch_idx > 0:
                time.sleep(BATCH_DELAY)  # rate limit protection
            future = executor.submit(_run_batch, batch)
            try:
                batch_results = future.result()
                for item in batch_results:
                    url  = item.get("url", "")
                    rows = item.get("rows", [])
                    if url and rows:
                        _optimization_store[url] = rows
                optimizer_status["processed"] += len(batch)
            except Exception as e:
                logger.error("Optimizer batch failed: %s", e)
                optimizer_status["processed"] += len(batch)
                # BUG-N36: write rule-based fallback rows so these pages always
                # appear in /optimize-table rather than silently disappearing.
                for item in [_rule_based_rows(p) for p in batch]:
                    url  = item.get("url", "")
                    rows = item.get("rows", [])
                    if url and rows:
                        _optimization_store[url] = rows

    optimizer_status.update({"running": False, "done": True})


def _has_api_key() -> bool:
    """Check if the current provider has its API key set."""
    key_map = {
        "groq":   "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "ollama": None,
    }
    env_var = key_map.get(AI_PROVIDER)
    if env_var is None:
        return True
    return bool(os.getenv(env_var, ""))


# ── Batch with retry ──────────────────────────────────────────────────────────

def _run_batch(batch: list[dict]) -> list[dict]:
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            return _call_ai(batch)
        except Exception as e:
            logger.warning("Optimizer attempt %d failed: %s", attempt, e)
            if attempt <= MAX_RETRIES:
                # BUG-N25: use constant 2s delay instead of exponential 2**attempt.
                # Exponential backoff caused up to 4s dead time per retry on free-tier;
                # constant delay is predictable and sufficient for rate-limit recovery.
                time.sleep(2)

    logger.error("All optimizer retries failed — using rule-based fallback")
    return [_rule_based_rows(p) for p in batch]


# ── AI call — routes to selected provider ─────────────────────────────────────

def _call_ai(batch: list[dict]) -> list[dict]:
    """Route to the correct AI provider and parse response."""
    prompt = _build_prompt(batch)

    if AI_PROVIDER == "groq":
        raw = _call_groq(prompt)
    elif AI_PROVIDER == "gemini":
        raw = _call_gemini(prompt)
    elif AI_PROVIDER == "openai":
        raw = _call_openai(prompt)
    elif AI_PROVIDER == "claude":
        raw = _call_claude(prompt)
    elif AI_PROVIDER == "ollama":
        raw = _call_ollama(prompt)
    else:
        raw = ""

    if not raw:
        return [_rule_based_rows(p) for p in batch]
    return _parse_response(batch, raw)


# ── Groq call (primary default) ──────────────────────────────────────────────

def _call_groq(prompt: str) -> str:
    """
    Call Groq API — primary AI provider.
    Uses llama3-70b-8192 (free tier: 30 req/min, no daily cap).
    temperature=0 for fully deterministic, consistent output.
    """
    try:
        from groq import Groq
    except ImportError:
        raise ImportError(
            "Groq SDK not installed. Run: pip install groq\n"
            "Then set: GROQ_API_KEY=gsk_..."
        )

    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        raise ValueError(
            "GROQ_API_KEY not set.\n"
            "Get a free key at: https://console.groq.com\n"
            "Then run: set GROQ_API_KEY=gsk_your_key"
        )

    client = Groq(api_key=key)
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a Senior Technical SEO Analyst. "
                    "Return ONLY valid JSON — no markdown fences, no explanation text. "
                    "Every optimized value must be real and immediately usable. "
                    "Never use placeholders like [brand], [keyword], or [insert]."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=2000,
    )
    return response.choices[0].message.content or ""


# ── Gemini call (optional fallback) ──────────────────────────────────────────

def _call_gemini(prompt: str) -> str:
    """Call Google Gemini. Returns raw text, empty string on failure."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError("Run: pip install google-genai")

    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=2000,
        ),
    )
    return response.text or ""


# ── OpenAI call ───────────────────────────────────────────────────────────────

def _call_openai(prompt: str) -> str:
    """Call OpenAI GPT-4o-mini."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Run: pip install openai")

    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")

    client   = OpenAI(api_key=key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system",
             "content": "You are a Senior Technical SEO Analyst. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


# ── Claude call ───────────────────────────────────────────────────────────────

def _call_claude(prompt: str) -> str:
    """Call Anthropic Claude."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("Run: pip install anthropic")

    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    client  = anthropic.Anthropic(api_key=key)
    message = client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=2000,
        temperature=0,
        system="You are a Senior Technical SEO Analyst. Return ONLY valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text if message.content else ""


# ── Ollama call ───────────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> str:
    """Call local Ollama instance."""
    import urllib.request

    ollama_host  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2")
    payload = json.dumps({
        "model": ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 2000},
    }).encode()

    req = urllib.request.Request(
        f"{ollama_host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("response", "")
    except Exception as exc:
        raise RuntimeError(f"Ollama call failed: {exc}")


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(batch: list[dict]) -> str:
    """
    Build the Live Optimization Table prompt.
    Only includes fields with actual issues to keep prompt tight and cost-efficient.
    """
    pages_info = []
    for p in batch:
        url       = p.get("url", "")
        title     = p.get("title", "") or "MISSING"
        meta      = p.get("meta_description", "") or "MISSING"
        h1_val    = (p.get("h1") or ["MISSING"])[0]
        canon     = p.get("canonical", "") or "MISSING"
        kws       = ", ".join((p.get("keywords") or [])[:8]) or "not extracted"
        body      = (p.get("body_text") or "")[:800].strip()
        issues    = p.get("issues", [])
        issue_set = set(issues)

        problem_fields = []
        if "Missing Title" in issue_set:
            problem_fields.append("  Title: MISSING")
        elif "Title Too Long" in issue_set:
            problem_fields.append(f'  Title: "{title}" [{len(title)} chars — over 60]')

        if "Missing Meta Description" in issue_set:
            problem_fields.append("  Meta Description: MISSING")
        elif "Duplicate Meta Description" in issue_set:
            problem_fields.append(f'  Meta Description: "{meta[:80]}..." [DUPLICATE]')

        if "Missing H1" in issue_set:
            problem_fields.append("  H1: MISSING")
        elif "Multiple H1 Tags" in issue_set:
            problem_fields.append(f'  H1: MULTIPLE — {" | ".join(p.get("h1", [])[:3])}')

        if "Missing H2" in issue_set:
            problem_fields.append("  H2: MISSING")

        if "Missing Canonical" in issue_set:
            problem_fields.append("  Canonical: MISSING")
        elif "Canonical Mismatch" in issue_set:
            problem_fields.append(f'  Canonical: "{canon}" [MISMATCH]')

        if not problem_fields:
            continue

        pages_info.append(
            f"URL: {url}\n"
            f"Page keywords: {kws}\n"
            f"Content preview:\n{body}\n"
            f"Problem fields:\n" + "\n".join(problem_fields) + "\n"
            f"All issues: {', '.join(issues)}"
        )

    if not pages_info:
        return "[]"

    pages_block = "\n\n" + ("=" * 60) + "\n\n".join(pages_info)

    return f"""You are a Senior Technical SEO Analyst. Generate a Live Optimization Table.

ABSOLUTE RULES:
1. optimized_value must be IMMEDIATELY usable — paste into CMS right now.
2. NEVER use placeholders: [brand], [keyword], [insert], [CTA], or similar.
3. Use ONLY the provided keywords and content preview.
4. Title: 50-60 chars exactly. Meta: 140-160 chars exactly.
5. Return ONLY valid JSON — no markdown, no explanation outside JSON.

{pages_block}

Return this exact JSON (one entry per URL, one row per problem field):
[
  {{
    "url": "<exact url>",
    "rows": [
      {{
        "field": "Title",
        "status": "Missing",
        "current_value": "<current or MISSING>",
        "optimized_value": "<paste-ready 50-60 char title using page keywords>",
        "seo_logic": "<why this improves ranking for this specific page>"
      }},
      {{
        "field": "Meta Description",
        "status": "Missing",
        "current_value": "<current or MISSING>",
        "optimized_value": "<paste-ready 140-160 char meta — no placeholders>",
        "seo_logic": "<CTR improvement for this page>"
      }},
      {{
        "field": "H1",
        "status": "Missing",
        "current_value": "<current or MISSING>",
        "optimized_value": "<paste-ready H1 using page keywords>",
        "seo_logic": "<topical authority improvement>"
      }},
      {{
        "field": "H2",
        "status": "Missing",
        "current_value": "MISSING",
        "optimized_value": "<logical subheading from content>",
        "seo_logic": "<content structure improvement>"
      }},
      {{
        "field": "Canonical",
        "status": "Missing",
        "current_value": "MISSING",
        "optimized_value": "<exact self-referencing canonical URL>",
        "seo_logic": "<duplicate content prevention>"
      }}
    ]
  }}
]"""


# ── Response parser ───────────────────────────────────────────────────────────

def _sanitize_optimized_value(value: str) -> str:
    """
    BUG-008: reject AI values that contain placeholder text such as
    [brand], {keyword}, <insert here>.  Replace them with a signal so
    users know AI couldn't produce a clean value rather than silently
    copying broken text into their CMS.
    """
    if not value:
        return value
    # BUG-N11: check both bracket-style and word-based placeholders
    if _PLACEHOLDER_RE.search(value) or _PLACEHOLDER_WORDS.search(value):
        logger.warning("Optimizer: placeholder detected in AI value — falling back: %s", value[:80])
        return "Insufficient Data — AI produced a placeholder value; run again or edit manually."
    return value


def _parse_response(batch: list[dict], raw: str) -> list[dict]:
    """Parse AI JSON response into optimization rows."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"```", "", cleaned).strip()

    match = re.search(r"\[.*\]", cleaned, re.S)
    if not match:
        logger.warning("No JSON array in optimizer response: %s", raw[:200])
        return [_rule_based_rows(p) for p in batch]

    try:
        data = json.loads(match.group())
        result_map = {}
        for item in data:
            if isinstance(item, dict) and "url" in item:
                rows = item.get("rows", [])
                for row in rows:
                    row["url"] = item["url"]
                    # BUG-008: sanitize each row's optimized_value
                    row["optimized_value"] = _sanitize_optimized_value(
                        row.get("optimized_value", "")
                    )
                result_map[item["url"]] = item

        return [
            result_map.get(p["url"], _rule_based_rows(p))
            for p in batch
        ]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Optimizer JSON parse error: %s", e)
        return [_rule_based_rows(p) for p in batch]


# ── Rule-based fallback ───────────────────────────────────────────────────────

_STATUS_MAP = {
    "Missing Title":                ("Title",            "Missing"),
    "Title Too Long":               ("Title",            "Too Long"),
    "Title Too Short":              ("Title",            "Too Short"),   # BUG-012
    "Missing Meta Description":     ("Meta Description", "Missing"),
    "Duplicate Meta Description":   ("Meta Description", "Duplicate"),
    "Meta Description Too Long":    ("Meta Description", "Too Long"),    # BUG-013
    "Meta Description Too Short":   ("Meta Description", "Too Short"),   # BUG-013
    "Missing H1":                   ("H1",               "Missing"),
    "Multiple H1 Tags":             ("H1",               "Multiple"),
    "Missing H2":                   ("H2",               "Missing"),
    "Missing Canonical":            ("Canonical",        "Missing"),
    "Canonical Mismatch":           ("Canonical",        "Mismatch"),
}

_FALLBACK_LOGIC = {
    ("Title", "Missing"):              "Missing title prevents Google from understanding page topic — direct ranking loss.",
    ("Title", "Too Long"):             "Title truncation in SERPs reduces CTR by hiding the key message.",
    ("Title", "Too Short"):            "Short titles (<30 chars) like 'Home' give Google no topical signal — expand with primary keyword.",
    ("Meta Description", "Missing"):   "Google generates auto-snippets which often miss the page value proposition.",
    ("Meta Description", "Duplicate"): "Identical snippets reduce click differentiation in SERPs.",
    ("Meta Description", "Too Long"):  "Meta >160 chars is truncated in SERPs, cutting off the call-to-action.",
    ("Meta Description", "Too Short"): "Short meta (<70 chars) wastes available SERP real estate and reduces CTR.",
    ("H1", "Missing"):                 "H1 absence removes the strongest on-page topical signal for crawlers.",
    ("H1", "Multiple"):                "Multiple H1 tags dilute page authority and confuse topical focus.",
    ("H2", "Missing"):                 "No H2 structure reduces scannability and secondary keyword coverage.",
    ("Canonical", "Missing"):          "Missing canonical allows duplicate URL variants to split link equity.",
    ("Canonical", "Mismatch"):         "Mismatched canonical signals this page is a duplicate — suppresses ranking.",
}


def _rule_based_rows(page: dict) -> dict:
    url    = page.get("url", "")
    issues = page.get("issues", [])
    seen   = set()
    rows   = []

    for issue in issues:
        if issue not in _STATUS_MAP:
            continue
        field, status = _STATUS_MAP[issue]
        if field in seen:
            continue
        seen.add(field)

        current = {
            "Title":            page.get("title", "") or "MISSING",
            "Meta Description": page.get("meta_description", "") or "MISSING",
            "H1":               (page.get("h1") or ["MISSING"])[0],
            "H2":               (page.get("h2") or ["MISSING"])[0],
            "Canonical":        page.get("canonical", "") or "MISSING",
        }.get(field, "MISSING")

        rows.append({
            "url":             url,
            "field":           field,
            "status":          status,
            "current_value":   current,
            "optimized_value": "Insufficient Data — run AI analysis for page-specific optimization.",
            "seo_logic":       _FALLBACK_LOGIC.get((field, status), "Review this field."),
        })

    return {"url": url, "rows": rows}
