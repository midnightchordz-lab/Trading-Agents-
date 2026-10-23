"""Apple token revocation — the part of Sign in with Apple that App Review
actually checks on account deletion.

WHAT APPLE REQUIRES: an app offering Sign in with Apple must, when the user
deletes their account, call Apple's revoke endpoint so the Apple side of the
relationship ends too. Apple's own technote is explicit that this endpoint is
the only way to invalidate a user's tokens without user interaction. Deleting
our row is not enough on its own.

WHAT THAT TAKES, AND WHY IT IS MORE THAN ONE CALL: the revoke endpoint wants a
REFRESH TOKEN, and the native sign-in flow never gives us one — it hands the
app an identity token (which proves who the user is, and cannot be revoked) and
a one-time AUTHORIZATION CODE. So the code has to be exchanged for a refresh
token at sign-in time and stored, and that exchange, like the revocation, has
to be authenticated with a `client_secret` that is itself an ES256 JWT signed
with a private key from the Apple Developer account.

CONFIGURATION, and the failure mode when it is missing: APPLE_TEAM_ID,
APPLE_KEY_ID and APPLE_PRIVATE_KEY (the contents of the one-time-download .p8).
Without them `configured()` is False, every function here is a no-op that logs,
and account deletion still deletes the account — Apple's guidance is that the
deletion must complete whether or not the token could be revoked. An account
that signed in before these were configured has no stored refresh token, and
nothing can be done for it retroactively: there is no way to obtain one without
the user signing in again.

`client_id` for a NATIVE app is the BUNDLE IDENTIFIER, not a Services ID —
the same distinction that decides the identity token's audience.
"""
import os
import time
from typing import Optional

import httpx
import jwt

from core import logger

APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_REVOKE_URL = "https://appleid.apple.com/auth/revoke"
APPLE_AUDIENCE = "https://appleid.apple.com"

TEAM_ID = (os.environ.get("APPLE_TEAM_ID", "") or "").strip()
KEY_ID = (os.environ.get("APPLE_KEY_ID", "") or "").strip()
# A .p8 is a multi-line PEM and an env var is one line, so `\n` escapes are
# accepted as well as real newlines — the deployment secrets panel is a
# single-line text box.
PRIVATE_KEY = (os.environ.get("APPLE_PRIVATE_KEY", "") or "").strip().replace("\\n", "\n")
# Defaults to the first configured identity-token audience, which for a native
# app is the bundle identifier — so this only needs setting if they ever differ.
CLIENT_ID = (os.environ.get("APPLE_CLIENT_ID", "") or "").strip()

# Five minutes. Apple allows up to six months, but this secret is minted per
# request and never stored, so there is nothing to gain from a long life.
CLIENT_SECRET_TTL = 300


def default_client_id() -> str:
    from deps import APPLE_AUDIENCES
    return CLIENT_ID or (APPLE_AUDIENCES[0] if APPLE_AUDIENCES else "")


def configured() -> bool:
    return bool(TEAM_ID and KEY_ID and PRIVATE_KEY and default_client_id())


def client_secret() -> str:
    """The ES256 JWT Apple accepts in place of a client secret.

    `sub` is the client id and `aud` is Apple itself; getting either wrong
    comes back as `invalid_client`, which is indistinguishable from a bad key.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "iss": TEAM_ID,
            "iat": now,
            "exp": now + CLIENT_SECRET_TTL,
            "aud": APPLE_AUDIENCE,
            "sub": default_client_id(),
        },
        PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": KEY_ID},
    )


async def exchange_code_for_refresh_token(code: str) -> Optional[str]:
    """Turns the one-time authorization code from the native sign-in into a
    refresh token, which is the only thing revocation can use later.

    Returns None on any failure, and never raises: a sign-in must not fail
    because this optional step did. The code is single-use and short-lived, so
    there is nothing to retry later — if this misses, that account simply has
    no revocable token.
    """
    if not configured() or not code:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(APPLE_TOKEN_URL, data={
                "client_id": default_client_id(),
                "client_secret": client_secret(),
                "code": code,
                "grant_type": "authorization_code",
            })
        if resp.status_code != 200:
            # Apple's body names the problem (invalid_client = key/team/client
            # id mismatch, invalid_grant = the code was already used or was
            # minted for a different client). Worth logging verbatim; it
            # contains no user data.
            logger.warning(f"Apple code exchange failed [{resp.status_code}]: {resp.text[:200]}")
            return None
        token = resp.json().get("refresh_token")
        if not token:
            logger.warning("Apple code exchange returned no refresh token")
        return token
    except Exception as e:
        logger.warning(f"Apple code exchange error: {e}")
        return None


async def revoke_refresh_token(token: str) -> bool:
    """Revokes one token. True only when Apple confirms it.

    Apple answers 200 with an empty body on success. Never raises — account
    deletion must complete either way, so the caller logs and carries on.
    """
    if not configured() or not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(APPLE_REVOKE_URL, data={
                "client_id": default_client_id(),
                "client_secret": client_secret(),
                "token": token,
                "token_type_hint": "refresh_token",
            })
        if resp.status_code == 200:
            return True
        logger.warning(f"Apple token revocation failed [{resp.status_code}]: {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Apple token revocation error: {e}")
        return False
