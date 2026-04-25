"""
link_graph.py — Internal link graph analysis
Provides:
  - Iterative PageRank simulation on internal links
  - Orphan page detection (0 incoming internal links)
  - Crawl depth distribution map
  - Site silo / directory cluster analysis
  - Internal link equity distribution
"""
from __future__ import annotations

from collections import defaultdict, deque
from urllib.parse import urlparse, urljoin, urldefrag

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(url: str) -> str:
    """Strip fragment, trailing slash-normalise, lowercase scheme+host."""
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}"


def _silo(url: str) -> str:
    """Return first path segment as the silo name, e.g. '/blog/post' → 'blog'."""
    parts = [s for s in urlparse(url).path.split("/") if s]
    return parts[0] if parts else "(root)"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_link_graph(pages: list[dict]) -> dict:
    """
    Build an internal link graph from crawl_results.

    Input keys used per page dict:
        url        (str)  — canonical page URL
        links      (list[str]) — all href values extracted by crawler

    Returns:
        {
          "nodes": {url: {"out_links": [...], "in_count": int, "depth": int, "silo": str}},
          "edges": [(src, dst)],        # internal edges only
          "root":  str | None
        }
    """
    if not pages:
        return {"nodes": {}, "edges": [], "root": None}

    # Collect all known URLs first
    known = {_normalise(p["url"]) for p in pages if p.get("url")}

    nodes: dict[str, dict] = {}
    edges: list[tuple[str, str]] = []

    for page in pages:
        raw_url = page.get("url", "")
        if not raw_url:
            continue
        src = _normalise(raw_url)
        out = []
        for href in page.get("links", []):
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            abs_href = urljoin(raw_url, href)
            dst = _normalise(abs_href)
            if dst in known and dst != src:
                out.append(dst)
                edges.append((src, dst))
        nodes.setdefault(src, {"out_links": [], "in_count": 0, "depth": -1, "silo": _silo(src)})
        nodes[src]["out_links"] = list(dict.fromkeys(out))  # dedup, preserve order

    # Count in-links
    for _, dst in edges:
        if dst in nodes:
            nodes[dst]["in_count"] += 1

    # Pick root = node with most out-links or first in list
    root = max(nodes, key=lambda u: len(nodes[u]["out_links"]), default=None)

    # BFS depth from root
    if root:
        visited: set[str] = set()
        q: deque[tuple[str, int]] = deque([(root, 0)])
        while q:
            cur, d = q.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            nodes[cur]["depth"] = d
            for nxt in nodes[cur]["out_links"]:
                if nxt not in visited:
                    q.append((nxt, d + 1))

    return {"nodes": nodes, "edges": edges, "root": root}


# ---------------------------------------------------------------------------
# PageRank
# ---------------------------------------------------------------------------

def compute_pagerank(
    graph: dict,
    damping: float = 0.85,
    iterations: int = 50,
    tol: float = 1e-6,
) -> dict[str, float]:
    """
    Iterative PageRank on the internal link graph.
    Returns {url: pagerank_score} normalised so scores sum to 1.
    """
    nodes = graph["nodes"]
    if not nodes:
        return {}

    n = len(nodes)
    urls = list(nodes.keys())
    idx = {u: i for i, u in enumerate(urls)}
    pr = [1.0 / n] * n
    out_count = [max(len(nodes[u]["out_links"]), 1) for u in urls]

    for _ in range(iterations):
        new_pr = [(1.0 - damping) / n] * n
        for u, i in idx.items():
            for dst in nodes[u]["out_links"]:
                if dst in idx:
                    j = idx[dst]
                    new_pr[j] += damping * pr[i] / out_count[i]
        # Dangling node mass redistribution
        dangling_sum = sum(pr[i] for i, u in enumerate(urls) if out_count[i] == 1 and not nodes[u]["out_links"])
        new_pr = [v + damping * dangling_sum / n for v in new_pr]

        diff = sum(abs(new_pr[i] - pr[i]) for i in range(n))
        pr = new_pr
        if diff < tol:
            break

    total = sum(pr) or 1.0
    return {urls[i]: round(pr[i] / total, 6) for i in range(n)}


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------

def detect_orphans(graph: dict) -> list[dict]:
    """
    Returns pages with 0 incoming internal links (excluding the root/homepage).
    Each entry: {url, silo, depth, out_links_count}
    """
    nodes = graph["nodes"]
    root = graph.get("root")
    orphans = []
    for url, data in nodes.items():
        if url == root:
            continue
        if data["in_count"] == 0:
            orphans.append({
                "url": url,
                "silo": data["silo"],
                "depth": data["depth"],
                "out_links_count": len(data["out_links"]),
            })
    orphans.sort(key=lambda x: x["url"])
    return orphans


# ---------------------------------------------------------------------------
# Crawl depth distribution
# ---------------------------------------------------------------------------

def depth_distribution(graph: dict) -> dict:
    """
    Returns {depth_level: [urls]} and summary stats.
    depth=-1 means unreachable from root.
    """
    nodes = graph["nodes"]
    by_depth: dict[int, list[str]] = defaultdict(list)
    for url, data in nodes.items():
        by_depth[data["depth"]].append(url)

    distribution = {}
    for d in sorted(by_depth):
        label = f"depth_{d}" if d >= 0 else "unreachable"
        distribution[label] = {
            "count": len(by_depth[d]),
            "urls": sorted(by_depth[d]),
        }

    max_depth = max((d for d in by_depth if d >= 0), default=0)
    reachable = sum(len(v) for k, v in by_depth.items() if k >= 0)
    unreachable = len(by_depth.get(-1, []))

    return {
        "distribution": distribution,
        "max_depth": max_depth,
        "reachable": reachable,
        "unreachable": unreachable,
        "total": len(nodes),
    }


# ---------------------------------------------------------------------------
# Silo analysis
# ---------------------------------------------------------------------------

def silo_analysis(graph: dict, pagerank: dict[str, float]) -> list[dict]:
    """
    Groups pages by first path segment (silo) and returns per-silo metrics.
    """
    nodes = graph["nodes"]
    silos: dict[str, dict] = {}

    for url, data in nodes.items():
        s = data["silo"]
        if s not in silos:
            silos[s] = {"pages": [], "total_pr": 0.0, "total_in": 0, "cross_links": 0}
        silos[s]["pages"].append(url)
        silos[s]["total_pr"] += pagerank.get(url, 0.0)
        silos[s]["total_in"] += data["in_count"]

    # Count cross-silo links
    for src, dst in graph["edges"]:
        if nodes.get(src, {}).get("silo") != nodes.get(dst, {}).get("silo"):
            src_silo = nodes.get(src, {}).get("silo", "")
            if src_silo in silos:
                silos[src_silo]["cross_links"] += 1

    result = []
    for name, data in sorted(silos.items(), key=lambda x: -x[1]["total_pr"]):
        result.append({
            "silo": name,
            "page_count": len(data["pages"]),
            "total_pagerank": round(data["total_pr"], 6),
            "avg_pagerank": round(data["total_pr"] / max(len(data["pages"]), 1), 6),
            "total_in_links": data["total_in"],
            "cross_silo_links": data["cross_links"],
        })
    return result


# ---------------------------------------------------------------------------
# Top pages by PageRank
# ---------------------------------------------------------------------------

def top_pages_by_pr(
    graph: dict,
    pagerank: dict[str, float],
    n: int = 20,
) -> list[dict]:
    nodes = graph["nodes"]
    ranked = sorted(pagerank.items(), key=lambda x: -x[1])[:n]
    return [
        {
            "url": url,
            "pagerank": pr,
            "in_links": nodes.get(url, {}).get("in_count", 0),
            "out_links": len(nodes.get(url, {}).get("out_links", [])),
            "depth": nodes.get(url, {}).get("depth", -1),
            "silo": nodes.get(url, {}).get("silo", ""),
        }
        for url, pr in ranked
    ]


# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------

def compute_priority_scores(
    graph: dict,
    pagerank: dict[str, float],
    issue_counts: dict[str, int],
) -> dict[str, float]:
    """
    Priority score per page: 0–1, higher = fix this page first.

    Combines three equal-weight signals:
      depth_score   = 1 – (depth / max_depth)   shallow pages score higher
      pagerank_score = min-max normalised PR      high-authority pages score higher
      issue_score   = issues / max_issues         more broken = more urgent

    Pages with low crawl depth + high PageRank + many issues surface at the top.
    Scales to 10 k+ pages (pure dict arithmetic, no matrix ops).
    """
    nodes = graph.get("nodes", {})
    if not nodes:
        return {}

    valid_depths = [d["depth"] for d in nodes.values() if d["depth"] >= 0]
    max_depth    = max(valid_depths) if valid_depths else 1

    pr_values = list(pagerank.values()) or [0.0]
    min_pr, max_pr = min(pr_values), max(pr_values)
    pr_range = max_pr - min_pr or 1.0

    max_issues = max(issue_counts.values()) if issue_counts else 1

    scores: dict[str, float] = {}
    for url, data in nodes.items():
        depth = data.get("depth", -1)
        depth_score = 1.0 - (depth / max_depth) if depth >= 0 else 0.0

        pr          = pagerank.get(url, 0.0)
        pr_score    = (pr - min_pr) / pr_range

        n_issues    = issue_counts.get(url, 0)
        issue_score = n_issues / max_issues if max_issues else 0.0

        scores[url] = round((depth_score + pr_score + issue_score) / 3.0, 4)

    return scores


def pages_needing_attention(
    graph: dict,
    pagerank: dict[str, float],
    priority_scores: dict[str, float],
    pages: list[dict],
    top_n: int = 20,
) -> list[dict]:
    """
    Return the top_n pages sorted by priority_score descending.
    Each entry includes URL, score, depth, PageRank, issue count, and a
    sample of the top 5 issues so the caller has immediate context.
    """
    nodes        = graph.get("nodes", {})
    issue_lookup = {p.get("url", ""): p.get("issues", []) for p in pages}

    ranked = []
    for url, score in priority_scores.items():
        data   = nodes.get(url, {})
        issues = issue_lookup.get(url, [])
        ranked.append({
            "url":            url,
            "priority_score": score,
            "crawl_depth":    data.get("depth", -1),
            "pagerank":       round(pagerank.get(url, 0.0), 6),
            "issue_count":    len(issues),
            "issues":         issues[:5],
            "in_links":       data.get("in_count", 0),
        })

    ranked.sort(key=lambda x: -x["priority_score"])
    return ranked[:top_n]


# ---------------------------------------------------------------------------
# Render-blocking resources (PageSpeed Insights response parser)
# ---------------------------------------------------------------------------

def extract_render_blocking_resources(psi_response: dict) -> dict:
    """
    Parse a Google PageSpeed Insights API JSON response and extract the list
    of render-blocking resources reported by Lighthouse.

    Expected path:
      lighthouseResult.audits["render-blocking-resources"].details.items[]

    Each item contains: url, totalBytes, wastedMs.

    Returns:
    {
      "count":           int,
      "total_wasted_ms": float,
      "resources":       [{url, bytes, wasted_ms}],   # sorted worst-first
      "issues":          [str],                        # human-readable labels
    }
    """
    resources: list[dict] = []
    try:
        items = (
            psi_response
            .get("lighthouseResult", {})
            .get("audits", {})
            .get("render-blocking-resources", {})
            .get("details", {})
            .get("items", [])
        )
        for item in items:
            resources.append({
                "url":       item.get("url", ""),
                "bytes":     int(item.get("totalBytes", 0)),
                "wasted_ms": round(float(item.get("wastedMs", 0)), 1),
            })
        resources.sort(key=lambda x: -x["wasted_ms"])
    except Exception:
        pass

    total_wasted = round(sum(r["wasted_ms"] for r in resources), 1)
    issues = [
        f"Render-blocking: {r['url'].split('/')[-1] or r['url']} "
        f"(~{r['wasted_ms']} ms wasted)"
        for r in resources[:5] if r["wasted_ms"] > 0
    ]

    return {
        "count":           len(resources),
        "total_wasted_ms": total_wasted,
        "resources":       resources[:10],
        "issues":          issues,
    }


# ---------------------------------------------------------------------------
# Site-wide image loading summary
# ---------------------------------------------------------------------------

def image_loading_summary(pages: list[dict]) -> dict:
    """
    Aggregate lazy-loading and srcset coverage across all crawled pages.
    Uses the img_total / img_lazy_count / img_srcset_count fields added by
    crawler._parse() — no re-fetching needed.
    """
    total_imgs   = sum(p.get("img_total", 0)        for p in pages)
    total_lazy   = sum(p.get("img_lazy_count", 0)   for p in pages)
    total_srcset = sum(p.get("img_srcset_count", 0) for p in pages)

    lazy_pct   = round(total_lazy   / total_imgs * 100, 1) if total_imgs else 0.0
    srcset_pct = round(total_srcset / total_imgs * 100, 1) if total_imgs else 0.0

    issues = []
    if total_imgs:
        if lazy_pct < 50:
            issues.append(
                f"Only {lazy_pct}% of images use loading=\"lazy\" — "
                "add it to below-fold images to reduce initial page weight"
            )
        if srcset_pct < 30:
            issues.append(
                f"Only {srcset_pct}% of images have srcset — "
                "add srcset for responsive image delivery across device sizes"
            )

    return {
        "total_images": total_imgs,
        "lazy_count":   total_lazy,
        "lazy_pct":     lazy_pct,
        "srcset_count": total_srcset,
        "srcset_pct":   srcset_pct,
        "issues":       issues,
    }


# ---------------------------------------------------------------------------
# Site-wide breadcrumb coverage
# ---------------------------------------------------------------------------

def breadcrumb_summary(pages: list[dict]) -> dict:
    """
    Report what fraction of crawled pages have breadcrumb markup and
    which detection method was used (JSON-LD preferred over HTML nav).
    """
    total     = len(pages)
    detected  = sum(1 for p in pages if p.get("breadcrumb_detected"))
    jsonld    = sum(1 for p in pages if p.get("breadcrumb_source") == "json_ld")
    html_nav  = sum(1 for p in pages if p.get("breadcrumb_source") == "html_nav")
    coverage  = round(detected / total * 100, 1) if total else 0.0

    issues = []
    if coverage < 80 and total > 3:
        issues.append(
            f"Only {coverage}% of pages have breadcrumb markup — "
            "add BreadcrumbList JSON-LD or nav[aria-label=breadcrumb] "
            "to improve SERP breadcrumb display"
        )

    return {
        "pages_with_breadcrumbs": detected,
        "coverage_pct":           coverage,
        "by_source": {
            "json_ld":  jsonld,
            "html_nav": html_nav,
            "none":     total - detected,
        },
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyse_link_graph(pages: list[dict]) -> dict:
    """
    Full link graph analysis pipeline.

    Returns:
    {
      "total_pages": int,
      "total_internal_links": int,
      "orphan_pages": [...],
      "orphan_count": int,
      "depth": {...},                      # depth_distribution result
      "silos": [...],                      # silo_analysis result
      "top_pages": [...],                  # top 20 by PageRank
      "pagerank": {url: score},            # full PR map (scores sum to 1)
      "priority_scores": {url: score},     # 0–1, higher = fix first
      "pages_needing_attention": [...],    # top 20 by priority_score
      "image_loading": {...},              # site-wide lazy/srcset coverage
      "breadcrumbs": {...},                # site-wide breadcrumb coverage
    }
    """
    if not pages:
        return {
            "total_pages": 0,
            "total_internal_links": 0,
            "orphan_pages": [],
            "orphan_count": 0,
            "depth": {},
            "silos": [],
            "top_pages": [],
            "pagerank": {},
            "priority_scores": {},
            "pages_needing_attention": [],
            "image_loading": {},
            "breadcrumbs": {},
        }

    graph   = build_link_graph(pages)
    pr      = compute_pagerank(graph)
    orphans = detect_orphans(graph)
    depth   = depth_distribution(graph)
    silos   = silo_analysis(graph, pr)
    top     = top_pages_by_pr(graph, pr)

    # Build normalised-URL → issue count mapping for priority scoring
    issue_counts: dict[str, int] = {
        _normalise(p["url"]): len(p.get("issues", []))
        for p in pages if p.get("url")
    }
    priority  = compute_priority_scores(graph, pr, issue_counts)
    attention = pages_needing_attention(graph, pr, priority, pages)

    return {
        "total_pages":             len(graph["nodes"]),
        "total_internal_links":    len(graph["edges"]),
        "orphan_pages":            orphans,
        "orphan_count":            len(orphans),
        "depth":                   depth,
        "silos":                   silos,
        "top_pages":               top,
        "pagerank":                pr,
        "priority_scores":         priority,
        "pages_needing_attention": attention,
        "image_loading":           image_loading_summary(pages),
        "breadcrumbs":             breadcrumb_summary(pages),
    }
