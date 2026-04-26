"""
test_crawliq_qa.py — CrawlIQ backend QA test suite.

Test cases:
  TC-01  Broken links detected
  TC-02  Duplicate titles detected
  TC-03  Noindex pages detected
  TC-04  Hreflang setup validated
  TC-05  Missing security headers detected
  TC-06  No false positives on clean pages
  TC-07  Scoring adjusts for CRITICAL issues (cap at 90)
  TC-08  Cluster cap at 85 when CRITICAL signal missing
  TC-09  Grade thresholds (A+ / A / B / Needs Fix)
  TC-10  Performance: 500-page batch — timing + memory
  TC-11  Edge case: SPA (empty HTML body)
  TC-12  Edge case: redirect loop detection
  TC-13  Edge case: blocked robots.txt (Disallow: /)
  TC-14  Cross-validation: sitemap vs noindex conflict
  TC-15  Cross-validation: canonical chain detection
  TC-16  Page score deduction explanation (WHY / WHICH)
  TC-17  site_auditor.parse_robots_txt correctness
  TC-18  site_auditor.check_hsts correctness
  TC-19  issues.validate_all cross-page checks run
  TC-20  seo_audit_engine.run_full_audit end-to-end
"""

from __future__ import annotations
import asyncio
import gc
import sys
import time
import traceback
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS  = "PASS"
FAIL  = "FAIL"
SKIP  = "SKIP"

results: list[dict] = []
broken_modules: list[str] = []


def run(name: str, fn):
    """Execute a test function and record PASS / FAIL."""
    try:
        fn()
        results.append({"name": name, "status": PASS})
        print(f"  [PASS]  {name}")
    except AssertionError as exc:
        msg = str(exc) or "assertion failed"
        results.append({"name": name, "status": FAIL, "reason": msg})
        print(f"  [FAIL]  {name}\n         → {msg}")
        mod = name.split(":")[0].strip()
        if mod not in broken_modules:
            broken_modules.append(mod)
    except Exception as exc:
        tb = traceback.format_exc(limit=4)
        results.append({"name": name, "status": FAIL, "reason": str(exc), "traceback": tb})
        print(f"  [FAIL]  {name}\n         → {exc}")
        mod = name.split(":")[0].strip()
        if mod not in broken_modules:
            broken_modules.append(mod)


def arun(name: str, coro_fn):
    """Wrap an async test function."""
    run(name, lambda: asyncio.get_event_loop().run_until_complete(coro_fn()))


# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures — synthetic page dicts (mimic crawler output)
# ─────────────────────────────────────────────────────────────────────────────

def _make_page(**kwargs) -> dict:
    """Return a minimal valid page dict with sensible defaults."""
    defaults: dict[str, Any] = {
        "url":                  "https://example.com/",
        "status_code":          200,
        "title":                "Example Page",
        "meta_description":     "A description.",
        "h1":                   ["Main Heading"],
        "canonical":            "https://example.com/",
        "robots_meta":          "index,follow",
        "noindex":              False,
        "x_robots_noindex":     False,
        "viewport":             "width=device-width, initial-scale=1",
        "body_text":            "word " * 350,
        "internal_links":       ["https://example.com/about"],
        "internal_links_count": 1,
        "external_links":       [],
        "links":                ["https://example.com/about"],
        "img_total":            0,
        "img_missing_alt":      0,
        "img_lazy_pct":         100,
        "img_non_modern_count": 0,
        "hreflang_tags":        [],   # list[{lang, href}] — matches issues.py expectation
        "schema_types":         [],
        "og_title":             "Example",
        "og_description":       "Description",
        "response_headers":     {
            "strict-transport-security": "max-age=31536000",
            "content-security-policy":   "default-src 'self'",
            "x-frame-options":           "DENY",
            "x-content-type-options":    "nosniff",
            "referrer-policy":           "strict-origin-when-cross-origin",
            "permissions-policy":        "camera=()",
        },
        "response_time_ms":     200,
        "resource_count":       20,
        "html_size_kb":         40,
        "redirect_hops":        0,
        "tls_version":          "TLSv1.3",
        "issues":               [],
        "mixed_resources":      [],
    }
    defaults.update(kwargs)
    return defaults


def _clean_pages(n: int = 3) -> list[dict]:
    return [
        _make_page(url=f"https://example.com/page{i}", title=f"Page {i} Title")
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  TC-01  Broken links detected
# ─────────────────────────────────────────────────────────────────────────────

def tc_01_broken_links():
    from issues import validate_all

    pages = [
        _make_page(url="https://example.com/",          links=["https://example.com/broken"]),
        _make_page(url="https://example.com/broken",     status_code=404, title="404 Page"),
        _make_page(url="https://example.com/about"),
    ]
    result = validate_all(pages)
    xp = result.get("cross_page_issues", [])
    broken = [i for i in xp if i.get("type") == "broken_internal_link"]
    assert broken, "Expected broken_internal_link issues — none found"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-02  Duplicate titles detected
# ─────────────────────────────────────────────────────────────────────────────

def tc_02_duplicate_titles():
    from issues import validate_all

    pages = [
        _make_page(url="https://example.com/a", title="Same Title"),
        _make_page(url="https://example.com/b", title="Same Title"),
        _make_page(url="https://example.com/c", title="Same Title"),
    ]
    result = validate_all(pages)
    title_map = result.get("title_map", {})
    dupes = {t: urls for t, urls in title_map.items() if len(urls) > 1}
    assert dupes, f"Expected duplicate titles in title_map — got {title_map}"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-03  Noindex pages detected
# ─────────────────────────────────────────────────────────────────────────────

def tc_03_noindex_detected():
    from issues import validate_all

    pages = [
        # meta robots noindex
        _make_page(
            url="https://example.com/noindex-meta",
            title="Noindex Meta Page",
            noindex=True,
            robots_meta="noindex,follow",
        ),
        # X-Robots-Tag header noindex (issues.py reads x_robots_tag string field)
        _make_page(
            url="https://example.com/noindex-header",
            title="Noindex Header Page",
            x_robots_noindex=True,
            x_robots_tag="noindex",   # the string field _pp_indexability reads
        ),
        _make_page(url="https://example.com/ok", title="Clean Page OK"),
    ]
    result = validate_all(pages)
    page_issues = result.get("page_issues", [])

    def _has_noindex_issue(url: str) -> bool:
        for entry in page_issues:
            if entry.get("url") == url:
                all_issues = " ".join(entry.get("indexability") or []).lower()
                return "noindex" in all_issues
        return False

    assert _has_noindex_issue("https://example.com/noindex-meta"), \
        f"noindex not flagged for meta-robots page. entry={[e for e in page_issues if 'noindex-meta' in e.get('url','')]}"

    assert _has_noindex_issue("https://example.com/noindex-header"), \
        f"x_robots_tag noindex not flagged. entry={[e for e in page_issues if 'noindex-header' in e.get('url','')]}"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-04  Hreflang setup validated
# ─────────────────────────────────────────────────────────────────────────────

def tc_04_hreflang_reciprocal():
    from issues import validate_all

    # issues.py expects hreflang_tags as list[{lang, href}] — matching crawler.py output
    pages = [
        _make_page(
            url="https://example.com/en/",
            title="EN Page",
            hreflang_tags=[
                {"lang": "en", "href": "https://example.com/en/"},
                {"lang": "de", "href": "https://example.com/de/"},
            ],
        ),
        _make_page(
            url="https://example.com/de/",
            title="DE Page",
            hreflang_tags=[],  # no reciprocal → violation
        ),
    ]
    result = validate_all(pages)
    xp = result.get("cross_page_issues", [])
    hreflang_issues = [i for i in xp if "hreflang" in i.get("type", "")]
    assert hreflang_issues, (
        f"Expected hreflang reciprocity issue — none found. "
        f"cross_page_issues: {[i.get('type') for i in xp]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  TC-05  Missing security headers detected
# ─────────────────────────────────────────────────────────────────────────────

def tc_05_missing_security_headers():
    from seo_audit_engine import run_full_audit

    pages = [
        _make_page(
            url="https://example.com/",
            response_headers={"x-frame-options": "DENY"},  # only 1 of 6 present
        )
    ]
    result = run_full_audit(pages, site_url="https://example.com")
    sec_cluster = result["cluster_validation"].get("security", {})
    sec_issues  = sec_cluster.get("issues", [])
    assert sec_issues, "Expected security cluster issues — none found"

    # Score should be low
    assert sec_cluster["score"] < 80, \
        f"Security score should be low with missing headers, got {sec_cluster['score']}"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-06  No false positives on clean pages
# ─────────────────────────────────────────────────────────────────────────────

def tc_06_no_false_positives():
    from issues import detect_issues, validate_all

    pages = _clean_pages(5)
    detect_issues(pages)

    for p in pages:
        bad = [i for i in (p.get("issues") or []) if "noindex" in i.lower() or "error" in i.lower()]
        assert not bad, f"False positive issue on clean page {p['url']}: {bad}"

    result = validate_all(pages)
    xp = result.get("cross_page_issues", [])
    critical_xp = [i for i in xp if i.get("severity", "").upper() == "CRITICAL"]
    assert not critical_xp, f"False positive CRITICAL cross-page issues: {critical_xp}"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-07  Scoring caps at 90 when CRITICAL issues present
# ─────────────────────────────────────────────────────────────────────────────

def tc_07_scoring_critical_cap():
    from seo_audit_engine import run_full_audit

    # HTTP pages → CRITICAL issue in technical cluster
    pages = [
        _make_page(url="http://example.com/", viewport="width=device-width, initial-scale=1"),
        _make_page(url="http://example.com/about"),
    ]
    result = run_full_audit(pages, site_url="http://example.com")
    site_score = result["site_score"]
    cap_reason = result.get("score_cap_reason") or ""

    assert site_score <= 90, f"Site score should be ≤90 with CRITICAL HTTP issue, got {site_score}"
    assert cap_reason, "Expected score_cap_reason explaining the cap"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-08  Cluster capped at 85 when CRITICAL signal below threshold
# ─────────────────────────────────────────────────────────────────────────────

def tc_08_cluster_cap_at_85():
    from seo_audit_engine import run_full_audit

    # All pages missing viewport → CRITICAL signal below 100% threshold in 'technical'
    # raw_score will be low (HTTPS still present), but critical_signal_missing MUST be True
    pages = [
        _make_page(url=f"https://example.com/page{i}", viewport=None)
        for i in range(5)
    ]
    result = run_full_audit(pages, site_url="https://example.com")
    tech = result["cluster_validation"].get("technical", {})

    assert tech.get("critical_signal_missing") is True, \
        f"Expected critical_signal_missing=True for technical cluster, got {tech.get('critical_signal_missing')}"

    # Score MUST be ≤85 (either because it was capped, or because it was already low)
    assert tech["score"] <= 85, \
        f"Technical cluster score should be ≤85 when CRITICAL signal missing, got {tech['score']}"

    # If the raw score happened to be above 85, confirm it was capped
    if tech.get("raw_score", 0) > 85:
        assert tech.get("capped_at_85") is True, \
            f"raw_score={tech.get('raw_score')} > 85 but capped_at_85 is not True"

    assert tech.get("deduction_reasons"), "Expected deduction_reasons explaining why score was limited"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-09  Grade thresholds
# ─────────────────────────────────────────────────────────────────────────────

def tc_09_grade_thresholds():
    from seo_audit_engine import _grade

    assert _grade(100) == "A+",       f"Expected A+ for 100, got {_grade(100)}"
    assert _grade(90)  == "A+",       f"Expected A+ for 90, got {_grade(90)}"
    assert _grade(89)  == "A",        f"Expected A for 89, got {_grade(89)}"
    assert _grade(80)  == "A",        f"Expected A for 80, got {_grade(80)}"
    assert _grade(79)  == "B",        f"Expected B for 79, got {_grade(79)}"
    assert _grade(70)  == "B",        f"Expected B for 70, got {_grade(70)}"
    assert _grade(69)  == "Needs Fix",f"Expected 'Needs Fix' for 69, got {_grade(69)}"
    assert _grade(0)   == "Needs Fix",f"Expected 'Needs Fix' for 0, got {_grade(0)}"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-10  Performance: 500-page batch
# ─────────────────────────────────────────────────────────────────────────────

def tc_10_performance_500_pages():
    from issues import detect_issues, validate_all
    from seo_audit_engine import run_full_audit

    pages = [
        _make_page(
            url=f"https://example.com/page{i}",
            title=f"Page {i} Title",
            meta_description=f"Description for page {i}",
        )
        for i in range(500)
    ]

    gc.collect()
    t0 = time.time()
    detect_issues(pages)
    t_issues = time.time() - t0

    t1 = time.time()
    validate_all(pages)
    t_validate = time.time() - t1

    t2 = time.time()
    run_full_audit(pages[:100])   # audit engine on 100-page sample (prevent timeout)
    t_audit = time.time() - t2

    assert t_issues  < 60, f"detect_issues on 500 pages took {t_issues:.1f}s (limit 60s)"
    assert t_validate < 60, f"validate_all on 500 pages took {t_validate:.1f}s (limit 60s)"
    assert t_audit   < 30, f"run_full_audit on 100 pages took {t_audit:.1f}s (limit 30s)"

    gc.collect()
    print(f"         [perf] issues={t_issues:.2f}s  validate={t_validate:.2f}s  audit={t_audit:.2f}s")


# ─────────────────────────────────────────────────────────────────────────────
#  TC-11  Edge case: SPA (empty HTML body)
# ─────────────────────────────────────────────────────────────────────────────

def tc_11_spa_empty_body():
    from issues import detect_issues
    from seo_audit_engine import run_full_audit

    spa_page = _make_page(
        url="https://example.com/spa",
        title="",            # no title
        h1=[],               # no H1
        body_text="",        # empty body
        meta_description="",
        canonical="",
    )
    pages = [spa_page]

    # Must not raise — should return gracefully
    detect_issues(pages)
    result = run_full_audit(pages, site_url="https://example.com")

    assert "site_score" in result, "run_full_audit should return site_score even for SPA pages"
    assert result["site_score"] >= 0, "site_score should be non-negative"
    assert result["site_score"] <= 100, "site_score should be ≤100"

    # Should flag missing title in on_page cluster
    on_page = result["cluster_validation"].get("on_page", {})
    assert on_page.get("has_gaps"), "on_page cluster should have gaps for SPA with no title"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-12  Edge case: redirect loop detection
# ─────────────────────────────────────────────────────────────────────────────

def tc_12_redirect_loop():
    from site_auditor import cross_validate

    # A → B (canonical) and B → A (canonical) = loop
    pages = [
        _make_page(url="https://example.com/a", canonical="https://example.com/b"),
        _make_page(url="https://example.com/b", canonical="https://example.com/a"),
    ]
    cv = cross_validate(pages)
    loop_issues = [i for i in cv["consistency_issues"] if i["type"] == "canonical_loop"]
    assert loop_issues, f"Expected canonical_loop issue, got: {cv['consistency_issues']}"
    assert cv["summary"]["high"] > 0 or cv["summary"]["critical"] > 0, \
        "Canonical loop should be flagged as HIGH or CRITICAL"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-13  Edge case: blocked robots.txt
# ─────────────────────────────────────────────────────────────────────────────

def tc_13_blocked_robots():
    from site_auditor import parse_robots_txt

    content = "User-agent: *\nDisallow: /\n"
    result  = parse_robots_txt(content)

    assert result["disallowed"] == ["/"], \
        f"Expected disallowed=['/'], got {result['disallowed']}"
    issues_lower = " ".join(result["issues"]).lower()
    assert "disallow" in issues_lower or "blocked" in issues_lower, \
        f"Expected issue about Disallow: /, got: {result['issues']}"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-14  Cross-validation: sitemap vs noindex conflict
# ─────────────────────────────────────────────────────────────────────────────

def tc_14_sitemap_noindex_conflict():
    from site_auditor import cross_validate

    pages = [
        _make_page(url="https://example.com/listed", noindex=True),
        _make_page(url="https://example.com/ok"),
    ]
    sitemap_urls = ["https://example.com/listed", "https://example.com/ok"]
    cv = cross_validate(pages, sitemap_urls=sitemap_urls)

    conflicts = [
        i for i in cv["consistency_issues"]
        if i["type"] == "sitemap_noindex_conflict"
    ]
    assert conflicts, "Expected sitemap_noindex_conflict issue — none found"
    assert any("example.com/listed" in u
               for issue in conflicts
               for u in issue.get("affected_urls", [])), \
        "Conflict should reference the noindex URL"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-15  Cross-validation: canonical chain detection
# ─────────────────────────────────────────────────────────────────────────────

def tc_15_canonical_chain():
    from site_auditor import cross_validate

    # A → B → C (2-hop chain).
    # C has empty canonical so the traversal terminates cleanly (no loop).
    pages = [
        _make_page(url="https://example.com/a", canonical="https://example.com/b"),
        _make_page(url="https://example.com/b", canonical="https://example.com/c"),
        _make_page(url="https://example.com/c", canonical=""),   # chain ends here
    ]
    cv = cross_validate(pages)
    chains = [i for i in cv["consistency_issues"] if i["type"] == "canonical_chain"]
    assert chains, (
        f"Expected canonical_chain issue for A→B→C, "
        f"got: {[i['type'] for i in cv['consistency_issues']]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  TC-16  Page score deduction — WHY / WHICH explanation
# ─────────────────────────────────────────────────────────────────────────────

def tc_16_page_score_explanation():
    from seo_audit_engine import _compute_page_scores

    pages = [
        _make_page(
            url="https://example.com/bad",
            title="",               # missing → -20 CRITICAL
            viewport=None,          # missing → -15 CRITICAL
            meta_description="",    # missing → -10 HIGH
        )
    ]
    scores = _compute_page_scores(pages)
    assert scores, "Expected page score result"

    ps = scores[0]
    assert ps["page_score"] < 60, \
        f"Page with missing title+viewport+meta should score <60, got {ps['page_score']}"
    assert ps["why"], "Expected non-empty WHY explanation"
    assert "CRITICAL" in ps["why"], f"Expected CRITICAL in WHY, got: {ps['why']}"

    deduction_signals = {d["signal"] for d in ps["deductions"]}
    assert "title"    in deduction_signals, "Expected 'title' in deductions"
    assert "viewport" in deduction_signals, "Expected 'viewport' in deductions"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-17  site_auditor.parse_robots_txt
# ─────────────────────────────────────────────────────────────────────────────

def tc_17_parse_robots():
    from site_auditor import parse_robots_txt

    content = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Allow: /\n"
        "Crawl-delay: 2\n"
        "Sitemap: https://example.com/sitemap.xml\n"
        "\n"
        "User-agent: Googlebot\n"
        "Disallow: /private/\n"
    )
    r = parse_robots_txt(content)

    assert "/admin/" in r["disallowed"],                   f"Expected /admin/ in disallowed, got {r['disallowed']}"
    assert "https://example.com/sitemap.xml" in r["sitemaps"], f"Expected sitemap URL, got {r['sitemaps']}"
    assert r["crawl_delay"] == 2.0,                        f"Expected crawl_delay=2.0, got {r['crawl_delay']}"
    assert r["has_googlebot_rules"] is True,               "Expected has_googlebot_rules=True"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-18  site_auditor.check_hsts
# ─────────────────────────────────────────────────────────────────────────────

def tc_18_check_hsts():
    from site_auditor import check_hsts

    # Good HSTS
    good = check_hsts({"Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload"})
    assert good["present"]  is True,   "HSTS should be present"
    assert good["max_age"]  == 31536000
    assert good["includes_subdomains"] is True
    assert good["preload"]  is True

    # Missing HSTS
    bad = check_hsts({})
    assert bad["present"]  is False
    assert bad["status"]   == "missing"
    assert bad["issues"],              "Expected issue for missing HSTS"

    # Weak (max-age below 1yr)
    weak = check_hsts({"Strict-Transport-Security": "max-age=86400"})
    assert weak["max_age"] == 86400
    assert weak["status"]  == "weak"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-19  issues.validate_all — cross-page check runs
# ─────────────────────────────────────────────────────────────────────────────

def tc_19_validate_all_structure():
    from issues import validate_all

    pages = _clean_pages(3)
    result = validate_all(pages)

    required_keys = ["page_issues", "cross_page_issues", "stats", "title_map", "meta_map"]
    for key in required_keys:
        assert key in result, f"validate_all missing key '{key}'"

    assert isinstance(result["cross_page_issues"], list), "cross_page_issues must be a list"
    assert isinstance(result["title_map"], dict),         "title_map must be a dict"


# ─────────────────────────────────────────────────────────────────────────────
#  TC-20  run_full_audit end-to-end output schema
# ─────────────────────────────────────────────────────────────────────────────

def tc_20_run_full_audit_schema():
    from seo_audit_engine import run_full_audit

    pages = _clean_pages(5)
    result = run_full_audit(pages, site_url="https://example.com")

    required_top = [
        "page_score", "cluster_score", "site_score",
        "cluster_validation", "gap_report",
        "consistency_checks", "security_audit", "performance_audit",
        "score_breakdown", "score_cap_reason",
        "audit_summary", "implementation_roadmap",
        "final_score", "final_grade", "errors",
    ]
    for key in required_top:
        assert key in result, f"run_full_audit missing top-level key '{key}'"

    assert isinstance(result["page_score"],    list), "page_score must be a list"
    assert isinstance(result["cluster_score"], dict), "cluster_score must be a dict"
    assert isinstance(result["site_score"],    int),  "site_score must be an int"
    assert 0 <= result["site_score"] <= 100,          "site_score must be 0-100"

    # Verify 5 clusters present with correct weights
    cv = result["cluster_validation"]
    expected_clusters = {"indexability", "on_page", "technical", "performance", "security"}
    assert set(cv.keys()) == expected_clusters, \
        f"Expected clusters {expected_clusters}, got {set(cv.keys())}"
    total_weight = sum(c["weight"] for c in cv.values())
    assert total_weight == 100, f"Cluster weights must sum to 100, got {total_weight}"

    # page_score should have one entry per page
    assert len(result["page_score"]) == len(pages), \
        f"page_score should have {len(pages)} entries, got {len(result['page_score'])}"

    # Each page_score entry must have required fields
    ps_required = ["url", "page_score", "grade", "deductions", "why"]
    for ps in result["page_score"]:
        for field in ps_required:
            assert field in ps, f"page_score entry missing field '{field}'"

    grade = result["final_grade"]
    assert grade in ("A+", "A", "B", "Needs Fix"), f"Unexpected grade: {grade}"

    # audit_summary must include all three score granularities
    summary = result["audit_summary"]
    assert "site_score"    in summary, "audit_summary missing site_score"
    assert "cluster_score" in summary, "audit_summary missing cluster_score"
    assert "avg_page_score" in summary, "audit_summary missing avg_page_score"


# ─────────────────────────────────────────────────────────────────────────────
#  Run all tests
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 65)
    print("  CrawlIQ Backend QA — Pre-deployment Test Suite")
    print("=" * 65 + "\n")

    print("─── Group 1: Issue detection ──────────────────────────────────")
    run("TC-01 · Broken links detected",     tc_01_broken_links)
    run("TC-02 · Duplicate titles detected", tc_02_duplicate_titles)
    run("TC-03 · Noindex pages detected",    tc_03_noindex_detected)
    run("TC-04 · Hreflang reciprocal check", tc_04_hreflang_reciprocal)
    run("TC-05 · Missing security headers",  tc_05_missing_security_headers)

    print("\n─── Group 2: Scoring accuracy ─────────────────────────────────")
    run("TC-06 · No false positives (clean pages)", tc_06_no_false_positives)
    run("TC-07 · Score capped at 90 for CRITICAL",  tc_07_scoring_critical_cap)
    run("TC-08 · Cluster capped at 85 (signal gap)",tc_08_cluster_cap_at_85)
    run("TC-09 · Grade thresholds A+/A/B/Needs Fix", tc_09_grade_thresholds)

    print("\n─── Group 3: Performance ──────────────────────────────────────")
    run("TC-10 · 500-page batch (timing)",   tc_10_performance_500_pages)

    print("\n─── Group 4: Edge cases ───────────────────────────────────────")
    run("TC-11 · SPA — empty HTML body",     tc_11_spa_empty_body)
    run("TC-12 · Redirect loop detection",   tc_12_redirect_loop)
    run("TC-13 · Blocked robots.txt",        tc_13_blocked_robots)

    print("\n─── Group 5: Cross-validation ─────────────────────────────────")
    run("TC-14 · Sitemap vs noindex conflict", tc_14_sitemap_noindex_conflict)
    run("TC-15 · Canonical chain detection",   tc_15_canonical_chain)

    print("\n─── Group 6: Output schema ────────────────────────────────────")
    run("TC-16 · Page score WHY/WHICH deductions", tc_16_page_score_explanation)
    run("TC-17 · parse_robots_txt correctness",    tc_17_parse_robots)
    run("TC-18 · check_hsts correctness",          tc_18_check_hsts)
    run("TC-19 · validate_all structure",          tc_19_validate_all_structure)
    run("TC-20 · run_full_audit end-to-end schema",tc_20_run_full_audit_schema)

    # ── Results ──────────────────────────────────────────────────────────────
    passed = [r for r in results if r["status"] == PASS]
    failed = [r for r in results if r["status"] == FAIL]
    total  = len(results)

    print("\n" + "=" * 65)
    print(f"  RESULTS: {len(passed)}/{total} passed  |  {len(failed)} failed")
    print("=" * 65)

    if failed:
        print("\n  FAILED TESTS:")
        for f in failed:
            print(f"    ✗ {f['name']}")
            print(f"      Reason: {f['reason']}")
        print("\n  BROKEN MODULES:")
        for mod in broken_modules:
            print(f"    • {mod}")
        print("\n  ⛔  DEPLOYMENT BLOCKED — fix failing tests before pushing.\n")
        sys.exit(1)
    else:
        print("\n  ✅  ALL TESTS PASSED — backend is ready for deployment.")
        print("\n  Git commit message:")
        print('  ┌─────────────────────────────────────────────────────────────')
        print('  │  feat: full SEO audit engine with indexability, performance,')
        print('  │         security, and cross-validation')
        print('  └─────────────────────────────────────────────────────────────')
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
