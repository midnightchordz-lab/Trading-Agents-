"""Wallet, pricing, and smart-cache decisions — additive, pure functions.

The pricing model: users top up a balance (in USD) and each priced action
deducts from it. No subscription, no auto-renewal. Re-checking a symbol is
free when nothing has actually changed since the last completed analysis;
it's priced normally the moment the live price has moved meaningfully or
crossed the verdict's own target/stop — the same idea the grounding gate
already uses, applied to a caching decision instead of a validity check.

Real payment processing (Stripe / Apple In-App Purchase / Google Play
Billing) is NOT implemented here — see the integration notes in the
accompanying spec. Balances are credited via a placeholder "manual topup"
endpoint until a real payment processor is wired in with the app owner's
own merchant credentials.
"""
from __future__ import annotations

from typing import Optional

# Priced actions. Each one is roughly 3x the measured LLM cost, so margin
# holds even at high volume (the point of a usage-scaled model instead of
# a flat lifetime or subscription price).
# Priced actions, in USD (the wallet's currency; Razorpay charges in USD too).
# Each one is roughly 3x the measured LLM cost, so margin holds even at high
# volume (the point of a usage-scaled model instead of a flat lifetime or
# subscription price).
CURRENCY = "USD"
CURRENCY_SYMBOL = "$"
def is_launch_free_period(launch_free_until: str, now: "datetime") -> bool:
    """True if `now` is still before the configured launch-free cutoff
    date. Deliberately date-based, not a manual flag someone has to
    remember to flip — a launch promotion that depends on a human
    remembering to change something weeks later, while busy firefighting
    other post-launch issues, is exactly the kind of thing that quietly
    slips. `launch_free_until` is an ISO date string (e.g. "2026-10-16");
    an empty/missing/unparseable value means no free period is configured
    at all, so billing behaves exactly as it always has — this function
    can never accidentally make something free that wasn't explicitly
    configured to be."""
    if not launch_free_until:
        return False
    try:
        from datetime import datetime as _dt
        cutoff = _dt.fromisoformat(launch_free_until)
        if cutoff.tzinfo is None:
            from datetime import timezone as _tz
            cutoff = cutoff.replace(tzinfo=_tz.utc)
        if now.tzinfo is None:
            from datetime import timezone as _tz
            now = now.replace(tzinfo=_tz.utc)
        return now < cutoff
    except (ValueError, TypeError):
        return False


PRICES = {
    "full_analysis": 0.25,   # measured cost ~$0.081
    "compare": 0.39,         # two analyses bundled, cheaper than 2x full_analysis
    "portfolio_optimize": 0.05,  # no new LLM calls (reads cached verdicts only)
}

# Top-up packs a user may buy. Amounts are validated server-side against this
# list — a client can never name its own price.
TOPUP_PACKS = [5.0, 10.0, 25.0]

# Every new account gets this many analyses before the wallet is needed.
FREE_CREDITS_ON_SIGNUP = 10


def should_use_free_credit(free_credits_remaining: Optional[int]) -> bool:
    """Free credits are spent before any money is. Treated as 0 when the
    value is missing or nonsense, so a bad value can never grant runs."""
    try:
        return int(free_credits_remaining or 0) > 0
    except (TypeError, ValueError):
        return False


def is_valid_topup(amount: float) -> bool:
    return any(abs(amount - p) < 0.001 for p in TOPUP_PACKS)


def is_admin_phone(phone: Optional[str], admin_phones: list) -> bool:
    """Admins (the app owner's own accounts) skip billing entirely — no
    charge, no balance gate. Matched on the E.164 phone number the account
    signed in with, compared against the ADMIN_PHONES env list."""
    if not phone:
        return False
    normalized = phone.strip()
    return any(normalized == a.strip() for a in admin_phones if a.strip())

# How far the price can move from the cached verdict's reference price
# before a re-check is considered "something actually changed" and gets
# billed again. Expressed as a fraction (0.015 = 1.5%).
PRICE_MOVE_THRESHOLD = 0.015


def get_price(action: str) -> float:
    return PRICES.get(action, 0.0)


def has_sufficient_balance(balance: float, action: str) -> bool:
    return balance >= get_price(action)


def should_charge_for_recheck(
    cached_verdict: Optional[dict],
    live_price: Optional[float],
    reference_price: Optional[float],
) -> bool:
    """True if a fresh (paid) analysis should run instead of reusing the
    cached one. Charges when:
      - there's no usable cache to reuse (no cached verdict, no live price,
        no reference price to compare against) — fail toward charging,
        never toward silently serving a stale answer for free;
      - the live price has moved more than PRICE_MOVE_THRESHOLD from the
        reference price recorded when the cached verdict was produced;
      - the live price has crossed the cached verdict's own target or stop
        (that verdict is no longer just "not yet realized", it's now wrong
        or already played out).
    """
    if not cached_verdict or live_price is None or not reference_price:
        return True

    try:
        pct_move = abs(live_price - reference_price) / reference_price
    except ZeroDivisionError:
        return True
    if pct_move > PRICE_MOVE_THRESHOLD:
        return True

    decision = cached_verdict.get("decision")
    target = cached_verdict.get("target_price")
    stop = cached_verdict.get("stop_loss")

    if decision == "BUY":
        if target is not None and live_price >= target:
            return True
        if stop is not None and live_price <= stop:
            return True
    elif decision == "SELL":
        if target is not None and live_price <= target:
            return True
        if stop is not None and live_price >= stop:
            return True

    return False


def new_balance_after_charge(balance: float, action: str) -> float:
    """Never lets a balance go negative — caller must check
    has_sufficient_balance() first; this just guards against a
    double-charge race producing a negative number."""
    return max(0.0, round(balance - get_price(action), 4))
