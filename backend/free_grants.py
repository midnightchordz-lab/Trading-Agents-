"""One set of free runs per phone, however many accounts are made on it.

A new account's free allowance is three things: the sign-up credits, the
first Trade Review runs, and (while a launch window is open) the daily
launch-free runs. All three used to be per ACCOUNT, so a fresh email address
on the same phone meant a fresh allowance, every day if you liked.

The phone is now identified by the app's X-Install-Id header: Android's
ANDROID_ID or an iOS Keychain id, both of which survive a reinstall and a
"clear data" (unlike the wallet's random device_id, which is reset by either).
It is stored only as a keyed hash.

A new account ends in one of three states, decided once at sign-up:
  "granted"  — the full allowance.
  "withheld" — none of it: this phone already had a grant, or one of the older
               checks in signup_free_credits said no (deleted account's email,
               disposable domain, per-network or global caps). The account
               signs in and pays like anyone else.
  "deferred" — the request carried no install id: an old app version, the web
               app, or a script calling the API directly. Nothing is granted
               until the account is seen from a phone running the current app,
               which then claims it (claim_deferred) if that phone has not had
               a grant. Without this, anyone could keep farming through an old
               app version or a bare HTTP client.

This stops one person cycling email addresses on one phone. It does not stop
someone with many phones, or a script inventing install ids: the existing
per-network and global caps in signup_free_credits stay as the backstop.
"""
import hashlib
import hmac
import re
from typing import Optional

from fastapi import Request
from pymongo.errors import DuplicateKeyError

import wallet as wal
from core import db, logger, now_iso
from deps import HASH_SECRET, client_ip

# Android ids are 16 hex characters; iOS ids are UUIDs (or the app's own
# fallback); anything else is not something the app sends.
INSTALL_ID_RE = re.compile(r"^(android|ios):[A-Za-z0-9-]{8,64}$")

GRANTED, WITHHELD, DEFERRED = "granted", "withheld", "deferred"


def install_hash_for(request: Optional[Request]) -> Optional[str]:
    """Keyed hash of the request's install id, or None when it has none (or a
    malformed one). The raw id is never stored."""
    if request is None:
        return None
    raw = (request.headers.get("x-install-id") or "").strip()
    if not INSTALL_ID_RE.match(raw):
        return None
    return hmac.new(HASH_SECRET.encode(), f"install:{raw}".encode(), hashlib.sha256).hexdigest()


async def install_already_granted(install_hash: str) -> bool:
    return await db.free_credit_grants.find_one({"install_hash": install_hash}) is not None


async def gate_new_account(granted: int, request: Optional[Request]) -> tuple:
    """Applies the per-phone rule on top of signup_free_credits' answer.
    Returns (credits, state)."""
    if not granted:
        return 0, WITHHELD
    install = install_hash_for(request)
    if install is None:
        logger.info("free runs deferred: sign-up without an install id")
        return 0, DEFERRED
    if await install_already_granted(install):
        logger.info("free runs withheld: this phone already had a grant")
        return 0, WITHHELD
    return granted, GRANTED


def launch_free_allowed(user: Optional[dict]) -> bool:
    """Launch-free runs need a signed-in account that wasn't refused a grant.
    Accounts from before this rule have no flag and keep them."""
    return bool(user) and user.get("launch_free_eligible") is not False


def new_account_fields(credits: int, state: str) -> dict:
    """The free-allowance fields of a new user document. An account without a
    grant also starts with its free Trade Reviews used up and no launch-free
    runs, so all three allowances follow the same decision."""
    fields = {"free_credits_remaining": credits, "free_grant_state": state}
    if state != GRANTED:
        fields["trade_review_free_used"] = wal.TRADE_REVIEW_FREE_RUNS
        fields["launch_free_eligible"] = False
    return fields


async def claim_deferred(user: Optional[dict], request: Optional[Request]) -> Optional[dict]:
    """Grants a deferred account its free runs the first time it is seen from
    a phone running the current app, if that phone has not had a grant.
    Returns the (possibly updated) user."""
    if not user or user.get("free_grant_state") != DEFERRED:
        return user
    install = install_hash_for(request)
    if install is None:
        return user
    if await install_already_granted(install):
        await db.users.update_one({"id": user["id"], "free_grant_state": DEFERRED},
                                  {"$set": {"free_grant_state": WITHHELD}})
        return {**user, "free_grant_state": WITHHELD}
    # The per-network and global caps still apply. The identifier checks were
    # already passed at sign-up (a failure there is "withheld", not deferred).
    from routes.auth_routes import signup_free_credits
    credits = await signup_free_credits(None, request, None)
    if not credits:
        return user
    # The grant row goes in first: install_hash is unique, so two accounts on
    # one phone claiming at the same moment cannot both get one.
    row = {"user_id": user["id"], "device_id": None, "install_hash": install,
           "ip": client_ip(request), "created_at": now_iso(), "deferred": True}
    try:
        res = await db.free_credit_grants.insert_one(row)
    except DuplicateKeyError:
        await db.users.update_one({"id": user["id"], "free_grant_state": DEFERRED},
                                  {"$set": {"free_grant_state": WITHHELD}})
        return {**user, "free_grant_state": WITHHELD}
    claimed = await db.users.update_one(
        {"id": user["id"], "free_grant_state": DEFERRED},
        {"$set": {"free_grant_state": GRANTED, "free_credits_remaining": credits,
                  "trade_review_free_used": 0, "launch_free_eligible": True}},
    )
    if claimed.modified_count != 1:
        # Another request for this account claimed it first; give the phone's
        # grant back so it isn't spent twice.
        await db.free_credit_grants.delete_one({"_id": res.inserted_id})
        return user
    logger.info("deferred free runs claimed from the phone app")
    return {**user, "free_grant_state": GRANTED, "free_credits_remaining": credits,
            "trade_review_free_used": 0, "launch_free_eligible": True}


async def record_grant(user_id: str, device_id: Optional[str], request: Optional[Request]) -> bool:
    """Logs a sign-up grant. Returns False when another account on the same
    phone recorded one in the same instant (the unique install_hash index
    refuses the second); the caller then takes the new account's grant back."""
    row = {"user_id": user_id, "device_id": device_id, "ip": client_ip(request), "created_at": now_iso()}
    install = install_hash_for(request)
    if install:
        row["install_hash"] = install
    try:
        await db.free_credit_grants.insert_one(row)
        return True
    except DuplicateKeyError:
        await db.users.update_one({"id": user_id}, {"$set": new_account_fields(0, WITHHELD)})
        logger.info("free runs withheld: a second account on this phone signed up at the same moment")
        return False
