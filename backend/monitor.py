"""
monitor.py — CrawlIQ Scheduled SERP Monitoring (Phase 3)

Provides in-process, asyncio-based job scheduling for periodic SERP position
tracking. No external scheduler dependency — uses pure asyncio tasks + SQLite.

Architecture
────────────
  MonitorJob        — dataclass describing one scheduled check
  _job_store        — in-memory dict of active jobs (survives only until restart)
  _monitor_loop()   — asyncio task that fires each job on its interval
  SQLite            — saves every position snapshot via competitor_db

Public API
──────────
  schedule_job(domain, keywords, interval_hours) → job_id  (str)
  cancel_job(job_id)                              → bool
  list_jobs()                                     → list[MonitorJob]
  get_job_history(domain, keyword, limit)         → list[dict]
  start_monitor_service()                         → call once at startup
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Job dataclass ─────────────────────────────────────────────────────────────

@dataclass
class MonitorJob:
    job_id:         str
    domain:         str
    keywords:       list[str]
    interval_hours: float          # how often to run (e.g. 24 = daily)
    created_at:     str            # ISO-8601 UTC
    last_run_at:    Optional[str]  # ISO-8601 UTC or None
    next_run_at:    str            # ISO-8601 UTC
    run_count:      int   = 0
    active:         bool  = True
    last_error:     Optional[str] = None


# ── In-memory store ───────────────────────────────────────────────────────────

_job_store: dict[str, MonitorJob] = {}
_monitor_task: Optional[asyncio.Task] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_run(interval_hours: float) -> str:
    import time
    from datetime import timedelta
    return (
        datetime.now(timezone.utc) + timedelta(hours=interval_hours)
    ).isoformat()


# ── Core scheduling loop ─────────────────────────────────────────────────────

async def _run_job(job: MonitorJob) -> None:
    """
    Execute one monitor job: bulk SERP check for all keywords on the domain.
    Saves results to keyword_rankings table via competitor_db.
    """
    logger.info("Monitor: running job %s — %s (%d keywords)",
                job.job_id, job.domain, len(job.keywords))
    job.last_run_at = _now_iso()
    job.run_count  += 1
    job.last_error  = None

    try:
        from serp_scraper import bulk_serp_check
        results = await bulk_serp_check(job.keywords, job.domain, concurrency=2)

        # Persist to SQLite
        try:
            from competitor_db import save_monitor_rankings
            now = _now_iso()
            save_monitor_rankings(
                job_id   = job.job_id,
                domain   = job.domain,
                rankings = [
                    {
                        "keyword":    r["keyword"],
                        "position":   r.get("position"),
                        "in_top_10":  r.get("in_top_10", False),
                        "in_top_30":  r.get("in_top_30", False),
                        "checked_at": now,
                    }
                    for r in results
                ],
            )
        except Exception as db_exc:
            logger.warning("Monitor DB save failed for job %s: %s", job.job_id, db_exc)

        logger.info("Monitor: job %s complete — %d positions checked",
                    job.job_id, len(results))

    except Exception as exc:
        job.last_error = str(exc)
        logger.error("Monitor: job %s failed: %s", job.job_id, exc)

    finally:
        job.next_run_at = _next_run(job.interval_hours)


async def _monitor_loop() -> None:
    """
    Background loop that checks every 60s whether any job is due.
    Fires due jobs as separate asyncio tasks (non-blocking).
    """
    logger.info("Monitor service started — polling every 60s")
    while True:
        try:
            now = datetime.now(timezone.utc)
            for job in list(_job_store.values()):
                if not job.active:
                    continue
                try:
                    next_dt = datetime.fromisoformat(job.next_run_at)
                    # Ensure timezone-aware comparison
                    if next_dt.tzinfo is None:
                        from datetime import timezone as _tz
                        next_dt = next_dt.replace(tzinfo=_tz.utc)
                except Exception:
                    continue
                if now >= next_dt:
                    asyncio.create_task(_run_job(job))
        except Exception as exc:
            logger.error("Monitor loop error: %s", exc)
        await asyncio.sleep(60)


# ── Public API ────────────────────────────────────────────────────────────────

def start_monitor_service() -> None:
    """
    Start the background monitor loop as an asyncio task.
    Call once at FastAPI startup. Safe to call multiple times (idempotent).
    """
    global _monitor_task
    if _monitor_task and not _monitor_task.done():
        logger.debug("Monitor service already running")
        return
    try:
        loop = asyncio.get_event_loop()
        _monitor_task = loop.create_task(_monitor_loop())
        logger.info("Monitor service task created")
    except RuntimeError:
        # No event loop running yet — will be started by FastAPI lifespan
        logger.debug("Monitor service deferred — no event loop yet")


def schedule_job(
    domain:         str,
    keywords:       list[str],
    interval_hours: float = 24.0,
) -> MonitorJob:
    """
    Schedule a new SERP monitoring job.
    Returns the created MonitorJob. Runs immediately on first interval.
    """
    job_id = str(uuid.uuid4())
    now    = _now_iso()
    job    = MonitorJob(
        job_id         = job_id,
        domain         = domain,
        keywords       = keywords[:50],   # cap at 50 keywords per job
        interval_hours = max(0.5, interval_hours),  # minimum 30 minutes
        created_at     = now,
        last_run_at    = None,
        next_run_at    = now,   # run immediately on first tick
        run_count      = 0,
        active         = True,
    )
    _job_store[job_id] = job
    logger.info("Monitor: scheduled job %s — %s every %.1fh (%d keywords)",
                job_id, domain, interval_hours, len(keywords))
    return job


def cancel_job(job_id: str) -> bool:
    """Deactivate a job by ID. Returns True if found, False if not found."""
    job = _job_store.get(job_id)
    if not job:
        return False
    job.active = False
    logger.info("Monitor: cancelled job %s", job_id)
    return True


def delete_job(job_id: str) -> bool:
    """Remove a job entirely from the store. Returns True if found."""
    if job_id in _job_store:
        del _job_store[job_id]
        logger.info("Monitor: deleted job %s", job_id)
        return True
    return False


def list_jobs() -> list[dict]:
    """Return all jobs as plain dicts (serialisable for JSON)."""
    return [asdict(j) for j in _job_store.values()]


def get_job(job_id: str) -> Optional[dict]:
    """Return one job dict or None."""
    job = _job_store.get(job_id)
    return asdict(job) if job else None


def get_job_history(domain: str, keyword: str, limit: int = 30) -> list[dict]:
    """
    Retrieve historical position records for domain+keyword from SQLite.
    Returns newest-first list of {keyword, position, in_top_10, in_top_30, checked_at}.
    """
    try:
        from competitor_db import get_monitor_history
        return get_monitor_history(domain, keyword, limit=limit)
    except Exception as exc:
        logger.warning("get_job_history failed: %s", exc)
        return []


def get_domain_latest(domain: str) -> list[dict]:
    """Return latest tracked positions for all keywords on a domain."""
    try:
        from competitor_db import get_monitor_latest
        return get_monitor_latest(domain)
    except Exception as exc:
        logger.warning("get_domain_latest failed: %s", exc)
        return []
