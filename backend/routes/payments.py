"""Wallet balance and every way it can be topped up.

Razorpay payment links (Android/Web) and Apple In-App Purchase via RevenueCat
(iOS) both end up in the same idempotent ledger, so a balance can only ever
grow once per real payment, and only after the store itself confirms it.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import auth as au
import iap
import razorpay_pay as rzp
import wallet as wal
from core import db, logger, now_iso
from deps import (
    LAUNCH_FREE_UNTIL,
    WALLET_ENFORCEMENT_ENABLED,
    get_free_credits_remaining,
    get_wallet_balance,
    is_admin,
    launch_free_daily_remaining,
    public_base,
    require_user,
    wallet_key_for,
)

api_router = APIRouter(prefix="/api")

# Razorpay events that mean money went back to the customer. `refund.created`
# and `refund.processed` describe the same refund; both are handled and the
# second is a no-op, because either one can arrive first and a refund that
# never reaches "processed" was still money we no longer have.
REFUND_EVENTS = ("refund.created", "refund.processed")
# A chargeback: the bank pulls the money back, with no refund entity involved.
# Treated as a full clawback at the "lost" stage, when the outcome is final.
DISPUTE_EVENTS = ("payment.dispute.lost",)


class WalletTopup(BaseModel):
    device_id: Optional[str] = None  # ignored for signed-in users (account-keyed wallet)
    amount: float  # must match one of wallet.TOPUP_PACKS
    # Razorpay's payment links require BOTH an email and a phone number, but an
    # account only has whichever one was used to sign in. The app asks for the
    # missing one once and it's stored on the account from then on.
    email: Optional[str] = None
    phone: Optional[str] = None
    # Only meaningful the FIRST time a wallet ever tops up — see
    # create_topup_order for the locking logic. Ignored on every request
    # after that; an account's currency never changes once set.
    currency: Optional[str] = None
    # Device region (ISO 3166-1 alpha-2), used only to pick the currency for a
    # wallet that hasn't locked one yet.
    region: Optional[str] = None


def suggest_currency(user: Optional[dict], region: Optional[str]) -> str:
    """Which currency a wallet with no locked currency should use: INR for
    Indian users, USD for everyone else. This is a payment-method decision, not
    a pricing preference — UPI/GPay can only settle INR, so a USD payment link
    physically cannot offer them. The signal is the verified sign-in phone when
    there is one (a +91 number is unambiguous), otherwise the device region.
    Never consulted once a currency is locked."""
    for field in ("phone", "billing_phone"):
        if ((user or {}).get(field) or "").startswith("+91"):
            return "INR"
    if (region or "").strip().upper() == "IN":
        return "INR"
    return "USD"


@api_router.get("/pay/health")
async def pay_health():
    """Is this deployment able to take money? No session, no secrets.

    Added because diagnosing the deployed build took half an hour of guessing:
    every endpoint that could answer "are payments configured here" needed a
    session token, and a production token can't be minted from outside. One
    unauthenticated GET now answers it for any environment, and exposes
    nothing a user couldn't infer from the top-up screen itself."""
    return {
        "razorpay": rzp.payments_configured(),
        "razorpay_mode": ("live" if rzp.KEY_ID.startswith("rzp_live_") else "test") if rzp.KEY_ID else None,
        "razorpay_webhook_secret_set": bool(rzp.WEBHOOK_SECRET),
        # null until the startup probe has run; False means this container's
        # key pair was REJECTED by Razorpay (almost always a deployed image
        # carrying rotated-out keys), so every top-up here will 502.
        "razorpay_credentials_ok": rzp.CREDENTIALS_OK,
        # Public key id's last 4 chars — tells "this environment has the new
        # keys" from "this one is stale" without exposing anything secret.
        "razorpay_key_tail": rzp.key_tail(),
        # True when RAZORPAY_KEY_ID doesn't even look like a key id (the key
        # secret pasted into the wrong variable). Distinguishes "wrong value
        # in the wrong field" from "expired keys" — both otherwise present as
        # Razorpay "Authentication failed".
        "razorpay_key_id_malformed": rzp.key_id_malformed(),
        "apple_iap": iap.configured(),
        "currencies": list(wal.SUPPORTED_CURRENCIES),
        "wallet_enforcement": WALLET_ENFORCEMENT_ENABLED,
    }


@api_router.get("/wallet/balance")
async def wallet_balance(
    device_id: Optional[str] = None,
    region: Optional[str] = None,
    user: Optional[dict] = Depends(require_user),
):
    key = wallet_key_for(user, device_id)
    if not key:
        raise HTTPException(status_code=400, detail="device_id is required")
    balance = await get_wallet_balance(key)
    admin = is_admin(user)
    free_credits = await get_free_credits_remaining(user)
    launch_free = wal.is_launch_free_period(LAUNCH_FREE_UNTIL, datetime.now(timezone.utc))
    daily_left = await launch_free_daily_remaining(key) if launch_free else None
    wallet_doc = await db.wallets.find_one({"device_id": key})
    stored_currency = (wallet_doc or {}).get("currency")
    # Before the first top-up the app is shown the currency it will actually
    # get, so the packs on screen are the packs that will be charged.
    currency = stored_currency or suggest_currency(user, region)
    return {
        "device_id": key,
        "balance": round(balance, 2),
        "currency": currency,
        "symbol": wal.currency_symbol_for(currency),
        "prices": wal.prices_for(currency),
        "packs": wal.topup_packs_for(currency),
        # False until the account's first top-up locks a currency. The app uses
        # this to decide whether to ask once — and only once — which currency
        # to use; `currency_options` is what it renders, so the client needs no
        # currency knowledge of its own (no hardcoded amounts or symbols).
        "currency_locked": bool(stored_currency),
        "currency_options": [
            {
                "code": code,
                "symbol": wal.currency_symbol_for(code),
                "packs": wal.topup_packs_for(code),
                "prices": wal.prices_for(code),
            }
            for code in wal.SUPPORTED_CURRENCIES
        ],
        "free_credits_remaining": free_credits,
        # Admins are never billed, and neither is anyone with free credits
        # left — so the app shows them no balance gate. Nor is anyone with
        # launch-free allowance left today, otherwise the app would grey out
        # the analyze button for a drained wallet the backend would run free.
        "enforcement_enabled": (
            WALLET_ENFORCEMENT_ENABLED
            and not admin
            and not (launch_free and (daily_left or 0) > 0)
            and free_credits <= 0
        ),
        "is_admin": admin,
        "payments_live": rzp.payments_configured(),
        # iOS must buy through Apple IAP (guideline 3.1.1); the app shows the
        # StoreKit packs instead of the Razorpay ones when this is true.
        "iap_enabled": iap.configured(),
        "launch_free_active": launch_free,
        # Powers the on-screen countdown; `remaining` is null outside the window.
        "launch_free_daily_remaining": daily_left,
        "launch_free_daily_cap": wal.LAUNCH_FREE_DAILY_CAP,
    }


async def credit_wallet_once(payment_id: str, order: dict) -> bool:
    """The only place a balance is ever credited from a payment. The unique
    index on payment_id is what makes a double credit impossible, whichever
    of the callback / webhook arrives first (or twice)."""
    wallet_key = order["wallet_key"]
    # The currency the customer was actually charged in. Previously the ledger
    # recorded a hardcoded USD for every row, so an INR top-up was written down
    # as dollars — wrong in the books, and useless for the refund path that now
    # reads these rows back.
    paid_currency = order.get("currency") or wal.CURRENCY
    wallet_doc = await db.wallets.find_one({"device_id": wallet_key})
    locked_currency = (wallet_doc or {}).get("currency")
    if locked_currency and locked_currency != paid_currency:
        # A balance is a bare number in one currency, so adding a ₹ amount to a
        # $ balance (or the reverse) hands over roughly 80x or 1/80th of what
        # was paid. The currency lock is claimed atomically at order time so
        # this should be unreachable; if it ever happens, refuse and flag it for
        # a human rather than guessing a conversion rate.
        logger.error(
            f"currency mismatch: payment {payment_id} is {paid_currency} but wallet "
            f"{wallet_key} is locked to {locked_currency} — not credited"
        )
        await db.payments.update_one(
            {"razorpay_order_id": order["razorpay_order_id"]},
            {"$set": {"status": "currency_mismatch", "payment_id": payment_id,
                      "needs_review": True, "updated_at": now_iso()}},
        )
        return False
    try:
        await db.wallet_ledger.insert_one({
            "payment_id": payment_id,
            "order_id": order["razorpay_order_id"],
            "wallet_key": wallet_key,
            "amount": order["amount"],
            "currency": paid_currency,
            "source": "razorpay",
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        return False
    try:
        await db.wallets.update_one(
            {"device_id": wallet_key},
            {"$inc": {"balance": order["amount"]},
             "$set": {"device_id": wallet_key, "updated_at": now_iso()}},
            upsert=True,
        )
    except Exception:
        # Compensating rollback. The ledger row is the claim that makes a
        # double credit impossible — but if the balance was never actually
        # incremented, that same row would block EVERY future retry through
        # the unique index, permanently losing a real payment. Undo the claim
        # so the system is left cleanly retryable.
        await db.wallet_ledger.delete_one({"payment_id": payment_id})
        raise
    # A top-up into a wallet that had never chosen a currency locks it, for the
    # same reason the IAP path does: money in an unlabelled balance is what the
    # next reader would have to guess about.
    await db.wallets.update_one(
        {"device_id": wallet_key, "currency": {"$in": [None, ""]}},
        {"$set": {"currency": paid_currency}},
    )
    await db.payments.update_one(
        {"razorpay_order_id": order["razorpay_order_id"]},
        {"$set": {"status": "captured", "payment_id": payment_id, "updated_at": now_iso()}},
    )
    logger.info(f"wallet credited {paid_currency} {order['amount']} for {wallet_key} ({payment_id})")
    return True


async def claw_back_once(ledger_id: str, wallet_key: str, amount: float, currency: str,
                         source: str, reason: str, **extra) -> dict:
    """Takes credit back off a wallet exactly once, for a refund or chargeback.

    Mirrors the crediting path deliberately: the same `wallet_ledger`
    collection and the same unique index on `payment_id` make a double
    clawback impossible, whichever event (refund.created / refund.processed, or
    a re-delivered RevenueCat CANCELLATION) arrives first or twice. The row is
    written with a NEGATIVE amount, so the ledger still sums to the balance.

    **A balance can be lower than the refund** — the analyses were already run
    and the LLM already paid for. We take what is there and record the rest as
    a shortfall rather than driving the wallet negative, which would leave a
    user unable to use the app until they topped up someone else's refund.
    """
    try:
        await db.wallet_ledger.insert_one({
            "payment_id": ledger_id,
            "wallet_key": wallet_key,
            "amount": -abs(amount),
            "currency": currency,
            "source": source,
            "kind": "clawback",
            "reason": reason,
            "created_at": now_iso(),
            **extra,
        })
    except DuplicateKeyError:
        return {"clawed_back": False, "duplicate": True}

    debit = abs(amount)
    try:
        # One atomic decrement when the balance covers it...
        after = await db.wallets.find_one_and_update(
            {"device_id": wallet_key, "balance": {"$gte": debit}},
            {"$inc": {"balance": -debit}, "$set": {"updated_at": now_iso()}},
            return_document=ReturnDocument.AFTER,
        )
        if after is not None:
            recovered, shortfall = debit, 0.0
        else:
            # ...otherwise take everything that's left, in one operation, and
            # read what that was from the pre-update document.
            before = await db.wallets.find_one_and_update(
                {"device_id": wallet_key},
                {"$set": {"balance": 0.0, "updated_at": now_iso()}},
                return_document=ReturnDocument.BEFORE,
            )
            recovered = max(0.0, round((before or {}).get("balance", 0.0), 4))
            shortfall = round(debit - recovered, 4)
    except Exception:
        # Same compensating rollback as the credit path: without it the ledger
        # row claims this refund was handled while the balance was untouched,
        # and the unique index then blocks every retry from fixing it.
        await db.wallet_ledger.delete_one({"payment_id": ledger_id})
        raise

    await db.wallet_ledger.update_one(
        {"payment_id": ledger_id},
        {"$set": {"amount": -recovered, "requested": -debit, "shortfall": shortfall}},
    )
    logger.info(
        f"wallet clawback {currency} {recovered} from {wallet_key} ({ledger_id}, {reason})"
        + (f" — {shortfall} could not be recovered, balance was already spent" if shortfall else "")
    )
    return {"clawed_back": True, "recovered": recovered, "shortfall": shortfall}


async def handle_refund_event(refund_entity: dict, event_name: str) -> dict:
    """Razorpay refund / chargeback -> clawback.

    Keyed on the REFUND id, not the payment id: a payment can be refunded in
    parts, and each part is its own clawback. `refund.created` and
    `refund.processed` both describe the same refund, so the second one is a
    no-op via the unique index."""
    refund_id = refund_entity.get("id") if isinstance(refund_entity.get("id"), str) else None
    payment_id = refund_entity.get("payment_id") if isinstance(refund_entity.get("payment_id"), str) else None
    if not refund_id or not payment_id:
        return {"ok": True, "ignored": "refund event without ids"}

    record = await db.payments.find_one({"payment_id": payment_id})
    if not record:
        # Never credited here (or not ours), so there is nothing to take back.
        logger.info(f"refund {refund_id} for unknown payment {payment_id} — nothing to claw back")
        return {"ok": True, "ignored": "unknown payment"}

    amount_paise = refund_entity.get("amount")
    if not isinstance(amount_paise, (int, float)):
        return {"ok": True, "ignored": "refund event without an amount"}
    amount = round(float(amount_paise) / 100.0, 2)

    refund_currency = (refund_entity.get("currency") or record.get("currency") or "USD").upper()
    if refund_currency != (record.get("currency") or "USD").upper():
        # Refusing rather than converting, for the same reason crediting does.
        logger.error(f"refund {refund_id} is {refund_currency} but payment was {record.get('currency')}")
        return {"ok": True, "ignored": "refund currency mismatch"}

    result = await claw_back_once(
        f"refund:{refund_id}", record["wallet_key"], amount, refund_currency,
        source="razorpay", reason=event_name, refund_of=payment_id,
    )
    if result.get("clawed_back"):
        # Partial refunds leave the payment partly valid, so the status says
        # which it was rather than flattening both to "refunded".
        fully = abs(amount - float(record.get("amount", 0))) < 0.01
        await db.payments.update_one(
            {"payment_id": payment_id},
            {"$set": {"status": "refunded" if fully else "partially_refunded",
                      "refunded_amount": amount, "updated_at": now_iso()}},
        )
    return {"ok": True, **result}


async def settle_payment(order: dict, payment_id: str) -> str:
    """Confirms with Razorpay that the payment really is captured, then
    credits. Returns the payment status."""
    payment = await rzp.fetch_payment(payment_id)
    if payment.get("order_id") != order["razorpay_order_id"]:
        raise HTTPException(status_code=400, detail="Order mismatch")
    if payment.get("amount") != int(round(order["amount"] * 100)):
        raise HTTPException(status_code=400, detail="Amount mismatch")
    status = payment.get("status", "unknown")
    if status == "captured":
        await credit_wallet_once(payment_id, order)
    else:
        await db.payments.update_one(
            {"razorpay_order_id": order["razorpay_order_id"]},
            {"$set": {"status": status, "updated_at": now_iso()}},
        )
    return status


async def bind_order_id(record: dict, payment_id: str) -> dict:
    """A Payment Link's underlying Razorpay order only exists once the customer
    actually starts paying, so the stored record is created without one and the
    id is bound here — before the normal order-based verification runs."""
    payment = await rzp.fetch_payment(payment_id)
    order_id = payment.get("order_id")
    if order_id and record.get("razorpay_order_id") != order_id:
        await db.payments.update_one(
            {"reference_id": record["reference_id"]},
            {"$set": {"razorpay_order_id": order_id, "updated_at": now_iso()}},
        )
        record = {**record, "razorpay_order_id": order_id}
    return record


def sanitize_customer_name(raw: Optional[str]) -> str:
    """Razorpay requires the customer name to be letters and spaces, 3-50
    characters. A phone-OTP signup has no name at all and safely falls back to
    the generic one; a Google/Apple name is whatever the provider returned, so
    an emoji or a two-letter nickname would be sent verbatim.

    Honest note: the server logs have NEVER shown Razorpay rejecting this
    field, so this is insurance rather than the fix for the reported 502 — the
    real causes are in the log and are handled below."""
    cleaned = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z ]", "", raw or "")).strip()
    return cleaned[:50] if len(cleaned) >= 3 else "TradingAgents user"


def contact_rejection(phone: Optional[str]) -> Optional[str]:
    """Why Razorpay would refuse this number, or None if it looks real.

    THIS is what the logs actually show: "Recurring digits in customer contact
    are disallowed", four times, on the checkout path. It only ever hits new
    accounts, because only a user with no phone on their account gets asked for
    one — and a made-up 9999999999 is what people type into a field they did
    not expect. Razorpay then rejects the whole payment-link request, which
    surfaced as a 502 that said nothing about the number they just entered.
    Checking here turns that into an inline field error before any API call.

    Deliberately narrow: only patterns no real mobile number has. Anything
    Razorpay dislikes for a subtler reason still gets its message mapped back
    to the same field (see create_topup_order), so a miss here is not a 502."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    national = digits[-10:] if len(digits) > 10 else digits
    if len(national) < 7:
        return "That number looks too short — enter your mobile number with country code."
    if len(set(national)) <= 2:
        # 9999999999, 1111111111, 1212121212 — Razorpay's "recurring digits".
        return "Payments won't accept a made-up number. Enter your real mobile number."
    ascending = "01234567890123456789"
    if national in ascending or national in ascending[::-1]:
        return "Payments won't accept a made-up number. Enter your real mobile number."
    return None


# Razorpay error text that is about what the CUSTOMER typed, not about us. Each
# maps to the field the app should re-ask for, so the user sees the problem next
# to the input instead of a 502 they can do nothing about.
CUSTOMER_FIELD_ERRORS = (
    ("contact", ("contact", "phone", "recurring digits")),
    ("email", ("email",)),
    ("name", ("name",)),
)


def customer_field_for(description: str) -> Optional[str]:
    text = (description or "").lower()
    for field, needles in CUSTOMER_FIELD_ERRORS:
        if any(needle in text for needle in needles):
            return field
    return None


async def resolve_payment_customer(user: Optional[dict], body: "WalletTopup") -> tuple[dict, list]:
    """Razorpay rejects a payment link unless it carries a customer email AND
    contact number. Users sign in with only one of the two, so this fills in
    what's known, accepts whatever the app just asked for, remembers it on the
    account, and reports what's still missing.

    SECURITY: anything supplied here is UNVERIFIED, so it is stored under
    separate `billing_*` fields and never written over the `email` / `phone`
    set by the OTP / Google / Apple sign-in flows. Those verified fields are
    what the admin allowlist matches on — letting this endpoint overwrite them
    would have let any user type the owner's phone number and inherit the
    billing bypass."""
    email = (user or {}).get("email") or (user or {}).get("billing_email")
    phone = (user or {}).get("phone") or (user or {}).get("billing_phone")
    for raw in (body.email, body.phone):
        if not raw:
            continue
        kind, normalized = au.normalize_identifier(raw)
        if kind == "email":
            email = normalized
        elif kind == "phone":
            phone = normalized
    missing = [name for name, value in (("email", email), ("phone", phone)) if not value]
    if not missing and user:
        updates = {}
        if not user.get("email") and email != user.get("billing_email"):
            updates["billing_email"] = email
        if not user.get("phone") and phone != user.get("billing_phone"):
            updates["billing_phone"] = phone
        if updates:
            await db.users.update_one({"id": user["id"]}, {"$set": updates})
    customer = {"name": sanitize_customer_name((user or {}).get("name")), "email": email, "contact": phone}
    return customer, missing


@api_router.post("/pay/order")
async def create_topup_order(body: WalletTopup, request: Request, user: Optional[dict] = Depends(require_user)):
    """Creates a Razorpay Payment Link for one of the fixed top-up packs and
    returns its hosted checkout URL. The amount is validated here — never taken
    on trust."""
    if not rzp.payments_configured():
        raise HTTPException(status_code=503, detail="Payments aren't set up yet")
    key = wallet_key_for(user, body.device_id)
    if not key:
        raise HTTPException(status_code=400, detail="device_id is required")

    # Currency is chosen once and locked to the account forever — never
    # re-asked, never switched per top-up. A wallet that already has a
    # REAL BALANCE but no currency yet predates this feature and can only
    # have been earned in USD; lock it explicitly rather than let a later
    # currency choice silently reinterpret an existing balance as a
    # different currency (a $12.50 balance must never become ₹12.50).
    existing_wallet = await db.wallets.find_one({"device_id": key})
    if existing_wallet and existing_wallet.get("currency"):
        currency = existing_wallet["currency"]
    else:
        # A pre-existing balance was necessarily earned in USD (the only
        # currency there was), so it labels itself rather than being offered a
        # choice that would silently revalue it.
        desired = (
            "USD" if (existing_wallet and existing_wallet.get("balance", 0) > 0)
            else (body.currency if body.currency in wal.SUPPORTED_CURRENCIES
                  else suggest_currency(user, body.region))
        )
        # ATOMIC claim, not read-then-write. Two first top-ups arriving
        # together used to both see an unlocked wallet and both write — one in
        # INR, one in USD — and the last writer won. The link already handed to
        # the other customer then settled into a wallet locked to the OTHER
        # currency, so a ₹99 payment could add 99 to a USD balance (≈$99 of
        # analyses for ₹99) or $5 could add 5 to an INR balance. Only the
        # request that actually sets the field proceeds with its own choice;
        # every other one adopts what is already locked, so the link is always
        # created in the currency the wallet really has.
        await db.wallets.update_one(
            {"device_id": key},
            {"$setOnInsert": {"device_id": key, "balance": 0.0}},
            upsert=True,
        )
        claimed = await db.wallets.find_one_and_update(
            {"device_id": key, "currency": {"$in": [None, ""]}},
            {"$set": {"currency": desired}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed:
            currency = claimed["currency"]
        else:
            # Someone else locked it microseconds ago. Theirs stands.
            locked = await db.wallets.find_one({"device_id": key})
            currency = (locked or {}).get("currency") or desired
            if currency != desired:
                logger.info(f"currency already locked to {currency} for {key}; adopting it over {desired}")

    if not wal.is_valid_topup(body.amount, currency):
        raise HTTPException(
            status_code=400,
            detail=f"Choose one of the top-up packs: {', '.join(str(int(p)) for p in wal.topup_packs_for(currency))}",
        )

    customer, missing = await resolve_payment_customer(user, body)
    if missing:
        # Machine-readable so the app can ask for exactly what's missing.
        raise HTTPException(status_code=400, detail=f"contact_required:{','.join(missing)}")

    rejection = contact_rejection(customer.get("contact"))
    if rejection:
        # Before any API call: the user is told about their own input, next to
        # the input, instead of being handed Razorpay's rejection as a 502.
        raise HTTPException(status_code=400, detail=f"contact_invalid:phone:{rejection}")

    # Reuse the link this user already has open for the same pack instead of
    # asking Razorpay for another one. Tapping ₹99 twice — or backing out of
    # the browser and tapping again, which is what people actually do — used to
    # create a second link every time. That is how the most common failure in
    # the log happened: "Too many requests", 38 of 48 recorded 502s, Razorpay
    # rate-limiting link creation. Reuse also makes the second tap instant,
    # because it skips the network call entirely.
    open_link = await db.payments.find_one(
        {"wallet_key": key, "status": "created", "amount": body.amount,
         "currency": currency, "expires_at": {"$gt": now_iso()}},
        sort=[("created_at", -1)],
    )
    if open_link and open_link.get("short_url"):
        logger.info(f"reusing open payment link {open_link['razorpay_payment_link_id']} for {key}")
        return {
            "order_id": open_link["razorpay_payment_link_id"],
            "amount": open_link["amount"],
            "currency": open_link["currency"],
            "symbol": wal.currency_symbol_for(open_link["currency"]),
            "checkout_url": open_link["short_url"],
            "key_id": rzp.KEY_ID,
            "reused": True,
        }

    reference_id = f"wallet_{uuid.uuid4().hex[:20]}"
    try:
        link = await rzp.create_payment_link(
            amount=body.amount,
            currency=currency,
            reference_id=reference_id,
            notes={"wallet_key": key, "purpose": "wallet_topup"},
            callback_url=f"{public_base(request)}/api/pay/callback",
            description=f"{wal.currency_symbol_for(currency)}{body.amount:.0f} wallet top-up",
            customer=customer,
        )
    except rzp.RazorpayError as e:
        logger.error(f"razorpay payment link creation failed [{e.code}]: {e.description}")
        if rzp.is_rate_limited(e):
            # Nothing is wrong with the request, so don't imply there is.
            raise HTTPException(
                status_code=503,
                detail="busy:Payments are busy for a moment — tap again in a few seconds.",
            )
        field = customer_field_for(e.description)
        if field:
            # Razorpay refused something the customer typed. Ask for that field
            # again with Razorpay's reason, rather than failing the whole flow.
            raise HTTPException(status_code=400, detail=f"contact_invalid:{field}:{e.description}")
        if "authentication failed" in (e.description or "").lower():
            # Nothing the customer can fix and nothing about their input: this
            # container's Razorpay keys are wrong (in practice a deployed image
            # holding a rotated-out pair). Say so plainly instead of showing
            # "Razorpay: Authentication failed", which reads like the user's
            # own payment was declined.
            rzp.CREDENTIALS_OK = False
            logger.error("RAZORPAY CREDENTIALS REJECTED on /pay/order — this deployment's keys are stale")
            raise HTTPException(
                status_code=503,
                detail="Payments are temporarily unavailable — nothing was charged. Please try again later.",
            )
        raise HTTPException(status_code=502, detail=f"Razorpay: {e.description}")
    except Exception as e:
        logger.error(f"razorpay payment link creation failed: {e}")
        raise HTTPException(status_code=502, detail="Couldn't reach Razorpay — try again")

    await db.payments.insert_one({
        "razorpay_payment_link_id": link["id"],
        # A link's underlying order only exists once the customer starts paying.
        # The field is omitted (not null) so the unique sparse index ignores it.
        **({"razorpay_order_id": link["order_id"]} if link.get("order_id") else {}),
        "reference_id": reference_id,
        "wallet_key": key,
        "amount": body.amount,
        "currency": currency,
        "status": "created",
        "receipt": reference_id,
        "short_url": link["short_url"],
        "created_at": now_iso(),
        # Stored so reuse can only ever hand back a link Razorpay still
        # accepts; it mirrors the expire_by sent when the link was created.
        "expires_at": rzp.link_expiry_iso(link),
        "updated_at": now_iso(),
    })
    return {
        # The app polls /pay/status with whatever id it gets back.
        "order_id": link["id"],
        "amount": body.amount,
        "currency": currency,
        "checkout_url": link["short_url"],
    }


@api_router.api_route("/pay/callback", methods=["POST", "GET"], response_class=HTMLResponse)
async def pay_callback(request: Request):
    """Razorpay redirects the customer back here after the hosted payment page.
    Payment Links arrive as a GET with the razorpay_payment_link_* params; the
    older self-hosted checkout arrived as a form POST. Cancels, failures and
    some bank / UPI redirect chains arrive with no fields at all, so every
    field is read defensively (a strict Form(...) signature 422s the user
    mid-payment). Whatever arrives is a hint only: the signature is checked and
    the payment re-fetched from Razorpay before anything is credited."""
    fields: dict = {}
    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
            fields = dict(await request.form())
        elif "application/json" in ctype:
            try:
                fields = await request.json()
            except Exception:
                fields = {}
    # A JSON body can put a dict or list where a string is expected — e.g.
    # {"razorpay_order_id": {"$ne": ""}} — which MongoDB would read as a query
    # OPERATOR rather than a literal, matching an arbitrary payment record.
    # Every value taken from the body must be a plain string before it goes
    # anywhere near a lookup. (Query params are always strings already.)
    fields = {k: v for k, v in fields.items() if isinstance(v, str)} if isinstance(fields, dict) else {}
    q = request.query_params
    link_id = fields.get("razorpay_payment_link_id") or q.get("razorpay_payment_link_id")
    if link_id:
        return await link_callback(fields, q, link_id)

    razorpay_payment_id = fields.get("razorpay_payment_id") or q.get("razorpay_payment_id")
    razorpay_order_id = (
        fields.get("razorpay_order_id") or q.get("razorpay_order_id") or q.get("order_id")
    )
    razorpay_signature = fields.get("razorpay_signature") or q.get("razorpay_signature")

    order = await db.payments.find_one({"razorpay_order_id": razorpay_order_id}) if razorpay_order_id else None

    # Cancelled / failed / bodyless redirect — nothing to verify, nothing charged.
    if not razorpay_payment_id or not razorpay_signature:
        logger.info(f"payment callback without success fields for order {razorpay_order_id}")
        if order:
            await db.payments.update_one(
                {"razorpay_order_id": order["razorpay_order_id"], "status": {"$ne": "captured"}},
                {"$set": {
                    "status": "failed",
                    "failure": fields.get("error[description]") or q.get("error[description]"),
                    "updated_at": now_iso(),
                }},
            )
        return HTMLResponse(rzp.result_html("Payment wasn't completed — nothing was charged.", ok=False))

    if not order or not rzp.verify_checkout_signature(
        order["razorpay_order_id"], razorpay_payment_id, razorpay_signature
    ):
        logger.warning(f"invalid payment signature for order {razorpay_order_id}")
        return HTMLResponse(rzp.result_html("We couldn't verify that payment.", ok=False), status_code=400)
    try:
        status = await settle_payment(order, razorpay_payment_id)
    except Exception as e:
        logger.error(f"payment settle failed: {e}")
        return HTMLResponse(rzp.result_html("Payment received — we're still confirming it.", ok=True))
    if status == "captured":
        return HTMLResponse(rzp.result_html(
            f"Added {wal.currency_symbol_for(order.get('currency') or 'USD')}{order['amount']:.0f} to your wallet.", ok=True))
    return HTMLResponse(rzp.result_html("That payment didn't go through — nothing was charged.", ok=False))


async def link_callback(fields: dict, q, link_id: str) -> HTMLResponse:
    """Payment Link return leg. Signature message differs from checkout's:
    link_id|reference_id|status|payment_id."""
    def val(name: str) -> str:
        return fields.get(name) or q.get(name) or ""

    payment_id = val("razorpay_payment_id")
    reference_id = val("razorpay_payment_link_reference_id")
    link_status = val("razorpay_payment_link_status")
    signature = val("razorpay_signature")

    record = await db.payments.find_one({"razorpay_payment_link_id": link_id})
    if not payment_id or not signature:
        logger.info(f"payment link callback without success fields for {link_id}")
        if record:
            await db.payments.update_one(
                {"reference_id": record["reference_id"], "status": {"$ne": "captured"}},
                {"$set": {"status": "failed", "updated_at": now_iso()}},
            )
        return HTMLResponse(rzp.result_html("Payment wasn't completed — nothing was charged.", ok=False))

    if not record or record["reference_id"] != reference_id or not rzp.verify_link_signature(
        link_id=link_id, reference_id=reference_id, status=link_status,
        payment_id=payment_id, supplied=signature,
    ):
        logger.warning(f"invalid payment link signature for {link_id}")
        return HTMLResponse(rzp.result_html("We couldn't verify that payment.", ok=False), status_code=400)

    try:
        record = await bind_order_id(record, payment_id)
        status = await settle_payment(record, payment_id)
    except Exception as e:
        logger.error(f"payment link settle failed: {e}")
        return HTMLResponse(rzp.result_html("Payment received — we're still confirming it.", ok=True))
    if status == "captured":
        return HTMLResponse(rzp.result_html(
            f"Added {wal.currency_symbol_for(record.get('currency') or 'USD')}{record['amount']:.0f} to your wallet.", ok=True))
    return HTMLResponse(rzp.result_html("That payment didn't go through — nothing was charged.", ok=False))


async def mark_webhook_event_processed(event_id: str) -> None:
    """Records a webhook event as handled, only once processing has actually
    completed. If two deliveries of the same event somehow race to here the
    unique index still allows at most one insert — but that index is a
    backstop, not the duplicate guard; the up-front find_one in pay_webhook
    is."""
    try:
        await db.webhook_events.insert_one({"event_id": event_id, "received_at": now_iso()})
    except DuplicateKeyError:
        pass


@api_router.post("/pay/webhook")
async def pay_webhook(request: Request):
    """Razorpay's server-to-server confirmation. Verified against the raw body
    and de-duplicated by event id."""
    raw = await request.body()
    if not rzp.verify_webhook_signature(raw, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    # A duplicate CHECK, not a claim. Recording the event up front meant a
    # crash mid-processing left it marked "seen" forever, so Razorpay's retry
    # was answered "duplicate" and the payment was never processed at all. The
    # event is recorded only at the end of each path, once handling has
    # actually completed without raising.
    event_id = request.headers.get("X-Razorpay-Event-Id", str(uuid.uuid4()))
    if await db.webhook_events.find_one({"event_id": event_id}):
        return {"ok": True, "duplicate": True}

    event = json.loads(raw or b"{}")
    if not isinstance(event, dict):
        return {"ok": True}
    name = event.get("event")
    payload = event.get("payload", {}) or {}
    entity = (payload.get("payment", {}) or {}).get("entity", {})
    # Coerced to plain strings before they go near a lookup, for the same
    # reason as /pay/callback: a dict here would be read by MongoDB as a query
    # OPERATOR and could match an arbitrary payment record. Unlike the callback
    # this body is signature-verified against the raw bytes, so it can't be
    # forged without the webhook secret — this is defence in depth, not a hole
    # anyone can reach today.
    payment_id = entity.get("id") if isinstance(entity.get("id"), str) else None
    order_id = entity.get("order_id") if isinstance(entity.get("order_id"), str) else None

    # Payment Links carry their own entity and are the authoritative event for
    # the top-up flow — the underlying order id may not be on our record yet.
    link_entity = (payload.get("payment_link", {}) or {}).get("entity", {})
    link_id = link_entity.get("id") if isinstance(link_entity.get("id"), str) else None

    # Refunds and chargebacks are keyed on the refund/payment entity, never on
    # the payment link, so they are handled before the link branch returns.
    if name in REFUND_EVENTS:
        refund_entity = (payload.get("refund", {}) or {}).get("entity", {})
        if not isinstance(refund_entity, dict):
            refund_entity = {}
        result = await handle_refund_event(refund_entity, name)
        await mark_webhook_event_processed(event_id)
        return result
    if name in DISPUTE_EVENTS and payment_id:
        # A chargeback is money taken back by the bank, with no refund entity.
        record = await db.payments.find_one({"payment_id": payment_id})
        if record:
            result = await claw_back_once(
                f"chargeback:{payment_id}", record["wallet_key"], float(record.get("amount", 0)),
                (record.get("currency") or "USD").upper(), source="razorpay",
                reason=name, refund_of=payment_id,
            )
            if result.get("clawed_back"):
                await db.payments.update_one(
                    {"payment_id": payment_id},
                    {"$set": {"status": "chargeback", "updated_at": now_iso()}},
                )
        await mark_webhook_event_processed(event_id)
        return {"ok": True}

    if link_id:
        record = await db.payments.find_one({"razorpay_payment_link_id": link_id})
        if not record:
            await mark_webhook_event_processed(event_id)
            return {"ok": True}
        if name == "payment_link.paid" and payment_id:
            expected = int(round(record["amount"] * 100))
            if link_entity.get("amount_paid") == expected and link_entity.get("currency") == record["currency"]:
                record = await bind_order_id(record, payment_id)
                await settle_payment(record, payment_id)
        elif name in ("payment_link.expired", "payment_link.cancelled"):
            await db.payments.update_one(
                {"reference_id": record["reference_id"], "status": {"$ne": "captured"}},
                {"$set": {"status": "failed", "updated_at": now_iso()}},
            )
        await mark_webhook_event_processed(event_id)
        return {"ok": True}

    if not order_id:
        await mark_webhook_event_processed(event_id)
        return {"ok": True}

    order = await db.payments.find_one({"razorpay_order_id": order_id})
    if not order:
        await mark_webhook_event_processed(event_id)
        return {"ok": True}

    if name == "payment.captured" and payment_id:
        if entity.get("amount") == int(round(order["amount"] * 100)):
            await credit_wallet_once(payment_id, order)
    elif name == "payment.failed":
        await db.payments.update_one(
            {"razorpay_order_id": order_id},
            {"$set": {"status": "failed", "failure": entity.get("error_description"), "updated_at": now_iso()}},
        )
    await mark_webhook_event_processed(event_id)
    return {"ok": True}


@api_router.get("/pay/status/{order_id}")
async def pay_status(order_id: str, device_id: Optional[str] = None, user: Optional[dict] = Depends(require_user)):
    """Polled by the app after checkout closes. If the browser redirect never
    made it back (WebView dismissed, network dropped), this asks Razorpay
    directly and credits then — so a paid top-up is never lost. Accepts either
    a payment link id (plink_…) or a legacy order id."""
    order = await db.payments.find_one(
        {"$or": [{"razorpay_payment_link_id": order_id}, {"razorpay_order_id": order_id}]}
    )
    if not order:
        raise HTTPException(status_code=404, detail="Unknown order")
    # Ownership is required of EVERY caller. This used to pass None for the
    # device id, so an anonymous caller's key was always None and the check
    # below was skipped for having nothing to compare — anyone with an order
    # id could read its status and the wallet balance behind it.
    key = wallet_key_for(user, device_id)
    if not key or order["wallet_key"] != key:
        raise HTTPException(status_code=403, detail="Not your order")

    if order["status"] != "captured" and rzp.payments_configured():
        try:
            if order.get("razorpay_payment_link_id"):
                link = await rzp.fetch_payment_link(order["razorpay_payment_link_id"])
                expected = int(round(order["amount"] * 100))
                paid = (
                    link.get("status") == "paid"
                    and link.get("amount_paid") == expected
                    and link.get("currency") == order["currency"]
                )
                payment_id = next(
                    (p.get("payment_id") for p in reversed(link.get("payments") or []) if p.get("payment_id")),
                    None,
                )
                if paid and payment_id:
                    order = await bind_order_id(order, payment_id)
                    await settle_payment(order, payment_id)
                elif link.get("status") in ("expired", "cancelled"):
                    await db.payments.update_one(
                        {"reference_id": order["reference_id"], "status": {"$ne": "captured"}},
                        {"$set": {"status": "failed", "updated_at": now_iso()}},
                    )
            elif order.get("razorpay_order_id"):
                found = await rzp.razorpay_request("GET", f"/orders/{order['razorpay_order_id']}/payments")
                for p in found.get("items", []):
                    if p.get("status") == "captured":
                        await settle_payment(order, p["id"])
                        break
            order = await db.payments.find_one(
                {"$or": [{"razorpay_payment_link_id": order_id}, {"razorpay_order_id": order_id}]}
            )
        except Exception as e:
            logger.warning(f"payment status refresh failed: {e}")

    balance = await get_wallet_balance(order["wallet_key"])
    return {"order_id": order_id, "status": order["status"], "balance": round(balance, 2)}


# --- Apple In-App Purchase top-ups (iOS only), via RevenueCat. See iap.py for
# why this exists alongside Razorpay. ---
@api_router.get("/pay/iap/config")
async def iap_config(currency: str = "USD"):
    """What the iOS app needs to open StoreKit: the RevenueCat public SDK key
    and the product ids to offer. Served from here rather than bundled so the
    key can be rotated without shipping a new App Store build.

    `currency` is the account's own locked currency, passed by the app so the
    pack buttons show ₹99/₹199/₹499 rather than $5/$10/$25 for an INR wallet.
    It only affects the displayed amounts — what actually gets credited is
    resolved server-side from the stored wallet currency in the webhook."""
    resolved = currency if currency in wal.SUPPORTED_CURRENCIES else "USD"
    return {
        "enabled": iap.configured(),
        "ios_api_key": iap.IOS_PUBLIC_KEY if iap.configured() else "",
        "packs": iap.packs(resolved),
        "currency": resolved,
        "symbol": wal.currency_symbol_for(resolved),
    }


async def credit_iap_once(transaction_id: str, wallet_key: str, amount: float, product_id: str, currency: str, environment: str) -> bool:
    """Credits an Apple purchase exactly once. Shares wallet_ledger (and its
    unique index) with Razorpay so there is still only ONE place in the system
    a balance can grow, whichever store the money came from. Keyed on Apple's
    own transaction id, which is stable across RevenueCat's at-least-once
    webhook retries and across a re-delivered duplicate event.

    `amount` and `currency` are already resolved to the wallet's OWN locked
    currency by the caller, so this only ever adds a number to a balance kept
    in the same units — never a USD face value into an INR-locked balance."""
    ledger_id = f"apple:{transaction_id}"
    try:
        await db.wallet_ledger.insert_one({
            "payment_id": ledger_id,
            "order_id": None,
            "wallet_key": wallet_key,
            "amount": amount,
            "currency": currency,
            "source": "apple_iap",
            "product_id": product_id,
            # Recorded so sandbox-funded balance stays auditable and reversible,
            # and so the sandbox cap in iap_webhook can count it.
            "environment": environment,
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        return False
    try:
        await db.wallets.update_one(
            {"device_id": wallet_key},
            {"$inc": {"balance": amount},
             "$set": {"device_id": wallet_key, "updated_at": now_iso()}},
            upsert=True,
        )
    except Exception:
        # Same compensating rollback as credit_wallet_once, for the same
        # reason: without it, a failure here leaves a ledger row claiming
        # this transaction was credited when the balance was never actually
        # touched, and the unique index on payment_id then blocks every
        # future retry from ever fixing it.
        await db.wallet_ledger.delete_one({"payment_id": ledger_id})
        raise
    # An Apple purchase into a wallet that had never chosen a currency locks it
    # too, so the account's currency is always explicit once it holds real
    # money — rather than leaving an unlabelled balance that a later Razorpay
    # top-up would have to infer.
    await db.wallets.update_one(
        {"device_id": wallet_key, "currency": {"$exists": False}},
        {"$set": {"currency": currency}},
    )
    logger.info(f"wallet credited {currency} {amount} for {wallet_key} (apple iap {transaction_id})")
    return True


@api_router.post("/pay/iap/webhook")
async def iap_webhook(request: Request, authorization: Optional[str] = Header(None)):
    """The ONLY thing that credits a balance from an iOS purchase. The app's
    own purchase callback is never trusted: it can be interrupted, replayed or
    faked, and it is not Apple confirming the money moved."""
    if not iap.verify_webhook_auth(authorization):
        # Also covers "not configured at all": with no secret set, nothing is
        # accepted, rather than everything.
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event") or {}
    event_id = str(event.get("id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event id")

    action, detail = iap.classify_event(event)
    if action == "ignore":
        return {"ok": True, "ignored": detail["reason"]}
    if action == "invalid":
        logger.warning(f"iap webhook rejected: {detail['reason']} (event {event_id})")
        raise HTTPException(status_code=400, detail=detail["reason"])

    if action == "clawback":
        # Apple refunded the purchase. How much to take back is read from OUR
        # OWN ledger row rather than recomputed from the product table: the
        # pack price may have changed since, and the only honest amount to
        # reverse is the one that was actually added.
        credited = await db.wallet_ledger.find_one({"payment_id": f"apple:{detail['transaction_id']}"})
        if not credited:
            # Never credited here (sandbox cap, unknown account, or a purchase
            # from before this integration) — nothing to reverse.
            return {"ok": True, "ignored": "no credit on record for this transaction"}
        result = await claw_back_once(
            f"refund:apple:{detail['transaction_id']}", credited["wallet_key"],
            float(credited.get("amount", 0)), credited.get("currency") or "USD",
            source="apple_iap", reason=str(event.get("type")),
            refund_of=f"apple:{detail['transaction_id']}",
        )
        await db.webhook_events.update_one(
            {"event_id": f"rc:{event_id}"},
            {"$set": {"event_id": f"rc:{event_id}", "source": "revenuecat",
                      "transaction_id": detail["transaction_id"], "received_at": now_iso()}},
            upsert=True,
        )
        return {"ok": True, **result}

    # A balance is a bare number in the account's OWN locked currency, so how
    # much to add depends on that currency and not on what Apple charged or
    # what the event says. An INR-locked wallet buying `credits_5` gets ₹99,
    # never 5 — blindly adding a USD face value to an INR balance would hand
    # the user roughly 1/80th of what they paid.
    wallet_doc = await db.wallets.find_one({"device_id": detail["wallet_key"]})
    wallet_currency = (wallet_doc or {}).get("currency") or "USD"
    amount = iap.credit_amount_for(detail["product_id"], wallet_currency)
    event_currency = (event.get("currency") or "").upper()
    if event_currency and event_currency != wallet_currency:
        # Expected and harmless (Apple bills in the buyer's storefront
        # currency), but worth surfacing: a persistent mismatch means the App
        # Store price tier for that storefront has drifted from what we credit.
        logger.info(
            f"iap currency mismatch: apple charged {event_currency}, "
            f"crediting {wallet_currency} {amount} to {detail['wallet_key']}"
        )

    # Apple's sandbox completes purchases for free, so an unlimited sandbox
    # credit path is a free top-up button. It can't be refused outright —
    # App Store reviewers purchase in sandbox and reject apps that don't
    # deliver — so it credits a few times per account and then stops. See
    # iap.SANDBOX_CREDIT_LIMIT.
    environment = detail["environment"]
    if environment != iap.PRODUCTION_ENVIRONMENT:
        prior_sandbox = await db.wallet_ledger.count_documents({
            "wallet_key": detail["wallet_key"],
            "source": "apple_iap",
            "environment": {"$ne": iap.PRODUCTION_ENVIRONMENT},
        })
        if prior_sandbox >= iap.SANDBOX_CREDIT_LIMIT:
            logger.warning(
                f"iap sandbox credit refused for {detail['wallet_key']}: "
                f"{prior_sandbox} non-production credits already granted"
            )
            return {"ok": True, "ignored": "sandbox credit limit reached"}
        logger.warning(
            f"iap crediting a {environment} (non-production) purchase for "
            f"{detail['wallet_key']} — {prior_sandbox + 1}/{iap.SANDBOX_CREDIT_LIMIT}"
        )

    credited = await credit_iap_once(
        detail["transaction_id"], detail["wallet_key"], amount, detail["product_id"],
        wallet_currency, environment,
    )
    await db.webhook_events.update_one(
        {"event_id": f"rc:{event_id}"},
        {"$set": {"event_id": f"rc:{event_id}", "source": "revenuecat",
                  "transaction_id": detail["transaction_id"], "received_at": now_iso()}},
        upsert=True,
    )
    return {"ok": True, "credited": credited}


