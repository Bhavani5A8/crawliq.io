"""
schema_validator.py — CrawlIQ Structured Data (JSON-LD) Validator

Parses every <script type="application/ld+json"> block in a page's HTML,
validates required fields per schema.org type, and flags risky type choices
that could harm Google's classification of an informational/documentation site.

No external dependencies — pure stdlib.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── Required fields per @type ─────────────────────────────────────────────────
_REQUIRED: dict[str, list[str]] = {
    "Article":          ["headline", "author", "datePublished"],
    "BlogPosting":      ["headline", "author", "datePublished"],
    "NewsArticle":      ["headline", "author", "datePublished"],
    "Product":          ["name", "offers"],
    "FAQPage":          ["mainEntity"],
    "HowTo":            ["name", "step"],
    "BreadcrumbList":   ["itemListElement"],
    "Person":           ["name"],
    "Organization":     ["name"],
    "WebPage":          ["name"],
    "WebSite":          ["url"],
    "LocalBusiness":    ["name", "address", "telephone"],
    "Event":            ["name", "startDate", "location"],
    "Recipe":           ["name", "recipeIngredient", "recipeInstructions"],
    "JobPosting":       ["title", "datePosted", "description", "hiringOrganization"],
    "VideoObject":      ["name", "description", "thumbnailUrl", "uploadDate"],
    "Course":           ["name", "description"],
    "Review":           ["itemReviewed", "author", "reviewRating"],
    "SpeakableSpecification": ["cssSelector"],
}

# ── Schema types that risk SaaS/tool classification on informational pages ────
_RISKY_TYPES = {
    "SoftwareApplication", "WebApplication", "MobileApplication",
    "SaaS", "APIReference",
}

# ── Recommended safe types for documentation/informational sites ──────────────
_SAFE_TYPES = {
    "WebPage", "Article", "BlogPosting", "FAQPage", "HowTo",
    "BreadcrumbList", "Person", "Organization",
}

# ── Regex for JSON-LD script blocks ───────────────────────────────────────────
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# Core functions
# ══════════════════════════════════════════════════════════════════════════════

def extract_jsonld_blocks(html: str) -> list[dict]:
    """
    Extract and parse all JSON-LD blocks from a raw HTML string.
    Returns a list of parsed dicts. Malformed JSON is returned as an error dict.
    """
    blocks: list[dict] = []
    for match in _JSONLD_RE.finditer(html):
        raw = match.group(1).strip()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                blocks.extend(parsed)
            else:
                blocks.append(parsed)
        except json.JSONDecodeError as exc:
            blocks.append({
                "_parse_error": str(exc),
                "_raw_snippet": raw[:300],
            })
    return blocks


def validate_schema_block(schema: dict) -> dict:
    """
    Validate a single schema.org object.
    Returns a report dict with keys: type, valid, issues, warnings.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # JSON parse failures
    if "_parse_error" in schema:
        return {
            "type": None,
            "valid": False,
            "issues": [f"JSON syntax error: {schema['_parse_error']}"],
            "warnings": [],
        }

    schema_type = schema.get("@type")

    # @graph — recurse into children
    if not schema_type and "@graph" in schema:
        children = [validate_schema_block(s) for s in schema.get("@graph", [])]
        child_issues = sum(len(c["issues"]) for c in children)
        return {
            "type": "@graph",
            "valid": child_issues == 0,
            "issues": [],
            "warnings": [],
            "children": children,
        }

    if not schema_type:
        return {
            "type": None,
            "valid": False,
            "issues": ["Missing @type property"],
            "warnings": [],
        }

    # @context check
    context = schema.get("@context", "")
    if not context:
        warnings.append("Missing @context — should be 'https://schema.org'")
    elif "schema.org" not in str(context):
        warnings.append(f"Unexpected @context value: '{context}'")

    # Handle array types (e.g., ["Organization", "LocalBusiness"])
    types = schema_type if isinstance(schema_type, list) else [schema_type]

    for t in types:
        # Risky type check
        if t in _RISKY_TYPES:
            warnings.append(
                f"'{t}' may trigger SaaS/product classification — "
                "use WebPage or Article for documentation pages instead"
            )

        # Required field check
        required = _REQUIRED.get(t, [])
        for field in required:
            if field not in schema:
                issues.append(f"[{t}] Missing required field: '{field}'")

    primary_type = types[0] if types else "unknown"

    return {
        "type": primary_type,
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }


def validate_page_schemas(html: str) -> dict:
    """
    Full structured data audit for one page's HTML.
    Returns a report with counts, per-schema results, and a summary string.
    """
    blocks = extract_jsonld_blocks(html)

    if not blocks:
        return {
            "found": 0,
            "schemas": [],
            "total_issues": 0,
            "total_warnings": 0,
            "summary": "No JSON-LD structured data found on this page",
        }

    results = [validate_schema_block(b) for b in blocks]
    total_issues   = sum(len(r.get("issues", [])) for r in results)
    total_warnings = sum(len(r.get("warnings", [])) for r in results)
    types_found    = [r.get("type") for r in results if r.get("type")]

    return {
        "found":          len(blocks),
        "types_found":    types_found,
        "schemas":        results,
        "total_issues":   total_issues,
        "total_warnings": total_warnings,
        "summary": (
            f"{len(blocks)} schema block(s) found "
            f"({', '.join(t for t in types_found if t)}); "
            f"{total_issues} error(s), {total_warnings} warning(s)"
        ),
    }


def validate_url_schemas(pages: list[dict]) -> list[dict]:
    """
    Run schema validation across a list of crawled page dicts.
    Each page dict is expected to have 'url' and optionally 'raw_html'.
    Returns per-page validation reports.
    """
    reports = []
    for page in pages:
        html = page.get("raw_html", "")
        if not html:
            reports.append({
                "url": page.get("url", ""),
                "skipped": True,
                "reason": "raw_html not available in crawl data",
            })
            continue
        report = validate_page_schemas(html)
        report["url"] = page.get("url", "")
        reports.append(report)
    return reports
