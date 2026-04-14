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
        # Example future migration (add column that didn't exist in v1):
        # "ALTER TABLE competitor_snapshots ADD COLUMN phase INTEGER DEFAULT 1",
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


# ── Module-level init on first import ────────────────────────────────────────
try:
    init_db()
except Exception as _db_init_err:
    logger.warning("Competitor DB auto-init failed (will retry on first use): %s", _db_init_err)
