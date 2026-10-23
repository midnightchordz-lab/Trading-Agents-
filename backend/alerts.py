"""Abuse alerting: tell the owner when a whole-deployment budget is hit.

The global budgets (SMS, email OTP, free credits) already log an ERROR when
they trip. A log line only helps someone who happens to be reading logs, and
the whole point of those budgets is that they fire during an attack — at 3am,
while an attacker is spending real money. So they also send one email.

THREE RULES, because an alerting system that misbehaves is worse than none:

1. It NEVER affects the request. Every send happens in a background task and
   every failure is swallowed. A budget breach already withholds something;
   it must not also turn into a 500 because an email provider was slow.
2. It is RATE LIMITED to one email per alert kind per hour, using the same
   counter the rest of the app uses. An attack trips a budget on every request,
   and 10,000 identical emails is its own denial of service — of the owner's
   inbox, and of the email provider's reputation.
3. NO PII. Counts, caps and the alert kind only — never an email, phone or IP.
   The same rule the logs follow.
"""
import asyncio
import os
from typing import Optional

import mailer
import rate_limit as rl
from core import logger

# Where alerts go. Defaults to the first admin identifier if it is an email,
# so a deployment that already has an owner configured gets alerts without any
# new setup; unset and non-email means alerting is simply off.
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()

# One email per kind per hour. An attack trips a budget on every request.
ALERT_MAX_PER_HOUR = rl.env_int("ALERT_MAX_PER_HOUR", 1)


def alert_recipient() -> Optional[str]:
    if ALERT_EMAIL and "@" in ALERT_EMAIL:
        return ALERT_EMAIL
    from deps import ADMIN_IDENTIFIERS
    for identifier in ADMIN_IDENTIFIERS:
        if "@" in identifier:
            return identifier
    return None


def alert_html(title: str, lines: list[str]) -> str:
    rows = "".join(f"<p style='margin:4px 0;color:#333'>{line}</p>" for line in lines)
    return (
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px'>"
        f"<h2 style='color:#b00020;margin:0 0 12px'>{title}</h2>"
        f"{rows}"
        "<p style='margin:16px 0 4px;color:#666;font-size:13px'>Sent by TradingAgents because a "
        "whole-deployment safety budget was reached. No action is required for the app to keep "
        "working — the budget withholds the thing being abused and everything else carries on.</p>"
        "<p style='margin:4px 0;color:#666;font-size:13px'>At most one email per hour per alert "
        "type.</p></div>"
    )


async def send_alert(kind: str, title: str, lines: list[str]) -> None:
    """Fire-and-forget. Never raises, never blocks the caller's request."""
    try:
        recipient = alert_recipient()
        if not recipient or not mailer.email_configured():
            return
        allowed, _ = await rl.hit("alert", kind, ALERT_MAX_PER_HOUR, 3600)
        if not allowed:
            return
        await mailer.send_email(to=recipient, subject=f"[TradingAgents] {title}",
                                html=alert_html(title, lines))
        logger.info(f"abuse alert sent: {kind}")
    except Exception as e:
        # Alerting must never be able to break the thing it is watching.
        logger.warning(f"abuse alert failed to send ({kind}): {e}")


def raise_alert(kind: str, title: str, lines: list[str]) -> None:
    """Schedule an alert from a request handler without awaiting it."""
    try:
        asyncio.create_task(send_alert(kind, title, lines))
    except RuntimeError:
        # No running loop (a script or a unit test). Nothing to alert from.
        pass
