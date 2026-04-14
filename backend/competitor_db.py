"""
competitor_db.py — SQLite persistence layer for CrawlIQ Competitor Analysis.

Schema
──────
  competitor_snapshots   — one row per analysis task (target + competitors)
  keyword_rankings       — per-keyword position rows (Phase 3: trend tracking)
  cwv_history            — Core Web Vitals time-series (Phase 3)

All JSON columns are stored as TEXT and serialised with json.dumps/loads.
Uses WAL mode for safe concurrent reads during async API calls.

Public API
──────────
  init_db()                        → create tables if not exist
  save_snapshot(task_id, ...)      → insert pending row, return rowid
  update_snapshot(task_id, ...)    → patch status / metrics JSON
  get_snapshot(task_id)            → dict or None
  list_snapshots(domain, limit)    → list of summary dicts (newest first)
  delete_snapshot(task_id)         → hard delete
  save_keyword_rankings(...)       → bulk-insert keyword positions (Phase 3)
  get_keyword_history(domain, kw)  → time-series for one keyword (Phase 3)
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Database location ─────────────────────────────────────────────────────────
# Stored next to main.py so Docker volume mounts work transparently.
DB_PATH = Path(__file__).parent / "crawliq_competitor.db"


# ── Connection helper ─────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    """
    Open a thread-local SQLite connection with:
      - WAL journal for safe concurrent readers
      - JSON1 extension (built-in on CPython ≥ 3.8)
      - Foreign keys enforced
    """
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row          # dict-like row access
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")  # faster, still safe with WAL
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
-- Main snapshots table: one row per competitor analysis run
CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT    NOT NULL UNIQUE,
    target_url       TEXT    NOT NULL,
    competitor_urls  TEXT    NOT NULL,   -- JSON array of strings
    created_at       TEXT    NOT NULL,   -- ISO-8601 UTC
    completed_at     TEXT,               -- NULL until done
    status           TEXT    NOT NULL DEFAULT 'pending',
                                          -- pending / running / done / error
    error_msg        TEXT,               -- populated on status=error
    metrics          TEXT,               -- JSON: full analysis result (Phase 1+)
    summary          TEXT                -- JSON: lightweight scores for listing
);

CREATE INDEX IF NOT EXISTS idx_snap_target   ON competitor_snapshots(target_url);
CREATE INDEX IF NOT EXISTS idx_snap_status   ON competitor_snapshots(status);
CREATE INDEX IF NOT EXISTS idx_snap_created  ON competitor_snapshots(created_at DESC);

-- Keyword ranking positions: populated by Phase 3 (SERP tracking)
CREATE TABLE IF NOT EXISTS keyword_rankings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id  INTEGER NOT NULL REFERENCES competitor_snapshots(id) ON DELETE CASCADE,
    domain       TEXT    NOT NULL,
    keyword      TEXT    NOT NULL,
    position     INTEGER,                -- NULL = not ranking in top 100
    created_at   TEXT    NOT NULL        -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_kw_domain  ON keyword_rankings(domain);
CREATE INDEX IF NOT EXISTS idx_kw_keyword ON keyword_rankings(keyword);
CREATE INDEX IF NOT EXISTS idx_kw_date    ON keyword_rankings(created_at);

-- Core Web Vitals history: populated by Phase 3 (scheduled monitoring)
CREATE TABLE IF NOT EXISTS cwv_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id  INTEGER NOT NULL REFERENCES competitor_snapshots(id) ON DELETE CASCADE,
    url          TEXT    NOT NULL,
    strategy     TEXT    NOT NULL DEFAULT 'mobile',  -- mobile / desktop
    lcp_ms       REAL,
    inp_ms       REAL,
    cls          REAL,
    fcp_ms       REAL,
    ttfb_ms      REAL,
    perf_score   REAL,                   -- 0-1 Lighthouse performance score
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cwv_url  ON cwv_history(url);
CREATE INDEX IF NOT EXISTS idx_cwv_date ON cwv_history(created_at);

-- Monitor rankings: standalone SERP position history (Phase 3, no FK dependency)
CREATE TABLE IF NOT EXISTS monitor_rankings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL,
    domain      TEXT    NOT NULL,
    keyword     TEXT    NOT NULL,
    position    INTEGER,           -- NULL = not in top results
    in_top_10   INTEGER DEFAULT 0,
    in_top_30   INTEGER DEFAULT 0,
    checked_at  TEXT    NOT NULL   -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_mon_job     ON monitor_rankings(job_id);
CREATE INDEX IF NOT EXISTS idx_mon_domain  ON monitor_rankings(domain);
CREATE INDEX IF NOT EXISTS idx_mon_keyword ON monitor_rankings(keyword);
CREATE INDEX IF NOT EXISTS idx_mon_date    ON monitor_rankings(checked_at);

-- ── SaaS tables ──────────────────────────────────────────────────────────────

-- Users (auth + billing)
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    UNIQUE NOT NULL,
    name            TEXT,
    password_hash   TEXT    NOT NULL,
    tier            TEXT    DEFAULT 'free',   -- free / pro / agency
    api_key         TEXT    UNIQUE,
    logo_base64     TEXT,                     -- base64 PNG for white-label PDF
    pages_used      INTEGER DEFAULT 0,
    pages_reset_at  TEXT    NOT NULL,         -- ISO-8601: first day of billing month
    alert_email     TEXT,                     -- override email for alerts (NULL = use account email)
    rank_drop_threshold INTEGER DEFAULT 5,    -- alert when position drops by this many spots
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_email   ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key);

-- Projects (saved crawl sessions)
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    last_crawl_at TEXT,
    page_count    INTEGER DEFAULT 0,
    issue_count   INTEGER DEFAULT 0,
    health_score  REAL    DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_proj_user    ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_proj_url     ON projects(url);
CREATE INDEX IF NOT EXISTS idx_proj_created ON projects(created_at DESC);

-- Crawl snapshots (score history per project — stored newest-first)
CREATE TABLE IF NOT EXISTS crawl_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    crawled_at   TEXT    NOT NULL,
    page_count   INTEGER,
    issue_count  INTEGER,
    health_score REAL,
    results_json TEXT    -- JSON: top-100 pages (lightweight, not full blob)
);

CREATE INDEX IF NOT EXISTS idx_snap_proj ON crawl_snapshots(project_id);
CREATE INDEX IF NOT EXISTS idx_snap_date ON crawl_snapshots(crawled_at DESC);

-- Issue status tracking (open / in_progress / resolved)
CREATE TABLE IF NOT EXISTS issue_status (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    url         TEXT    NOT NULL,
    issue_type  TEXT    NOT NULL,
    status      TEXT    DEFAULT 'open',  -- open / in_progress / resolved
    note        TEXT,
    updated_at  TEXT    NOT NULL,
    UNIQUE(project_id, url, issue_type)
);

CREATE INDEX IF NOT EXISTS idx_issue_proj ON issue_status(project_id);
CREATE INDEX IF NOT EXISTS idx_issue_url  ON issue_status(url);
"""


def init_db() -> None:
    """Create all tables and indexes. Safe to call on every startup (IF NOT EXISTS)."""
    try:
        with _connect() as conn:
            conn.executescript(_DDL)
        _run_migrations()
        logger.info("Competitor DB initialised: %s", DB_PATH)
    except Exception as exc:
        logger.error("Competitor DB init failed: %s", exc)
        raise


def _run_migrations() -> None:
    """
    Apply forward-only schema migrations on existing databases.
    Each migration is wrapped in its own try/except so one failure
    doesn't block startup — new columns added here appear automatically
    on the next deploy without manual intervention.

    Pattern: ALTER TABLE ... ADD COLUMN — SQLite ignores duplicate-column
    errors (OperationalError: duplicate column name), so we swallow those.
    """
    migrations = [
        # SaaS columns added in v2 — safe to re-run (duplicate column errors are caught)
        "ALTER TABLE users ADD COLUMN alert_email TEXT",
        "ALTER TABLE users ADD COLUMN rank_drop_threshold INTEGER DEFAULT 5",
        "ALTER TABLE users ADD COLUMN logo_base64 TEXT",
    ]
    if not migrations:
        return
    with _connect() as conn:
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception as exc:
                # "duplicate column name" is expected on re-run — log and continue
                logger.debug("Migration skipped (already applied?): %s — %s", sql, exc)


# ── Snapshot CRUD ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_snapshot(
    task_id: str,
    target_url: str,
    competitor_urls: list[str],
) -> int:
    """
    Insert a new pending snapshot row.
    Returns the auto-increment row id.
    Raises on duplicate task_id (UNIQUE constraint).
    """
    sql = """
        INSERT INTO competitor_snapshots
            (task_id, target_url, competitor_urls, created_at, status)
        VALUES (?, ?, ?, ?, 'pending')
    """
    with _connect() as conn:
        cur = conn.execute(sql, (
            task_id,
            target_url,
            json.dumps(competitor_urls),
            _now_iso(),
        ))
        return cur.lastrowid


def update_snapshot(
    task_id: str,
    *,
    status: str | None = None,
    metrics: dict | None = None,
    summary: dict | None = None,
    error_msg: str | None = None,
    completed: bool = False,
) -> None:
    """
    Patch one or more fields on an existing snapshot.
    Only provided (non-None) kwargs are written.
    """
    sets: list[str] = []
    params: list[Any] = []

    if status is not None:
        sets.append("status = ?");          params.append(status)
    if metrics is not None:
        sets.append("metrics = ?");         params.append(json.dumps(metrics))
    if summary is not None:
        sets.append("summary = ?");         params.append(json.dumps(summary))
    if error_msg is not None:
        sets.append("error_msg = ?");       params.append(error_msg)
    if completed:
        sets.append("completed_at = ?");    params.append(_now_iso())

    if not sets:
        return

    params.append(task_id)
    sql = f"UPDATE competitor_snapshots SET {', '.join(sets)} WHERE task_id = ?"
    with _connect() as conn:
        conn.execute(sql, params)


def get_snapshot(task_id: str) -> dict | None:
    """Return the full snapshot dict, or None if not found."""
    sql = "SELECT * FROM competitor_snapshots WHERE task_id = ?"
    with _connect() as conn:
        row = conn.execute(sql, (task_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    # Deserialise JSON columns
    for col in ("competitor_urls", "metrics", "summary"):
        if d.get(col):
            try:
                d[col] = json.loads(d[col])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def list_snapshots(domain: str | None = None, limit: int = 50) -> list[dict]:
    """
    Return lightweight snapshot list (no full metrics blob), newest first.
    Optionally filter by target domain substring.
    """
    if domain:
        sql = """
            SELECT id, task_id, target_url, competitor_urls, created_at,
                   completed_at, status, error_msg, summary
            FROM competitor_snapshots
            WHERE target_url LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
        """
        params = (f"%{domain}%", limit)
    else:
        sql = """
            SELECT id, task_id, target_url, competitor_urls, created_at,
                   completed_at, status, error_msg, summary
            FROM competitor_snapshots
            ORDER BY created_at DESC
            LIMIT ?
        """
        params = (limit,)

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        for col in ("competitor_urls", "summary"):
            if d.get(col):
                try:
                    d[col] = json.loads(d[col])
                except (json.JSONDecodeError, TypeError):
                    pass
        result.append(d)
    return result


def delete_snapshot(task_id: str) -> bool:
    """Hard-delete a snapshot and its cascade children. Returns True if deleted."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM competitor_snapshots WHERE task_id = ?", (task_id,)
        )
        return cur.rowcount > 0


# ── Keyword rankings (Phase 3) ────────────────────────────────────────────────

def save_keyword_rankings(
    snapshot_id: int,
    rankings: list[dict],
) -> None:
    """
    Bulk-insert keyword ranking rows for Phase 3 trend tracking.
    Each dict must have: domain, keyword, position (int or None).
    """
    now = _now_iso()
    rows = [
        (snapshot_id, r["domain"], r["keyword"], r.get("position"), now)
        for r in rankings
    ]
    sql = """
        INSERT INTO keyword_rankings (snapshot_id, domain, keyword, position, created_at)
        VALUES (?, ?, ?, ?, ?)
    """
    with _connect() as conn:
        conn.executemany(sql, rows)
    logger.debug("Saved %d keyword ranking rows for snapshot %d", len(rows), snapshot_id)


def get_keyword_history(domain: str, keyword: str, days: int = 90) -> list[dict]:
    """
    Return position time-series for (domain, keyword) over last N days.
    Used for ranking velocity charts in Phase 3.
    """
    sql = """
        SELECT position, created_at
        FROM keyword_rankings
        WHERE domain = ? AND keyword = ?
          AND created_at >= datetime('now', ?)
        ORDER BY created_at ASC
    """
    with _connect() as conn:
        rows = conn.execute(sql, (domain, keyword, f"-{days} days")).fetchall()
    return [dict(r) for r in rows]


# ── CWV history (Phase 3) ─────────────────────────────────────────────────────

def save_cwv(snapshot_id: int, url: str, strategy: str, cwv: dict) -> None:
    """
    Persist one CWV measurement row.
    cwv dict keys: lcp_ms, inp_ms, cls, fcp_ms, ttfb_ms, perf_score
    """
    sql = """
        INSERT INTO cwv_history
            (snapshot_id, url, strategy, lcp_ms, inp_ms, cls,
             fcp_ms, ttfb_ms, perf_score, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with _connect() as conn:
        conn.execute(sql, (
            snapshot_id, url, strategy,
            cwv.get("lcp_ms"), cwv.get("inp_ms"), cwv.get("cls"),
            cwv.get("fcp_ms"), cwv.get("ttfb_ms"), cwv.get("perf_score"),
            _now_iso(),
        ))


def get_cwv_history(url: str, strategy: str = "mobile", days: int = 90) -> list[dict]:
    """Return CWV time-series for one URL (Phase 3 trending)."""
    sql = """
        SELECT lcp_ms, inp_ms, cls, fcp_ms, ttfb_ms, perf_score, created_at
        FROM cwv_history
        WHERE url = ? AND strategy = ?
          AND created_at >= datetime('now', ?)
        ORDER BY created_at ASC
    """
    with _connect() as conn:
        rows = conn.execute(sql, (url, strategy, f"-{days} days")).fetchall()
    return [dict(r) for r in rows]


# ── Monitor rankings (Phase 3) ────────────────────────────────────────────────

def save_monitor_rankings(job_id: str, domain: str, rankings: list[dict]) -> None:
    """
    Bulk-insert monitor_rankings rows for scheduled SERP tracking.
    Each dict must have: keyword, position (int|None), in_top_10, in_top_30, checked_at.
    """
    now = _now_iso()
    rows = [
        (
            job_id,
            domain,
            r["keyword"],
            r.get("position"),
            int(bool(r.get("in_top_10", False))),
            int(bool(r.get("in_top_30", False))),
            r.get("checked_at", now),
        )
        for r in rankings
    ]
    sql = """
        INSERT INTO monitor_rankings
            (job_id, domain, keyword, position, in_top_10, in_top_30, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    with _connect() as conn:
        conn.executemany(sql, rows)
    logger.debug("Saved %d monitor ranking rows for job %s", len(rows), job_id)


def get_monitor_history(
    domain: str,
    keyword: str,
    limit: int = 30,
) -> list[dict]:
    """
    Return position time-series for (domain, keyword) from monitor_rankings.
    Returns newest-first list of {keyword, position, in_top_10, in_top_30, checked_at}.
    """
    sql = """
        SELECT keyword, position, in_top_10, in_top_30, checked_at
        FROM monitor_rankings
        WHERE domain = ? AND keyword = ?
        ORDER BY checked_at DESC
        LIMIT ?
    """
    with _connect() as conn:
        rows = conn.execute(sql, (domain, keyword, limit)).fetchall()
    return [dict(r) for r in rows]


def get_monitor_latest(domain: str) -> list[dict]:
    """Return latest position for every tracked keyword on domain."""
    sql = """
        SELECT keyword, position, in_top_10, in_top_30, MAX(checked_at) as checked_at
        FROM monitor_rankings
        WHERE domain = ?
        GROUP BY keyword
        ORDER BY checked_at DESC
    """
    with _connect() as conn:
        rows = conn.execute(sql, (domain,)).fetchall()
    return [dict(r) for r in rows]


# ── Projects ──────────────────────────────────────────────────────────────────

def create_project(user_id: int | None, name: str, url: str) -> dict:
    """Create a new project. Returns the project dict."""
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (user_id, name, url, created_at) VALUES (?,?,?,?)",
            (user_id, name, url, now),
        )
        proj_id = cur.lastrowid
        row = conn.execute("SELECT * FROM projects WHERE id=?", (proj_id,)).fetchone()
        return dict(row)


def list_projects(user_id: int | None, limit: int = 50) -> list[dict]:
    """List projects for a user (or all if user_id is None), newest first."""
    with _connect() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM projects WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(row) if row else None


def update_project(project_id: int, **kwargs) -> None:
    """Update page_count, issue_count, health_score, last_crawl_at, name on a project."""
    allowed = {"page_count", "issue_count", "health_score", "last_crawl_at", "name"}
    sets, params = [], []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k}=?"); params.append(v)
    if not sets:
        return
    params.append(project_id)
    with _connect() as conn:
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", params)


def delete_project(project_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return cur.rowcount > 0


# ── Crawl snapshots ───────────────────────────────────────────────────────────

def save_crawl_snapshot(
    project_id:   int,
    page_count:   int,
    issue_count:  int,
    health_score: float,
    results_json: str = "",
) -> int:
    """Persist a crawl snapshot for score history. Returns snapshot id."""
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO crawl_snapshots
               (project_id, crawled_at, page_count, issue_count, health_score, results_json)
               VALUES (?,?,?,?,?,?)""",
            (project_id, now, page_count, issue_count, health_score, results_json),
        )
        return cur.lastrowid


def get_crawl_history(project_id: int, limit: int = 20) -> list[dict]:
    """Return score history for a project, newest first (no results_json blob)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, project_id, crawled_at, page_count, issue_count, health_score
               FROM crawl_snapshots
               WHERE project_id=?
               ORDER BY crawled_at DESC LIMIT ?""",
            (project_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Issue status ──────────────────────────────────────────────────────────────

def upsert_issue_status(
    project_id: int | None,
    url:        str,
    issue_type: str,
    status:     str,   # open / in_progress / resolved
    note:       str = "",
) -> None:
    """Create or update an issue status row."""
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO issue_status (project_id, url, issue_type, status, note, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(project_id, url, issue_type)
               DO UPDATE SET status=excluded.status, note=excluded.note, updated_at=excluded.updated_at""",
            (project_id, url, issue_type, status, note, now),
        )


def get_issue_statuses(project_id: int | None, url: str | None = None) -> list[dict]:
    """Return all issue status rows for a project (optionally filtered by URL)."""
    with _connect() as conn:
        if url:
            rows = conn.execute(
                "SELECT * FROM issue_status WHERE project_id=? AND url=?",
                (project_id, url),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM issue_status WHERE project_id=? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
    return [dict(r) for r in rows]


# ── Module-level init on first import ────────────────────────────────────────
try:
    init_db()
except Exception as _db_init_err:
    logger.warning("Competitor DB auto-init failed (will retry on first use): %s", _db_init_err)
