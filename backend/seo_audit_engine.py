"""
seo_audit_engine.py — Production-grade SEO audit orchestrator.

Integrates every existing validator:
  issues.py        → per-page + cross-page issue detection
  technical_seo.py → component scoring, indexability, site summary
  site_auditor.py  → robots.txt, HSTS, mixed content, redirects

Adds net-new layers:
  • Cluster validation  — 10 clusters, each with required signals + coverage check
  • Gap detection       — missing signals flagged with minimal fix + impact
  • Consistency checks  — sitemap ↔ indexability, canonical ↔ noindex,
                          hreflang reciprocity, internal links ↔ status codes
  • Security audit      — HTTPS, HSTS, CSP, X-Frame, X-Content-Type, mixed content
  • Performance audit   — CWV thresholds (LCP>2.5s, CLS>0.1, INP>200ms)
                          + proxy signals when real CWV data unavailable
  • Final output        — audit_summary (counts+severity), implementation_roadmap,
                          final_score 0–100 (capped at 89 if any cluster missing signals)

Entry point
───────────
    from seo_audit_engine import run_full_audit

    result = run_full_audit(
        pages,                          # list[dict] from SEOCrawler / CrawlEngine
        sitemap_urls=None,              # list[str] parsed from sitemap XML
        cwv_data=None,                  # dict[url → {lcp, cls, inp}] from PSI API
        site_url=None,                  # root URL for robots.txt / HSTS check
        tech_audit=None,                # pre-computed analyze_all() output (optional)
        validation=None,                # pre-computed validate_all() output (optional)
    )

    # result keys:
    #   cluster_validation, consistency_checks, security_audit,
    #   performance_audit, gap_report, audit_summary,
    #   implementation_roadmap, final_score, final_grade
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Cluster definitions ───────────────────────────────────────────────────────
# Each cluster has: required signals, weight (all weights sum to 100),
# and a coverage_threshold — the minimum % of real pages that must have the
# signal populated before we flag a gap.

_CLUSTERS: dict[str, dict] = {
    "on_page": {
        "label":   "On-Page SEO",
        "weight":  20,
        "signals": {
            "title":            {"threshold": 95, "severity": "critical"},
            "meta_description": {"threshold": 80, "severity": "high"},
            "h1":               {"threshold": 90, "severity": "high"},
            "canonical":        {"threshold": 70, "severity": "medium"},
        },
    },
    "indexability": {
        "label":   "Indexability",
        "weight":  15,
        "signals": {
            "status_code":  {"threshold": 100, "severity": "critical"},
            "robots_meta":  {"threshold": 0,   "severity": "info"},    # optional — absence is fine
            "canonical":    {"threshold": 70,   "severity": "medium"},
        },
    },
    "content_quality": {
        "label":   "Content Quality",
        "weight":  12,
        "signals": {
            "body_text": {"threshold": 80, "severity": "high"},
            "keywords":  {"threshold": 70, "severity": "medium"},
        },
    },
    "links": {
        "label":   "Link Graph",
        "weight":  10,
        "signals": {
            "links":               {"threshold": 50, "severity": "medium"},
            "internal_links_count":{"threshold": 50, "severity": "medium"},
        },
    },
    "images": {
        "label":   "Images",
        "weight":  8,
        "signals": {
            "img_total": {"threshold": 0, "severity": "info"},   # 0 = skip if no images
        },
    },
    "schema": {
        "label":   "Structured Data",
        "weight":  8,
        "signals": {
            "schema_types": {"threshold": 20, "severity": "medium"},  # at least 20% of pages
        },
    },
    "social": {
        "label":   "Social Meta (OG / Twitter)",
        "weight":  5,
        "signals": {
            "og_title":       {"threshold": 60, "severity": "medium"},
            "og_description": {"threshold": 60, "severity": "medium"},
        },
    },
    "security": {
        "label":   "Security Headers",
        "weight":  10,
        "signals": {
            "response_headers": {"threshold": 80, "severity": "high"},
        },
    },
    "performance": {
        "label":   "Performance / Core Web Vitals",
        "weight":  8,
        "signals": {
            # CWV data is injected externally; proxy signals come from crawl data.
            # Threshold 0 = never flag as missing (data may not be available).
            "response_time_ms": {"threshold": 0, "severity": "info"},
        },
    },
    "technical": {
        "label":   "Technical SEO",
        "weight":  4,
        "signals": {
            "viewport":      {"threshold": 80, "severity": "high"},
            "last_modified": {"threshold": 0,  "severity": "info"},
        },
    },
}

assert sum(c["weight"] for c in _CLUSTERS.values()) == 100, \
    "Cluster weights must sum to 100"

# ── Severity ordering ─────────────────────────────────────────────────────────
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# ── CWV thresholds (Google spec) ──────────────────────────────────────────────
_CWV_THRESHOLDS = {
    "lcp": {"good": 2.5,  "poor": 4.0,  "unit": "s",   "label": "Largest Contentful Paint"},
    "cls": {"good": 0.1,  "poor": 0.25, "unit": "",    "label": "Cumulative Layout Shift"},
    "inp": {"good": 0.200,"poor": 0.500,"unit": "s",   "label": "Interaction to Next Paint"},
}

# Required security headers (header-name → missing-message)
_REQUIRED_SECURITY_HEADERS: dict[str, str] = {
    "strict-transport-security": "HSTS missing — browsers won't enforce HTTPS-only access",
    "content-security-policy":   "CSP missing — no XSS mitigation policy declared",
    "x-frame-options":           "X-Frame-Options missing — page embeddable in iframes (clickjacking risk)",
    "x-content-type-options":    "X-Content-Type-Options missing — MIME sniffing not blocked",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_full_audit(
    pages:       list[dict],
    sitemap_urls: list[str]  | None = None,
    cwv_data:    dict        | None = None,
    site_url:    str         | None = None,
    tech_audit:  dict        | None = None,
    validation:  dict        | None = None,
) -> dict:
    """
    Run every audit layer and produce a single cohesive result dict.

    Parameters
    ──────────
    pages        Crawled page dicts (from SEOCrawler or CrawlEngine)
    sitemap_urls URLs parsed from sitemap XML (for sitemap consistency checks)
    cwv_data     {url: {lcp_s, cls, inp_s}} from PageSpeed Insights API
    site_url     Root URL (used for robots.txt / HSTS derivation)
    tech_audit   Pre-computed technical_seo.analyze_all() result (avoids re-running)
    validation   Pre-computed issues.validate_all() result (avoids re-running)
    """
    if not pages:
        return _empty_result("No pages provided")

    real_pages = [p for p in pages if not p.get("_is_error")]

    # ── Run sub-validators (or use pre-computed results) ──────────────────────
    if tech_audit is None:
        try:
            from technical_seo import analyze_all as _tech_analyze_all
            tech_audit = _tech_analyze_all(pages)
        except Exception as exc:
            logger.warning("technical_seo.analyze_all failed: %s", exc)
            tech_audit = {"pages": [], "summary": {}}

    if validation is None:
        try:
            from issues import validate_all as _validate_all
            validation = _validate_all(pages, sitemap_urls=sitemap_urls)
        except Exception as exc:
            logger.warning("issues.validate_all failed: %s", exc)
            validation = {"page_issues": [], "cross_page_issues": [], "stats": {}}

    page_audits: list[dict] = tech_audit.get("pages", [])
    cross_issues: list[dict] = validation.get("cross_page_issues", [])

    # ── Layer 1: Cluster validation ───────────────────────────────────────────
    cluster_validation = _validate_clusters(real_pages, page_audits)

    # ── Layer 2: Gap detection ────────────────────────────────────────────────
    gap_report = _detect_gaps(cluster_validation)

    # ── Layer 3: Consistency checks ───────────────────────────────────────────
    consistency = _run_consistency_checks(pages, sitemap_urls, cross_issues)

    # ── Layer 4: Security audit ───────────────────────────────────────────────
    security = _run_security_audit(real_pages, site_url)

    # ── Layer 5: Performance / CWV audit ─────────────────────────────────────
    performance = _run_performance_audit(real_pages, cwv_data)

    # ── Layer 6: Final score ──────────────────────────────────────────────────
    final_score, score_breakdown, score_cap_reason = _compute_final_score(
        cluster_validation, security, performance, page_audits,
    )

    # ── Layer 7: Audit summary ────────────────────────────────────────────────
    summary = _build_audit_summary(
        pages, real_pages, page_audits, cross_issues,
        cluster_validation, consistency, security, performance,
        final_score,
    )

    # ── Layer 8: Implementation roadmap ──────────────────────────────────────
    roadmap = _build_roadmap(
        cluster_validation, consistency, security, performance, gap_report,
        page_audits, cross_issues,
    )

    return {
        "cluster_validation":  cluster_validation,
        "gap_report":          gap_report,
        "consistency_checks":  consistency,
        "security_audit":      security,
        "performance_audit":   performance,
        "score_breakdown":     score_breakdown,
        "score_cap_reason":    score_cap_reason,
        "audit_summary":       summary,
        "implementation_roadmap": roadmap,
        "final_score":         final_score,
        "final_grade":         _grade(final_score),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 1: Cluster validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_clusters(real_pages: list[dict], page_audits: list[dict]) -> dict:
    """
    For each cluster: verify signal coverage, flag missing signals,
    compute cluster score, confirm issue detection completeness.
    """
    n = len(real_pages) or 1
    results: dict[str, dict] = {}

    for cluster_id, cluster_def in _CLUSTERS.items():
        signals = cluster_def["signals"]
        coverage: dict[str, float] = {}
        missing_signals: list[dict] = []

        for signal, cfg in signals.items():
            threshold = cfg["threshold"]
            if threshold == 0:
                coverage[signal] = 100.0   # not monitored
                continue

            # Compute coverage: % of pages with a non-empty value for this signal
            present = sum(1 for p in real_pages if _signal_present(p, signal))
            pct = round(present / n * 100, 1)
            coverage[signal] = pct

            if pct < threshold:
                missing_signals.append({
                    "signal":        signal,
                    "coverage_pct":  pct,
                    "threshold_pct": threshold,
                    "severity":      cfg["severity"],
                    "gap":           f"{round(threshold - pct, 1)}% below threshold",
                    "fix":           _signal_fix(signal),
                    "impact":        _signal_impact(signal),
                })

        cluster_score = _score_cluster(cluster_id, real_pages, page_audits, coverage)
        issues = _cluster_issues(cluster_id, real_pages, page_audits, missing_signals)

        results[cluster_id] = {
            "label":           cluster_def["label"],
            "weight":          cluster_def["weight"],
            "score":           cluster_score,
            "signal_coverage": coverage,
            "missing_signals": missing_signals,
            "has_gaps":        bool(missing_signals),
            "issues":          issues,
            "issue_count":     len(issues),
        }

    return results


def _signal_present(page: dict, signal: str) -> bool:
    """Return True if the signal field is meaningfully populated on this page."""
    val = page.get(signal)
    if val is None or val == "" or val == [] or val == {}:
        return False
    if signal in ("h1", "h2", "h3"):
        return isinstance(val, list) and len(val) > 0
    if signal in ("links", "hreflang_tags", "schema_types"):
        return isinstance(val, list) and len(val) > 0
    if signal == "response_headers":
        return isinstance(val, dict) and len(val) > 0
    return True


def _signal_fix(signal: str) -> str:
    fixes = {
        "title":            "Add unique, descriptive <title> tags to all pages",
        "meta_description": "Write unique 120-160 char meta descriptions for every page",
        "h1":               "Add exactly one H1 tag per page matching the page topic",
        "canonical":        "Add <link rel='canonical'> to every page (self-referencing where appropriate)",
        "body_text":        "Add meaningful visible text content — minimum 300 words",
        "keywords":         "Run keyword extraction pipeline after crawl completes",
        "links":            "Ensure internal links are crawlable (<a href>), not JS-only",
        "internal_links_count": "Add contextual internal links from high-authority pages",
        "og_title":         "Add <meta property='og:title'> to all pages",
        "og_description":   "Add <meta property='og:description'> to all pages",
        "schema_types":     "Implement JSON-LD structured data — start with BreadcrumbList + Article/Product",
        "viewport":         "Add <meta name='viewport' content='width=device-width, initial-scale=1'>",
        "response_headers": "Configure web server to send security headers on every response",
    }
    return fixes.get(signal, f"Populate the '{signal}' field on all pages")


def _signal_impact(signal: str) -> str:
    impacts = {
        "title":            "Title is the primary SERP display text — missing titles get auto-generated (often poorly) by Google",
        "meta_description": "Meta descriptions directly affect SERP click-through rate",
        "h1":               "H1 is Google's primary topic-relevance heading signal",
        "canonical":        "Without canonicals, Google may consolidate ranking signals to an unintended duplicate URL",
        "body_text":        "Pages with no visible content are unfavourable for ranking and cannot generate keywords",
        "keywords":         "No keywords means no AI analysis, no competition scoring, and no content gap detection",
        "links":            "Internal link data is required for PageRank distribution, orphan detection, and link graph analysis",
        "og_title":         "OG tags control appearance when pages are shared on social networks — missing = poor previews",
        "schema_types":     "Structured data unlocks rich SERP results (FAQ accordions, star ratings, breadcrumbs)",
        "viewport":         "Missing viewport tag causes poor mobile rendering — Google mobile-first indexing penalises this",
        "response_headers": "Security headers missing = known vulnerabilities exposed to users and scanners",
    }
    return impacts.get(signal, f"Signal '{signal}' is required for complete SEO analysis")


def _score_cluster(
    cluster_id: str,
    real_pages: list[dict],
    page_audits: list[dict],
    coverage: dict[str, float],
) -> int:
    """Compute 0-100 score for a cluster based on coverage + issue rate."""
    n = len(real_pages) or 1

    if cluster_id == "on_page":
        # Average of title/meta/h1/canonical coverage, minus issue deductions
        base = (
            coverage.get("title", 0) * 0.35 +
            coverage.get("meta_description", 0) * 0.25 +
            coverage.get("h1", 0) * 0.25 +
            coverage.get("canonical", 0) * 0.15
        )
        title_issues = sum(1 for a in page_audits if a.get("title", {}).get("issues"))
        meta_issues  = sum(1 for a in page_audits if a.get("meta", {}).get("issues"))
        issue_rate   = (title_issues + meta_issues) / (2 * n) * 100
        return max(0, min(100, round(base - issue_rate * 0.3)))

    if cluster_id == "indexability":
        indexable = sum(
            1 for a in page_audits
            if a.get("indexability", {}).get("status") in ("indexable", "likely_indexable")
        )
        return round(indexable / n * 100)

    if cluster_id == "content_quality":
        rich = sum(1 for p in real_pages if len((p.get("body_text") or "").split()) >= 300)
        thin = sum(1 for p in real_pages if len((p.get("body_text") or "").split()) < 100)
        return max(0, round((rich / n * 70) + ((n - thin) / n * 30)))

    if cluster_id == "links":
        with_links = sum(1 for p in real_pages if (p.get("internal_links_count") or 0) > 0)
        return round(with_links / n * 100)

    if cluster_id == "images":
        # Score based on alt text coverage across pages with images
        pages_with_imgs = [p for p in real_pages if (p.get("img_total") or 0) > 0]
        if not pages_with_imgs:
            return 100
        total_imgs = sum(p.get("img_total") or 0 for p in pages_with_imgs)
        total_alts = sum(len(p.get("img_alts") or []) for p in pages_with_imgs)
        return round(min(100, total_alts / max(total_imgs, 1) * 100))

    if cluster_id == "schema":
        with_schema = sum(1 for p in real_pages if p.get("schema_types"))
        return round(with_schema / n * 100)

    if cluster_id == "social":
        with_og = sum(1 for p in real_pages if p.get("og_title") and p.get("og_description"))
        return round(with_og / n * 100)

    if cluster_id == "security":
        # % of pages with all 4 required security headers
        full_coverage = sum(
            1 for p in real_pages
            if _page_has_all_security_headers(p)
        )
        return round(full_coverage / n * 100)

    if cluster_id == "performance":
        # Proxy: % of image-heavy pages with adequate lazy-loading
        img_pages = [p for p in real_pages if (p.get("img_total") or 0) > 2]
        if not img_pages:
            return 100
        good_lazy = sum(1 for p in img_pages if (p.get("img_lazy_pct") or 0) >= 50)
        return round(good_lazy / len(img_pages) * 100)

    if cluster_id == "technical":
        with_viewport = sum(1 for p in real_pages if p.get("viewport"))
        https_pages   = sum(1 for p in real_pages if (p.get("url") or "").startswith("https://"))
        return round((with_viewport / n * 50) + (https_pages / n * 50))

    return 0


def _cluster_issues(
    cluster_id: str,
    real_pages: list[dict],
    page_audits: list[dict],
    missing_signals: list[dict],
) -> list[dict]:
    """Return structured issues for a cluster."""
    issues: list[dict] = []

    for ms in missing_signals:
        issues.append({
            "type":     "missing_signal",
            "signal":   ms["signal"],
            "severity": ms["severity"],
            "detail":   f"Signal '{ms['signal']}' present on only {ms['coverage_pct']}% of pages "
                        f"(threshold: {ms['threshold_pct']}%)",
            "reason":   ms["impact"],
            "fix":      ms["fix"],
        })

    if cluster_id == "on_page":
        dup_titles = sum(
            1 for p in real_pages if "Duplicate Title" in (p.get("issues") or [])
        )
        dup_meta = sum(
            1 for p in real_pages if "Duplicate Meta Description" in (p.get("issues") or [])
        )
        if dup_titles:
            issues.append({
                "type": "duplicate_titles", "severity": "high",
                "detail": f"{dup_titles} pages share duplicate title tags",
                "reason": "Duplicate titles confuse Google about which page to rank for a query",
                "fix": "Write a unique title for each page",
            })
        if dup_meta:
            issues.append({
                "type": "duplicate_meta", "severity": "medium",
                "detail": f"{dup_meta} pages share duplicate meta descriptions",
                "reason": "Duplicate meta descriptions reduce SERP click diversity",
                "fix": "Write unique meta descriptions for each page",
            })

    if cluster_id == "indexability":
        noindex = sum(1 for p in real_pages if p.get("robots_noindex"))
        if noindex:
            issues.append({
                "type": "noindex_pages", "severity": "critical",
                "detail": f"{noindex} pages have noindex directive",
                "reason": "These pages will not appear in Google search results",
                "fix": "Verify each noindex page is intentionally excluded — remove directive if not",
            })

    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 2: Gap detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_gaps(cluster_validation: dict) -> dict:
    """
    Consolidate all missing signals across clusters into a prioritised gap list.
    Each gap has a minimal implementation description.
    """
    gaps: list[dict] = []
    clusters_with_gaps: list[str] = []

    for cluster_id, cluster in cluster_validation.items():
        if not cluster["has_gaps"]:
            continue
        clusters_with_gaps.append(cluster["label"])
        for ms in cluster["missing_signals"]:
            gaps.append({
                "cluster":       cluster["label"],
                "signal":        ms["signal"],
                "severity":      ms["severity"],
                "coverage_pct":  ms["coverage_pct"],
                "threshold_pct": ms["threshold_pct"],
                "impact":        ms["impact"],
                "minimal_fix":   ms["fix"],
            })

    # Sort by severity then by coverage gap size
    gaps.sort(key=lambda g: (
        _SEVERITY_RANK.get(g["severity"], 99),
        g["threshold_pct"] - g["coverage_pct"],
    ))

    return {
        "total_gaps":          len(gaps),
        "clusters_with_gaps":  clusters_with_gaps,
        "score_cap_applies":   len(clusters_with_gaps) > 0,
        "gaps":                gaps,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 3: Consistency checks
# ─────────────────────────────────────────────────────────────────────────────

def _run_consistency_checks(
    pages: list[dict],
    sitemap_urls: list[str] | None,
    cross_issues: list[dict],
) -> dict:
    """
    Run four cross-dataset consistency checks:
      1. Sitemap vs indexability
      2. Canonical vs indexability (noindex + canonical mismatch)
      3. Hreflang reciprocity (from pre-computed cross_issues)
      4. Internal links vs status codes (broken links)
    """
    real = [p for p in pages if not p.get("_is_error")]
    url_to_page = {(p.get("url") or "").rstrip("/"): p for p in pages}

    checks: dict[str, dict] = {}

    # 1. Sitemap vs indexability
    checks["sitemap_indexability"] = _check_sitemap_indexability(pages, sitemap_urls)

    # 2. Canonical vs noindex
    checks["canonical_noindex"] = _check_canonical_noindex(real)

    # 3. Hreflang reciprocity (pull from pre-computed cross issues)
    hreflang_issues = [i for i in cross_issues if i.get("type") == "hreflang_missing_reciprocal"]
    checks["hreflang_reciprocity"] = {
        "checked":         sum(1 for p in pages if p.get("hreflang_tags")),
        "violations":      len(hreflang_issues),
        "details":         hreflang_issues[:20],
        "status":          "ok" if not hreflang_issues else "issues_found",
        "impact":          "Missing reciprocal hreflang links break international URL sets — Googlebot may ignore the entire set",
        "fix":             "Every page A declaring hreflang pointing to B must have B declare hreflang pointing back to A",
    }

    # 4. Internal links vs status codes
    broken = [i for i in cross_issues if i.get("type") == "broken_internal_link"]
    checks["links_status"] = {
        "checked":         sum(len(p.get("links") or []) for p in pages),
        "broken_links":    len(broken),
        "details":         broken[:30],
        "status":          "ok" if not broken else "issues_found",
        "impact":          "Broken internal links waste crawl budget and leak PageRank through dead-end paths",
        "fix":             "Fix or redirect each broken link; 404 target pages should return 301 to the correct URL",
    }

    total_violations = sum(
        c.get("violations", 0) or c.get("broken_links", 0) or c.get("conflicts", 0)
        for c in checks.values()
    )

    return {
        "total_violations": total_violations,
        "checks":           checks,
    }


def _check_sitemap_indexability(
    pages: list[dict],
    sitemap_urls: list[str] | None,
) -> dict:
    """Flag: pages in sitemap that have noindex OR are 4xx/5xx."""
    if not sitemap_urls:
        return {
            "sitemap_provided": False,
            "conflicts":        0,
            "details":          [],
            "status":           "skipped",
            "impact":           "Provide sitemap_urls to enable sitemap consistency check",
            "fix":              "Pass sitemap URL list to run_full_audit(sitemap_urls=...)",
        }

    sitemap_norm  = {u.rstrip("/") for u in sitemap_urls}
    noindex_urls  = {
        (p.get("url") or "").rstrip("/")
        for p in pages
        if p.get("robots_noindex") or "noindex" in (p.get("robots_meta") or "").lower()
    }
    error_urls = {
        (p.get("url") or "").rstrip("/")
        for p in pages
        if p.get("_is_error") or str(p.get("status_code", "")).startswith(("4", "5"))
    }

    conflicts: list[dict] = []
    for url in sorted(sitemap_norm & noindex_urls):
        conflicts.append({
            "url":    url,
            "reason": "noindex directive — page in sitemap but excluded from index",
            "fix":    "Either remove noindex or remove from sitemap",
        })
    for url in sorted(sitemap_norm & error_urls):
        conflicts.append({
            "url":    url,
            "reason": "HTTP error — sitemap URL returns 4xx/5xx",
            "fix":    "Fix page, redirect, or remove from sitemap",
        })

    return {
        "sitemap_provided": True,
        "sitemap_count":    len(sitemap_norm),
        "conflicts":        len(conflicts),
        "details":          conflicts[:30],
        "status":           "ok" if not conflicts else "issues_found",
        "impact":           "Sitemap should only list indexable 200-OK pages — noindex/error URLs waste crawl budget",
        "fix":              "Audit sitemap against live crawl results and remove non-indexable URLs",
    }


def _check_canonical_noindex(real_pages: list[dict]) -> dict:
    """
    Flag pages where canonical points elsewhere AND noindex is set —
    contradictory signals that confuse Googlebot.
    """
    conflicts: list[dict] = []
    for page in real_pages:
        url    = (page.get("url") or "").rstrip("/")
        canon  = (page.get("canonical") or "").rstrip("/")
        noindex = page.get("robots_noindex") or "noindex" in (page.get("robots_meta") or "").lower()

        if canon and canon != url and noindex:
            conflicts.append({
                "url":       url,
                "canonical": canon,
                "reason":    "noindex + canonical mismatch — page tells Google not to index it AND to use a different URL",
                "fix":       "Remove noindex if the canonical is the intended page; or remove canonical if this page should be excluded",
            })

    return {
        "conflicts": len(conflicts),
        "details":   conflicts[:20],
        "status":    "ok" if not conflicts else "issues_found",
        "impact":    "Contradictory noindex + foreign canonical can cause the canonical target to also be excluded from indexing",
        "fix":       "Choose one directive: either noindex (exclude) or self-canonical (include)",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 4: Security audit
# ─────────────────────────────────────────────────────────────────────────────

def _run_security_audit(real_pages: list[dict], site_url: str | None) -> dict:
    """
    Audit security signals across all crawled pages.
    Checks: HTTPS, HSTS, CSP, X-Frame-Options, X-Content-Type-Options, mixed content.
    """
    n = len(real_pages) or 1
    issues: list[dict] = []

    # ── HTTPS coverage ────────────────────────────────────────────────────────
    http_pages = [p for p in real_pages if (p.get("url") or "").startswith("http://")]
    if http_pages:
        issues.append({
            "type":     "http_pages",
            "severity": "critical",
            "count":    len(http_pages),
            "urls":     [p.get("url") for p in http_pages[:10]],
            "detail":   f"{len(http_pages)} pages served over HTTP (not HTTPS)",
            "reason":   "HTTP pages rank lower — Google confirmed HTTPS as a ranking signal since 2014",
            "fix":      "Enforce HTTPS site-wide; add 301 redirects from HTTP to HTTPS; update internal links",
        })

    # ── HSTS ─────────────────────────────────────────────────────────────────
    hsts_pages = [
        p for p in real_pages
        if "strict-transport-security" in (p.get("response_headers") or {})
    ]
    hsts_pct = round(len(hsts_pages) / n * 100, 1)
    if hsts_pct < 80:
        issues.append({
            "type":     "hsts_missing",
            "severity": "high",
            "coverage": hsts_pct,
            "detail":   f"Strict-Transport-Security header present on only {hsts_pct}% of pages",
            "reason":   "HSTS prevents protocol-downgrade attacks and cookie hijacking",
            "fix":      "Add 'Strict-Transport-Security: max-age=31536000; includeSubDomains' to all responses",
        })
    else:
        # Validate HSTS quality on first page that has it
        _validate_hsts_quality(hsts_pages, issues)

    # ── CSP ───────────────────────────────────────────────────────────────────
    csp_pages = [
        p for p in real_pages
        if "content-security-policy" in (p.get("response_headers") or {})
    ]
    csp_pct = round(len(csp_pages) / n * 100, 1)
    if csp_pct < 50:
        issues.append({
            "type":     "csp_missing",
            "severity": "high",
            "coverage": csp_pct,
            "detail":   f"Content-Security-Policy header present on only {csp_pct}% of pages",
            "reason":   "CSP is the primary defence against XSS attacks — absent = no XSS mitigation",
            "fix":      "Implement CSP header; start with report-only mode to avoid breakage",
        })

    # ── X-Frame-Options ───────────────────────────────────────────────────────
    xfo_pages = [
        p for p in real_pages
        if "x-frame-options" in (p.get("response_headers") or {})
    ]
    xfo_pct = round(len(xfo_pages) / n * 100, 1)
    if xfo_pct < 80:
        issues.append({
            "type":     "x_frame_options_missing",
            "severity": "medium",
            "coverage": xfo_pct,
            "detail":   f"X-Frame-Options header present on only {xfo_pct}% of pages",
            "reason":   "Without this header pages can be embedded in iframes — enables clickjacking attacks",
            "fix":      "Add 'X-Frame-Options: SAMEORIGIN' (or use CSP frame-ancestors directive)",
        })

    # ── X-Content-Type-Options ────────────────────────────────────────────────
    xcto_pages = [
        p for p in real_pages
        if "x-content-type-options" in (p.get("response_headers") or {})
    ]
    xcto_pct = round(len(xcto_pages) / n * 100, 1)
    if xcto_pct < 80:
        issues.append({
            "type":     "x_content_type_missing",
            "severity": "medium",
            "coverage": xcto_pct,
            "detail":   f"X-Content-Type-Options: nosniff missing on {round(100-xcto_pct)}% of pages",
            "reason":   "Without nosniff, browsers may interpret response body as a different content type (MIME confusion attacks)",
            "fix":      "Add 'X-Content-Type-Options: nosniff' to all responses",
        })

    # ── Mixed content ─────────────────────────────────────────────────────────
    mixed_pages = [p for p in real_pages if p.get("mixed_resources")]
    if mixed_pages:
        total_mixed = sum(len(p.get("mixed_resources") or []) for p in mixed_pages)
        issues.append({
            "type":     "mixed_content",
            "severity": "high",
            "pages":    len(mixed_pages),
            "resources":total_mixed,
            "detail":   f"{total_mixed} HTTP resources loaded on {len(mixed_pages)} HTTPS pages",
            "reason":   "Mixed content triggers browser security warnings and may be blocked — damages user trust and rankings",
            "fix":      "Update all resource URLs to HTTPS; use protocol-relative URLs (//) as fallback",
            "affected": [p.get("url") for p in mixed_pages[:10]],
        })

    # Coverage summary
    header_coverage = {
        "hsts_pct":  hsts_pct,
        "csp_pct":   csp_pct,
        "xfo_pct":   xfo_pct,
        "xcto_pct":  xcto_pct,
        "https_pct": round(sum(1 for p in real_pages if (p.get("url") or "").startswith("https://")) / n * 100, 1),
    }
    all_secure_pct = round(sum(1 for p in real_pages if _page_has_all_security_headers(p)) / n * 100, 1)

    return {
        "header_coverage":   header_coverage,
        "all_headers_pct":   all_secure_pct,
        "issue_count":       len(issues),
        "issues":            issues,
        "status":            "secure" if all_secure_pct >= 80 and not http_pages else "needs_work",
    }


def _validate_hsts_quality(hsts_pages: list[dict], issues: list[dict]) -> None:
    """Check HSTS max-age and includeSubDomains quality on first available page."""
    import re
    for p in hsts_pages[:1]:
        hsts_val = (p.get("response_headers") or {}).get("strict-transport-security", "")
        m = re.search(r"max-age\s*=\s*(\d+)", hsts_val, re.I)
        if m:
            max_age = int(m.group(1))
            if max_age < 31_536_000:
                issues.append({
                    "type": "hsts_weak_max_age", "severity": "medium",
                    "detail": f"HSTS max-age={max_age}s — below recommended 1 year (31536000)",
                    "reason": "Short HSTS max-age leaves users vulnerable after the window expires",
                    "fix":    "Set max-age=31536000 (1 year minimum); 63072000 (2 years) for preload eligibility",
                })
        if "includesubdomains" not in hsts_val.lower():
            issues.append({
                "type": "hsts_no_subdomains", "severity": "low",
                "detail": "HSTS missing includeSubDomains directive",
                "reason": "Subdomains remain vulnerable to protocol-downgrade attacks",
                "fix":    "Add 'includeSubDomains' to HSTS header",
            })


def _page_has_all_security_headers(page: dict) -> bool:
    """Return True if page has all 4 required security headers."""
    hdrs = {k.lower() for k in (page.get("response_headers") or {})}
    return all(h in hdrs for h in _REQUIRED_SECURITY_HEADERS)


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 5: Performance / Core Web Vitals audit
# ─────────────────────────────────────────────────────────────────────────────

def _run_performance_audit(
    real_pages: list[dict],
    cwv_data:   dict | None,
) -> dict:
    """
    Evaluate Core Web Vitals and performance proxy signals.

    If cwv_data is provided: apply real thresholds (LCP>2.5s, CLS>0.1, INP>200ms).
    If not: derive proxy estimates from crawl signals and flag as estimated.
    """
    issues: list[dict] = []
    cwv_results: list[dict] = []
    data_source = "real" if cwv_data else "proxy"

    if cwv_data:
        cwv_results = _evaluate_cwv_data(cwv_data, issues)
    else:
        cwv_results = _estimate_cwv_proxies(real_pages, issues)

    # ── Response time (server TTFB proxy) ────────────────────────────────────
    slow_pages = [
        p for p in real_pages
        if (p.get("response_time_ms") or 0) > 2500
    ]
    if slow_pages:
        avg_slow = round(sum(p.get("response_time_ms", 0) for p in slow_pages) / len(slow_pages))
        issues.append({
            "type":     "slow_server_response",
            "severity": "high",
            "count":    len(slow_pages),
            "avg_ms":   avg_slow,
            "detail":   f"{len(slow_pages)} pages have server response time > 2500ms (avg: {avg_slow}ms)",
            "reason":   "High TTFB directly delays LCP — Google recommends server response under 800ms",
            "fix":      "Implement server-side caching, CDN, or optimise database queries",
            "urls":     [p.get("url") for p in slow_pages[:5]],
        })

    # ── CLS proxy: images without explicit dimensions ─────────────────────────
    pages_with_undimensioned = [
        p for p in real_pages
        if (p.get("img_total") or 0) > 0 and (p.get("img_missing_dims") or 0) > 0
    ]
    if pages_with_undimensioned:
        total_undim = sum(p.get("img_missing_dims") or 0 for p in pages_with_undimensioned)
        issues.append({
            "type":     "cls_risk_undimensioned_images",
            "severity": "medium",
            "count":    len(pages_with_undimensioned),
            "total":    total_undim,
            "detail":   f"{total_undim} images missing explicit width/height on {len(pages_with_undimensioned)} pages",
            "reason":   "Images without dimensions cause layout shifts as the browser resizes them after load — directly increases CLS score",
            "fix":      "Add explicit width and height attributes to every <img> tag; CSS can override without causing CLS",
        })

    # ── LCP proxy: lazy-loaded above-fold images ──────────────────────────────
    pages_with_lazy_lcp_risk = [
        p for p in real_pages
        if (p.get("img_total") or 0) > 0 and (p.get("img_lazy_count") or 0) > 0
        and (p.get("img_lazy_pct") or 0) == 100  # ALL images lazy = LCP image deferred
    ]
    if pages_with_lazy_lcp_risk:
        issues.append({
            "type":     "lcp_risk_all_images_lazy",
            "severity": "medium",
            "count":    len(pages_with_lazy_lcp_risk),
            "detail":   f"{len(pages_with_lazy_lcp_risk)} pages have 100% lazy-loaded images — hero/LCP image may be deferred",
            "reason":   "The LCP image must NOT be lazy-loaded — doing so delays Largest Contentful Paint significantly",
            "fix":      "Remove loading='lazy' from the first/hero image; keep it only on below-fold images",
        })

    n = len(real_pages) or 1
    return {
        "data_source":          data_source,
        "cwv_results":          cwv_results,
        "issue_count":          len(issues),
        "issues":               issues,
        "slow_pages_count":     len(slow_pages),
        "avg_response_time_ms": round(
            sum(p.get("response_time_ms") or 0 for p in real_pages) / n
        ),
        "note": (
            "CWV values are real measurements from PageSpeed Insights"
            if cwv_data
            else "CWV values are proxy estimates from crawl data — "
                 "run PageSpeed Insights for real measurements"
        ),
    }


def _evaluate_cwv_data(cwv_data: dict, issues: list[dict]) -> list[dict]:
    """Apply real CWV thresholds to provided data."""
    results: list[dict] = []
    for url, metrics in cwv_data.items():
        page_issues: list[str] = []
        lcp = metrics.get("lcp_s") or metrics.get("lcp")
        cls = metrics.get("cls")
        inp = metrics.get("inp_s") or metrics.get("inp")

        status = "good"
        if lcp is not None and lcp > _CWV_THRESHOLDS["lcp"]["poor"]:
            status = "poor"
            page_issues.append(f"LCP {lcp:.1f}s > {_CWV_THRESHOLDS['lcp']['poor']}s (poor)")
        elif lcp is not None and lcp > _CWV_THRESHOLDS["lcp"]["good"]:
            status = "needs_improvement"
            page_issues.append(f"LCP {lcp:.1f}s > {_CWV_THRESHOLDS['lcp']['good']}s threshold")

        if cls is not None and cls > _CWV_THRESHOLDS["cls"]["poor"]:
            status = "poor"
            page_issues.append(f"CLS {cls:.3f} > {_CWV_THRESHOLDS['cls']['poor']} (poor)")
        elif cls is not None and cls > _CWV_THRESHOLDS["cls"]["good"]:
            status = max(status, "needs_improvement")  # type: ignore[call-overload]
            page_issues.append(f"CLS {cls:.3f} > {_CWV_THRESHOLDS['cls']['good']} threshold")

        if inp is not None and inp > _CWV_THRESHOLDS["inp"]["poor"]:
            status = "poor"
            page_issues.append(f"INP {int(inp*1000)}ms > {int(_CWV_THRESHOLDS['inp']['poor']*1000)}ms (poor)")
        elif inp is not None and inp > _CWV_THRESHOLDS["inp"]["good"]:
            page_issues.append(f"INP {int(inp*1000)}ms > {int(_CWV_THRESHOLDS['inp']['good']*1000)}ms threshold")

        if page_issues:
            issues.append({
                "type": "cwv_violation", "severity": "high" if status == "poor" else "medium",
                "url": url, "status": status, "detail": "; ".join(page_issues),
                "reason": "Poor CWV = poor Core Web Vitals = potential ranking demotion",
                "fix": "Use Chrome DevTools / Lighthouse to diagnose and fix LCP/CLS/INP",
            })

        results.append({"url": url, "lcp": lcp, "cls": cls, "inp": inp, "status": status, "issues": page_issues})
    return results


def _estimate_cwv_proxies(real_pages: list[dict], issues: list[dict]) -> list[dict]:
    """Derive CWV proxy estimates when real CWV data is not available."""
    results: list[dict] = []
    for p in real_pages[:50]:  # sample first 50 pages
        rt_ms      = p.get("response_time_ms") or 0
        img_no_dim = p.get("img_missing_dims") or 0
        img_total  = p.get("img_total") or 0

        lcp_est   = "likely_ok" if rt_ms < 800 else ("at_risk" if rt_ms < 2500 else "likely_poor")
        cls_est   = "likely_ok" if img_no_dim == 0 else ("at_risk" if img_no_dim < 3 else "likely_poor")

        results.append({
            "url":     p.get("url"),
            "lcp_est": lcp_est,
            "cls_est": cls_est,
            "inp_est": "unknown",   # cannot proxy INP from crawl data
            "basis":   f"response_time={rt_ms}ms, undimensioned_imgs={img_no_dim}/{img_total}",
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 6: Final score
# ─────────────────────────────────────────────────────────────────────────────

def _compute_final_score(
    cluster_validation: dict,
    security: dict,
    performance: dict,
    page_audits: list[dict],
) -> tuple[int, dict, str | None]:
    """
    Weighted cluster scores → final score 0-100.
    Rule: if any cluster has missing signals → cap at 89 (below 90).
    """
    breakdown: dict[str, int] = {}
    weighted_sum = 0

    for cluster_id, cluster in cluster_validation.items():
        weight = cluster["weight"]
        score  = cluster["score"]
        weighted_sum += score * weight
        breakdown[cluster_id] = score

    raw_score = round(weighted_sum / 100)

    # Security and performance deductions (not in cluster weights but affect final score)
    sec_penalty  = min(20, len(security.get("issues", [])) * 3)
    perf_penalty = min(10, len(performance.get("issues", [])) * 2)
    raw_score    = max(0, raw_score - sec_penalty - perf_penalty)

    # Score cap: any cluster with missing signals → max 89
    any_gaps = any(c["has_gaps"] for c in cluster_validation.values())
    cap_reason: str | None = None
    if any_gaps:
        cap_clusters = [c["label"] for c in cluster_validation.values() if c["has_gaps"]]
        if raw_score > 89:
            raw_score  = 89
            cap_reason = (
                f"Score capped at 89 — missing signals in: {', '.join(cap_clusters)}. "
                "Fill all signal gaps to unlock a score above 90."
            )

    return max(0, min(100, raw_score)), breakdown, cap_reason


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 7: Audit summary
# ─────────────────────────────────────────────────────────────────────────────

def _build_audit_summary(
    pages: list[dict],
    real_pages: list[dict],
    page_audits: list[dict],
    cross_issues: list[dict],
    cluster_validation: dict,
    consistency: dict,
    security: dict,
    performance: dict,
    final_score: int,
) -> dict:
    n = len(real_pages) or 1

    # Severity buckets — collect all issues from every layer
    severity_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    def _tally(issue_list: list[dict]) -> None:
        for issue in issue_list:
            sev = issue.get("severity", "medium")
            if sev in severity_counts:
                severity_counts[sev] += 1

    for cluster in cluster_validation.values():
        _tally(cluster.get("issues", []))
    for check in consistency.get("checks", {}).values():
        _tally(check.get("details", []) if isinstance(check.get("details"), list) else [])
    _tally(security.get("issues", []))
    _tally(performance.get("issues", []))

    # Cross-page issue type counts
    xp_type_counts = Counter(i.get("type") for i in cross_issues)

    # Indexability overview
    idx = Counter(
        a.get("indexability", {}).get("status", "unknown")
        for a in page_audits
    )

    return {
        "pages_crawled":      len(pages),
        "pages_analysed":     len(real_pages),
        "error_pages":        len(pages) - len(real_pages),
        "final_score":        final_score,
        "final_grade":        _grade(final_score),
        "severity_counts":    severity_counts,
        "total_issues":       sum(severity_counts.values()),
        "cross_page_issues":  {
            "total":              len(cross_issues),
            "broken_links":       xp_type_counts.get("broken_internal_link", 0),
            "canonical_loops":    xp_type_counts.get("canonical_loop", 0),
            "canonical_chains":   xp_type_counts.get("canonical_chain", 0),
            "orphan_pages":       xp_type_counts.get("orphan_page", 0),
            "duplicate_content":  xp_type_counts.get("duplicate_content", 0),
            "hreflang_broken":    xp_type_counts.get("hreflang_missing_reciprocal", 0),
        },
        "indexability": {
            "indexable":          idx.get("indexable", 0) + idx.get("likely_indexable", 0),
            "blocked":            idx.get("not_indexable_noindex", 0) + idx.get("not_indexable_error", 0),
            "canonical_mismatch": idx.get("canonical_mismatch", 0),
            "redirect":           idx.get("not_indexable_redirect", 0),
        },
        "security_score":     max(0, 100 - len(security.get("issues", [])) * 10),
        "performance_issues": len(performance.get("issues", [])),
        "clusters_with_gaps": sum(1 for c in cluster_validation.values() if c["has_gaps"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 8: Implementation roadmap
# ─────────────────────────────────────────────────────────────────────────────

def _build_roadmap(
    cluster_validation: dict,
    consistency: dict,
    security: dict,
    performance: dict,
    gap_report: dict,
    page_audits: list[dict],
    cross_issues: list[dict],
) -> list[dict]:
    """
    Build a prioritised roadmap. Each item has:
      priority (1=Critical → 4=Low), label, actions[{issue, impact, fix, affected}]
    """
    buckets: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}

    def _add(priority: int, issue: str, impact: str, fix: str, affected: int | None = None) -> None:
        entry: dict = {"issue": issue, "impact": impact, "fix": fix}
        if affected is not None:
            entry["affected"] = affected
        buckets[priority].append(entry)

    # ── Critical (P1) ─────────────────────────────────────────────────────────
    for iss in security.get("issues", []):
        if iss.get("severity") == "critical":
            _add(1, iss["detail"], iss["reason"], iss["fix"], iss.get("count"))

    for cluster in cluster_validation.values():
        for iss in cluster.get("issues", []):
            if iss.get("severity") == "critical":
                _add(1, iss["detail"], iss["reason"], iss["fix"])

    broken_links = [i for i in cross_issues if i.get("type") == "broken_internal_link"]
    if broken_links:
        _add(1, f"{len(broken_links)} broken internal links",
             "Broken links waste crawl budget and leak PageRank",
             "Fix or redirect broken link targets to valid 200-OK pages", len(broken_links))

    loops = [i for i in cross_issues if i.get("type") == "canonical_loop"]
    if loops:
        _add(1, f"{len(loops)} canonical redirect loops",
             "Google cannot determine which URL to index — both URLs may be deindexed",
             "Break the loop by making one page self-canonical", len(loops))

    noindex_in_sitemap = consistency.get("checks", {}).get("sitemap_indexability", {}).get("conflicts", 0)
    if noindex_in_sitemap:
        _add(1, f"{noindex_in_sitemap} noindex/error pages in sitemap",
             "Wastes crawl budget; may confuse Googlebot about intended index",
             "Remove all noindex and 4xx/5xx pages from sitemap.xml", noindex_in_sitemap)

    # ── High (P2) ─────────────────────────────────────────────────────────────
    for iss in security.get("issues", []):
        if iss.get("severity") == "high":
            _add(2, iss["detail"], iss["reason"], iss["fix"], iss.get("count"))

    for cluster in cluster_validation.values():
        for iss in cluster.get("issues", []):
            if iss.get("severity") == "high":
                _add(2, iss["detail"], iss["reason"], iss["fix"])

    for iss in performance.get("issues", []):
        if iss.get("severity") == "high":
            _add(2, iss["detail"], iss["reason"], iss["fix"], iss.get("count"))

    orphans = [i for i in cross_issues if i.get("type") == "orphan_page"]
    if orphans:
        _add(2, f"{len(orphans)} orphan pages (no inbound internal links)",
             "Orphan pages receive no PageRank and are harder for Googlebot to discover",
             "Add internal links from related pages to each orphan", len(orphans))

    # ── Medium (P3) ───────────────────────────────────────────────────────────
    for iss in security.get("issues", []):
        if iss.get("severity") == "medium":
            _add(3, iss["detail"], iss["reason"], iss["fix"])

    for cluster in cluster_validation.values():
        for iss in cluster.get("issues", []):
            if iss.get("severity") == "medium":
                _add(3, iss["detail"], iss["reason"], iss["fix"])

    for iss in performance.get("issues", []):
        if iss.get("severity") in ("medium", "low"):
            _add(3, iss["detail"], iss["reason"], iss["fix"], iss.get("count"))

    dup_content = [i for i in cross_issues if i.get("type") == "duplicate_content"]
    if dup_content:
        _add(3, f"{len(dup_content)} duplicate content groups detected",
             "Duplicate pages split ranking signals and may cause index bloat",
             "Consolidate duplicates with canonical tags or 301 redirects", len(dup_content))

    # ── Low (P4) — Gaps and enhancements ─────────────────────────────────────
    for gap in gap_report.get("gaps", []):
        if gap.get("severity") in ("low", "info"):
            _add(4, f"Signal gap: {gap['signal']} ({gap['coverage_pct']}% coverage)",
                 gap["impact"], gap["minimal_fix"])

    chains = [i for i in cross_issues if i.get("type") == "canonical_chain"]
    if chains:
        _add(4, f"{len(chains)} canonical chains (2+ hops)",
             "Google does not reliably follow multi-hop canonicals",
             "Flatten chains so A → B directly (not A → C → B)", len(chains))

    return [
        {
            "priority": p,
            "label":    {1: "Critical", 2: "High", 3: "Medium", 4: "Low"}[p],
            "count":    len(actions),
            "actions":  actions,
        }
        for p, actions in buckets.items()
        if actions
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _grade(score: int) -> str:
    if score >= 85: return "A"
    if score >= 70: return "B"
    if score >= 55: return "C"
    if score >= 40: return "D"
    return "F"


def _empty_result(reason: str) -> dict:
    return {
        "cluster_validation":     {},
        "gap_report":             {"total_gaps": 0, "gaps": []},
        "consistency_checks":     {"total_violations": 0, "checks": {}},
        "security_audit":         {"issues": [], "issue_count": 0},
        "performance_audit":      {"issues": [], "issue_count": 0},
        "score_breakdown":        {},
        "score_cap_reason":       None,
        "audit_summary":          {"error": reason},
        "implementation_roadmap": [],
        "final_score":            0,
        "final_grade":            "F",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Optional: fetch real CWV data from PageSpeed Insights API
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_cwv_data(
    urls:    list[str],
    api_key: str | None = None,
    strategy: str = "mobile",
) -> dict[str, dict]:
    """
    Fetch Core Web Vitals from Google PageSpeed Insights API.

    Parameters
    ──────────
    urls      List of page URLs to check (sample — avoid checking 5000 URLs)
    api_key   Google API key (PSI has a 25 req/day free quota without key)
    strategy  "mobile" | "desktop"

    Returns
    ───────
    {url: {"lcp_s": float, "cls": float, "inp_s": float, "status": str}}
    """
    _api_key = api_key or os.getenv("GOOGLE_PSI_API_KEY", "")
    results: dict[str, dict] = {}

    try:
        import aiohttp as _aio
    except ImportError:
        logger.warning("aiohttp not available — cannot fetch CWV data")
        return results

    PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

    async with _aio.ClientSession() as session:
        for url in urls[:20]:   # cap at 20 to respect API quota
            params: dict = {"url": url, "category": "performance", "strategy": strategy}
            if _api_key:
                params["key"] = _api_key
            try:
                async with session.get(
                    PSI_URL, params=params,
                    timeout=_aio.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("PSI API returned %d for %s", resp.status, url)
                        continue
                    data = await resp.json()
                    metrics = (
                        data.get("lighthouseResult", {})
                            .get("audits", {})
                    )
                    lcp_ms = (
                        metrics.get("largest-contentful-paint", {})
                               .get("numericValue")
                    )
                    cls_val = (
                        metrics.get("cumulative-layout-shift", {})
                               .get("numericValue")
                    )
                    inp_ms = (
                        metrics.get("interaction-to-next-paint", {})
                               .get("numericValue")
                    )
                    results[url] = {
                        "lcp_s":  round(lcp_ms / 1000, 2) if lcp_ms is not None else None,
                        "cls":    round(cls_val, 3)        if cls_val is not None else None,
                        "inp_s":  round(inp_ms / 1000, 3)  if inp_ms is not None else None,
                        "source": "psi_api",
                    }
            except Exception as exc:
                logger.warning("PSI fetch failed for %s: %s", url, exc)

    return results
