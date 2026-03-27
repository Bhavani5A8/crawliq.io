"""
ai_analysis.py — Priority scoring utility.

Priority scoring utility — provider-agnostic.
The assign_priority() function is imported by gemini_analysis.py
and seo_optimizer.py. No AI provider dependency here.

No external dependencies required.
"""

# ── Priority rules ────────────────────────────────────────────────────────────
_HIGH   = {"Broken Page", "Missing Title"}
_MEDIUM = {"Missing Meta Description", "Missing H1",
           "Duplicate Meta Description", "Multiple H1 Tags"}


def assign_priority(issues: list[str]) -> str:
    """
    Return the highest severity level present in the issue list.
    High > Medium > Low.
    Pure Python — no API calls, no external dependencies.
    """
    if not issues:
        return ""
    issue_set = set(issues)
    if issue_set & _HIGH:
        return "High"
    if issue_set & _MEDIUM:
        return "Medium"
    return "Low"


def batch_pages(pages: list[dict], size: int = 5) -> list[list[dict]]:
    """Split pages-with-issues into chunks (kept for API compatibility)."""
    pages_with_issues = [p for p in pages if p.get("issues")]
    return [pages_with_issues[i:i + size]
            for i in range(0, len(pages_with_issues), size)]
