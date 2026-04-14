"""
auth.py — JWT authentication + user management for CrawlIQ SaaS.

Design
──────
  Passwords hashed with bcrypt via passlib.
  JWTs signed with HS256, configurable expiry (default 30 days).
  API keys: UUID4 stored in users table, usable as Bearer token.
  Tiers: free (200 pages/mo, 3 projects), pro (5000/mo, 20 projects),
         agency (unlimited).

Public API
──────────
  register(email, password, name)         → User dict
  login(email, password)                  → access_token str
  get_user_by_token(token)                → User dict or None
  get_user_by_api_key(api_key)            → User dict or None
  get_user_by_id(user_id)                 → User dict or None
  update_user(user_id, **kwargs)          → None
  rotate_api_key(user_id)                 → new_key str
  check_crawl_quota(user_id, n_pages)     → (allowed: bool, msg: str)
  record_pages_crawled(user_id, n_pages)  → None
  TIER_LIMITS                             → dict

Environment
───────────
  JWT_SECRET   — signing secret (defaults to a stable fallback for dev)
  JWT_EXPIRE_DAYS — token lifetime in days (default 30)

Dependencies (requirements.txt)
───────────────────────────────
  python-jose[cryptography]>=3.3.0,<4.0
  passlib[bcrypt]>=1.7.4,<2.0
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tier config ───────────────────────────────────────────────────────────────

TIER_LIMITS: dict[str, dict] = {
    "free":   {"pages_per_month": 200,   "projects": 3,   "monitor_jobs": 2},
    "pro":    {"pages_per_month": 5_000, "projects": 20,  "monitor_jobs": 15},
    "agency": {"pages_per_month": -1,    "projects": -1,  "monitor_jobs": -1},
}

# ── JWT config ────────────────────────────────────────────────────────────────

_JWT_SECRET      = os.getenv("JWT_SECRET", "crawliq-dev-secret-change-in-production")
_JWT_ALGORITHM   = "HS256"
_JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "30"))

# ── Dependency availability ───────────────────────────────────────────────────

try:
    from jose import jwt as _jose_jwt, JWTError as _JWTError
    _JOSE_OK = True
except ImportError:
    _JOSE_OK = False
    logger.warning("python-jose not installed — auth disabled. pip install python-jose[cryptography]")

try:
    from passlib.context import CryptContext as _CryptContext
    _pwd_ctx = _CryptContext(schemes=["bcrypt"], deprecated="auto")
    _PASSLIB_OK = True
except ImportError:
    _PASSLIB_OK = False
    logger.warning("passlib not installed — auth disabled. pip install passlib[bcrypt]")


def _auth_available() -> bool:
    return _JOSE_OK and _PASSLIB_OK


# ── DB helpers (lazy import to avoid circular imports) ───────────────────────

def _db():
    from competitor_db import _connect
    return _connect()


# ── Password helpers ──────────────────────────────────────────────────────────

def _hash_password(plain: str) -> str:
    if not _PASSLIB_OK:
        raise RuntimeError("passlib not available")
    return _pwd_ctx.hash(plain)


def _verify_password(plain: str, hashed: str) -> bool:
    if not _PASSLIB_OK:
        return False
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:
        return False


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _create_token(user_id: int) -> str:
    if not _JOSE_OK:
        raise RuntimeError("python-jose not available")
    expire = datetime.now(timezone.utc) + timedelta(days=_JWT_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return _jose_jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _decode_token(token: str) -> Optional[int]:
    """Returns user_id int or None on any error."""
    if not _JOSE_OK:
        return None
    try:
        payload = _jose_jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return int(payload["sub"])
    except Exception:
        return None


# ── User CRUD (via competitor_db schema extension) ────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_user(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    d.pop("password_hash", None)    # never expose
    return d


def register(email: str, password: str, name: str = "") -> dict:
    """
    Create a new user. Raises ValueError on duplicate email or weak password.
    Returns user dict (no password_hash).
    """
    if not _auth_available():
        raise RuntimeError("Auth libraries not installed")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    email = email.strip().lower()
    pw_hash   = _hash_password(password)
    api_key   = str(uuid.uuid4()).replace("-", "")
    now       = _now_iso()
    # First day of current month as reset anchor
    reset_at  = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0).isoformat(timespec="seconds")
    try:
        with _db() as conn:
            cur = conn.execute(
                """INSERT INTO users
                   (email, name, password_hash, tier, api_key, pages_used,
                    pages_reset_at, created_at)
                   VALUES (?, ?, ?, 'free', ?, 0, ?, ?)""",
                (email, name or email.split("@")[0], pw_hash, api_key, reset_at, now),
            )
            user_id = cur.lastrowid
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return _row_to_user(row)
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValueError(f"Email already registered: {email}")
        raise


def login(email: str, password: str) -> str:
    """
    Verify credentials. Returns a JWT access token string.
    Raises ValueError on wrong email/password.
    """
    if not _auth_available():
        raise RuntimeError("Auth libraries not installed")
    email = email.strip().lower()
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row is None or not _verify_password(password, row["password_hash"]):
        raise ValueError("Invalid email or password")
    return _create_token(row["id"])


def get_user_by_token(token: str) -> Optional[dict]:
    """Decode JWT and return user dict, or None if invalid."""
    user_id = _decode_token(token)
    if user_id is None:
        return None
    return get_user_by_id(user_id)


def get_user_by_api_key(api_key: str) -> Optional[dict]:
    """Look up user by API key string. Returns user dict or None."""
    if not api_key:
        return None
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE api_key=?", (api_key,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def update_user(user_id: int, **kwargs) -> None:
    """Update any column(s) on the users table. Allowed: name, tier, logo_base64."""
    allowed = {"name", "tier", "logo_base64"}
    sets = []
    params = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return
    params.append(user_id)
    with _db() as conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", params)


def rotate_api_key(user_id: int) -> str:
    """Generate and store a new API key for the user. Returns the new key."""
    new_key = str(uuid.uuid4()).replace("-", "")
    with _db() as conn:
        conn.execute("UPDATE users SET api_key=? WHERE id=?", (new_key, user_id))
    return new_key


# ── Quota tracking ────────────────────────────────────────────────────────────

def _maybe_reset_quota(user: dict) -> bool:
    """
    If the user's pages_reset_at is in a previous month, reset pages_used to 0.
    Returns True if a reset was performed.
    """
    reset_at_str = user.get("pages_reset_at", "")
    if not reset_at_str:
        return False
    try:
        reset_dt = datetime.fromisoformat(reset_at_str)
        now      = datetime.now(timezone.utc)
        # Reset if we're in a new calendar month
        if now.year > reset_dt.year or now.month > reset_dt.month:
            new_reset = now.replace(day=1, hour=0, minute=0, second=0).isoformat(timespec="seconds")
            with _db() as conn:
                conn.execute(
                    "UPDATE users SET pages_used=0, pages_reset_at=? WHERE id=?",
                    (new_reset, user["id"]),
                )
            return True
    except Exception:
        pass
    return False


def check_crawl_quota(user_id: int, n_pages: int) -> tuple[bool, str]:
    """
    Returns (allowed, message).
    Always allowed for agency tier or unlimited (-1) configs.
    """
    user = get_user_by_id(user_id)
    if user is None:
        return True, ""    # unauthenticated — allow (backward compat)
    _maybe_reset_quota(user)
    user = get_user_by_id(user_id)   # reload after potential reset
    tier   = user.get("tier", "free")
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    limit  = limits["pages_per_month"]
    if limit == -1:
        return True, ""    # unlimited
    used = user.get("pages_used", 0)
    remaining = limit - used
    if remaining <= 0:
        return False, (
            f"Monthly crawl limit reached ({limit} pages for {tier} tier). "
            "Upgrade to Pro for 5,000 pages/month."
        )
    if n_pages > remaining:
        return False, (
            f"Requested {n_pages} pages but only {remaining} remain this month "
            f"({tier} tier: {limit}/mo)."
        )
    return True, ""


def record_pages_crawled(user_id: int, n_pages: int) -> None:
    """Increment pages_used counter for the user."""
    if user_id is None:
        return
    with _db() as conn:
        conn.execute(
            "UPDATE users SET pages_used = pages_used + ? WHERE id=?",
            (n_pages, user_id),
        )
