"""Apple In-App Purchase top-ups, via RevenueCat.

Why this exists alongside Razorpay: App Store Review guideline 3.1.1 requires
that anything consumed inside an iOS app — here, wallet credit that buys AI
analyses — be sold through Apple's own In-App Purchase. A Razorpay checkout on
iOS is a guaranteed rejection. Android and Web keep using Razorpay, where no
such rule applies.

Trust boundary, deliberately identical in spirit to razorpay_pay.py: the app
never tells the backend how much to credit. StoreKit takes the money, Apple
confirms it to RevenueCat, and RevenueCat posts a signed webhook to us. Only
that webhook moves a balance, and only by looking the immutable Apple product
id up in PRODUCT_CREDIT below.

Deliveries are at-least-once, so crediting is idempotent on Apple's own
transaction id (see credit_iap_once in server.py).
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# App Store Connect product id -> USD added to the wallet. Must stay in sync
# with the Consumable products configured in App Store Connect and imported
# into RevenueCat; the ids are the contract between the three.
PRODUCT_CREDIT = {
    "credits_5": 5.0,
    "credits_10": 10.0,
    "credits_25": 25.0,
}

# The same three packs expressed in INR, mirroring wallet.TOPUP_PACKS_INR.
#
# Why this table has to exist: a wallet balance is a bare number in the
# account's own locked currency. An INR-locked account that buys `credits_5`
# from the App Store must be credited ₹99, NOT 5 — adding the USD face value
# to an INR balance would hand the user about 1/80th of what they paid, which
# is exactly the silent-revaluation bug the two-currency spec exists to
# prevent, only in the opposite (and worse) direction. Apple charges the user
# in their own storefront currency from its India price tier, so `credits_5`
# means "the small pack" in whichever currency the account lives in — the same
# meaning it has on the Razorpay side.
PRODUCT_CREDIT_INR = {
    "credits_5": 99.0,
    "credits_10": 199.0,
    "credits_25": 499.0,
}

# Public SDK key — safe to ship to the app. Served to the client from the
# backend rather than baked into the bundle so it can be rotated without a
# new App Store build.
IOS_PUBLIC_KEY = os.environ.get("REVENUECAT_IOS_KEY", "")
# The "Authorization header value" set on the webhook in the RevenueCat
# dashboard. Without it the endpoint accepts nothing at all — an unauthenticated
# webhook here would let anyone mint wallet balance.
WEBHOOK_AUTH = os.environ.get("REVENUECAT_WEBHOOK_AUTH", "")

# Consumables arrive as NON_RENEWING_PURCHASE. Subscription events must never
# credit the wallet (they'd credit again on every renewal).
CREDITED_EVENT_TYPE = "NON_RENEWING_PURCHASE"
APPLE_STORES = ("APP_STORE", "MAC_APP_STORE")


def configured() -> bool:
    """True once the iOS purchase path is usable end to end. Both halves are
    required: without the key the app can't open StoreKit, and without the
    webhook secret a purchase would take money and never credit."""
    return bool(IOS_PUBLIC_KEY and WEBHOOK_AUTH)


def verify_webhook_auth(supplied: Optional[str]) -> bool:
    """Constant-time compare against the dashboard-configured header value."""
    if not WEBHOOK_AUTH:
        return False
    return hmac.compare_digest(WEBHOOK_AUTH, supplied or "")


def packs(currency: str = "USD") -> list:
    """Product ids + amounts, cheapest first, for the iOS top-up UI. Amounts
    are in the account's locked currency so the buttons read ₹99 / ₹199 / ₹499
    for an INR wallet and $5 / $10 / $25 for a USD one."""
    table = PRODUCT_CREDIT_INR if currency == "INR" else PRODUCT_CREDIT
    return [
        {"product_id": pid, "amount": amount}
        for pid, amount in sorted(table.items(), key=lambda kv: kv[1])
    ]


def credit_amount_for(product_id: str, wallet_currency: str = "USD") -> Optional[float]:
    """How much to add to a balance for one Apple purchase, in the units that
    balance is actually kept in. Resolved from the immutable product id and the
    account's own locked currency — never from anything in the webhook body."""
    table = PRODUCT_CREDIT_INR if wallet_currency == "INR" else PRODUCT_CREDIT
    return table.get(product_id)


def classify_event(event: dict) -> tuple:
    """Pure decision about one RevenueCat event.

    Returns (action, detail) where action is:
      - "credit": detail carries wallet_key, amount, transaction_id, product_id
      - "ignore": not ours to act on (a renewal, an Android purchase, a
        transfer...). Must still be answered 200, or RevenueCat retries it
        forever.
      - "invalid": shaped like a purchase we should have handled but isn't
        usable (unknown product, anonymous user, no transaction id). Answered
        400 so it shows up as a failure in the dashboard instead of silently
        dropping a paid purchase.
    """
    if not isinstance(event, dict):
        return "invalid", {"reason": "malformed event"}
    if event.get("type") != CREDITED_EVENT_TYPE:
        return "ignore", {"reason": f"event type {event.get('type')}"}
    if event.get("store") not in APPLE_STORES:
        return "ignore", {"reason": f"store {event.get('store')}"}

    product_id = event.get("product_id")
    amount = PRODUCT_CREDIT.get(product_id)
    app_user_id = str(event.get("app_user_id") or "")
    transaction_id = str(event.get("transaction_id") or "")
    if amount is None:
        return "invalid", {"reason": f"unknown product {product_id}"}
    if not app_user_id.startswith("user:") or len(app_user_id) <= len("user:"):
        # The app configures RevenueCat with `user:<account id>` only after
        # sign-in, so an anonymous id means we cannot know whose wallet this is.
        return "invalid", {"reason": "purchase not tied to an account"}
    if not transaction_id:
        return "invalid", {"reason": "missing transaction id"}

    return "credit", {
        "wallet_key": app_user_id,
        "amount": amount,
        "product_id": product_id,
        "transaction_id": transaction_id,
        "environment": event.get("environment") or "",
    }
