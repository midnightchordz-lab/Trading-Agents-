"""Sign-in: phone/email one-time codes, Google, Apple — and account deletion.

Email codes go out through Emergent managed email, SMS through Twilio, Google
through Emergent managed auth. This is the ONLY place a verified `email` /
`phone` is written onto a user document; the admin allowlist matches on those
fields, so anything else writing them would be a privilege escalation.
"""
import asyncio
import time
import uuid
from typing import Optional

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import auth as au
import mailer
import sms
import wallet as wal
from core import db, logger, now_iso
from deps import APPLE_SERVICES_ID, AUTH_DEBUG_RETURN_OTP, CONSENT_VERSION, JWT_SECRET, get_current_user

api_router = APIRouter(prefix="/api")


# --- Auth endpoints. Phone/email OTP + Google sign-in. Email codes go out via
# Emergent managed email, SMS via Twilio, and Google sign-in via Emergent
# managed auth. Apple sign-in is still a placeholder (needs an Apple Services
# ID from the app owner). ---
EMERGENT_SESSION_DATA_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"


class OtpRequest(BaseModel):
    identifier: str  # phone or email, auto-detected
    device_id: Optional[str] = None  # to link an existing anonymous wallet on first login


class OtpVerify(BaseModel):
    identifier: str
    otp: str
    device_id: Optional[str] = None


class SocialSignIn(BaseModel):
    token: str  # Google id_token or Apple identity_token
    device_id: Optional[str] = None


async def send_welcome_if_new(user: dict) -> None:
    """One welcome email per account, ever. Only for accounts that have an
    email address (phone-only sign-ups have nowhere to send it)."""
    email = user.get("email")
    if not email or user.get("welcome_sent_at"):
        return
    # Claim it first so a double sign-in can't send twice.
    claimed = await db.users.update_one(
        {"id": user["id"], "welcome_sent_at": {"$in": [None, ""]}},
        {"$set": {"welcome_sent_at": now_iso()}},
    )
    if claimed.modified_count == 0:
        return
    if not await mailer.send_welcome_email(email):
        await db.users.update_one({"id": user["id"]}, {"$set": {"welcome_sent_at": None}})


async def find_or_create_user(identifier_type: str, identifier: str) -> dict:
    key = "phone" if identifier_type == "phone" else "email"
    existing = await db.users.find_one({key: identifier})
    if existing:
        return existing
    user = {"id": str(uuid.uuid4()), "phone": None, "email": None, "google_sub": None,
            "apple_sub": None, "free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP,
            "created_at": now_iso()}
    user[key] = identifier
    await db.users.insert_one({**user})
    return user


async def link_device_wallet_to_user(device_id: Optional[str], user_id: str) -> None:
    """On first login, merge an existing anonymous device wallet balance
    into the user's own wallet rather than losing it. Additive — never
    removes the device-keyed record, just credits the user-keyed one."""
    if not device_id:
        return
    device_wallet = await db.wallets.find_one({"device_id": device_id})
    if not device_wallet or device_wallet.get("balance", 0) <= 0:
        return
    user_wallet = await db.wallets.find_one({"device_id": f"user:{user_id}"})
    current = float((user_wallet or {}).get("balance") or 0.0)
    merged = round(current + device_wallet["balance"], 4)
    await db.wallets.update_one(
        {"device_id": f"user:{user_id}"},
        {"$set": {"device_id": f"user:{user_id}", "balance": merged, "updated_at": now_iso()}},
        upsert=True,
    )
    await db.wallets.update_one({"device_id": device_id}, {"$set": {"balance": 0.0, "updated_at": now_iso()}})


@api_router.post("/auth/otp/request")
async def auth_otp_request(body: OtpRequest):
    id_type, identifier = au.normalize_identifier(body.identifier)
    if not id_type:
        raise HTTPException(status_code=400, detail="Enter a valid phone number or email address")

    recent = await db.otp_requests.find({"identifier": identifier}, None).sort("created_at", -1).to_list(20)
    recent_ts = [r["created_at"] for r in recent]
    if au.is_rate_limited(recent_ts):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")

    if id_type == "phone" and not sms.sms_configured():
        raise HTTPException(
            status_code=503,
            detail="Text messages aren't available yet — sign in with your email address instead",
        )

    otp = au.generate_otp()
    doc = {
        "id": str(uuid.uuid4()),
        "identifier": identifier,
        "identifier_type": id_type,
        "otp_hash": au.hash_otp(otp),
        "attempts": 0,
        "verified": False,
        "created_at": now_iso(),
    }
    await db.otp_requests.insert_one({**doc})

    ttl_minutes = au.OTP_TTL_SECONDS // 60
    if id_type == "email":
        delivered = await mailer.send_otp_email(identifier, otp, ttl_minutes)
        if not delivered and not AUTH_DEBUG_RETURN_OTP:
            raise HTTPException(status_code=502, detail="Couldn't send the code — try again in a moment")
    else:
        delivered, err = await sms.send_otp_sms(identifier, otp, ttl_minutes, mailer.EMAIL_FROM_NAME)
        if not delivered and not AUTH_DEBUG_RETURN_OTP:
            raise HTTPException(status_code=502, detail=err or "Couldn't send the text — try again")

    response = {"identifier": identifier, "identifier_type": id_type, "sent": True}
    if AUTH_DEBUG_RETURN_OTP:
        # DEV/TEST ONLY, off by default. Never enable this in production —
        # it puts the code in the API response for anyone to read.
        response["debug_otp"] = otp
    return response


@api_router.post("/auth/otp/verify")
async def auth_otp_verify(body: OtpVerify):
    id_type, identifier = au.normalize_identifier(body.identifier)
    if not id_type:
        raise HTTPException(status_code=400, detail="Enter a valid phone number or email address")

    record = await db.otp_requests.find_one({"identifier": identifier, "verified": False}, sort=[("created_at", -1)])
    if not record:
        raise HTTPException(status_code=400, detail="No pending code for this identifier — request a new one")
    if au.is_otp_expired(record["created_at"]):
        raise HTTPException(status_code=400, detail="Code expired — request a new one")
    if record.get("attempts", 0) >= au.OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many incorrect attempts — request a new code")
    if not au.verify_otp_code(body.otp, record["otp_hash"]):
        await db.otp_requests.update_one({"id": record["id"]}, {"$set": {"attempts": record.get("attempts", 0) + 1}})
        raise HTTPException(status_code=400, detail="Incorrect code")

    await db.otp_requests.update_one({"id": record["id"]}, {"$set": {"verified": True}})
    user = await find_or_create_user(id_type, identifier)
    await link_device_wallet_to_user(body.device_id, user["id"])
    asyncio.create_task(send_welcome_if_new(user))
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "phone": user.get("phone"), "email": user.get("email")}}


class GoogleSession(BaseModel):
    session_id: str  # one-time id Emergent appends to the redirect URL
    device_id: Optional[str] = None


@api_router.post("/auth/session")
async def auth_session(body: GoogleSession):
    """Exchanges the one-time Emergent session_id for one of our own session
    tokens, creating/reusing a user keyed on the Google email."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(EMERGENT_SESSION_DATA_URL, headers={"X-Session-ID": body.session_id})
    except Exception as e:
        logger.warning(f"google session exchange failed: {e}")
        raise HTTPException(status_code=502, detail="Couldn't reach the sign-in service — try again")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="That sign-in link has expired — try again")

    data = resp.json()
    email = (data.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Google didn't return an email address")

    user = await db.users.find_one({"email": email})
    if not user:
        user = {"id": str(uuid.uuid4()), "phone": None, "email": email, "google_sub": data.get("id"),
                "apple_sub": None, "name": data.get("name"), "picture": data.get("picture"),
                "free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP, "created_at": now_iso()}
        await db.users.insert_one({**user})
    elif not user.get("google_sub"):
        await db.users.update_one({"id": user["id"]}, {"$set": {"google_sub": data.get("id")}})

    await link_device_wallet_to_user(body.device_id, user["id"])
    asyncio.create_task(send_welcome_if_new(user))
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "phone": user.get("phone"), "email": user.get("email")}}


_apple_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}
APPLE_JWKS_CACHE_TTL = 3600  # seconds — Apple's signing keys rotate infrequently


def fetch_apple_jwks() -> list:
    now = time.time()
    if _apple_jwks_cache["keys"] is not None and (now - _apple_jwks_cache["fetched_at"]) < APPLE_JWKS_CACHE_TTL:
        return _apple_jwks_cache["keys"]
    r = requests.get("https://appleid.apple.com/auth/keys", timeout=10)
    r.raise_for_status()
    keys = r.json().get("keys", [])
    _apple_jwks_cache["keys"] = keys
    _apple_jwks_cache["fetched_at"] = now
    return keys


@api_router.post("/auth/apple")
async def auth_apple(body: SocialSignIn):
    if not APPLE_SERVICES_ID:
        raise HTTPException(status_code=501, detail="Apple sign-in is not configured yet (APPLE_SERVICES_ID unset)")
    try:
        jwks = await asyncio.to_thread(fetch_apple_jwks)
    except Exception as e:
        logger.warning(f"Apple JWKS fetch failed: {e}")
        raise HTTPException(status_code=502, detail="Could not verify Apple sign-in right now — try again")
    claims = au.verify_apple_id_token(body.token, APPLE_SERVICES_ID, jwks)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid Apple sign-in token")
    user = await db.users.find_one({"apple_sub": claims.get("sub")})
    if not user:
        user = {"id": str(uuid.uuid4()), "phone": None, "email": claims.get("email"),
                "google_sub": None, "apple_sub": claims.get("sub"),
                "free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP, "created_at": now_iso()}
        await db.users.insert_one({**user})
    await link_device_wallet_to_user(body.device_id, user["id"])
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "email": user.get("email")}}


@api_router.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    consent = user.get("consent") or {}
    return {
        "id": user["id"], "phone": user.get("phone"), "email": user.get("email"),
        "consent_given": bool(consent.get("agreed")) and consent.get("version") == CONSENT_VERSION,
        "consent_version_required": CONSENT_VERSION,
    }


class ConsentRequest(BaseModel):
    agreed: bool


@api_router.post("/consent")
async def record_consent(body: ConsentRequest, user: dict = Depends(get_current_user)):
    """Records explicit, affirmative consent — never implied by continued
    use, never pre-checked client-side. DPDP requires this be as easy to
    give as to withdraw; withdrawal is handled by DELETE /account below,
    since this app cannot function without the baseline data (phone/email,
    wallet) consent covers — there's no coherent partial-withdrawal state
    to represent, so withdrawing means deleting the account."""
    if not body.agreed:
        raise HTTPException(
            status_code=400,
            detail="This endpoint only records agreement. To withdraw consent, delete your account instead.",
        )
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"consent": {"agreed": True, "agreed_at": now_iso(), "version": CONSENT_VERSION}}},
    )
    return {"consent_given": True, "consent_version": CONSENT_VERSION}


@api_router.delete("/account")
async def delete_account(user: dict = Depends(get_current_user)):
    """Apple requires in-app account deletion, not just 'contact support'.
    Deletes the account record and its wallet — the two things that
    directly identify and grant access to this person. Deliberately does
    NOT delete payment/transaction records (payments, wallet_ledger):
    financial recordkeeping obligations generally require retaining those
    regardless of account deletion — this is a data-retention judgment
    call, not an oversight, and should be confirmed against your actual
    compliance requirements before relying on it. Analyses aren't deleted
    either, since they were never linked to this account's identity in the
    first place (see the Privacy Policy)."""
    user_id = user["id"]
    await db.users.delete_one({"id": user_id})
    await db.wallets.delete_one({"device_id": f"user:{user_id}"})
    return {"deleted": True}

