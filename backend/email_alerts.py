"""
email_alerts.py — Async email notification system for CrawlIQ.

Sends alerts for:
  - SERP rank drops (> threshold positions)
  - New critical SEO issues found after a crawl
  - Weekly digest of tracked keyword positions

Transport
─────────
Uses aiosmtplib (async SMTP) with TLS.
Falls back to logging if SMTP not configured (dev mode).

Environment variables
─────────────────────
  SMTP_HOST       — SMTP server hostname (e.g. smtp.gmail.com)
  SMTP_PORT       — Port (default 587 for STARTTLS)
  SMTP_USER       — Login username / sender address
  SMTP_PASS       — Login password or App Password
  ALERT_FROM      — Displayed "From" name+address (defaults to SMTP_USER)
  SMTP_USE_TLS    — "1" for SSL on connect (port 465), "0" for STARTTLS (default)

Public API
──────────
  async send_rank_drop_alert(to_email, domain, drops)
  async send_issue_alert(to_email, domain, issues)
  async send_weekly_digest(to_email, domain, rankings)
  is_configured() → bool
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SMTP_HOST     = os.getenv("SMTP_HOST", "")
_SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER     = os.getenv("SMTP_USER", "")
_SMTP_PASS     = os.getenv("SMTP_PASS", "")
_ALERT_FROM    = os.getenv("ALERT_FROM", _SMTP_USER)
_SMTP_USE_TLS  = os.getenv("SMTP_USE_TLS", "0") == "1"

try:
    import aiosmtplib as _aiosmtp
    _SMTP_LIB = True
except ImportError:
    _SMTP_LIB = False


def is_configured() -> bool:
    """Returns True if SMTP credentials are set."""
    return bool(_SMTP_HOST and _SMTP_USER and _SMTP_PASS)


async def _send(to_email: str, subject: str, body_html: str) -> bool:
    """
    Internal send helper. Returns True on success, False on failure.
    Falls back to logger.info if SMTP not configured (dev mode).
    """
    if not is_configured():
        logger.info("Email alert (no SMTP configured): to=%s | %s", to_email, subject)
        return False
    if not _SMTP_LIB:
        logger.warning("aiosmtplib not installed — cannot send email. pip install aiosmtplib")
        return False

    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = _ALERT_FROM or _SMTP_USER
    msg["To"]      = to_email
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        if _SMTP_USE_TLS:
            await _aiosmtp.send(
                msg,
                hostname=_SMTP_HOST,
                port=_SMTP_PORT,
                username=_SMTP_USER,
                password=_SMTP_PASS,
                use_tls=True,
            )
        else:
            await _aiosmtp.send(
                msg,
                hostname=_SMTP_HOST,
                port=_SMTP_PORT,
                username=_SMTP_USER,
                password=_SMTP_PASS,
                start_tls=True,
            )
        logger.info("Email sent: to=%s subject=%r", to_email, subject)
        return True
    except Exception as exc:
        logger.error("Email send failed: to=%s error=%s", to_email, exc)
        return False


# ── HTML email template ───────────────────────────────────────────────────────

_BASE = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{{font-family:system-ui,sans-serif;background:#0F1117;color:#E5E7EB;margin:0;padding:20px}}
  .card{{background:#1A1D2E;border:1px solid #2D3048;border-radius:8px;padding:20px;max-width:600px;margin:0 auto}}
  .logo{{font-size:18px;font-weight:800;color:#6366F1;margin-bottom:16px}}
  .tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700}}
  .red{{color:#EF4444}} .green{{color:#10B981}} .yellow{{color:#F59E0B}} .cyan{{color:#22D3EE}}
  table{{width:100%;border-collapse:collapse;margin-top:12px;font-size:12px}}
  th{{background:#0F1117;color:#9CA3AF;padding:6px 10px;text-align:left}}
  td{{padding:6px 10px;border-bottom:1px solid #2D3048}}
  .footer{{font-size:10px;color:#6B7280;margin-top:16px;text-align:center}}
  a{{color:#6366F1}}
</style></head>
<body><div class="card">
  <div class="logo">CrawlIQ</div>
  {content}
  <div class="footer">Manage alerts in your <a href="#">CrawlIQ dashboard</a> · Unsubscribe</div>
</div></body></html>
"""


async def send_rank_drop_alert(
    to_email: str,
    domain:   str,
    drops:    list[dict],   # [{keyword, old_pos, new_pos, delta}]
) -> bool:
    """
    Send an alert when tracked keywords drop in SERP position.
    drops: list of {keyword, old_pos, new_pos, delta (positive = drop)}
    """
    rows = "".join(
        f"<tr>"
        f"<td>{d['keyword']}</td>"
        f"<td class='red'>#{d['new_pos']}</td>"
        f"<td style='color:#9CA3AF'>was #{d['old_pos']}</td>"
        f"<td class='red'>▼ {d['delta']}</td>"
        f"</tr>"
        for d in drops
    )
    content = f"""
    <h2 style='color:#EF4444;margin:0 0 4px'>⚠ Rank Drop Alert — {domain}</h2>
    <p style='color:#9CA3AF;font-size:13px;margin:0 0 12px'>
      {len(drops)} keyword(s) dropped in position since the last check.
    </p>
    <table>
      <thead><tr><th>Keyword</th><th>New</th><th>Previous</th><th>Change</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """
    subject = f"⚠ [{domain}] {len(drops)} keyword(s) dropped — CrawlIQ Alert"
    return await _send(to_email, subject, _BASE.format(content=content))


async def send_issue_alert(
    to_email: str,
    domain:   str,
    new_issues: list[dict],  # [{url, issue, priority}]
) -> bool:
    """
    Send an alert when a new crawl finds critical issues not seen before.
    """
    high   = [i for i in new_issues if i.get("priority") == "High"]
    medium = [i for i in new_issues if i.get("priority") == "Medium"]

    def rows(items, color):
        return "".join(
            f"<tr><td style='color:{color}'>{i['issue']}</td><td style='color:#9CA3AF;font-size:11px'>{i['url'][:60]}</td></tr>"
            for i in items[:10]
        )

    content = f"""
    <h2 style='color:#F59E0B;margin:0 0 4px'>🔍 New SEO Issues — {domain}</h2>
    <p style='color:#9CA3AF;font-size:13px;margin:0 0 12px'>
      Latest crawl found {len(new_issues)} new issue(s):
      <span class='red'>{len(high)} high</span>,
      <span class='yellow'>{len(medium)} medium</span>.
    </p>
    <table>
      <thead><tr><th>Issue</th><th>URL</th></tr></thead>
      <tbody>
        {rows(high, '#EF4444')}
        {rows(medium, '#F59E0B')}
      </tbody>
    </table>
    {"<p style='color:#9CA3AF;font-size:11px'>...and more. Open dashboard to see all.</p>" if len(new_issues) > 10 else ""}
    """
    subject = f"🔍 [{domain}] {len(new_issues)} new SEO issues — CrawlIQ"
    return await _send(to_email, subject, _BASE.format(content=content))


async def send_weekly_digest(
    to_email: str,
    domain:   str,
    rankings: list[dict],  # [{keyword, position, in_top_10, in_top_30}]
) -> bool:
    """Send a weekly digest of current tracked keyword positions."""
    top10  = [r for r in rankings if r.get("in_top_10")]
    top30  = [r for r in rankings if r.get("in_top_30") and not r.get("in_top_10")]
    other  = [r for r in rankings if not r.get("in_top_30")]

    def pos_row(r, color):
        pos = f"#{r['position']}" if r.get("position") else "—"
        return f"<tr><td>{r['keyword']}</td><td style='color:{color};font-weight:700'>{pos}</td></tr>"

    rows = (
        "".join(pos_row(r, "#10B981") for r in top10[:5]) +
        "".join(pos_row(r, "#F59E0B") for r in top30[:5]) +
        "".join(pos_row(r, "#EF4444") for r in other[:5])
    )

    content = f"""
    <h2 style='color:#6366F1;margin:0 0 4px'>📊 Weekly SERP Digest — {domain}</h2>
    <p style='color:#9CA3AF;font-size:13px;margin:0 0 12px'>
      Tracking {len(rankings)} keywords ·
      <span class='green'>{len(top10)} in top 10</span> ·
      <span class='yellow'>{len(top30)} in top 30</span> ·
      <span class='red'>{len(other)} not ranking</span>
    </p>
    <table>
      <thead><tr><th>Keyword</th><th>Position</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    {"<p style='color:#9CA3AF;font-size:11px'>Showing top 15 of " + str(len(rankings)) + " keywords.</p>" if len(rankings) > 15 else ""}
    """
    subject = f"📊 [{domain}] Weekly SERP digest — CrawlIQ"
    return await _send(to_email, subject, _BASE.format(content=content))
