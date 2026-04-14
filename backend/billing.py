"""
billing.py — Stripe subscription management for CrawlIQ SaaS.

Handles:
  - Stripe Checkout session creation (upgrade flow)
  - Webhook processing (subscription created/updated/deleted)
  - Customer portal session (manage billing)
  - Tier sync from Stripe → users.tier in SQLite

Environment variables
─────────────────────
  STRIPE_SECRET_KEY       — sk_live_... or sk_test_...
  STRIPE_WEBHOOK_SECRET   — whsec_... (from Stripe dashboard)
  STRIPE_PRO_PRICE_ID     — price_... for Pro monthly plan
  STRIPE_AGENCY_PRICE_ID  — price_... for Agency monthly plan
  APP_BASE_URL            — https://yourapp.com (for redirect URLs)

Public API
──────────
  is_configured()                          → bool
  create_checkout_session(user, tier)      → {url, session_id}
  create_portal_session(customer_id)       → {url}
  handle_webhook(payload_bytes, sig_header)→ dict
  get_subscription_status(customer_id)    → dict
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_STRIPE_SECRET      = os.getenv("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET     = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PRO_PRICE_ID       = os.getenv("STRIPE_PRO_PRICE_ID", "")
_AGENCY_PRICE_ID    = os.getenv("STRIPE_AGENCY_PRICE_ID", "")
_APP_BASE_URL       = os.getenv("APP_BASE_URL", "http://localhost:7860")

# Price → tier mapping
_PRICE_TIER: dict[str, str] = {}


def is_configured() -> bool:
    return bool(_STRIPE_SECRET)


def _stripe():
    """Lazy import of stripe SDK."""
    try:
        import stripe as _s
        _s.api_key = _STRIPE_SECRET
        # Build price→tier map once
        if _PRO_PRICE_ID:
            _PRICE_TIER[_PRO_PRICE_ID]    = "pro"
        if _AGENCY_PRICE_ID:
            _PRICE_TIER[_AGENCY_PRICE_ID] = "agency"
        return _s
    except ImportError:
        raise RuntimeError("stripe package not installed — pip install stripe>=7.0.0")


def create_checkout_session(user: dict, tier: str) -> dict:
    """
    Create a Stripe Checkout session for upgrading to `tier`.
    Returns {url, session_id}.
    """
    s = _stripe()
    price_id = _PRO_PRICE_ID if tier == "pro" else _AGENCY_PRICE_ID
    if not price_id:
        raise ValueError(f"STRIPE_{tier.upper()}_PRICE_ID not configured")

    # Get or create Stripe customer
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = s.Customer.create(
            email=user.get("email", ""),
            name=user.get("name", ""),
            metadata={"crawliq_user_id": str(user["id"])},
        )
        customer_id = customer.id
        # Persist customer_id
        try:
            from competitor_db import _connect
            with _connect() as conn:
                conn.execute(
                    "UPDATE users SET stripe_customer_id=? WHERE id=?",
                    (customer_id, user["id"]),
                )
        except Exception as exc:
            logger.warning("Could not save stripe_customer_id: %s", exc)

    session = s.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{_APP_BASE_URL}/?upgrade=success&tier={tier}",
        cancel_url=f"{_APP_BASE_URL}/?upgrade=cancelled",
        metadata={"crawliq_user_id": str(user["id"]), "tier": tier},
    )
    return {"url": session.url, "session_id": session.id}


def create_portal_session(customer_id: str) -> dict:
    """Create a Stripe Customer Portal session for billing management."""
    s = _stripe()
    session = s.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{_APP_BASE_URL}/",
    )
    return {"url": session.url}


def handle_webhook(payload_bytes: bytes, sig_header: str) -> dict:
    """
    Verify and process a Stripe webhook event.
    Returns {"type": event_type, "action": "..."}.
    """
    s = _stripe()
    try:
        event = s.Webhook.construct_event(payload_bytes, sig_header, _WEBHOOK_SECRET)
    except Exception as exc:
        raise ValueError(f"Webhook signature verification failed: {exc}")

    etype = event["type"]
    data  = event["data"]["object"]
    logger.info("Stripe webhook: %s", etype)

    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        _sync_subscription(data)
    elif etype == "customer.subscription.deleted":
        _downgrade_subscription(data)
    elif etype == "checkout.session.completed":
        sub_id = data.get("subscription")
        if sub_id:
            sub = s.Subscription.retrieve(sub_id)
            _sync_subscription(sub)

    return {"type": etype, "processed": True}


def _sync_subscription(sub) -> None:
    """Sync a Stripe subscription to user tier in DB."""
    try:
        customer_id = sub.get("customer") or sub.customer
        status      = sub.get("status") or sub.status        # active / past_due / etc.
        # Determine tier from price
        items = sub.get("items", {}).get("data", []) if isinstance(sub, dict) else sub.items.data
        tier = "free"
        for item in items:
            price_id = (item.get("price", {}).get("id") if isinstance(item, dict)
                        else item.price.id)
            if price_id in _PRICE_TIER:
                tier = _PRICE_TIER[price_id]
                break

        if status not in ("active", "trialing"):
            tier = "free"

        from competitor_db import _connect
        with _connect() as conn:
            conn.execute(
                "UPDATE users SET tier=? WHERE stripe_customer_id=?",
                (tier, customer_id),
            )
        logger.info("Synced tier=%s for customer=%s", tier, customer_id)
    except Exception as exc:
        logger.error("Subscription sync failed: %s", exc)


def _downgrade_subscription(sub) -> None:
    """Downgrade user to free when subscription is cancelled."""
    try:
        customer_id = sub.get("customer") or sub.customer
        from competitor_db import _connect
        with _connect() as conn:
            conn.execute(
                "UPDATE users SET tier='free' WHERE stripe_customer_id=?",
                (customer_id,),
            )
        logger.info("Downgraded customer=%s to free", customer_id)
    except Exception as exc:
        logger.error("Subscription downgrade failed: %s", exc)


def get_subscription_status(customer_id: str) -> dict:
    """Retrieve current subscription status for a customer."""
    if not customer_id:
        return {"tier": "free", "status": "none"}
    try:
        s = _stripe()
        subs = s.Subscription.list(customer=customer_id, limit=1, status="active")
        if not subs.data:
            return {"tier": "free", "status": "none"}
        sub = subs.data[0]
        items = sub.items.data
        tier = "free"
        for item in items:
            pid = item.price.id
            if pid in _PRICE_TIER:
                tier = _PRICE_TIER[pid]
        return {
            "tier":               tier,
            "status":             sub.status,
            "current_period_end": sub.current_period_end,
        }
    except Exception as exc:
        logger.warning("get_subscription_status failed: %s", exc)
        return {"tier": "free", "status": "error"}
