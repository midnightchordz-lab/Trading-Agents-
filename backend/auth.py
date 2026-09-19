"""Authentication primitives — additive, mostly pure functions.

Covers: OTP generation/hashing/verification, JWT session tokens, phone/email
identifier normalization, and rate limiting. Uses only libraries already in
requirements.txt (pyjwt, passlib[bcrypt]) — no new dependencies.

NOT included here: actually sending an OTP (SMS/email delivery) and live
verification of Google/Apple ID tokens against their public keys. Both need
real provider credentials (an SMS/email provider, a Google OAuth client ID,
an Apple Sign In configuration) that only the app owner can provision — see
the integration notes in the accompanying spec. The functions below that
stand in for those (`send_otp_stub`, `verify_google_id_token_stub`,
`verify_google_id_token_stub`) are explicit placeholders; Apple tokens are
verified for real in `verify_apple_id_token`.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import jwt
import bcrypt

# NOTE: uses the `bcrypt` package directly rather than passlib's CryptContext
# wrapper. passlib's bcrypt backend detection is broken against
# bcrypt>=4.1 (it looks for a `__about__` attribute bcrypt no longer has),
# which throws on every hash/verify call — a real, reproducible incompatibility
# with the bcrypt==4.1.3 already pinned in requirements.txt, not just a quirk
# of this sandbox. Calling bcrypt directly sidesteps it entirely.

OTP_LENGTH = 6
OTP_TTL_SECONDS = 300  # 5 minutes
OTP_MAX_ATTEMPTS = 5
OTP_RATE_LIMIT_WINDOW_SECONDS = 3600
OTP_RATE_LIMIT_MAX_REQUESTS = 5
SESSION_TTL_DAYS = 30

IdentifierType = Literal["phone", "email"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")  # loose E.164-ish check


_DISPOSABLE_EMAIL_DOMAINS = {
    # Starter list of well-known throwaway providers, used to raise the cost
    # of free-credit farming via one-time addresses. NOT exhaustive and NOT
    # maintained — a real deployment should swap this for a live
    # disposable-domain service rather than trust a static set in source.
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "throwawaymail.com", "trashmail.com",
    "getnada.com", "sharklasers.com", "dispostable.com",
}


def normalize_identifier(raw: str) -> tuple[Optional[IdentifierType], Optional[str]]:
    """Detects whether raw looks like an email or a phone number and
    normalizes it. Returns (None, None) if it looks like neither.

    Gmail canonicalization closes the cheapest free-credit farming path:
    `a.b+tag@gmail.com` and `ab@gmail.com` are the SAME real inbox, but were
    treated as different accounts, each claiming a fresh signup bonus.
    Collapsing them means one inbox is one account. Only applied to
    Gmail/Googlemail, because dot-insensitivity is a Gmail-specific
    behaviour — doing it globally would merge genuinely distinct addresses
    at other providers."""
    value = (raw or "").strip()
    if not value:
        return None, None
    if _EMAIL_RE.match(value):
        email = value.lower()
        local, _, domain = email.partition("@")
        if domain in _DISPOSABLE_EMAIL_DOMAINS:
            return None, None
        if domain in ("gmail.com", "googlemail.com"):
            local = local.split("+", 1)[0].replace(".", "")
            if not local:
                return None, None
            email = f"{local}@gmail.com"
        return "email", email
    digits_and_plus = re.sub(r"[\s\-()]", "", value)
    if _PHONE_RE.match(digits_and_plus):
        return "phone", digits_and_plus
    return None, None


def generate_otp() -> str:
    """A cryptographically random numeric OTP, zero-padded to OTP_LENGTH."""
    n = secrets.randbelow(10**OTP_LENGTH)
    return str(n).zfill(OTP_LENGTH)


def hash_otp(otp: str) -> str:
    return bcrypt.hashpw(otp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_otp_code(otp: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(otp.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def is_otp_expired(created_at_iso: str, ttl_seconds: int = OTP_TTL_SECONDS) -> bool:
    try:
        created = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age > ttl_seconds


def is_rate_limited(
    recent_request_timestamps: list[str],
    window_seconds: int = OTP_RATE_LIMIT_WINDOW_SECONDS,
    max_requests: int = OTP_RATE_LIMIT_MAX_REQUESTS,
) -> bool:
    """True if too many OTP requests have gone out for this identifier
    recently. recent_request_timestamps: ISO timestamps of prior requests."""
    now = datetime.now(timezone.utc)
    count = 0
    for ts in recent_request_timestamps:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if (now - t).total_seconds() <= window_seconds:
            count += 1
    return count >= max_requests


def create_session_token(user_id: str, secret: str, ttl_days: int = SESSION_TTL_DAYS) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ttl_days)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_session_token(token: str, secret: str) -> Optional[dict]:
    """Returns the payload dict, or None if the token is invalid/expired.
    Never raises — callers treat None as unauthenticated."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# --- Admin allowlist (additive; configured via env, not hardcoded) ---

def parse_admin_identifiers(raw: str) -> set:
    """Comma-separated phone/email allowlist, normalized the same way as
    login identifiers so formatting differences (spacing, missing +) don't
    cause a false negative."""
    out: set = set()
    for piece in (raw or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        _id_type, normalized = normalize_identifier(piece)
        if normalized:
            out.add(normalized)
    return out


def is_admin(user: Optional[dict], admin_identifiers: set) -> bool:
    """True if the user's phone or email is in the admin allowlist."""
    if not admin_identifiers or not user:
        return False
    phone = user.get("phone")
    email = user.get("email")
    return bool((phone and phone in admin_identifiers) or (email and email in admin_identifiers))


# --- Placeholders requiring real provider credentials ---

def send_otp_stub(identifier: str, identifier_type: IdentifierType, otp: str) -> None:
    """PLACEHOLDER. Does not send anything. Wire this to an SMS provider
    (Twilio, MSG91, etc.) for phone, or an email provider (SES, SendGrid,
    etc.) for email, using your own provider credentials."""
    return None


def verify_google_id_token_stub(id_token: str, expected_audience: str) -> Optional[dict]:
    """PLACEHOLDER. Real verification needs your own Google OAuth client ID
    (as expected_audience) and network access to Google's public JWKS
    endpoint to validate the token's signature. Returns None until wired."""
    return None


def verify_apple_id_token(identity_token: str, expected_audience: str, jwks: list) -> Optional[dict]:
    """Verifies a real Sign in with Apple identity token against Apple's
    published public keys. `jwks` is Apple's JWKS response (the list under
    its "keys" field) — fetched separately by the caller (see
    fetch_apple_jwks in server.py), since this function has no network
    access itself and is fully testable with a synthetic JWKS + token pair.
    Returns {"sub": ..., "email": ...} on success, None on any verification
    failure — a malformed token, a tampered signature, an expired token, or
    a wrong audience are all treated the same way: reject, don't
    authenticate. Never raises."""
    try:
        unverified_header = jwt.get_unverified_header(identity_token)
    except Exception:
        return None
    kid = unverified_header.get("kid")
    if not kid:
        return None
    matching_key = next((k for k in jwks if isinstance(k, dict) and k.get("kid") == kid), None)
    if not matching_key:
        return None
    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(matching_key))
        payload = jwt.decode(
            identity_token,
            key=public_key,
            algorithms=["RS256"],
            audience=expected_audience,
            issuer="https://appleid.apple.com",
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    return {"sub": sub, "email": payload.get("email")}
