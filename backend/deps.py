"""Session dependencies and the per-account wallet reads they guard.

Split out of server.py because both the payment routes and the analyze route
need the same answers — who is calling, which wallet is theirs, are they an
admin, how much free allowance is left — and neither should own them.
"""
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException, Request

import auth as au
import wallet as wal
from core import db



# --- Auth config + session dependencies. Defined here (ahead of the wallet
# and analyze endpoints) because those endpoints depend on them. The auth
# endpoints themselves live further down. ---
AUTH_REQUIRED_ENABLED = os.environ.get("AUTH_REQUIRED_ENABLED", "false").lower() == "true"
AUTH_DEBUG_RETURN_OTP = os.environ.get("AUTH_DEBUG_RETURN_OTP", "false").lower() == "true"  # DEV ONLY
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET or len(JWT_SECRET) < 32 or JWT_SECRET == "dev-only-change-me":
    # Fail closed, and do it unconditionally rather than only when auth is
    # enforced: tokens are issued (and owner_hash_for keyed) regardless of
    # that flag, so a missing or guessable secret means anyone can forge a
    # session for any account — including an admin one. A deploy that loses
    # its env file must refuse to start rather than quietly run forgeable.
    raise RuntimeError(
        "JWT_SECRET must be set to a real, random value of at least 32 characters. "
        "A missing, short or default secret lets anyone forge a valid session token — "
        "refusing to start."
    )
# Comma-separated phone/email allowlist — NOT hardcoded in source. Ships
# with one default so the requested super-user works immediately; change
# or extend via the real env var in your deployment, not by editing this line.
ADMIN_IDENTIFIERS = au.parse_admin_identifiers(os.environ.get("ADMIN_IDENTIFIERS", "+918446307145"))
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")  # unused: Google runs through Emergent managed auth
APPLE_SERVICES_ID = os.environ.get("APPLE_SERVICES_ID", "")


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency for endpoints that always require a valid session."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    payload = au.decode_session_token(token, JWT_SECRET)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = await db.users.find_one({"id": payload["sub"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def require_admin(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency for admin-only endpoints. Requires a valid
    session AND that the account's phone/email is in ADMIN_IDENTIFIERS.
    Intentionally unused in this pass — it exists for a future,
    narrowly-scoped admin endpoint that exposes no individual user data."""
    user = await get_current_user(authorization)
    if not au.is_admin(user, ADMIN_IDENTIFIERS):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Session dependency for the priced/user-specific endpoints (analyze,
    wallet). A token is always honoured when present; it is *mandatory* only
    when AUTH_REQUIRED_ENABLED is on, so the flag stays a single switch."""
    if not authorization:
        if AUTH_REQUIRED_ENABLED:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return None
    return await get_current_user(authorization)


def wallet_key_for(user: Optional[dict], device_id: Optional[str]) -> Optional[str]:
    """Signed-in users get an account-keyed wallet so the balance follows them
    across devices; anonymous callers fall back to their device id."""
    if user:
        return f"user:{user['id']}"
    return device_id


ADMIN_PHONES = [p for p in os.environ.get("ADMIN_PHONES", "").split(",") if p.strip()]


def is_admin(user: Optional[dict]) -> bool:
    """Admin allowlist now lives in ADMIN_IDENTIFIERS (phone OR email,
    normalized); ADMIN_PHONES is still honoured so an existing deployment's
    env keeps working."""
    return au.is_admin(user, ADMIN_IDENTIFIERS) or (
        bool(user) and wal.is_admin_phone(user.get("phone"), ADMIN_PHONES)
    )


# --- Wallet / usage-based pricing (additive; OFF by default — see WALLET_ENFORCEMENT_ENABLED) ---
WALLET_ENFORCEMENT_ENABLED = os.environ.get("WALLET_ENFORCEMENT_ENABLED", "false").lower() == "true"
# Bump this whenever the privacy notice / data practices materially change
# — a stored consent only counts for the version it was actually given
# against. DPDP (India) requires informed, specific consent; a user who
# agreed to an older notice hasn't agreed to a materially different one.
CONSENT_VERSION = "1.0"
# Launch promotion: everyone bypasses billing until this date, automatically
# — no manual flag to remember to flip weeks later. Empty by default (no
# free period unless explicitly configured). ISO date, e.g. "2026-10-16".
LAUNCH_FREE_UNTIL = os.environ.get("LAUNCH_FREE_UNTIL", "")
# Used to build the Razorpay checkout/callback URLs, which must be absolute
# and publicly reachable over HTTPS.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def public_base(request: Request) -> str:
    """Absolute origin used for the Razorpay checkout + callback URLs. Derived
    from the incoming request (honouring the ingress' forwarded headers) so
    preview and production each point back at themselves — a stale
    PUBLIC_BASE_URL baked into a deployed image would otherwise send paying
    customers to the wrong host, where their order id doesn't exist. The env
    var stays as a fallback for local/CLI use."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}".rstrip("/") if host else PUBLIC_BASE_URL

async def get_free_credits_remaining(user: Optional[dict]) -> int:
    """Accounts created before free credits existed are backfilled on first
    read, so nobody is worse off than a brand-new signup."""
    if not user:
        return 0
    if "free_credits_remaining" not in user:
        await db.users.update_one(
            {"id": user["id"], "free_credits_remaining": {"$exists": False}},
            {"$set": {"free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP}},
        )
        return wal.FREE_CREDITS_ON_SIGNUP
    try:
        return max(0, int(user.get("free_credits_remaining") or 0))
    except (TypeError, ValueError):
        return 0


async def consume_free_credit(user: dict) -> bool:
    """Atomically spends one free credit. The `$gt: 0` guard is what stops
    two concurrent analyses from spending the same last credit twice."""
    res = await db.users.update_one(
        {"id": user["id"], "free_credits_remaining": {"$gt": 0}},
        {"$inc": {"free_credits_remaining": -1}},
    )
    return res.modified_count == 1


async def get_wallet_balance(device_id: str) -> float:
    doc = await db.wallets.find_one({"device_id": device_id})
    # A wallet document can legitimately exist before it holds any money — the
    # currency lock and the launch-free day counter both create one — so a
    # missing `balance` reads as zero rather than raising.
    return float((doc or {}).get("balance") or 0.0)


def owner_hash_for(user: Optional[dict]) -> Optional[str]:
    """Keyed digest of the account id, stored on analyses so a record can be
    matched back to whoever ran it WITHOUT writing an identity onto it. Keyed
    with JWT_SECRET, so the hashes are useless to anyone reading the database
    without the server key."""
    if not user or not user.get("id"):
        return None
    return hmac.new(JWT_SECRET.encode(), f"analysis-owner:{user['id']}".encode(), hashlib.sha256).hexdigest()


def own_analyses_filter(user: Optional[dict]) -> dict:
    """Mongo filter for "analyses this account is entitled to see". History is
    private per account: you see runs you started, plus runs where an unchanged
    cached verdict was served to you (a free re-check reuses another account's
    document, and from your side that was still your own re-check — so it has
    to appear in your history and stay openable).

    An account with no owner hash (unauthenticated mode) matches nothing, which
    is the safe direction: it can never enumerate someone else's runs."""
    h = owner_hash_for(user)
    if not h:
        return {"id": {"$in": []}}
    return {"$or": [{"owner_hash": h}, {"viewer_hashes": h}]}


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def launch_free_daily_remaining(key: str) -> int:
    """Read-only view of today's remaining launch-free allowance."""
    wallet_doc = await db.wallets.find_one({"device_id": key}) or {}
    _, count_today = wal.launch_free_daily_state(
        wallet_doc.get("launch_free_daily_date", ""),
        wallet_doc.get("launch_free_daily_count", 0),
        _utc_day(),
    )
    return max(0, wal.LAUNCH_FREE_DAILY_CAP - count_today)


async def latest_completed_analysis_for(symbol: str, language: str = "en") -> Optional[dict]:
    """Most recent completed analysis document for a symbol IN THE REQUESTED
    LANGUAGE (full doc, not just the verdict) — used only for the wallet's
    free-recheck decision. A cached run in another language is not a valid
    answer, so it isn't reused. Never triggers a new run."""
    return await db.analyses.find_one(
        {"symbol": symbol, "language": language, "status": "completed", "verdict": {"$ne": None}},
        {"_id": 0, "owner_hash": 0, "viewer_hashes": 0},
        sort=[("updated_at", -1)],
    )

