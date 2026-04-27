"""
seo_audit_engine.py — Production-grade SEO audit orchestrator with strict validation.

Scoring architecture
────────────────────
  Five equal-weight clusters (20 pts each → 100 pts total):
    indexability  • on_page  • technical  • performance  • security

  Cluster scoring rules:
    • 100% signal coverage required per cluster.
    • If any CRITICAL signal is below its threshold → cluster score capped at 85.
    • If any CRITICAL issue exists anywhere → site_score capped at 90.

  Three output granularities:
    page_score    per-page 0-100 score with deduction breakdown + WHY explanation
    cluster_score {cluster_id: score} dict (each 0-100, capped at 85 if gaps)
    site_score    final weighted average 0-100 (capped at 90 if CRITICAL issues)

  Grade thresholds:
    90-100 → A+   (no critical gaps, all signals covered)
    80-89  → A
    70-79  → B
    <70    → Needs Fix

Entry point
───────────
    from seo_audit_engine import run_full_audit

    result = run_full_audit(
        pages,                          # list[dict] from SEOCrawler
        sitemap_urls=None,              # list[str] parsed from sitemap XML
        cwv_data=None,                  # dict[url → {lcp_s, cls, inp_s}] from PSI API
        site_url=None,                  # root URL for robots.txt / HSTS check
        tech_audit=None,                # pre-computed analyze_all() output (optional)
        validation=None,                # pre-computed validate_all() output (optional)
    )

    # Key result fields:
    #   page_score            list[{url, page_score, grade, deductions, why}]
    #   cluster_score         {cluster_id: score}
    #   site_score            int (0-100)
    #   final_grade           "A+" | "A" | "B" | "Needs Fix"
    #   score_cap_reason      str explaining WHY/WHICH signals triggered a cap
    #   cluster_validation    full per-cluster breakdown with deduction_reasons
    #   gap_report            missing-signal details + minimal fix guidance
    #   implementation_roadmap prioritised action list (Critical → High → Medium → Low)
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

# Five equal-weight clusters (5 × 20 = 100).
# severity "CRITICAL" → cluster capped at 85 if signal below threshold.
# severity "HIGH"     → penalises cluster score but no hard cap.
# threshold 0         → not a coverage signal; quality metrics evaluated inside _score_cluster.
_CLUSTERS: dict[str, dict] = {
    "indexability": {
        "label":   "Indexability",
        "weight":  20,
        "signals": {
            # status_ok: every page must return 2xx
            "status_ok": {
                "threshold":   100,
                "severity":    "CRITICAL",
                "description": "Every page must return HTTP 2xx status",
            },
            # canonical: strong majority of pages should declare a canonical
            "canonical": {
                "threshold":   80,
                "severity":    "HIGH",
                "description": "80%+ of pages should declare a <link rel=canonical>",
            },
        },
    },
    "on_page": {
        "label":   "On-Page SEO",
        "weight":  20,
        "signals": {
            "title": {
                "threshold":   100,
                "severity":    "CRITICAL",
                "description": "Every page must have a <title> tag",
            },
            "meta_description": {
                "threshold":   90,
                "severity":    "HIGH",
                "description": "90%+ of pages need a meta description",
            },
            "h1": {
                "threshold":   95,
                "severity":    "HIGH",
                "description": "95%+ of pages need at least one H1 heading",
            },
            "canonical": {
                "threshold":   80,
                "severity":    "HIGH",
                "description": "80%+ of pages need a self-canonical link",
            },
        },
    },
    "technical": {
        "label":   "Technical SEO",
        "weight":  20,
        "signals": {
            # viewport: missing tag = Google mobile-first penalty
            "viewport": {
                "threshold":   100,
                "severity":    "CRITICAL",
                "description": "Every page must have a mobile viewport meta tag",
            },
            # https: HTTPS is a confirmed ranking signal
            "https": {
                "threshold":   100,
                "severity":    "CRITICAL",
                "description": "Every page must be served over HTTPS",
            },
        },
    },
    "performance": {
        "label":   "Performance",
        "weight":  20,
        "signals": {
            # threshold 0 = quality metric; evaluated per-value not per-coverage
            "response_time_ms": {
                "threshold":   0,
                "severity":    "HIGH",
                "description": "Server response time (proxy signal for TTFB / LCP)",
            },
            "resource_count": {
                "threshold":   0,
                "severity":    "HIGH",
                "description": "Total resource-loading elements per page",
            },
        },
    },
    "security": {
        "label":   "Security",
        "weight":  20,
        "signals": {
            # all_security_headers: all 6 required headers must be present on every page
            "all_security_headers": {
                "threshold":   100,
                "severity":    "CRITICAL",
                "description": "All 6 required security headers must be present on every page",
            },
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

# Explicit CWV status ordering — avoids relying on alphabetical string comparison
_CWV_STATUS_RANK: dict[str, int] = {"good": 0, "needs_improvement": 1, "poor": 2}

# Required security headers (header-name → missing-message)
_REQUIRED_SECURITY_HEADERS: dict[str, str] = {
    "strict-transport-security": "HSTS missing — browsers won't enforce HTTPS-only access",
    "content-security-policy":   "CSP missing — no XSS mitigation policy declared",
    "x-frame-options":           "X-Frame-Options missing — page embeddable in iframes (clickjacking risk)",
    "x-content-type-options":    "X-Content-Type-Options missing — MIME sniffing not blocked",
    "referrer-policy":           "Referrer-Policy missing — full URL leaked in Referer on cross-origin requests",
    "permissions-policy":        "Permissions-Policy missing — browser features not explicitly restricted",
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
    _errors: list[str] = []

    # ── Annotate canonical chains on real pages ────────────────────────────────
    # Attaches canonical_status / canonical_chain fields so the frontend and
    # downstream audit rules can surface loop / broken_target / too_deep issues.
    try:
        _annotate_canonical_chains(real_pages)
    except Exception:
        pass  # Non-fatal — audit continues without chain annotations

    # ── Run sub-validators (or use pre-computed results) ──────────────────────
    if tech_audit is None:
        try:
            from technical_seo import analyze_all as _tech_analyze_all
            tech_audit = _tech_analyze_all(pages)
        except Exception as exc:
            _errors.append(f"technical_seo.analyze_all: {exc}")
            logger.warning("technical_seo.analyze_all failed: %s", exc)
            tech_audit = {"pages": [], "summary": {}}

    if validation is None:
        try:
            from issues import validate_all as _validate_all
            validation = _validate_all(pages, sitemap_urls=sitemap_urls)
        except Exception as exc:
            _errors.append(f"issues.validate_all: {exc}")
            logger.warning("issues.validate_all failed: %s", exc)
            validation = {"page_issues": [], "cross_page_issues": [], "stats": {}}

    page_audits: list[dict] = tech_audit.get("pages", [])
    cross_issues: list[dict] = validation.get("cross_page_issues", [])

    # ── Layer 1: Cluster validation ───────────────────────────────────────────
    cluster_validation = _validate_clusters(real_pages, page_audits)

    # ── Layer 2: Gap detection ────────────────────────────────────────────────
    gap_report = _detect_gaps(cluster_validation)

    # ── Layer 3: Per-page scoring ─────────────────────────────────────────────
    page_scores = _compute_page_scores(real_pages)

    # ── Layer 4: Consistency checks ───────────────────────────────────────────
    consistency = _run_consistency_checks(pages, sitemap_urls, cross_issues)

    # ── Layer 5: Security audit ───────────────────────────────────────────────
    security = _run_security_audit(real_pages, site_url)

    # ── Layer 6: Performance / CWV audit ─────────────────────────────────────
    performance = _run_performance_audit(real_pages, cwv_data)

    # ── Layer 7: Final score ──────────────────────────────────────────────────
    site_score, cluster_scores, score_cap_reason = _compute_final_score(
        cluster_validation, security, performance, page_audits,
    )

    # ── Layer 8: Audit summary ────────────────────────────────────────────────
    summary = _build_audit_summary(
        pages, real_pages, page_audits, cross_issues,
        cluster_validation, consistency, security, performance,
        site_score, page_scores,
    )

    # ── Layer 9: Implementation roadmap ──────────────────────────────────────
    roadmap = _build_roadmap(
        cluster_validation, consistency, security, performance, gap_report,
        page_audits, cross_issues,
    )

    return {
        # ── Scores (three granularities) ─────────────────────────────────────
        "page_score":          page_scores,          # per-page: [{url, page_score, grade, deductions, why}]
        "cluster_score":       cluster_scores,        # {cluster_id: score}
        "site_score":          site_score,            # final weighted site score (0-100)
        # ── Full audit data ───────────────────────────────────────────────────
        "cluster_validation":  cluster_validation,
        "gap_report":          gap_report,
        "consistency_checks":  consistency,
        "security_audit":      security,
        "performance_audit":   performance,
        "score_breakdown":     cluster_scores,        # alias kept for backward compat
        "score_cap_reason":    score_cap_reason,
        "audit_summary":       summary,
        "implementation_roadmap": roadmap,
        "final_score":         site_score,            # alias kept for backward compat
        "final_grade":         _grade(site_score),
        "errors":              _errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Layer 1: Cluster validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_clusters(real_pages: list[dict], page_audits: list[dict]) -> dict:
    """
    For each cluster:
      • Measure signal coverage (% of pages where each signal is present).
      • If any CRITICAL-severity signal is below its threshold → flag
        critical_signal_missing=True and cap the cluster score at 85.
      • Record deduction_reasons explaining WHY the score was reduced and
        WHICH signals are missing.
    """
    n = len(real_pages) or 1
    results: dict[str, dict] = {}

    for cluster_id, cluster_def in _CLUSTERS.items():
        signals = cluster_def["signals"]
        coverage: dict[str, float] = {}
        missing_signals: list[dict] = []
        critical_signal_missing = False

        for signal, cfg in signals.items():
            threshold = cfg["threshold"]
            severity  = cfg["severity"]

            if threshold == 0:
                # Quality metric — not a coverage signal; always mark as covered.
                coverage[signal] = 100.0
                continue

            present = sum(1 for p in real_pages if _signal_present(p, signal))
            pct = round(present / n * 100, 1)
            coverage[signal] = pct

            if pct < threshold:
                if severity == "CRITICAL":
                    critical_signal_missing = True
                missing_signals.append({
                    "signal":        signal,
                    "coverage_pct":  pct,
                    "threshold_pct": threshold,
                    "severity":      severity,
                    "gap":           f"{round(threshold - pct, 1)}% below threshold",
                    "fix":           _signal_fix(signal),
                    "impact":        _signal_impact(signal),
                })

        raw_score     = _score_cluster(cluster_id, real_pages, page_audits, coverage)
        capped_at_85  = critical_signal_missing and raw_score > 85
        cluster_score = 85 if capped_at_85 else raw_score

        # Build human-readable deduction reasons
        deduction_reasons: list[str] = []
        if capped_at_85:
            crit_names = [ms["signal"] for ms in missing_signals if ms["severity"] == "CRITICAL"]
            deduction_reasons.append(
                f"Score capped at 85 — CRITICAL signal(s) below 100% coverage: "
                f"{', '.join(crit_names)}"
            )
        for ms in missing_signals:
            deduction_reasons.append(
                f"[{ms['severity']}] '{ms['signal']}' at {ms['coverage_pct']}% "
                f"(need {ms['threshold_pct']}%) — {ms['fix']}"
            )

        issues = _cluster_issues(cluster_id, real_pages, page_audits, missing_signals)

        results[cluster_id] = {
            "label":                   cluster_def["label"],
            "weight":                  cluster_def["weight"],
            "score":                   cluster_score,
            "raw_score":               raw_score,
            "capped_at_85":            capped_at_85,
            "critical_signal_missing": critical_signal_missing,
            "signal_coverage":         coverage,
            "missing_signals":         missing_signals,
            "has_gaps":                bool(missing_signals),
            "deduction_reasons":       deduction_reasons,
            "issues":                  issues,
            "issue_count":             len(issues),
        }

    return results


def _signal_present(page: dict, signal: str) -> bool:
    """Return True if the signal field is meaningfully populated on this page."""
    # Special computed signals
    if signal == "status_ok":
        status = page.get("status_code", 200)
        return isinstance(status, int) and 200 <= status < 400
    if signal == "https":
        return (page.get("url") or "").startswith("https://")
    if signal == "all_security_headers":
        return _page_has_all_security_headers(page)

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
        "title":                "Add unique, descriptive <title> tags to all pages",
        "meta_description":     "Write unique 120-160 char meta descriptions for every page",
        "h1":                   "Add exactly one H1 tag per page matching the page topic",
        "canonical":            "Add <link rel='canonical'> to every page (self-referencing where appropriate)",
        "viewport":             "Add <meta name='viewport' content='width=device-width, initial-scale=1'>",
        "https":                "Redirect all HTTP URLs to HTTPS with 301 redirects; update internal links",
        "status_ok":            "Fix or 301-redirect all pages returning 4xx/5xx status codes",
        "all_security_headers": (
            "Configure your web server / CDN to send all 6 headers on every response: "
            "Strict-Transport-Security, Content-Security-Policy, X-Frame-Options, "
            "X-Content-Type-Options, Referrer-Policy, Permissions-Policy"
        ),
        "response_headers":     "Configure web server to send security headers on every response",
    }
    return fixes.get(signal, f"Ensure '{signal}' is populated on all pages")


def _signal_impact(signal: str) -> str:
    impacts = {
        "title":                "Title is the primary SERP display text — missing titles get auto-generated (often poorly) by Google",
        "meta_description":     "Meta descriptions directly affect SERP click-through rate",
        "h1":                   "H1 is Google's primary topic-relevance heading signal",
        "canonical":            "Without canonicals, Google may consolidate ranking signals to an unintended duplicate URL",
        "viewport":             "Missing viewport causes poor mobile rendering — Google mobile-first indexing penalises this",
        "https":                "HTTPS is a confirmed Google ranking signal; HTTP pages rank lower and trigger browser security warnings",
        "status_ok":            "4xx/5xx pages waste crawl budget and are excluded from Google's index",
        "all_security_headers": (
            "Missing security headers expose users to XSS, clickjacking, and MIME-sniffing attacks; "
            "also a signal of poor security hygiene to security scanners"
        ),
        "response_headers":     "Security headers missing = known vulnerabilities exposed to users and scanners",
    }
    return impacts.get(signal, f"Signal '{signal}' is required for complete SEO analysis")


def _score_cluster(
    cluster_id: str,
    real_pages: list[dict],
    page_audits: list[dict],
    coverage: dict[str, float],
) -> int:
    """
    Compute raw 0-100 score for a cluster based on coverage + quality signals.
    Capping at 85 for missing CRITICAL signals is applied in _validate_clusters, not here.
    """
    n = len(real_pages) or 1

    if cluster_id == "indexability":
        # Primary: % of pages that are indexable (2xx, no noindex)
        indexable = sum(
            1 for a in page_audits
            if a.get("indexability", {}).get("status") in ("indexable", "likely_indexable")
        )
        base = round(indexable / n * 100)
        # Deduct for 4xx / 5xx pages (each error page is a wasted crawl-budget slot)
        errors = sum(
            1 for p in real_pages
            if isinstance(p.get("status_code"), int) and p.get("status_code", 200) >= 400
        )
        error_penalty = round(errors / n * 30)
        return max(0, base - error_penalty)

    if cluster_id == "on_page":
        # Weighted coverage of the four on-page signals
        base = (
            coverage.get("title",            0) * 0.35 +
            coverage.get("meta_description", 0) * 0.25 +
            coverage.get("h1",               0) * 0.25 +
            coverage.get("canonical",        0) * 0.15
        )
        # Deduct for quality issues (duplicate / overlength titles and metas)
        dup_titles  = sum(1 for a in page_audits if a.get("title", {}).get("issues"))
        dup_metas   = sum(1 for a in page_audits if a.get("meta",  {}).get("issues"))
        issue_rate  = (dup_titles + dup_metas) / (2 * n) * 100
        return max(0, min(100, round(base - issue_rate * 0.3)))

    if cluster_id == "technical":
        # viewport coverage (50%) + HTTPS coverage (50%)
        # Bonus: correct viewport (width=device-width AND NOT user-scalable=no)
        with_viewport = sum(1 for p in real_pages if p.get("viewport"))
        https_pages   = sum(1 for p in real_pages if (p.get("url") or "").startswith("https://"))
        correct_vp    = sum(
            1 for p in real_pages
            if "width=device-width" in (p.get("viewport") or "").lower()
            and "user-scalable=no"  not in (p.get("viewport") or "").lower()
        )
        return max(0, round(
            (with_viewport / n * 40) +
            (https_pages   / n * 40) +
            (correct_vp    / n * 20)
        ))

    if cluster_id == "performance":
        # Four proxy signals — each gets a sub-score, then weighted average
        # 1. Lazy-loading: % of image-heavy pages using lazy-load on 50%+ images
        img_pages  = [p for p in real_pages if (p.get("img_total") or 0) > 2]
        good_lazy  = (
            sum(1 for p in img_pages if (p.get("img_lazy_pct") or 0) >= 50)
            if img_pages else n
        )
        lazy_score = round(good_lazy / len(img_pages) * 100) if img_pages else 100

        # 2. Request budget: penalise pages with >100 resource-loading elements
        bad_requests   = sum(1 for p in real_pages if (p.get("resource_count") or 0) > 100)
        request_score  = max(0, 100 - round(bad_requests / n * 100))

        # 3. Page weight: penalise pages with >2 MB HTML
        oversized      = sum(1 for p in real_pages if (p.get("html_size_kb") or 0) > 2048)
        size_score     = max(0, 100 - round(oversized / n * 100))

        # 4. Image format: penalise legacy formats (JPEG/PNG/GIF instead of WebP/AVIF)
        legacy_img     = sum(1 for p in real_pages if (p.get("img_non_modern_count") or 0) > 0)
        format_score   = max(0, 100 - round(legacy_img / n * 60))

        return round(
            lazy_score    * 0.35 +
            request_score * 0.30 +
            size_score    * 0.20 +
            format_score  * 0.15
        )

    if cluster_id == "security":
        # % of pages with ALL six required security headers
        full_coverage = sum(
            1 for p in real_pages if _page_has_all_security_headers(p)
        )
        return round(full_coverage / n * 100)

    return 0


def _cluster_issues(
    cluster_id: str,
    real_pages: list[dict],
    page_audits: list[dict],
    missing_signals: list[dict],
) -> list[dict]:
    """
    Return structured issues for each cluster.
    Each issue includes: type, severity, detail (WHAT), reason (WHY), fix (HOW).
    """
    issues: list[dict] = []
    n = len(real_pages) or 1

    # Always lead with missing-signal issues so the explanation is clear
    for ms in missing_signals:
        issues.append({
            "type":     "missing_signal",
            "signal":   ms["signal"],
            "severity": ms["severity"],
            "detail":   (
                f"Signal '{ms['signal']}' present on only {ms['coverage_pct']}% of pages "
                f"(need {ms['threshold_pct']}%)"
            ),
            "reason":   ms["impact"],
            "fix":      ms["fix"],
        })

    # ── Cluster-specific quality issues ──────────────────────────────────────

    if cluster_id == "indexability":
        noindex = sum(
            1 for p in real_pages
            if p.get("noindex")
            or "noindex" in (p.get("robots_meta") or "").lower()
            or p.get("x_robots_noindex")
        )
        errors_4xx = sum(
            1 for p in real_pages if str(p.get("status_code", "")).startswith("4")
        )
        errors_5xx = sum(
            1 for p in real_pages if str(p.get("status_code", "")).startswith("5")
        )
        if errors_5xx:
            issues.append({
                "type":     "5xx_pages",
                "severity": "CRITICAL",
                "detail":   f"{errors_5xx} pages return 5xx server errors",
                "reason":   "Server errors block indexing and signal instability to Google",
                "fix":      "Investigate server logs and resolve the root cause",
            })
        if errors_4xx:
            issues.append({
                "type":     "4xx_pages",
                "severity": "CRITICAL",
                "detail":   f"{errors_4xx} pages return 4xx errors",
                "reason":   "4xx pages cannot be indexed and waste crawl budget",
                "fix":      "Fix broken pages or 301-redirect them to a valid URL",
            })
        if noindex:
            issues.append({
                "type":     "noindex_pages",
                "severity": "CRITICAL",
                "detail":   f"{noindex} pages carry a noindex directive",
                "reason":   "Noindex pages are excluded from Google's index and will not rank",
                "fix":      "Verify each noindex page is intentionally excluded — remove the directive if not",
            })

    if cluster_id == "on_page":
        long_title = sum(1 for p in real_pages if len(p.get("title") or "") > 60)
        long_meta  = sum(1 for p in real_pages if len(p.get("meta_description") or "") > 160)
        dup_title  = sum(
            1 for p in real_pages if "Duplicate Title" in (p.get("issues") or [])
        )
        dup_meta   = sum(
            1 for p in real_pages if "Duplicate Meta Description" in (p.get("issues") or [])
        )
        if dup_title:
            issues.append({
                "type":     "duplicate_titles",
                "severity": "HIGH",
                "detail":   f"{dup_title} pages share duplicate <title> tags",
                "reason":   "Duplicate titles confuse Google about which page to rank for a query",
                "fix":      "Write a unique, descriptive title for each page",
            })
        if dup_meta:
            issues.append({
                "type":     "duplicate_meta",
                "severity": "HIGH",
                "detail":   f"{dup_meta} pages share duplicate meta descriptions",
                "reason":   "Duplicate meta descriptions reduce SERP click diversity",
                "fix":      "Write unique 120–160 character meta descriptions for each page",
            })
        if long_title:
            issues.append({
                "type":     "title_too_long",
                "severity": "MEDIUM",
                "detail":   f"{long_title} pages have titles exceeding 60 characters",
                "reason":   "Titles truncated in SERP lose keyword visibility beyond ~580px",
                "fix":      "Trim titles to 30–60 characters; put primary keyword first",
            })
        if long_meta:
            issues.append({
                "type":     "meta_too_long",
                "severity": "MEDIUM",
                "detail":   f"{long_meta} pages have meta descriptions exceeding 160 characters",
                "reason":   "Google truncates long meta descriptions, cutting off the CTA",
                "fix":      "Trim meta descriptions to 120–160 characters",
            })

    if cluster_id == "technical":
        no_viewport = sum(1 for p in real_pages if not p.get("viewport"))
        http_pages  = sum(
            1 for p in real_pages if (p.get("url") or "").startswith("http://")
        )
        bad_viewport = sum(
            1 for p in real_pages
            if "user-scalable=no" in (p.get("viewport") or "").lower()
        )
        if http_pages:
            issues.append({
                "type":     "http_pages",
                "severity": "CRITICAL",
                "detail":   f"{http_pages} pages served over unencrypted HTTP",
                "reason":   "HTTPS is a confirmed Google ranking signal; HTTP shows browser security warnings",
                "fix":      "Redirect all HTTP URLs to HTTPS with 301 redirects; update internal links",
            })
        if no_viewport:
            issues.append({
                "type":     "missing_viewport",
                "severity": "CRITICAL",
                "detail":   f"{no_viewport} pages missing mobile viewport meta tag",
                "reason":   "Missing viewport causes poor mobile rendering — Google mobile-first indexing penalises this",
                "fix":      "Add <meta name='viewport' content='width=device-width, initial-scale=1'> to every page",
            })
        if bad_viewport:
            issues.append({
                "type":     "restrictive_viewport",
                "severity": "HIGH",
                "detail":   f"{bad_viewport} pages use user-scalable=no or maximum-scale=1",
                "reason":   "Disabling user zoom is an accessibility violation and a mobile UX signal for Google",
                "fix":      "Remove user-scalable=no and maximum-scale=1 from all viewport tags",
            })

    if cluster_id == "performance":
        slow = sum(1 for p in real_pages if (p.get("response_time_ms") or 0) > 2500)
        heavy_requests = sum(
            1 for p in real_pages if (p.get("resource_count") or 0) > 100
        )
        oversized = sum(1 for p in real_pages if (p.get("html_size_kb") or 0) > 2048)
        legacy_img = sum(
            1 for p in real_pages if (p.get("img_non_modern_count") or 0) > 0
        )
        if slow:
            issues.append({
                "type":     "slow_response_time",
                "severity": "HIGH",
                "detail":   f"{slow} pages have server response time > 2500ms",
                "reason":   "High TTFB is the strongest crawl-time predictor of poor LCP — Google recommends < 800ms",
                "fix":      "Add server-side caching, use a CDN, or optimise database queries to reduce TTFB",
            })
        if heavy_requests:
            issues.append({
                "type":     "excessive_resource_requests",
                "severity": "HIGH",
                "detail":   f"{heavy_requests} pages load more than 100 resource elements",
                "reason":   "Each additional request adds round-trip latency — a key LCP risk factor",
                "fix":      "Bundle scripts/styles, lazy-load below-fold resources, remove unused third-party scripts",
            })
        if oversized:
            issues.append({
                "type":     "oversized_html",
                "severity": "HIGH",
                "detail":   f"{oversized} pages have HTML response > 2 MB",
                "reason":   "Large HTML payloads delay Time-to-First-Byte and parsing start",
                "fix":      "Enable GZIP/Brotli compression; defer large inline scripts/styles to external files",
            })
        if legacy_img:
            issues.append({
                "type":     "legacy_image_formats",
                "severity": "MEDIUM",
                "detail":   f"{legacy_img} pages use JPEG/PNG/GIF instead of WebP or AVIF",
                "reason":   "Legacy formats are 25–50% larger than WebP — increases LCP and wastes bandwidth",
                "fix":      "Convert images to WebP or AVIF; use <picture> srcset for progressive adoption",
            })

    if cluster_id == "security":
        missing_header_counts: dict[str, int] = {}
        for p in real_pages:
            hdrs = {k.lower(): v for k, v in (p.get("response_headers") or {}).items()}
            for header in _REQUIRED_SECURITY_HEADERS:
                if header not in hdrs:
                    missing_header_counts[header] = missing_header_counts.get(header, 0) + 1

        for header, count in sorted(missing_header_counts.items(), key=lambda x: -x[1]):
            pct = round(count / n * 100, 1)
            sev = "CRITICAL" if header == "strict-transport-security" else "HIGH"
            issues.append({
                "type":     "missing_security_header",
                "severity": sev,
                "detail":   f"{header} missing on {count} pages ({pct}%)",
                "reason":   _REQUIRED_SECURITY_HEADERS.get(header, f"{header} not set"),
                "fix":      f"Configure your web server/CDN to send the '{header}' header on every response",
            })

        tls_issues = [
            p for p in real_pages
            if p.get("tls_version") and p["tls_version"] in ("TLSv1", "TLSv1.1", "SSLv3", "SSLv2")
        ]
        if tls_issues:
            issues.append({
                "type":     "legacy_tls",
                "severity": "CRITICAL",
                "detail":   f"{len(tls_issues)} pages negotiated a legacy TLS version (< TLS 1.2)",
                "reason":   "TLS < 1.2 has known cryptographic weaknesses and fails PCI-DSS compliance",
                "fix":      "Disable TLS 1.0/1.1 on the server; enforce TLS 1.2+ minimum",
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
#  Per-page scoring
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (signal_key, severity, max_points, description_when_missing)
# Points sum to 100; missing a signal deducts its points from the page score.
_PAGE_SIGNAL_WEIGHTS: list[tuple[str, str, int, str]] = [
    ("title",                "CRITICAL", 20, "Missing <title> tag"),
    ("viewport",             "CRITICAL", 15, "Missing mobile viewport meta tag"),
    ("https",                "CRITICAL", 15, "Page not served over HTTPS"),
    ("h1",                   "HIGH",     12, "Missing H1 heading"),
    ("meta_description",     "HIGH",     10, "Missing meta description"),
    ("canonical",            "HIGH",      8, "Missing <link rel=canonical>"),
    ("all_security_headers", "HIGH",     10, "One or more required security headers absent"),
    ("no_noindex",           "CRITICAL",  7, "Page has a noindex directive (excluded from index)"),
    # total = 97; remaining 3 pts are a baseline everyone starts with
]
# Status-code overrides (applied after deductions)
_STATUS_CAP = {4: 30, 5: 20}   # 4xx → cap score at 30; 5xx → cap score at 20


def _compute_page_scores(real_pages: list[dict]) -> list[dict]:
    """
    Compute a per-page score (0–100) for every crawled page.

    Each page starts at 100. Missing signals subtract their weight.
    4xx/5xx pages are capped at 30/20 respectively.

    Returns a list of dicts, one per page:
    {
        "url":        str,
        "page_score": int,           # 0-100
        "grade":      str,           # A+ / A / B / Needs Fix
        "deductions": [
            {"signal": str, "severity": str, "points_lost": int, "reason": str}
        ],
        "why":        str,           # human-readable score explanation
    }
    """
    results: list[dict] = []

    for page in real_pages:
        score = 100
        deductions: list[dict] = []

        for signal, severity, points, reason in _PAGE_SIGNAL_WEIGHTS:
            # "no_noindex" is an inverse signal — penalise if noindex IS present
            if signal == "no_noindex":
                is_noindex = (
                    page.get("noindex")
                    or page.get("x_robots_noindex")
                    or "noindex" in (page.get("robots_meta") or "").lower()
                )
                present = not is_noindex
            else:
                present = _signal_present(page, signal)

            if not present:
                score -= points
                deductions.append({
                    "signal":      signal,
                    "severity":    severity,
                    "points_lost": points,
                    "reason":      reason,
                })

        # Status-code override
        status = page.get("status_code", 200)
        if isinstance(status, int):
            prefix = status // 100
            if prefix in _STATUS_CAP:
                cap = _STATUS_CAP[prefix]
                if score > cap:
                    deductions.append({
                        "signal":      "status_code",
                        "severity":    "CRITICAL",
                        "points_lost": score - cap,
                        "reason":      f"HTTP {status} error page — page cannot be indexed",
                    })
                    score = cap

        score = max(0, min(100, score))
        grade = _grade(score)

        # Build a single-sentence WHY explanation
        if not deductions:
            why = "All required signals present — no deductions applied."
        else:
            crit_items = [d["reason"] for d in deductions if d["severity"] == "CRITICAL"]
            high_items = [d["reason"] for d in deductions if d["severity"] == "HIGH"]
            parts: list[str] = []
            if crit_items:
                parts.append("CRITICAL: " + "; ".join(crit_items))
            if high_items:
                parts.append("HIGH: " + "; ".join(high_items))
            total_lost = sum(d["points_lost"] for d in deductions)
            why = f"-{total_lost} pts — {' | '.join(parts)}" if parts else f"-{total_lost} pts"

        results.append({
            "url":        page.get("url", ""),
            "page_score": score,
            "grade":      grade,
            "deductions": deductions,
            "why":        why,
        })

    return results


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
    checks: dict[str, dict] = {}

    # 1. Sitemap vs indexability
    checks["sitemap_indexability"] = _check_sitemap_indexability(pages, sitemap_urls)

    # 2. Canonical vs noindex
    checks["canonical_noindex"] = _check_canonical_noindex(real)

    # 3. Hreflang reciprocity — prefer pre-computed cross issues; fall back to direct check
    hreflang_issues = [i for i in cross_issues if i.get("type") == "hreflang_missing_reciprocal"]
    pages_with_hreflang = [p for p in pages if p.get("hreflang_tags")]
    if not hreflang_issues and pages_with_hreflang:
        hreflang_issues = _check_hreflang_reciprocity_direct(pages)
    checks["hreflang_reciprocity"] = {
        "checked":    len(pages_with_hreflang),
        "violations": len(hreflang_issues),
        "details":    hreflang_issues[:20],
        "status":     "ok" if not hreflang_issues else "issues_found",
        "impact":     "Missing reciprocal hreflang links break international URL sets — Googlebot may ignore the entire set",
        "fix":        "Every page A declaring hreflang pointing to B must have B declare hreflang pointing back to A",
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


def _check_hreflang_reciprocity_direct(pages: list[dict]) -> list[dict]:
    """
    Direct hreflang reciprocity check used when cross_issues is unavailable.
    For every (page_url → target_url, lang) pair, verify target_url declares
    a reciprocal hreflang back to page_url.
    """
    url_to_hreflang: dict[str, list[dict]] = {}
    for p in pages:
        url = (p.get("url") or "").rstrip("/")
        tags = p.get("hreflang_tags") or []
        if url and tags:
            url_to_hreflang[url] = tags

    violations: list[dict] = []
    for src_url, tags in url_to_hreflang.items():
        for tag in tags:
            tgt = (tag.get("href") or "").rstrip("/")
            lang = tag.get("lang", "")
            if not tgt or lang == "x-default":
                continue
            tgt_tags = url_to_hreflang.get(tgt, [])
            reciprocal_hrefs = {(t.get("href") or "").rstrip("/") for t in tgt_tags}
            if src_url not in reciprocal_hrefs:
                violations.append({
                    "type":    "hreflang_missing_reciprocal",
                    "source":  src_url,
                    "target":  tgt,
                    "lang":    lang,
                    "reason":  f"{tgt} does not declare a hreflang back to {src_url}",
                })
    return violations


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


def resolve_canonical_chain(
    start_url: str,
    all_pages: dict,
    max_hops: int = 5,
) -> tuple[list[str], str]:
    """
    Walk the canonical chain from start_url and return (chain, status).

    Status values:
      'ok'             — canonical resolves to itself or a single valid target
      'self'           — canonical points back to start_url (self-referencing, OK)
      'loop'           — circular canonical reference detected
      'broken_target'  — canonical points to a URL not in the crawled set
      'too_deep'       — chain exceeds max_hops (likely a misconfiguration)
    """
    chain = [start_url]
    current = start_url

    for _ in range(max_hops):
        page = all_pages.get(current)
        if not page:
            return chain, "broken_target"

        canonical = (page.get("canonical") or "").strip().rstrip("/")
        current_norm = current.rstrip("/")

        if not canonical or canonical == current_norm:
            status = "self" if (canonical == start_url.rstrip("/") and len(chain) == 1) else "ok"
            return chain, status

        if canonical in [c.rstrip("/") for c in chain]:
            return chain + [canonical], "loop"

        chain.append(canonical)
        current = canonical

    return chain, "too_deep"


def _annotate_canonical_chains(real_pages: list[dict]) -> None:
    """
    Walk canonical chains for every page in the crawled set.
    Attaches canonical_status and canonical_chain fields in-place.
    Called during audit so frontend can display chain issues.
    """
    pages_by_url = {(p.get("url") or "").rstrip("/"): p for p in real_pages}
    for page in real_pages:
        url = (page.get("url") or "").rstrip("/")
        chain, status = resolve_canonical_chain(url, pages_by_url)
        page["canonical_status"] = status
        page["canonical_chain"] = chain if status not in ("ok", "self") else []


def _check_canonical_noindex(real_pages: list[dict]) -> dict:
    """
    Flag pages where canonical points elsewhere AND noindex is set —
    contradictory signals that confuse Googlebot.
    """
    conflicts: list[dict] = []
    for page in real_pages:
        url    = (page.get("url") or "").rstrip("/")
        canon  = (page.get("canonical") or "").rstrip("/")
        noindex = (
            page.get("robots_noindex")
            or "noindex" in (page.get("robots_meta") or "").lower()
            or "noindex" in (page.get("x_robots_tag") or "").lower()
        )

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

    # ── Referrer-Policy ───────────────────────────────────────────────────────
    rp_pages = [p for p in real_pages if "referrer-policy" in (p.get("response_headers") or {})]
    rp_pct   = round(len(rp_pages) / n * 100, 1)
    if rp_pct < 80:
        issues.append({
            "type":     "referrer_policy_missing",
            "severity": "high",
            "coverage": rp_pct,
            "detail":   f"Referrer-Policy header present on only {rp_pct}% of pages",
            "reason":   "Without Referrer-Policy, browsers send full URL in Referer header on cross-origin requests — leaks internal URL structure and query params",
            "fix":      "Add 'Referrer-Policy: strict-origin-when-cross-origin' to all responses",
        })

    # ── Permissions-Policy ────────────────────────────────────────────────────
    pp_pages = [p for p in real_pages if "permissions-policy" in (p.get("response_headers") or {})]
    pp_pct   = round(len(pp_pages) / n * 100, 1)
    if pp_pct < 80:
        issues.append({
            "type":     "permissions_policy_missing",
            "severity": "high",
            "coverage": pp_pct,
            "detail":   f"Permissions-Policy header present on only {pp_pct}% of pages",
            "reason":   "Without Permissions-Policy, malicious iframes or injected scripts can access camera, microphone, and geolocation APIs",
            "fix":      "Add 'Permissions-Policy: geolocation=(), microphone=(), camera=()' as a restrictive baseline",
        })

    # ── TLS version (where detected) ─────────────────────────────────────────
    import re as _re
    weak_tls_pages = []
    for p in real_pages:
        _tls = (p.get("tls_version") or "").strip()
        if _tls:
            _m = _re.search(r"(\d+\.\d+)", _tls)
            if _m and float(_m.group(1)) < 1.2:
                weak_tls_pages.append(p)
    if weak_tls_pages:
        _ver_sample = (weak_tls_pages[0].get("tls_version") or "unknown")
        issues.append({
            "type":     "weak_tls",
            "severity": "critical",
            "count":    len(weak_tls_pages),
            "version":  _ver_sample,
            "detail":   f"{len(weak_tls_pages)} page(s) served over {_ver_sample} (TLS < 1.2)",
            "reason":   "TLS 1.0 and 1.1 are deprecated by RFC 8996 — all major browsers block these connections; PCI-DSS 3.2+ requires TLS 1.2+",
            "fix":      "Upgrade server TLS configuration to TLS 1.2 minimum; enable TLS 1.3 for forward secrecy",
            "affected": [p.get("url") for p in weak_tls_pages[:10]],
        })

    # Coverage summary
    header_coverage = {
        "hsts_pct":             hsts_pct,
        "csp_pct":              csp_pct,
        "xfo_pct":              xfo_pct,
        "xcto_pct":             xcto_pct,
        "referrer_policy_pct":  rp_pct,
        "permissions_policy_pct": pp_pct,
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

    # ── Resource request count (LCP budget) ───────────────────────────────────
    high_request_pages = [
        p for p in real_pages
        if (p.get("resource_count") or 0) > 100
    ]
    if high_request_pages:
        avg_rc = round(sum(p.get("resource_count", 0) for p in high_request_pages) / len(high_request_pages))
        issues.append({
            "type":     "excessive_resource_requests",
            "severity": "high",
            "count":    len(high_request_pages),
            "avg_resources": avg_rc,
            "detail":   f"{len(high_request_pages)} page(s) have >100 resource references (avg: {avg_rc})",
            "reason":   "Each resource is an HTTP round-trip; >100 requests significantly inflates LCP and Time to Interactive",
            "fix":      "Consolidate scripts/CSS, lazy-load below-fold media, use HTTP/2 push or prefetch hints",
            "affected": [p.get("url") for p in high_request_pages[:5]],
        })

    # ── HTML page size (>2MB is abnormal) ─────────────────────────────────────
    oversized_pages = [
        p for p in real_pages
        if (p.get("html_size_kb") or 0) > 2048
    ]
    if oversized_pages:
        max_size = max(p.get("html_size_kb", 0) for p in oversized_pages)
        issues.append({
            "type":     "oversized_html_response",
            "severity": "high",
            "count":    len(oversized_pages),
            "max_kb":   max_size,
            "detail":   f"{len(oversized_pages)} page(s) have HTML response >2MB (largest: {max_size:.0f}KB)",
            "reason":   "Oversized HTML delays HTML parse start, increasing TTFB and FCP; common cause: inline base64 assets or server-side rendered data",
            "fix":      "Remove inline SVG/base64, move large data out of HTML into async fetches, enable gzip/Brotli compression",
            "affected": [p.get("url") for p in oversized_pages[:5]],
        })

    # ── Legacy image formats (not WebP / AVIF) ────────────────────────────────
    legacy_img_pages = [
        p for p in real_pages
        if (p.get("img_non_modern_count") or 0) > 0
    ]
    if legacy_img_pages:
        total_legacy = sum(p.get("img_non_modern_count", 0) for p in legacy_img_pages)
        issues.append({
            "type":     "legacy_image_formats",
            "severity": "medium",
            "count":    len(legacy_img_pages),
            "total_images": total_legacy,
            "detail":   f"{total_legacy} image(s) on {len(legacy_img_pages)} page(s) served in legacy format (JPEG/PNG/GIF)",
            "reason":   "WebP is 25–34% smaller than JPEG at equal quality; AVIF is 50% smaller — converting reduces image payload and improves LCP",
            "fix":      "Convert images to WebP (libvips, squoosh, sharp); serve AVIF with WebP fallback via <picture> element",
            "affected": [p.get("url") for p in legacy_img_pages[:5]],
        })

    # ── Mobile viewport coverage ──────────────────────────────────────────────
    no_viewport_pages = [p for p in real_pages if not (p.get("viewport") or "").strip()]
    if no_viewport_pages:
        issues.append({
            "type":     "missing_viewport",
            "severity": "high",
            "count":    len(no_viewport_pages),
            "detail":   f"{len(no_viewport_pages)} page(s) missing mobile viewport meta tag",
            "reason":   "Google uses mobile-first indexing — pages without viewport tag render at desktop width on mobile, suppressing mobile rankings",
            "fix":      "Add <meta name='viewport' content='width=device-width, initial-scale=1'> to every page <head>",
            "affected": [p.get("url") for p in no_viewport_pages[:5]],
        })

    timed_pages = [p for p in real_pages if (p.get("response_time_ms") or 0) > 0]
    avg_rt = (
        round(sum(p["response_time_ms"] for p in timed_pages) / len(timed_pages))
        if timed_pages else 0
    )
    return {
        "data_source":          data_source,
        "cwv_results":          cwv_results,
        "issue_count":          len(issues),
        "issues":               issues,
        "slow_pages_count":     len(slow_pages),
        "avg_response_time_ms": avg_rt,
        "timed_pages":          len(timed_pages),
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
            if _CWV_STATUS_RANK.get(status, 0) < _CWV_STATUS_RANK["needs_improvement"]:
                status = "needs_improvement"
            page_issues.append(f"CLS {cls:.3f} > {_CWV_THRESHOLDS['cls']['good']} threshold")

        if inp is not None and inp > _CWV_THRESHOLDS["inp"]["poor"]:
            status = "poor"
            page_issues.append(f"INP {int(inp*1000)}ms > {int(_CWV_THRESHOLDS['inp']['poor']*1000)}ms (poor)")
        elif inp is not None and inp > _CWV_THRESHOLDS["inp"]["good"]:
            if _CWV_STATUS_RANK.get(status, 0) < _CWV_STATUS_RANK["needs_improvement"]:
                status = "needs_improvement"
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
    Compute the final site score (0-100) from cluster scores.

    Rules applied in order:
      1. site_score = weighted average of all cluster scores
         (cluster scores are already capped at 85 if a CRITICAL signal is missing)
      2. If any CRITICAL issue exists anywhere → cap site_score at 90
      3. Returns (site_score, cluster_scores_dict, cap_reason_or_None)
    """
    cluster_scores: dict[str, int] = {}
    weighted_sum = 0

    for cluster_id, cluster in cluster_validation.items():
        weight = cluster["weight"]
        score  = cluster["score"]   # already ≤85 if critical signal missing
        weighted_sum += score * weight
        cluster_scores[cluster_id] = score

    site_score = round(weighted_sum / 100)

    # ── Collect every CRITICAL issue from all audit layers ──────────────────
    critical_sources: list[str] = []

    for cluster in cluster_validation.values():
        for iss in cluster.get("issues", []):
            if iss.get("severity", "").upper() == "CRITICAL":
                critical_sources.append(
                    f"[{cluster['label']}] {iss['detail'][:70]}"
                )
        # Also flag clusters whose CRITICAL signal coverage is below threshold
        if cluster.get("critical_signal_missing"):
            for ms in cluster.get("missing_signals", []):
                if ms.get("severity") == "CRITICAL":
                    critical_sources.append(
                        f"[{cluster['label']}] Signal '{ms['signal']}' at "
                        f"{ms['coverage_pct']}% coverage (need {ms['threshold_pct']}%)"
                    )

    for iss in security.get("issues", []):
        if iss.get("severity", "").upper() == "CRITICAL":
            critical_sources.append(f"[Security] {iss['detail'][:70]}")

    for iss in performance.get("issues", []):
        if iss.get("severity", "").upper() == "CRITICAL":
            critical_sources.append(f"[Performance] {iss['detail'][:70]}")

    # ── Apply caps and build explanation ────────────────────────────────────
    cap_reason: str | None = None

    if critical_sources and site_score > 90:
        site_score = 90
        cap_reason = (
            f"Score capped at 90 — {len(critical_sources)} CRITICAL issue(s) present. "
            f"WHY: {' | '.join(critical_sources[:4])}"
        )
    elif critical_sources:
        # Score already ≤90 but still explain WHY CRITICAL issues exist
        cap_reason = (
            f"{len(critical_sources)} CRITICAL issue(s) are blocking a top score. "
            f"WHY: {' | '.join(critical_sources[:4])}"
        )

    # Signal-gap cap context (cluster-level ≤85 caps already applied; surface which ones)
    capped_clusters = [
        f"{c['label']} (capped at 85 — missing: "
        f"{', '.join(ms['signal'] for ms in c['missing_signals'] if ms['severity'] == 'CRITICAL')})"
        for c in cluster_validation.values()
        if c.get("capped_at_85")
    ]
    if capped_clusters:
        gap_note = "Clusters capped at 85 due to missing CRITICAL signals: " + "; ".join(capped_clusters)
        cap_reason = f"{cap_reason} | {gap_note}" if cap_reason else gap_note

    return max(0, min(100, site_score)), cluster_scores, cap_reason


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
    page_scores: list[dict] | None = None,
) -> dict:
    n = len(real_pages) or 1

    # Severity buckets — collect issues from every layer, normalise to lowercase
    severity_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    def _tally(issue_list: list[dict]) -> None:
        for issue in issue_list:
            sev = issue.get("severity", "medium").lower()
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

    # Page-score distribution
    ps = page_scores or []
    grade_dist: dict[str, int] = {"A+": 0, "A": 0, "B": 0, "Needs Fix": 0}
    for p in ps:
        g = p.get("grade", "Needs Fix")
        grade_dist[g] = grade_dist.get(g, 0) + 1
    avg_page_score = round(sum(p["page_score"] for p in ps) / len(ps)) if ps else 0

    # Cluster scores snapshot
    cluster_snapshot = {
        cid: {
            "score":          c["score"],
            "grade":          _grade(c["score"]),
            "capped_at_85":   c.get("capped_at_85", False),
            "has_gaps":       c["has_gaps"],
            "deductions":     c.get("deduction_reasons", []),
        }
        for cid, c in cluster_validation.items()
    }

    return {
        # ── Scale ─────────────────────────────────────────────────────────────
        "pages_crawled":     len(pages),
        "pages_analysed":    len(real_pages),
        "error_pages":       len(pages) - len(real_pages),
        # ── Three score granularities ─────────────────────────────────────────
        "site_score":        final_score,
        "site_grade":        _grade(final_score),
        "cluster_score":     cluster_snapshot,
        "avg_page_score":    avg_page_score,
        "page_grade_distribution": grade_dist,
        # ── Issues overview ───────────────────────────────────────────────────
        "severity_counts":   severity_counts,
        "total_issues":      sum(severity_counts.values()),
        "cross_page_issues": {
            "total":             len(cross_issues),
            "broken_links":      xp_type_counts.get("broken_internal_link", 0),
            "canonical_loops":   xp_type_counts.get("canonical_loop", 0),
            "canonical_chains":  xp_type_counts.get("canonical_chain", 0),
            "orphan_pages":      xp_type_counts.get("orphan_page", 0),
            "duplicate_content": xp_type_counts.get("duplicate_content", 0),
            "hreflang_broken":   xp_type_counts.get("hreflang_missing_reciprocal", 0),
        },
        "indexability": {
            "indexable":         idx.get("indexable", 0) + idx.get("likely_indexable", 0),
            "blocked":           idx.get("not_indexable_noindex", 0) + idx.get("not_indexable_error", 0),
            "canonical_mismatch":idx.get("canonical_mismatch", 0),
            "redirect":          idx.get("not_indexable_redirect", 0),
        },
        "clusters_with_gaps":sum(1 for c in cluster_validation.values() if c["has_gaps"]),
        # backward-compat aliases
        "final_score":       final_score,
        "final_grade":       _grade(final_score),
        "security_score":    max(0, 100 - len(security.get("issues", [])) * 10),
        "performance_issues":len(performance.get("issues", [])),
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
            if iss.get("type") == "missing_signal":
                continue  # signal gaps are routed through gap_report below
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

    canon_noindex = consistency.get("checks", {}).get("canonical_noindex", {}).get("conflicts", 0)
    if canon_noindex:
        _add(1, f"{canon_noindex} pages with contradictory noindex + foreign canonical",
             "Contradictory signals can cause the canonical target to also be excluded from indexing",
             "Choose one directive per page: either noindex (exclude) or self-canonical (include)",
             canon_noindex)

    # ── High (P2) ─────────────────────────────────────────────────────────────
    for iss in security.get("issues", []):
        if iss.get("severity") == "high":
            _add(2, iss["detail"], iss["reason"], iss["fix"], iss.get("count"))

    for cluster in cluster_validation.values():
        for iss in cluster.get("issues", []):
            if iss.get("type") == "missing_signal":
                continue
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
    hreflang_violations = consistency.get("checks", {}).get("hreflang_reciprocity", {}).get("violations", 0)
    if hreflang_violations:
        _add(3, f"{hreflang_violations} hreflang reciprocity violations",
             "Missing reciprocal hreflang links break international URL sets — Googlebot may ignore the entire set",
             "For every page A→B hreflang, ensure B declares hreflang pointing back to A",
             hreflang_violations)

    for iss in security.get("issues", []):
        if iss.get("severity") == "medium":
            _add(3, iss["detail"], iss["reason"], iss["fix"])

    for cluster in cluster_validation.values():
        for iss in cluster.get("issues", []):
            if iss.get("type") == "missing_signal":
                continue
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

    # ── Signal coverage gaps — routed to the correct priority bucket ──────────
    _gap_priority = {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 4}
    for gap in gap_report.get("gaps", []):
        p = _gap_priority.get(gap.get("severity", "info"), 4)
        _add(
            p,
            f"Coverage gap: {gap['signal']} ({gap['coverage_pct']}% covered, need {gap['threshold_pct']}%)",
            gap["impact"],
            gap["minimal_fix"],
        )

    chains = [i for i in cross_issues if i.get("type") == "canonical_chain"]
    if chains:
        _add(4, f"{len(chains)} canonical chains (2+ hops)",
             "Google does not reliably follow multi-hop canonicals",
             "Flatten chains so A → B directly (not A → C → B)", len(chains))

    # Dedup: within each bucket, drop actions whose first 38 chars match a seen entry.
    # 38 chars captures "count + metric subject" before minor wording diverges between
    # cluster issues and dedicated-audit issues (e.g. response-time, image dimensions).
    deduped: dict[int, list[dict]] = {}
    for p, actions in buckets.items():
        seen: set[str] = set()
        unique: list[dict] = []
        for a in actions:
            key = a["issue"][:38].lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(a)
        deduped[p] = unique

    return [
        {
            "priority": p,
            "label":    {1: "Critical", 2: "High", 3: "Medium", 4: "Low"}[p],
            "count":    len(actions),
            "actions":  actions,
        }
        for p, actions in deduped.items()
        if actions
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _grade(score: int) -> str:
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B"
    return "Needs Fix"


def _empty_result(reason: str) -> dict:
    return {
        "page_score":             [],
        "cluster_score":          {},
        "site_score":             0,
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
        "final_grade":            "Needs Fix",
        "errors":                 [reason],
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
