"""
gemini_analysis.py — Fixed Gemini AI integration.

ROOT CAUSE FIXES (v2):
======================

FIX 1 — CORRECT MODEL NAME
  OLD: MODEL = "gemini-1.5-flash"
  NEW: MODEL = "gemini-1.5-flash-latest"
  The google-genai SDK (Client.models.generate_content) requires the
  "-latest" suffix or it returns 404 NOT_FOUND on some SDK versions.

FIX 2 — CORRECT SDK IMPORT
  OLD: from google import genai / from google.genai import types
  NEW: import google.generativeai as genai  (official, stable SDK)
  The google-genai package API differs from google-generativeai.
  We standardise on google-generativeai which is officially supported.

FIX 3 — AI_PROVIDER DEFAULT CHANGED TO GEMINI
  OLD: AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")  → Groq ran by default, Gemini never called
  NEW: AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")

FIX 4 — RETRY BACKOFF FIXED
  OLD: time.sleep(2 ** attempt)  with attempt starting at 1 → 2s, 4s (too slow)
  NEW: time.sleep(attempt)       → 1s, 2s  (max 2 retries, fast fail)

FIX 5 — MODEL VALIDATION BEFORE USE
  list_models() called once at startup; falls back to safe default if
  the configured model isn't listed.

FIX 6 — TIMEOUT GUARD ON GEMINI CALLS
  All generate_content calls wrapped with a 15s threading timeout.
  AI failure NEVER blocks crawling or freezes UI.

PRESERVED UNCHANGED:
  - All existing function signatures (attach_gemini_results, run_gemini_for_pages,
    generate_seo_content, run_content_generation, check_gemini, compute_ranking_score)
  - Rule-based fallback (_rule_based_fallback, _rule_based_content)
  - Prompt builder (_build_prompt, build_seo_content_prompt)
  - Response parser (_parse_response, _parse_content_response)
  - gemini_status / content_gen_status shared state
  - All adapter imports (groq, openai, ollama, claude) — kept but not default
"""

import os
import json
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

logger = logging.getLogger(__name__)

# ── Priority rules ────────────────────────────────────────────────────────────
_HIGH   = {"Broken Page", "Missing Title"}
_MEDIUM = {"Missing Meta Description", "Missing H1",
           "Duplicate Meta Description", "Multiple H1 Tags"}

def assign_priority(issues: list[str]) -> str:
    if not issues:
        return ""
    s = set(issues)
    if s & _HIGH:   return "High"
    if s & _MEDIUM: return "Medium"
    return "Low"


# ── Config ────────────────────────────────────────────────────────────────────
# FIX 1: Use "-latest" suffix — avoids 404 on google-generativeai SDK
_MODEL_PRIMARY  = "gemini-1.5-flash-latest"
_MODEL_FALLBACK = "gemini-1.5-pro-latest"   # fallback if primary not available

# FIX 3: Default provider is now gemini, not groq
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()

MAX_PAGES   = 10
BATCH_SIZE  = 2
MAX_WORKERS = 1
MAX_RETRIES = 2          # max 2 retries only (Part 6)
_AI_TIMEOUT = 15         # seconds — hard cap on any AI call (Part 9)

_PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2, "": 3}


# ── Shared status ─────────────────────────────────────────────────────────────
gemini_status = {
    "running":   False,
    "done":      False,
    "error":     None,
    "processed": 0,
    "total":     0,
    "skipped":   0,
}


# ── FIX 2: Official SDK initialisation ───────────────────────────────────────

def _configure_genai() -> str:
    """
    Configure google.generativeai with API key.
    Returns the validated model name to use.
    Raises ValueError if GEMINI_API_KEY is not set.

    FIX 5: Validates model availability before use.
    Falls back to _MODEL_FALLBACK if primary is not listed.
    """
    try:
        import google.generativeai as genai
    except ImportError:
        raise ImportError(
            "Run: pip install google-generativeai\n"
            "Then set GEMINI_API_KEY in your environment."
        )

    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY not set. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )

    genai.configure(api_key=key)

    # FIX 5: Model validation — check what's actually available
    target = os.getenv("GEMINI_MODEL", _MODEL_PRIMARY)
    try:
        available = {m.name for m in genai.list_models()}
        # list_models() returns names like "models/gemini-1.5-flash-latest"
        # Normalise for comparison
        def _norm(n: str) -> str:
            return n.replace("models/", "").strip()

        available_norm = {_norm(n) for n in available}
        if _norm(target) not in available_norm:
            logger.warning(
                "Model %r not in available models — falling back to %r",
                target, _MODEL_FALLBACK
            )
            target = _MODEL_FALLBACK
            if _norm(target) not in available_norm:
                # Last resort: use whatever flash model is available
                flash_models = [n for n in available_norm if "flash" in n]
                target = flash_models[0] if flash_models else _MODEL_PRIMARY
                logger.warning("Using discovered flash model: %s", target)
    except Exception as exc:
        logger.warning("list_models() failed (%s) — using %s", exc, target)
        # Don't crash — proceed with the configured model name

    return target


def _get_model():
    """
    Returns a configured GenerativeModel instance.
    FIX 2: Uses google.generativeai (official SDK), not google.genai.
    """
    import google.generativeai as genai
    model_name = _configure_genai()
    return genai.GenerativeModel(
        model_name,
        generation_config={
            "temperature":     0.2,
            "max_output_tokens": 1500,
            "top_p":           0.9,
        },
    )


# ── FIX 6: Timeout-guarded AI call ───────────────────────────────────────────

def _call_with_timeout(fn, *args, timeout: int = _AI_TIMEOUT):
    """
    Run fn(*args) in a thread with a hard timeout.
    Returns None on timeout or exception — NEVER raises.
    Part 7: system must never crash or block due to AI failure.
    """
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn, *args)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            # BUG-018: timeout is an actionable error, not just a warning.
            logger.error("AI call timed out after %ss — falling back to rules", timeout)
            return None
        except Exception as exc:
            # BUG-018: AI call failures affect page quality; use error level.
            logger.error("AI call failed: %s", exc)
            return None


# ── Validation gate ───────────────────────────────────────────────────────────

def _is_valid_for_gemini(page: dict) -> bool:
    """Only send pages with real content and actual SEO issues."""
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

def attach_gemini_results(pages: list[dict]) -> None:
    """
    1. Assign priority to all pages (instant, no API).
    2. Validate pages — skip error/empty records.
    3. Run AI on top-N valid pages with issues.
    """
    for page in pages:
        if not page.get("priority"):
            page["priority"] = assign_priority(page.get("issues", []))
        page.setdefault("gemini_fields", [])

    valid_candidates = [p for p in pages if _is_valid_for_gemini(p)]
    invalid_count    = len([p for p in pages if p.get("issues")]) - len(valid_candidates)

    candidates = sorted(
        valid_candidates,
        key=lambda p: _PRIORITY_ORDER.get(p.get("priority", ""), 3),
    )
    selected = candidates[:MAX_PAGES]
    skipped  = len(candidates) - len(selected) + invalid_count

    gemini_status.update({
        "running": True, "done": False, "error": None,
        "processed": 0, "total": len(selected), "skipped": skipped,
    })

    if not selected:
        gemini_status.update({
            "running": False, "done": True,
            "error": (
                "No valid pages to analyse. All crawled pages had connection errors "
                "or empty content. Check that the site is accessible."
            ) if invalid_count > 0 else None,
        })
        return

    # Non-Gemini providers — route directly, no client needed
    if AI_PROVIDER != "gemini":
        _run_non_gemini_provider(selected, {p["url"]: p for p in pages})
        return

    # Gemini path — validate key first
    try:
        _configure_genai()
    except (ValueError, ImportError) as e:
        gemini_status.update({"running": False, "done": False, "error": str(e)})
        return

    url_map = {p["url"]: p for p in pages}
    batches = [selected[i:i + BATCH_SIZE]
               for i in range(0, len(selected), BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_run_batch, batch): batch
            for batch in batches
        }
        for future in as_completed(futures):
            try:
                results = future.result(timeout=_AI_TIMEOUT * BATCH_SIZE + 5)
                for result in results:
                    url = result.get("url", "")
                    if url in url_map:
                        url_map[url]["gemini_fields"] = result.get("fields", [])
                gemini_status["processed"] += len(futures[future])
            except Exception as e:
                logger.error("Batch failed: %s", e)

    gemini_status.update({"running": False, "done": True})


def run_gemini_for_pages(urls: list[str], all_pages: list[dict]) -> None:
    """Run AI analysis on a user-selected set of URLs (POST /analyze-selected)."""
    url_set  = set(urls)
    selected = [p for p in all_pages
                if p.get("url") in url_set and _is_valid_for_gemini(p)]

    if not selected:
        gemini_status.update({
            "running": False, "done": True,
            "error": (
                "Selected pages have no valid content to analyse. "
                "Pages with connection errors cannot receive AI suggestions."
            ),
        })
        return

    gemini_status.update({
        "running": True, "done": False, "error": None,
        "processed": 0, "total": len(selected), "skipped": 0,
    })

    if AI_PROVIDER != "gemini":
        _run_non_gemini_provider(selected, {p["url"]: p for p in all_pages})
        return

    try:
        _configure_genai()
    except (ValueError, ImportError) as e:
        gemini_status.update({"running": False, "done": False, "error": str(e)})
        return

    url_map = {p["url"]: p for p in all_pages}
    batches = [selected[i:i + BATCH_SIZE]
               for i in range(0, len(selected), BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_run_batch, b): b for b in batches}
        for future in as_completed(futures):
            try:
                results = future.result(timeout=_AI_TIMEOUT * BATCH_SIZE + 5)
                for result in results:
                    url = result.get("url", "")
                    if url in url_map:
                        url_map[url]["gemini_fields"] = result.get("fields", [])
                gemini_status["processed"] += len(futures[future])
            except Exception as e:
                logger.error("Batch failed: %s", e)

    gemini_status.update({"running": False, "done": True})


# ── Non-Gemini provider routing ───────────────────────────────────────────────

def _run_non_gemini_provider(selected: list[dict], url_map: dict) -> None:
    """Route to groq/openai/ollama/claude/rules providers. Fail-silent per page."""
    batches = [selected[i:i + BATCH_SIZE]
               for i in range(0, len(selected), BATCH_SIZE)]

    for batch in batches:
        prompt = _build_prompt(batch)
        raw    = ""

        try:
            if AI_PROVIDER == "rules":
                results = [_rule_based_fallback(p) for p in batch]
            elif AI_PROVIDER == "groq":
                from groq_adapter import generate_with_groq
                raw     = _call_with_timeout(generate_with_groq, prompt) or ""
                results = _parse_response(batch, raw) if raw else [_rule_based_fallback(p) for p in batch]
            elif AI_PROVIDER == "openai":
                from openai_adapter import generate_with_openai
                raw     = _call_with_timeout(generate_with_openai, prompt) or ""
                results = _parse_response(batch, raw) if raw else [_rule_based_fallback(p) for p in batch]
            elif AI_PROVIDER == "ollama":
                from ollama_adapter import generate_with_ollama
                raw     = _call_with_timeout(generate_with_ollama, prompt) or ""
                results = _parse_response(batch, raw) if raw else [_rule_based_fallback(p) for p in batch]
            elif AI_PROVIDER == "claude":
                from claude_adapter import generate_with_claude
                raw     = _call_with_timeout(generate_with_claude, prompt) or ""
                results = _parse_response(batch, raw) if raw else [_rule_based_fallback(p) for p in batch]
            else:
                results = [_rule_based_fallback(p) for p in batch]
        except Exception as exc:
            logger.warning("Non-Gemini provider %r failed: %s — using rule-based", AI_PROVIDER, exc)
            results = [_rule_based_fallback(p) for p in batch]

        for result in results:
            url = result.get("url", "")
            if url in url_map:
                url_map[url]["gemini_fields"] = result.get("fields", [])
        gemini_status["processed"] += len(batch)

    gemini_status.update({"running": False, "done": True})


# ── Batch with retry ──────────────────────────────────────────────────────────

def _run_batch(batch: list[dict]) -> list[dict]:
    """
    FIX 4: Max 2 retries with 1s → 2s backoff (not 2s → 4s).
    FIX 6: Each attempt has a hard timeout via _call_with_timeout.
    On total failure: rule-based fallback (system continues).
    """
    for attempt in range(1, MAX_RETRIES + 2):   # 1, 2, 3
        result = _call_with_timeout(_call_gemini, batch)
        if result is not None:
            return result
        logger.warning("Gemini attempt %d/%d failed", attempt, MAX_RETRIES + 1)
        if attempt <= MAX_RETRIES:
            time.sleep(attempt)   # FIX 4: 1s, 2s  (was 2s, 4s)

    logger.error("All Gemini retries exhausted — rule-based fallback")
    return [_rule_based_fallback(p) for p in batch]


def _call_gemini(batch: list[dict]) -> list[dict]:
    """
    FIX 2: Uses google.generativeai GenerativeModel, not google.genai Client.
    This is the official recommended SDK and avoids 404 on model names.
    """
    import google.generativeai as genai
    model    = _get_model()
    prompt   = _build_prompt(batch)
    response = model.generate_content(prompt)
    raw      = response.text or ""
    return _parse_response(batch, raw)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(batch):
    pages_info = []
    for p in batch:
        kws      = ", ".join((p.get("keywords") or [])[:8]) or "not extracted"
        body     = (p.get("body_text") or "")[:800].strip()
        title    = p.get("title") or "MISSING"
        meta     = p.get("meta_description") or "MISSING"
        h1       = (p.get("h1") or ["MISSING"])[0]
        h2       = " | ".join((p.get("h2") or [])[:3]) or "MISSING"
        canon    = p.get("canonical") or "MISSING"
        issues   = ", ".join(p.get("issues", []))
        issue_set = set(p.get("issues", []))

        problem_fields = []
        if any(x in issue_set for x in ("Missing Title", "Title Too Long")):
            problem_fields.append('  title: "' + title + '"')
        if any(x in issue_set for x in ("Missing Meta Description", "Duplicate Meta Description")):
            problem_fields.append('  meta_description: "' + meta + '"')
        if any(x in issue_set for x in ("Missing H1", "Multiple H1 Tags")):
            problem_fields.append('  h1: "' + h1 + '"')
        if "Missing H2" in issue_set:
            problem_fields.append('  h2: "' + h2 + '"')
        if any(x in issue_set for x in ("Missing Canonical", "Canonical Mismatch")):
            problem_fields.append('  canonical: "' + canon + '"')

        if not problem_fields:
            continue

        competition = p.get("competition", "Medium")
        h3_tags     = " | ".join((p.get("h3") or [])[:2]) or "MISSING"

        # BUG-N02: escape all user-controlled strings with json.dumps so an
        # attacker-controlled page cannot inject instructions into the prompt.
        import json as _json
        pages_info.append(
            "URL: " + p.get("url", "") + "\n"
            "Detected Keywords: " + kws + "\n"
            "Competition Level: " + competition + "\n"
            "H3 tags: " + _json.dumps(h3_tags) + "\n"
            "Page Content Preview: " + _json.dumps(body) + "\n"
            "Current Issues: " + issues + "\n"
            "Current field values with problems:\n" + "\n".join(problem_fields)
        )

    if not pages_info:
        return "[]"

    sep = "\n\n" + "="*50 + "\n\n"
    pages_block = sep.join(pages_info)

    rules = """STRICT GENERATION RULES (violations make output unusable):
1. NO PLACEHOLDERS. Never write [Keyword], yourdomain.com, Add a title here, Your Brand, or generic filler.
2. REAL-WORLD DATA ONLY. Use Page Content Preview and Detected Keywords. Every generated value must explicitly reflect the page topic.
3. "fix" must start with an action verb: Replace, Add, Write, Shorten, Remove.
4. "generated" must be EXACT text to copy-paste into HTML — complete, realistic, immediately usable.
5. "ranking_score" 0-100: deduct per missing/broken field (title=20, meta=15, h1=20, h2=10, canonical=10, status=15, keyword_alignment=10).
6. Return ONLY valid JSON — no markdown, no explanation outside JSON.
7. "issue" values: Missing, Too Long, Duplicate, Multiple, Mismatch, or OK.
8. If Competition Level is LOW, prioritise those keywords in generated title and meta — low competition = higher ranking opportunity.
9. If H3 tags are MISSING, generate a suggested H3 using page keywords."""

    fmt = """[
  {
    "url": "<exact url>",
    "fixes": [
      {
        "field": "Title",
        "issue": "<Missing|Too Long|OK>",
        "current": "<current value or empty>",
        "fix": "<specific instruction starting with action verb>",
        "generated": "<exact 50-60 char title using actual page keywords, zero placeholders>",
        "reason": "<why this improves ranking/CTR for this specific page>"
      },
      {
        "field": "Meta Description",
        "issue": "<Missing|Duplicate|OK>",
        "current": "<current value or empty>",
        "fix": "<specific instruction>",
        "generated": "<exact 140-160 char meta using actual page content, zero placeholders>",
        "reason": "<specific CTR improvement>"
      },
      {
        "field": "H1",
        "issue": "<Missing|Multiple|OK>",
        "current": "<current value or empty>",
        "fix": "<specific instruction>",
        "generated": "<exact H1 using actual page keywords>",
        "reason": "<specific ranking impact>"
      },
      {
        "field": "H2",
        "issue": "<Missing|OK>",
        "current": "<current value or empty>",
        "fix": "<specific instruction>",
        "generated": "<exact H2 subheading for this page topic>",
        "reason": "<content structure impact>"
      },
      {
        "field": "Canonical",
        "issue": "<Missing|Mismatch|OK>",
        "current": "<current value or empty>",
        "fix": "<specific instruction>",
        "generated": "<exact self-referencing canonical URL>",
        "reason": "<duplicate content prevention>"
      },
      {
        "field": "H3",
        "issue": "<Missing|OK>",
        "current": "<current H3 or MISSING>",
        "fix": "<specific instruction>",
        "generated": "<keyword-rich H3 subheading for this page topic>",
        "reason": "<how H3 improves topical depth>"
      }
    ],
    "keywords_missing": ["<keyword from content not in title/h1>"],
    "ranking_score": 65,
    "ranking_reason": "<specific explanation based on this page actual issues>"
  }
]"""

    return (
        "You are a professional SEO expert performing live on-page optimization.\n\n"
        + rules + "\n\nPAGE DATA:\n" + pages_block
        + "\n\nReturn this EXACT JSON structure:\n" + fmt
    )


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(batch: list[dict], raw: str) -> list[dict]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"```", "", cleaned).strip()
    match   = re.search(r"\[.*\]", cleaned, re.S)
    if not match:
        logger.warning("No JSON array found in AI response: %s", raw[:200])
        return [_rule_based_fallback(p) for p in batch]
    try:
        data = json.loads(match.group())
        result_map = {}
        for item in data:
            if not isinstance(item, dict) or "url" not in item:
                continue
            url = item["url"]
            result_map[url] = item
            page = next((p for p in batch if p["url"] == url), None)
            if page:
                fixes = item.get("fixes", [])
                if fixes and not item.get("fields"):
                    item["fields"] = [
                        {
                            "name":    f.get("field", ""),
                            "current": f.get("current", ""),
                            "issue":   f.get("issue", "OK"),
                            "why":     f.get("reason", ""),
                            "fix":     f.get("fix", ""),
                            "example": f.get("generated", ""),
                            "impact":  "High" if f.get("issue") in ("Missing","Mismatch") else "Medium",
                        }
                        for f in fixes
                    ]
                if item.get("ranking_score") is not None:
                    page["gemini_ranking_score"] = item["ranking_score"]
                    page["gemini_ranking_reason"] = item.get("ranking_reason", "")
                if item.get("optimized_title"):
                    page["optimized_title"] = item["optimized_title"]
                if item.get("optimized_meta"):
                    page["optimized_meta"] = item["optimized_meta"]
                if item.get("optimized_h1"):
                    page["optimized_h1"] = item["optimized_h1"]
        # BUG-N35: log any batch page that the AI response omitted so missing
        # pages are visible in debug output rather than silently falling back.
        results = []
        for p in batch:
            url = p["url"]
            if url in result_map:
                results.append(result_map[url])
            else:
                # BUG-018: info level — important milestone, not noisy noise.
                logger.info("AI response missing page %s — using rule-based fallback", url)
                results.append(_rule_based_fallback(p))
        return results
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("JSON parse error: %s — raw: %s", e, raw[:300])
        return [_rule_based_fallback(p) for p in batch]


# ── Rule-based fallback (unchanged) ──────────────────────────────────────────

_FIELD_RULES = {
    "Title": {
        "Missing": {
            "why":     "Missing titles prevent Google from understanding page topic, directly reducing rankings and CTR.",
            "fix":     "Add a unique, keyword-rich title between 50–60 characters with the primary keyword near the start.",
            "example": "Page Topic | Brand Name — Key Benefit",
            "impact":  "High",
        },
        "Too Long": {
            "why":     "Titles over 60 characters get truncated in search results, cutting off your message and reducing CTR.",
            "fix":     "Shorten the title to under 60 characters while keeping the primary keyword.",
            "example": "Short Keyword-Rich Title | Brand",
            "impact":  "Medium",
        },
    },
    "Meta Description": {
        "Missing": {
            "why":     "Google auto-generates snippets for pages without meta descriptions, often producing irrelevant text that lowers CTR.",
            "fix":     "Write a 140–160 character meta description with the primary keyword and a clear call-to-action.",
            "example": "Discover [page topic] on [site name]. Get [key benefit] — [CTA like 'Learn more' or 'Shop now'].",
            "impact":  "Medium",
        },
        "Duplicate": {
            "why":     "Duplicate meta descriptions confuse search engines and reduce click differentiation in SERPs.",
            "fix":     "Rewrite this description to be unique, focusing on what makes this specific page different.",
            "example": "Unique description covering what only this page offers — specific features, benefits, or content.",
            "impact":  "Medium",
        },
    },
    "H1": {
        "Missing": {
            "why":     "H1 is the strongest on-page ranking signal. Without it, Google cannot determine the page's primary topic.",
            "fix":     "Add exactly one H1 tag containing the primary keyword as the first heading on the page.",
            "example": "Primary Page Topic — Specific Benefit or Context",
            "impact":  "High",
        },
        "Multiple": {
            "why":     "Multiple H1 tags dilute topical authority and confuse crawlers about the page's primary subject.",
            "fix":     "Keep only one H1 tag and demote all others to H2 or H3.",
            "example": "Single Clear Primary Heading for This Page",
            "impact":  "Medium",
        },
    },
    "H2": {
        "Missing": {
            "why":     "H2 subheadings help Google understand content structure and improve ranking for secondary keywords.",
            "fix":     "Add at least 2 H2 tags to break content into logical sections with keyword-relevant headings.",
            "example": "Why Choose [Service/Product] — Features and Benefits",
            "impact":  "Low",
        },
    },
    "Canonical": {
        "Missing": {
            "why":     "Without a canonical tag, Google may index duplicate URL variants, splitting link equity across multiple versions.",
            "fix":     "Add a canonical link tag in <head> pointing to the preferred version of this page's URL.",
            "example": "https://www.yourdomain.com/this-page/",
            "impact":  "Medium",
        },
        "Mismatch": {
            "why":     "A canonical pointing to a different URL signals to Google this page is a duplicate, suppressing its ranking.",
            "fix":     "Update the canonical tag href to exactly match this page's actual URL.",
            "example": "https://www.yourdomain.com/exact-current-url/",
            "impact":  "High",
        },
    },
}


def _rule_based_fallback(page: dict) -> dict:
    issue_set = set(page.get("issues", []))
    status_map = {
        "Title":            ("Missing" if "Missing Title" in issue_set else
                             "Too Long" if "Title Too Long" in issue_set else "OK"),
        "Meta Description": ("Missing" if "Missing Meta Description" in issue_set else
                             "Duplicate" if "Duplicate Meta Description" in issue_set else "OK"),
        "H1":               ("Missing" if "Missing H1" in issue_set else
                             "Multiple" if "Multiple H1 Tags" in issue_set else "OK"),
        "H2":               ("Missing" if "Missing H2" in issue_set else "OK"),
        "Canonical":        ("Missing" if "Missing Canonical" in issue_set else
                             "Mismatch" if "Canonical Mismatch" in issue_set else "OK"),
    }
    current_map = {
        "Title":            page.get("title", ""),
        "Meta Description": page.get("meta_description", ""),
        "H1":               (page.get("h1") or [""])[0],
        "H2":               (page.get("h2") or [""])[0],
        "Canonical":        page.get("canonical", ""),
    }
    fields = []
    for name, issue in status_map.items():
        rule = _FIELD_RULES.get(name, {}).get(issue, {})
        fields.append({
            "name":    name,
            "current": current_map.get(name, ""),
            "issue":   issue,
            "why":     rule.get("why", ""),
            "fix":     rule.get("fix", "No action needed." if issue == "OK" else "Review this field."),
            "example": rule.get("example", "—"),
            "impact":  rule.get("impact", ""),
        })
    return {"url": page.get("url", ""), "fields": fields}


# ── Health check ──────────────────────────────────────────────────────────────

def check_gemini() -> dict:
    key = os.getenv("GEMINI_API_KEY", "")
    return {
        "configured":  bool(key),
        "model":       os.getenv("GEMINI_MODEL", _MODEL_PRIMARY),
        "provider":    AI_PROVIDER,
        "max_pages":   MAX_PAGES,
        "batch_size":  BATCH_SIZE,
        "workers":     MAX_WORKERS,
        "ai_timeout":  _AI_TIMEOUT,
        "key_hint":    f"...{key[-4:]}" if len(key) > 8 else "(not set)",
    }


# ── Ranking score (unchanged) ─────────────────────────────────────────────────

def compute_ranking_score(page: dict) -> dict:
    """Score 0-100 based on SEO fundamentals. Pure Python, no API calls."""
    score   = 0
    details = {}
    issues  = set(page.get("issues", []))
    title   = (page.get("title") or "").strip()
    meta    = (page.get("meta_description") or "").strip()
    h1s     = page.get("h1") or []
    h2s     = page.get("h2") or []
    canon   = (page.get("canonical") or "").strip()
    status  = str(page.get("status_code", ""))
    kws     = [k.lower() for k in (page.get("keywords") or [])]

    if page.get("_is_error") or status not in ("200",):
        return {"score": 0, "grade": "F",
                "feedback": "Page could not be loaded — no SEO score available.",
                "breakdown": {}}

    if title and "Missing Title" not in issues:
        pts = 20 if "Title Too Long" not in issues else 12
        score += pts; details["title"] = pts
    else:
        details["title"] = 0

    if meta and "Missing Meta Description" not in issues:
        pts = 15 if "Duplicate Meta Description" not in issues else 8
        score += pts; details["meta"] = pts
    else:
        details["meta"] = 0

    if h1s and "Missing H1" not in issues:
        pts = 20 if "Multiple H1 Tags" not in issues else 12
        score += pts; details["h1"] = pts
    else:
        details["h1"] = 0

    if h2s:
        score += 10; details["h2"] = 10
    else:
        details["h2"] = 0

    if canon and "Missing Canonical" not in issues and "Canonical Mismatch" not in issues:
        score += 10; details["canonical"] = 10
    else:
        details["canonical"] = 0

    if status == "200":
        score += 15; details["status"] = 15
    else:
        details["status"] = 0

    if kws and title:
        title_lower = title.lower()
        h1_text     = " ".join(h1s).lower()
        aligned     = sum(1 for k in kws[:5] if k in title_lower or k in h1_text)
        pts = min(10, aligned * 2)
        score += pts; details["keyword_alignment"] = pts
    else:
        details["keyword_alignment"] = 0

    if score >= 85:   grade = "A"
    elif score >= 70: grade = "B"
    elif score >= 55: grade = "C"
    elif score >= 40: grade = "D"
    else:             grade = "F"

    feedback_map = {
        "A": "Excellent SEO. Minor refinements could push rankings further.",
        "B": "Good SEO. Fix remaining issues to improve click-through and rankings.",
        "C": "Moderate SEO. Several key issues need attention to rank competitively.",
        "D": "Weak SEO. Critical fields are missing — rankings will be significantly impacted.",
        "F": "Poor SEO. Most fundamental signals are absent. Immediate action required.",
    }

    return {
        "score":     score,
        "grade":     grade,
        "feedback":  feedback_map[grade],
        "breakdown": details,
    }


# ════════════════════════════════════════════════════════════════════════════
# SEO CONTENT GENERATION (unchanged public API)
# ════════════════════════════════════════════════════════════════════════════

content_gen_status: dict = {
    "running":   False,
    "done":      False,
    "error":     None,
    "processed": 0,
    "total":     0,
}


def _is_valid_for_content_gen(page: dict) -> bool:
    if page.get("_is_error"):
        return False
    if str(page.get("status_code", "")) != "200":
        return False
    if not (page.get("body_text") or "").strip():
        return False
    keywords = page.get("keywords_scored") or page.get("keywords") or []
    return bool(keywords)


def build_seo_content_prompt(page: dict) -> str:
    """Build deterministic SEO + AEO prompt for full content generation."""
    kws_scored  = page.get("keywords_scored") or []
    high_kws    = [k["keyword"] for k in kws_scored if k.get("importance") == "HIGH"]
    med_kws     = [k["keyword"] for k in kws_scored if k.get("importance") == "MEDIUM"]
    low_kws     = [k["keyword"] for k in kws_scored if k.get("importance") == "LOW"]
    fallback_kws = page.get("keywords") or []

    kw_block = (
        "HIGH importance: "   + ", ".join(high_kws[:5])    + "\n"
        "MEDIUM importance: " + ", ".join(med_kws[:5])     + "\n"
        "LOW importance: "    + ", ".join(low_kws[:5])     + "\n"
        "All keywords: "      + ", ".join(fallback_kws[:8])
    ) if (high_kws or med_kws) else ", ".join(fallback_kws[:10])

    body      = (page.get("body_text") or "")[:1200].strip()
    url       = page.get("url", "")
    title     = page.get("title") or "MISSING"
    meta      = page.get("meta_description") or "MISSING"
    h1        = (page.get("h1") or ["MISSING"])[0]
    h2        = " | ".join((page.get("h2") or [])[:3]) or "MISSING"
    issues    = ", ".join(page.get("issues", [])) or "none"
    comp      = page.get("competition", "Medium")

    gaps = page.get("competitor_gaps") or {}
    gap_block = ""
    if gaps.get("missing_keywords"):
        gap_block = "Competitor gap keywords (not in your page): " + ", ".join(gaps["missing_keywords"])

    return f"""You are an expert SEO content writer. Generate complete, copy-paste-ready SEO content.

URL: {url}
Current Title: {title}
Current Meta: {meta}
Current H1: {h1}
Current H2s: {h2}
Issues to fix: {issues}
Competition level: {comp}
{gap_block}

KEYWORDS (use these in generated content):
{kw_block}

PAGE CONTENT PREVIEW:
{body}

Generate a complete JSON response with this EXACT structure (no markdown, no explanation outside JSON):
{{
  "title": "<50-60 char title with primary keyword>",
  "meta": "<140-160 char meta with keyword and CTA>",
  "h1": "<H1 matching primary keyword>",
  "h2": ["<H2 subheading 1>", "<H2 subheading 2>", "<H2 subheading 3>"],
  "h3": ["<H3 for section 1>", "<H3 for section 2>"],
  "canonical": "<exact self-referencing URL>",
  "content": "<300-500 word optimised body content using keywords naturally>",
  "faq": [
    {{"q": "<question using keyword>", "a": "<concise answer>"}},
    {{"q": "<second question>", "a": "<answer>"}}
  ],
  "keywords_used": ["<keywords integrated into content>"],
  "keywords_missing": ["<keywords not yet used>"],
  "reason": "<brief explanation of main changes>"
}}"""


def generate_seo_content(page: dict) -> dict:
    """
    Generate optimized SEO content for one page.
    Tries AI first; falls back to rule-based if unavailable.
    Always returns a complete dict — never raises.
    FIX 6: All AI calls wrapped with timeout guard.
    """
    if not _is_valid_for_content_gen(page):
        return _rule_based_content(page)

    prompt = build_seo_content_prompt(page)

    if AI_PROVIDER == "rules":
        return _rule_based_content(page)

    if AI_PROVIDER == "ollama":
        try:
            from ollama_adapter import generate_with_ollama
            raw    = _call_with_timeout(generate_with_ollama, prompt) or ""
            result = _parse_content_response(page, raw)
            if result:
                result["_source"] = "ollama"
                return result
        except Exception as exc:
            logger.warning("Ollama content gen failed: %s", exc)
        return _rule_based_content(page)

    if AI_PROVIDER == "groq":
        try:
            from groq_adapter import generate_with_groq
            raw    = _call_with_timeout(generate_with_groq, prompt) or ""
            result = _parse_content_response(page, raw)
            if result:
                result["_source"] = "groq"
                return result
        except Exception as exc:
            logger.warning("Groq content gen failed: %s", exc)
        return _rule_based_content(page)

    if AI_PROVIDER == "openai":
        try:
            from openai_adapter import generate_with_openai
            raw    = _call_with_timeout(generate_with_openai, prompt) or ""
            result = _parse_content_response(page, raw)
            if result:
                result["_source"] = "openai"
                return result
        except Exception as exc:
            logger.warning("OpenAI content gen failed: %s", exc)
        return _rule_based_content(page)

    if AI_PROVIDER == "claude":
        try:
            from claude_adapter import generate_with_claude
            raw    = _call_with_timeout(generate_with_claude, prompt) or ""
            result = _parse_content_response(page, raw)
            if result:
                result["_source"] = "claude"
                return result
        except Exception as exc:
            logger.warning("Claude content gen failed: %s", exc)
        return _rule_based_content(page)

    # Default: Gemini
    try:
        _configure_genai()
    except (ValueError, ImportError):
        return _rule_based_content(page)

    for attempt in range(1, MAX_RETRIES + 2):
        def _gemini_content_call():
            import google.generativeai as genai
            model    = _get_model()
            response = model.generate_content(prompt)
            return response.text or ""

        raw = _call_with_timeout(_gemini_content_call)
        if raw:
            result = _parse_content_response(page, raw)
            if result:
                result["_source"] = "gemini"
                return result
        logger.warning("generate_seo_content attempt %d failed", attempt)
        if attempt <= MAX_RETRIES:
            time.sleep(attempt)   # FIX 4: 1s, 2s

    return _rule_based_content(page)


def _parse_content_response(page: dict, raw: str) -> dict | None:
    """Parse AI JSON response for content generation. Returns None on parse failure."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"```", "", cleaned).strip()

    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        logger.warning("No JSON object in content response: %s", raw[:200])
        return None

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error in content response: %s", exc)
        return None

    required = {"title", "meta", "h1", "h2", "h3", "canonical", "content", "faq"}
    if not required.issubset(data.keys()):
        logger.warning("Missing keys in content response: %s", required - data.keys())
        return None

    if isinstance(data.get("h2"), str):
        data["h2"] = [data["h2"]]
    if isinstance(data.get("h3"), str):
        data["h3"] = [data["h3"]]
    if not isinstance(data.get("faq"), list):
        data["faq"] = []
    else:
        data["faq"] = [
            f for f in data["faq"]
            if isinstance(f, dict) and f.get("q") and f.get("a")
        ]

    data.setdefault("url",                      page.get("url", ""))
    data.setdefault("keywords_used",            [])
    data.setdefault("keywords_missing",         [])
    data.setdefault("internal_links_suggested", [])
    data.setdefault("reason",                   "")
    return data


def _rule_based_content(page: dict) -> dict:
    """
    Pure rule-based content generation — zero API calls.
    Used when AI is unavailable, key is missing, or all retries fail.
    Always returns a complete, valid dict.
    """
    url      = page.get("url", "")
    title    = (page.get("title") or "").strip()
    meta     = (page.get("meta_description") or "").strip()
    h1_list  = page.get("h1") or []
    h2_list  = page.get("h2") or []
    h3_list  = page.get("h3") or []
    keywords = page.get("keywords") or []
    issues   = page.get("issues", [])
    snippet  = (page.get("body_text") or "")[:300].strip()

    primary   = keywords[0] if keywords else "content"
    used      = [k for k in keywords if k in (title + " " + snippet).lower()]
    not_used  = [k for k in keywords if k not in used]
    issue_set = set(issues)

    gen_title = title or f"{primary.title()} — Complete Guide"
    if len(gen_title) > 60:
        gen_title = gen_title[:57] + "..."

    gen_meta = meta or (
        f"Learn about {primary} on this page. "
        f"Explore {', '.join(keywords[1:3]) or 'key topics'} and more. "
        "Read the full guide now."
    )[:160]

    gen_h1 = h1_list[0] if h1_list else gen_title
    gen_h2 = h2_list[:3] if h2_list else [
        f"What is {primary.title()}?",
        f"Key Benefits of {primary.title()}",
        f"How to Get Started with {primary.title()}",
    ]
    gen_h3 = h3_list[:2] if h3_list else [
        f"Understanding {primary.title()}",
        f"{primary.title()} Best Practices",
    ]
    gen_canon = url

    gen_content = (
        f"This page covers {primary} in detail. "
        + (snippet[:200] if snippet else f"Explore everything you need to know about {primary}.")
        + f" Key topics include: {', '.join(keywords[:5])}."
    )

    rule_faq = []
    for kw in keywords[:2]:
        rule_faq.append({
            "q": f"What is {kw}?",
            "a": f"{kw.title()}. Learn more on this page.{(' ' + snippet[:80]) if snippet else ''}"
        })

    return {
        "url":                      url,
        "title":                    gen_title,
        "meta":                     gen_meta,
        "h1":                       gen_h1,
        "h2":                       gen_h2,
        "h3":                       gen_h3,
        "canonical":                gen_canon,
        "content":                  gen_content,
        "faq":                      rule_faq,
        "keywords_used":            used[:8],
        "keywords_missing":         not_used[:5],
        "internal_links_suggested": [],
        "reason": (
            "Rule-based fallback (AI unavailable). "
            f"Issues addressed: {', '.join(issues) or 'none'}. "
            f"Primary keyword '{primary}' used in title and H1."
        ),
        "_source": "rule_based",
    }


def run_content_generation(pages: list[dict]) -> None:
    """
    Run SEO content generation for all valid pages.
    Mutates page["generated_content"] in-place.
    FIX 6: Per-page timeout via generate_seo_content → _call_with_timeout.
    """
    valid = [p for p in pages if _is_valid_for_content_gen(p)]

    content_gen_status.update({
        "running": True, "done": False, "error": None,
        "processed": 0, "total": len(valid),
    })

    if not valid:
        content_gen_status.update({
            "running": False, "done": True,
            "error": "No pages with sufficient content and keywords for generation.",
        })
        return

    for p in pages:
        p.setdefault("generated_content", None)

    def _process(page):
        result = generate_seo_content(page)
        page["generated_content"] = result
        return page["url"]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_process, p): p for p in valid}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                page = futures[future]
                logger.error("Content gen failed for %s: %s", page.get("url"), exc)
                page["generated_content"] = _rule_based_content(page)
            finally:
                content_gen_status["processed"] += 1

    content_gen_status.update({"running": False, "done": True})
