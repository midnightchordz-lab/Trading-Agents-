"""Sign-in: phone/email one-time codes, Google, Apple — and account deletion.

Email codes go out through Emergent managed email, SMS through Twilio, Google
through Emergent managed auth. This is the ONLY place a verified `email` /
`phone` is written onto a user document; the admin allowlist matches on those
fields, so anything else writing them would be a privilege escalation.
"""
import asyncio
import hashlib
import hmac
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from pymongo import ReturnDocument

import auth as au
import limits as lim
import mailer
import sms
import wallet as wal
from core import db, logger, now_iso
from deps import (APPLE_SERVICES_ID, AUTH_DEBUG_RETURN_OTP, CONSENT_VERSION, HASH_SECRET, JWT_SECRET,
                  TRUSTED_PROXY_HOPS, client_ip, get_current_user)

# Per-IP ceiling on OTP requests, deliberately much looser than the 5/hour
# per-identifier limit: it exists to stop one source pumping SMS across many
# numbers, not to police a single person retrying their own code.
OTP_MAX_PER_IP_PER_HOUR = 20

# Whole-deployment ceilings per hour, because no per-IP rule stops a
# distributed source. Sized far above any plausible real hour for this app
# (dozens of sign-ins), so they are an abuse backstop rather than a throttle
# real users meet. The SMS budget is separate and much tighter because those
# are the requests that cost money and reach strangers' phones; hitting it
# leaves email sign-in working, so the app never becomes unusable.
OTP_SMS_GLOBAL_MAX_PER_HOUR = int(os.environ.get("OTP_SMS_GLOBAL_MAX_PER_HOUR", "60"))
OTP_EMAIL_GLOBAL_MAX_PER_HOUR = int(os.environ.get("OTP_EMAIL_GLOBAL_MAX_PER_HOUR", "1000"))

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


def free_credit_tombstone_hash_for(identifier: str) -> str:
    """Same HMAC pattern as owner_hash_for, keyed on the normalized
    email/phone rather than an account id. Lets the tombstone survive
    account deletion without storing the identifier itself in plain text."""
    return hmac.new(HASH_SECRET.encode(), f"free-credit-tombstone:{identifier}".encode(), hashlib.sha256).hexdigest()


async def signup_free_credits(device_id: Optional[str], request: Optional[Request],
                              identifier: Optional[str] = None) -> int:
    """How many free credits a BRAND-NEW account should start with.

    Free credits are per account, not per device, so rotating a device id on
    its own grants nothing — the actual farming path is creating another
    account, since any disposable email address is worth
    wal.FREE_CREDITS_ON_SIGNUP real LLM analyses. The device id and the client
    IP are the two signals we have for spotting the same person doing it
    repeatedly: a device may seed a grant once ever, and a single network only
    a handful per day.

    Returns 0 rather than refusing the sign-in. The account is still created
    and fully usable — it just pays like everyone else. Blocking sign-in on a
    shared office or carrier-NAT address would lock out genuine users, which is
    a far worse outcome than someone getting ten free analyses."""
    ip = client_ip(request)
    if identifier:
        # The direct fix for the actual exploit path: delete the account,
        # sign back in with the SAME email, and find_or_create_user makes a
        # brand-new account with a fresh grant, since deletion only removed
        # the user/wallet records, not the fact that this identifier already
        # had one. Device/IP throttling below is real but evadable by
        # rotating either signal; this check is not, since it's keyed on the
        # one thing farming this way still requires: a working email/phone.
        tombstone_hash = free_credit_tombstone_hash_for(identifier)
        if await db.free_credit_tombstones.find_one({"hash": tombstone_hash}):
            logger.info("free credits withheld: this identifier already had an account")
            return 0
    if device_id:
        seen = await db.free_credit_grants.count_documents({"device_id": device_id})
        if seen >= wal.FREE_CREDIT_GRANTS_PER_DEVICE:
            logger.info(f"free credits withheld: device {device_id} already seeded {seen} account(s)")
            return 0
    if ip:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        recent = await db.free_credit_grants.count_documents({"ip": ip, "created_at": {"$gt": since}})
        if recent >= wal.FREE_CREDIT_GRANTS_PER_IP_PER_DAY:
            logger.info(f"free credits withheld: {recent} grants from this address in the last day")
            return 0
    return wal.FREE_CREDITS_ON_SIGNUP


async def record_free_credit_grant(user_id: str, device_id: Optional[str], request: Optional[Request]) -> None:
    """Logged only when credits were actually granted, so a withheld grant
    never consumes the allowance it was already denied."""
    await db.free_credit_grants.insert_one({
        "user_id": user_id,
        "device_id": device_id,
        "ip": client_ip(request),
        "created_at": now_iso(),
    })


async def otp_count_since(since: str, extra: Optional[dict] = None) -> int:
    return await db.otp_requests.count_documents({"created_at": {"$gt": since}, **(extra or {})})


def identity_type_for(user: dict) -> str:
    """"phone" or "email" — which identity this account verified at sign-in.

    Stored on every account created from now on. For older ones it is inferred:
    a Google/Apple account is always an email, and otherwise a stored phone is
    the only field that can have come from a verified sign-in (an email could
    have been typed for a payment receipt — see the startup migration)."""
    stored = user.get("identity_type")
    if stored in ("phone", "email"):
        return stored
    if user.get("google_sub") or user.get("apple_sub"):
        return "email"
    return "phone" if user.get("phone") else "email"


def identity_for(user: dict) -> Optional[str]:
    return user.get("phone") if identity_type_for(user) == "phone" else user.get("email")


async def find_or_create_user(identifier_type: str, identifier: str,
                              device_id: Optional[str] = None,
                              request: Optional[Request] = None,
                              raw_identifier: Optional[str] = None) -> dict:
    key = "phone" if identifier_type == "phone" else "email"
    existing = await db.users.find_one({key: identifier})
    if not existing and key == "email" and raw_identifier:
        # Gmail canonicalization changed what this lookup asks for. Anyone who
        # signed up as "first.last@gmail.com" BEFORE that change is stored
        # under the dotted address, and without this fallback they'd be handed
        # a brand-new empty account — silently losing a wallet balance they
        # paid for. Match their existing record and leave it exactly as it is.
        pre_canonical = raw_identifier.strip().lower()
        if pre_canonical != identifier:
            existing = await db.users.find_one({key: pre_canonical})
    if existing:
        return existing
    granted = await signup_free_credits(device_id, request, identifier)
    user = {"id": str(uuid.uuid4()), "phone": None, "email": None, "google_sub": None,
            "apple_sub": None, "free_credits_remaining": granted,
            # Which field the account actually verified at sign-in. Recorded so
            # the app can always show the identity the person signed in WITH,
            # never an address that arrived some other way (a payment receipt).
            "identity_type": key,
            "created_at": now_iso()}
    user[key] = identifier
    await db.users.insert_one({**user})
    if granted:
        await record_free_credit_grant(user["id"], device_id, request)
    return user


async def link_device_wallet_to_user(device_id: Optional[str], user_id: str) -> None:
    """On first login, merge an existing anonymous device wallet balance
    into the user's own wallet rather than losing it. Additive — never
    removes the device-keyed record, just credits the user-keyed one.

    Claim-then-credit, both halves atomic: the device wallet is zeroed in the
    SAME operation that reads its balance, and the user wallet is credited with
    `$inc` rather than a computed total. Two concurrent logins could otherwise
    both read the same pre-zero balance and each add it, or both compute a
    total from the same stale read — either way multiplying real money. Only
    the request whose claim actually zeroed a positive balance credits
    anything, and it credits exactly what it claimed."""
    if not device_id:
        return
    claimed = await db.wallets.find_one_and_update(
        {"device_id": device_id, "balance": {"$gt": 0}},
        {"$set": {"balance": 0.0, "updated_at": now_iso()}},
    )
    if not claimed:
        return
    await db.wallets.update_one(
        {"device_id": f"user:{user_id}"},
        {"$set": {"device_id": f"user:{user_id}", "updated_at": now_iso()},
         "$inc": {"balance": round(float(claimed["balance"]), 4)}},
        upsert=True,
    )


@api_router.post("/auth/otp/request", dependencies=[Depends(lim.limit_otp_request)])
async def auth_otp_request(body: OtpRequest, request: Request):
    id_type, identifier = au.normalize_identifier(body.identifier)
    if not id_type:
        raise HTTPException(status_code=400, detail="Enter a valid phone number or email address")

    recent = await db.otp_requests.find({"identifier": identifier}, None).sort("created_at", -1).to_list(20)
    recent_ts = [r["created_at"] for r in recent]
    if au.is_rate_limited(recent_ts):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")

    # Per-identifier limiting alone doesn't stop pumping: an attacker cycling
    # through unlimited phone numbers stays under 5/hour on every single one
    # while still running up real SMS spend and spamming strangers under this
    # app's name. A looser per-IP ceiling closes that without troubling one
    # real person signing in.
    #
    # Honest scope: this is per-IP, so it only stops ONE source. A distributed
    # source, or a topology change that makes client_ip fall back to the
    # left-most (spoofable) entry, is caught by the whole-deployment budgets
    # below instead.
    ip = client_ip(request)
    if ip:
        recent_ip = await db.otp_requests.find({"ip": ip}, None).sort("created_at", -1).to_list(50)
        if au.is_rate_limited([r["created_at"] for r in recent_ip],
                              window_seconds=3600, max_requests=OTP_MAX_PER_IP_PER_HOUR):
            raise HTTPException(status_code=429,
                                detail="Too many attempts from this network — try again later")

    # Whole-deployment budgets. Nothing keyed on the caller can stop a
    # distributed pump — a botnet gets a fresh address per request and a fresh
    # identifier per request, and stays under every per-IP and per-identifier
    # limit while spending our Twilio balance. These two ceilings are the only
    # thing that bounds the total damage, so they are checked for everyone,
    # after the cheaper per-caller checks.
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    if id_type == "phone":
        # Checked first and sized tighter: texts cost money and land on real
        # strangers' phones. Exhausting it must not lock the app — email
        # sign-in still works, so people can still get in.
        sent_sms = await otp_count_since(hour_ago, {"identifier_type": "phone"})
        if sent_sms >= OTP_SMS_GLOBAL_MAX_PER_HOUR:
            logger.error(
                f"GLOBAL SMS OTP BUDGET REACHED: {sent_sms} texts in the last hour "
                f"(cap {OTP_SMS_GLOBAL_MAX_PER_HOUR}) — possible pumping attack"
            )
            raise HTTPException(
                status_code=503,
                detail="Text messages are busy right now — sign in with your email address instead",
            )
    else:
        # Separate budget per channel rather than one combined ceiling: email
        # is cheap, so a generous email allowance must not be eaten by SMS
        # traffic, and the tight SMS allowance must not be inflated by email
        # traffic. A single shared number would have done both.
        sent_email = await otp_count_since(hour_ago, {"identifier_type": "email"})
        if sent_email >= OTP_EMAIL_GLOBAL_MAX_PER_HOUR:
            logger.error(
                f"GLOBAL EMAIL OTP BUDGET REACHED: {sent_email} codes in the last hour "
                f"(cap {OTP_EMAIL_GLOBAL_MAX_PER_HOUR}) — possible pumping attack"
            )
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
        "ip": ip,
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


@api_router.post("/auth/otp/verify", dependencies=[Depends(lim.limit_otp_verify)])
async def auth_otp_verify(body: OtpVerify, request: Request):
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
        # $inc, not a computed total: N wrong guesses arriving together must
        # cost N attempts. Reading the count and writing count+1 let a burst of
        # parallel guesses all record the same value, so the cap of 5 could be
        # spent many times over.
        after = await db.otp_requests.find_one_and_update(
            {"id": record["id"]}, {"$inc": {"attempts": 1}}, return_document=ReturnDocument.AFTER
        )
        if (after or {}).get("attempts", 0) >= au.OTP_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many incorrect attempts — request a new code")
        raise HTTPException(status_code=400, detail="Incorrect code")

    # Atomic claim: only ONE concurrent request carrying the same valid code
    # can flip verified False -> True and proceed past here. Without it both
    # requests pass the check above, both call link_device_wallet_to_user, and
    # each reads the same not-yet-zeroed device balance — multiplying real
    # money.
    claimed = await db.otp_requests.update_one(
        {"id": record["id"], "verified": False}, {"$set": {"verified": True}}
    )
    if claimed.modified_count == 0:
        raise HTTPException(status_code=400, detail="This code was already used — request a new one")

    user = await find_or_create_user(id_type, identifier, body.device_id, request, body.identifier)
    await link_device_wallet_to_user(body.device_id, user["id"])
    asyncio.create_task(send_welcome_if_new(user))
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "phone": user.get("phone"), "email": user.get("email"),
                                     "identity": identity_for(user), "identity_type": identity_type_for(user)}}


class GoogleSession(BaseModel):
    session_id: str  # one-time id Emergent appends to the redirect URL
    device_id: Optional[str] = None


@api_router.post("/auth/session", dependencies=[Depends(lim.limit_auth_exchange)])
async def auth_session(body: GoogleSession, request: Request):
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
        # Same identifier-keyed tombstone as the OTP path: deleting the account
        # and signing back in with the same Google address must not mint a
        # second free grant either.
        granted = await signup_free_credits(body.device_id, request, email)
        user = {"id": str(uuid.uuid4()), "phone": None, "email": email, "google_sub": data.get("id"),
                "apple_sub": None, "name": data.get("name"), "picture": data.get("picture"),
                "free_credits_remaining": granted, "created_at": now_iso()}
        await db.users.insert_one({**user})
        if granted:
            await record_free_credit_grant(user["id"], body.device_id, request)
    elif not user.get("google_sub"):
        await db.users.update_one({"id": user["id"]}, {"$set": {"google_sub": data.get("id")}})

    await link_device_wallet_to_user(body.device_id, user["id"])
    asyncio.create_task(send_welcome_if_new(user))
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "phone": user.get("phone"), "email": user.get("email"),
                                     "identity": identity_for(user), "identity_type": identity_type_for(user)}}


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


@api_router.post("/auth/apple", dependencies=[Depends(lim.limit_auth_exchange)])
async def auth_apple(body: SocialSignIn, request: Request):
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
        # Apple can withhold the address (private relay), in which case there
        # is nothing to key a tombstone on and the device/IP caps are all we
        # have — signup_free_credits skips the check on a falsy identifier.
        granted = await signup_free_credits(body.device_id, request, claims.get("email"))
        user = {"id": str(uuid.uuid4()), "phone": None, "email": claims.get("email"),
                "google_sub": None, "apple_sub": claims.get("sub"),
                "free_credits_remaining": granted, "created_at": now_iso()}
        await db.users.insert_one({**user})
        if granted:
            await record_free_credit_grant(user["id"], body.device_id, request)
    await link_device_wallet_to_user(body.device_id, user["id"])
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "email": user.get("email"),
                                     "identity": identity_for(user), "identity_type": identity_type_for(user)}}


@api_router.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    consent = user.get("consent") or {}
    return {
        "id": user["id"], "phone": user.get("phone"), "email": user.get("email"),
        # The identity this account signed in WITH, so the app never has to
        # guess between a phone and an email that may have arrived from
        # somewhere else. `identity_type` is stored from signup onward; older
        # accounts are inferred from what they actually have.
        "identity_type": identity_type_for(user),
        "identity": identity_for(user),
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
    identifier = identity_for(user)
    if identifier:
        await db.free_credit_tombstones.update_one(
            {"hash": free_credit_tombstone_hash_for(identifier)},
            {"$setOnInsert": {"deleted_at": now_iso()}},
            upsert=True,
        )
    await db.users.delete_one({"id": user_id})
    await db.wallets.delete_one({"device_id": f"user:{user_id}"})
    return {"deleted": True}

