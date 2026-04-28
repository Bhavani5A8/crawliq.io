"""
database.py — CrawlIQ persistent storage layer (SQLite + SQLAlchemy)

Tables:
  users          - user accounts (mirrors auth.py users but adds project FK)
  projects       - user-owned project groups
  crawl_jobs     - one record per crawl run (persistent across restarts)
  crawl_results  - per-page audit data linked to a crawl job
  serp_tracking  - keyword rank snapshots
  schedules      - recurring crawl schedule definitions

Usage:
    from database import init_db, get_db, save_crawl_job, update_crawl_job, save_crawl_results
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    Column, String, Integer, Float, Text, Index,
    ForeignKey, event, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# ── Database path ─────────────────────────────────────────────────────────────
# Stored next to main.py so it persists as long as the HF Space volume does.
_DB_PATH = Path(__file__).parent / "crawliq.db"
DATABASE_URL = f"sqlite:///{_DB_PATH}"

Base = declarative_base()


# ══════════════════════════════════════════════════════════════════════════════
# Models
# ══════════════════════════════════════════════════════════════════════════════

class DBUser(Base):
    """Mirrors the user records created by auth.py (email is the join key)."""
    __tablename__ = "db_users"

    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email      = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(Float, default=time.time)

    projects = relationship("DBProject", back_populates="user",
                            cascade="all, delete-orphan")


class DBProject(Base):
    __tablename__ = "db_projects"

    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("db_users.id"), nullable=False, index=True)
    name       = Column(String, nullable=False)
    created_at = Column(Float, default=time.time)

    user       = relationship("DBUser", back_populates="projects")
    crawl_jobs = relationship("DBCrawlJob", back_populates="project",
                              cascade="all, delete-orphan")
    schedules  = relationship("DBSchedule", back_populates="project",
                              cascade="all, delete-orphan")


class DBCrawlJob(Base):
    __tablename__ = "db_crawl_jobs"

    id            = Column(String, primary_key=True)           # same as in-memory job_id
    project_id    = Column(String, ForeignKey("db_projects.id"), nullable=True, index=True)
    url           = Column(String, nullable=False)
    status        = Column(String, default="queued")           # queued|running|completed|failed
    pages_crawled = Column(Integer, default=0)
    created_at    = Column(Float, default=time.time)
    completed_at  = Column(Float, nullable=True)
    error         = Column(Text, nullable=True)

    project = relationship("DBProject", back_populates="crawl_jobs")
    results = relationship("DBCrawlResult", back_populates="job",
                           cascade="all, delete-orphan")


class DBCrawlResult(Base):
    __tablename__ = "db_crawl_results"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id      = Column(String, ForeignKey("db_crawl_jobs.id"), nullable=False, index=True)
    page_url    = Column(String, nullable=False)
    title       = Column(String, nullable=True)
    status_code = Column(Integer, nullable=True)
    meta_data   = Column(Text, nullable=True)   # JSON blob of full page dict
    issues      = Column(Text, nullable=True)   # JSON list of issue strings

    job = relationship("DBCrawlJob", back_populates="results")


class DBSerpTracking(Base):
    __tablename__ = "db_serp_tracking"

    id        = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    domain    = Column(String, nullable=False, index=True)
    keyword   = Column(String, nullable=False, index=True)
    position  = Column(Integer, nullable=True)
    result_url = Column(String, nullable=True)
    timestamp = Column(Float, default=time.time)
    raw_data  = Column(Text, nullable=True)   # full JSON from serp_scraper

    __table_args__ = (
        Index("ix_serp_domain_kw", "domain", "keyword"),
    )


class DBSchedule(Base):
    __tablename__ = "db_schedules"

    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("db_projects.id"), nullable=False, index=True)
    url        = Column(String, nullable=False)
    interval   = Column(String, default="weekly")   # daily | weekly
    max_pages  = Column(Integer, default=50)
    last_run   = Column(Float, nullable=True)
    next_run   = Column(Float, nullable=True)
    active     = Column(Integer, default=1)          # 1 = enabled
    created_at = Column(Float, default=time.time)

    project = relationship("DBProject", back_populates="schedules")


# ── Engine & session factory ───────────────────────────────────────────────────

_engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# Enable WAL mode for safer concurrent access on HuggingFace
@event.listens_for(_engine, "connect")
def _set_wal_mode(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create all tables if they do not yet exist. Safe to call on every startup."""
    Base.metadata.create_all(bind=_engine)


def get_db() -> Iterator[Session]:
    """Yield a scoped DB session; always closed on exit."""
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Helper functions  (synchronous — safe to call from async via run_in_executor
# or directly from FastAPI sync routes)
# ══════════════════════════════════════════════════════════════════════════════

def ensure_user(email: str) -> str:
    """Return existing DBUser.id for this email, or create one. Returns id."""
    db = _SessionLocal()
    try:
        user = db.query(DBUser).filter(DBUser.email == email).first()
        if not user:
            user = DBUser(email=email)
            db.add(user)
            db.commit()
            db.refresh(user)
        return user.id
    finally:
        db.close()


def create_project_db(user_id: str, name: str) -> dict:
    db = _SessionLocal()
    try:
        proj = DBProject(user_id=user_id, name=name)
        db.add(proj)
        db.commit()
        db.refresh(proj)
        return _proj_dict(proj)
    finally:
        db.close()


def list_projects_db(user_id: str) -> list[dict]:
    db = _SessionLocal()
    try:
        rows = (db.query(DBProject)
                .filter(DBProject.user_id == user_id)
                .order_by(DBProject.created_at.desc())
                .all())
        return [_proj_dict(r) for r in rows]
    finally:
        db.close()


def get_project_db(project_id: str) -> dict | None:
    db = _SessionLocal()
    try:
        p = db.query(DBProject).filter(DBProject.id == project_id).first()
        return _proj_dict(p) if p else None
    finally:
        db.close()


def save_crawl_job_db(job_id: str, url: str, project_id: str | None = None) -> None:
    """Persist a new crawl job record (status = running)."""
    db = _SessionLocal()
    try:
        existing = db.query(DBCrawlJob).filter(DBCrawlJob.id == job_id).first()
        if existing:
            return
        record = DBCrawlJob(id=job_id, url=url, project_id=project_id, status="running")
        db.add(record)
        db.commit()
    finally:
        db.close()


def update_crawl_job_db(job_id: str, status: str, pages_crawled: int,
                        error: str | None = None) -> None:
    db = _SessionLocal()
    try:
        update_vals: dict = {
            "status": status,
            "pages_crawled": pages_crawled,
            "completed_at": time.time(),
        }
        if error:
            update_vals["error"] = error
        db.query(DBCrawlJob).filter(DBCrawlJob.id == job_id).update(update_vals)
        db.commit()
    finally:
        db.close()


def save_crawl_results_db(job_id: str, results: list[dict]) -> None:
    """Bulk-insert per-page crawl results linked to a job."""
    if not results:
        return
    db = _SessionLocal()
    try:
        records = []
        for r in results:
            page_url = r.get("url", "")
            if not page_url:
                continue
            # Separate issues from the rest of the meta blob
            issues = r.get("issues", [])
            meta = {k: v for k, v in r.items()
                    if k not in ("url", "title", "status_code", "issues")}
            records.append(DBCrawlResult(
                job_id      = job_id,
                page_url    = page_url,
                title       = r.get("title"),
                status_code = r.get("status_code"),
                meta_data   = json.dumps(meta, default=str),
                issues      = json.dumps(issues, default=str),
            ))
        if records:
            db.bulk_save_objects(records)
            db.commit()
    finally:
        db.close()


def get_crawl_history_db(project_id: str, limit: int = 20) -> list[dict]:
    db = _SessionLocal()
    try:
        rows = (db.query(DBCrawlJob)
                .filter(DBCrawlJob.project_id == project_id)
                .order_by(DBCrawlJob.created_at.desc())
                .limit(limit)
                .all())
        return [_job_dict(r) for r in rows]
    finally:
        db.close()


def get_job_results_db(job_id: str) -> list[dict]:
    db = _SessionLocal()
    try:
        rows = db.query(DBCrawlResult).filter(DBCrawlResult.job_id == job_id).all()
        out = []
        for r in rows:
            page: dict = {"url": r.page_url, "title": r.title,
                          "status_code": r.status_code}
            if r.meta_data:
                try:
                    page.update(json.loads(r.meta_data))
                except Exception:
                    pass
            if r.issues:
                try:
                    page["issues"] = json.loads(r.issues)
                except Exception:
                    page["issues"] = []
            out.append(page)
        return out
    finally:
        db.close()


def save_serp_db(domain: str, keyword: str, position: int | None,
                 result_url: str | None, raw: dict) -> dict:
    db = _SessionLocal()
    try:
        rec = DBSerpTracking(
            domain     = domain,
            keyword    = keyword,
            position   = position,
            result_url = result_url,
            raw_data   = json.dumps(raw, default=str),
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return {
            "id": rec.id, "domain": rec.domain, "keyword": rec.keyword,
            "position": rec.position, "timestamp": rec.timestamp,
        }
    finally:
        db.close()


def get_serp_history_db(domain: str, keyword: str, limit: int = 30) -> list[dict]:
    db = _SessionLocal()
    try:
        rows = (db.query(DBSerpTracking)
                .filter(DBSerpTracking.domain == domain,
                        DBSerpTracking.keyword == keyword)
                .order_by(DBSerpTracking.timestamp.desc())
                .limit(limit)
                .all())
        return [{"position": r.position, "timestamp": r.timestamp,
                 "result_url": r.result_url} for r in rows]
    finally:
        db.close()


def create_schedule_db(project_id: str, url: str,
                       interval: str = "weekly", max_pages: int = 50) -> dict:
    db = _SessionLocal()
    try:
        next_run = time.time() + (86400 if interval == "daily" else 604800)
        sched = DBSchedule(project_id=project_id, url=url,
                           interval=interval, max_pages=max_pages,
                           next_run=next_run)
        db.add(sched)
        db.commit()
        db.refresh(sched)
        return _sched_dict(sched)
    finally:
        db.close()


def list_schedules_db(project_id: str) -> list[dict]:
    db = _SessionLocal()
    try:
        rows = (db.query(DBSchedule)
                .filter(DBSchedule.project_id == project_id,
                        DBSchedule.active == 1)
                .all())
        return [_sched_dict(r) for r in rows]
    finally:
        db.close()


def get_due_schedules_db() -> list[dict]:
    """Return all active schedules whose next_run is in the past."""
    db = _SessionLocal()
    try:
        now = time.time()
        rows = (db.query(DBSchedule)
                .filter(DBSchedule.active == 1,
                        DBSchedule.next_run <= now)
                .all())
        return [_sched_dict(r) for r in rows]
    finally:
        db.close()


def mark_schedule_ran_db(schedule_id: str) -> None:
    db = _SessionLocal()
    try:
        sched = db.query(DBSchedule).filter(DBSchedule.id == schedule_id).first()
        if sched:
            sched.last_run = time.time()
            interval_secs = 86400 if sched.interval == "daily" else 604800
            sched.next_run = time.time() + interval_secs
            db.commit()
    finally:
        db.close()


# ── Private dict serialisers ──────────────────────────────────────────────────

def _proj_dict(p: DBProject) -> dict:
    return {"id": p.id, "user_id": p.user_id, "name": p.name,
            "created_at": p.created_at}


def _job_dict(j: DBCrawlJob) -> dict:
    return {"id": j.id, "project_id": j.project_id, "url": j.url,
            "status": j.status, "pages_crawled": j.pages_crawled,
            "created_at": j.created_at, "completed_at": j.completed_at,
            "error": j.error}


def _sched_dict(s: DBSchedule) -> dict:
    return {"id": s.id, "project_id": s.project_id, "url": s.url,
            "interval": s.interval, "max_pages": s.max_pages,
            "last_run": s.last_run, "next_run": s.next_run,
            "active": bool(s.active), "created_at": s.created_at}
