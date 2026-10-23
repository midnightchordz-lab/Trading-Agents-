"""Apple token revocation on account deletion.

WHY IT EXISTS: App Review requires an app offering Sign in with Apple to revoke
the user's Apple tokens when they delete their account — deleting our own row
leaves the Apple side of the relationship intact. Apple's technote is explicit
that the revoke endpoint is the only way to do it without user interaction.

WHAT MAKES IT AWKWARD, and what most of these tests are about: the revoke
endpoint wants a REFRESH TOKEN, and native sign-in never produces one. It hands
over an identity token (unrevocable) and a single-use AUTHORIZATION CODE, so
the code must be exchanged at sign-in and the refresh token stored. Two network
calls, both authenticated with an ES256 JWT signed by a key from the developer
account — and every one of them must be unable to break sign-in or deletion
when it fails or is unconfigured.
"""
import os
import sys
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import apple_revoke  # noqa: E402
from routes import auth_routes  # noqa: E402
from tests.async_loop import run_async  # noqa: E402


@pytest.fixture
def configured(monkeypatch):
    """A real ES256 key pair, so the client secret is genuinely signed and can
    be verified here rather than merely produced."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(apple_revoke, "TEAM_ID", "TEAM123456")
    monkeypatch.setattr(apple_revoke, "KEY_ID", "KEY7654321")
    monkeypatch.setattr(apple_revoke, "PRIVATE_KEY", pem)
    monkeypatch.setattr(apple_revoke, "CLIENT_ID", "com.emergent.tradeagentapp.kht259")
    return {"public": key.public_key()}


# --- off until configured, and harmless while off -------------------------

def test_it_is_off_until_all_three_credentials_are_set(monkeypatch):
    monkeypatch.setattr(apple_revoke, "TEAM_ID", "")
    assert apple_revoke.configured() is False
    monkeypatch.setattr(apple_revoke, "TEAM_ID", "TEAM123456")
    monkeypatch.setattr(apple_revoke, "KEY_ID", "")
    assert apple_revoke.configured() is False
    monkeypatch.setattr(apple_revoke, "KEY_ID", "KEY7654321")
    monkeypatch.setattr(apple_revoke, "PRIVATE_KEY", "")
    assert apple_revoke.configured() is False


def test_while_unconfigured_both_calls_are_silent_no_ops(monkeypatch):
    """They must not raise and must not pretend to have worked — a revocation
    that silently reports success is worse than one that reports failure."""
    monkeypatch.setattr(apple_revoke, "TEAM_ID", "")
    assert run_async(apple_revoke.exchange_code_for_refresh_token("some-code")) is None
    assert run_async(apple_revoke.revoke_refresh_token("some-token")) is False


def test_an_empty_code_or_token_is_refused_without_a_network_call(configured):
    assert run_async(apple_revoke.exchange_code_for_refresh_token("")) is None
    assert run_async(apple_revoke.revoke_refresh_token("")) is False


# --- the client secret, which is where `invalid_client` comes from --------

def test_the_client_secret_carries_the_claims_apple_checks(configured):
    secret = apple_revoke.client_secret()
    claims = jwt.decode(secret, configured["public"], algorithms=["ES256"],
                        audience="https://appleid.apple.com")
    assert claims["iss"] == "TEAM123456", "iss must be the TEAM id"
    assert claims["sub"] == "com.emergent.tradeagentapp.kht259", \
        "sub must be the client id — for a native app, the bundle identifier"
    assert claims["aud"] == "https://appleid.apple.com"
    assert claims["exp"] > claims["iat"]
    assert claims["exp"] - claims["iat"] <= 300, "this secret is minted per request; keep it short"


def test_the_client_secret_names_the_key_in_its_header(configured):
    header = jwt.get_unverified_header(apple_revoke.client_secret())
    assert header["kid"] == "KEY7654321", "without kid Apple cannot pick the key and returns invalid_client"
    assert header["alg"] == "ES256", "a .p8 is an EC key; RS256 would be rejected"


def test_the_client_id_falls_back_to_the_identity_token_audience(monkeypatch):
    """For a native app both are the bundle identifier, so setting it twice is
    a chance for them to disagree."""
    monkeypatch.setattr(apple_revoke, "CLIENT_ID", "")
    import deps
    monkeypatch.setattr(deps, "APPLE_AUDIENCES", ["com.example.app", "host.exp.Exponent"])
    assert apple_revoke.default_client_id() == "com.example.app"


def test_an_escaped_newline_private_key_is_accepted():
    """A .p8 is a multi-line PEM and a deployment secret is a single-line text
    box, so `\\n` has to work — otherwise the key silently fails to parse."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "apple_revoke.py")).read()
    assert 'replace("\\\\n", "\\n")' in source


# --- the two network calls, with Apple faked ------------------------------

class FakeResponse:
    def __init__(self, status_code: int, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    """Captures what we send Apple, and replies with what the test wants."""

    def __init__(self, response, sink):
        self._response = response
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None):
        self._sink.append({"url": url, "data": data})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def fake_apple(monkeypatch, response):
    sink = []
    monkeypatch.setattr(apple_revoke.httpx, "AsyncClient",
                        lambda *a, **k: FakeClient(response, sink))
    return sink


def test_the_code_exchange_asks_apple_for_a_refresh_token(configured, monkeypatch):
    sink = fake_apple(monkeypatch, FakeResponse(200, {"refresh_token": "r3fr3sh"}))
    assert run_async(apple_revoke.exchange_code_for_refresh_token("auth-code-1")) == "r3fr3sh"
    sent = sink[0]
    assert sent["url"] == "https://appleid.apple.com/auth/token"
    assert sent["data"]["grant_type"] == "authorization_code"
    assert sent["data"]["code"] == "auth-code-1"
    assert sent["data"]["client_id"] == "com.emergent.tradeagentapp.kht259"
    assert sent["data"]["client_secret"]


def test_revocation_sends_the_refresh_token_with_the_matching_hint(configured, monkeypatch):
    sink = fake_apple(monkeypatch, FakeResponse(200))
    assert run_async(apple_revoke.revoke_refresh_token("r3fr3sh")) is True
    sent = sink[0]
    assert sent["url"] == "https://appleid.apple.com/auth/revoke"
    assert sent["data"]["token"] == "r3fr3sh"
    assert sent["data"]["token_type_hint"] == "refresh_token", \
        "the hint must match the token type or Apple cannot revoke it"


@pytest.mark.parametrize("response", [
    FakeResponse(400, text='{"error":"invalid_client"}'),
    FakeResponse(400, text='{"error":"invalid_grant"}'),
    FakeResponse(500, text="upstream error"),
    FakeResponse(200, {}),  # 200 but no token in the body
])
def test_the_code_exchange_reports_failure_rather_than_guessing(configured, monkeypatch, response):
    fake_apple(monkeypatch, response)
    assert run_async(apple_revoke.exchange_code_for_refresh_token("auth-code-1")) is None


@pytest.mark.parametrize("response", [
    FakeResponse(400, text='{"error":"invalid_client"}'),
    FakeResponse(401, text='{"error":"invalid_client"}'),
    FakeResponse(500, text="upstream error"),
])
def test_revocation_returns_false_on_anything_but_200(configured, monkeypatch, response):
    fake_apple(monkeypatch, response)
    assert run_async(apple_revoke.revoke_refresh_token("r3fr3sh")) is False


def test_neither_call_ever_raises(configured, monkeypatch):
    """A sign-in must not fail because an optional exchange did, and a deletion
    must not fail because Apple was unreachable."""
    fake_apple(monkeypatch, RuntimeError("connection reset"))
    assert run_async(apple_revoke.exchange_code_for_refresh_token("c")) is None
    fake_apple(monkeypatch, RuntimeError("connection reset"))
    assert run_async(apple_revoke.revoke_refresh_token("t")) is False


# --- wiring: sign-in stores it, deletion spends it ------------------------

def test_the_signin_endpoint_accepts_an_authorization_code():
    fields = auth_routes.SocialSignIn.model_fields
    assert "authorization_code" in fields
    assert fields["authorization_code"].default is None, "sign-in must work without it"


def test_the_stored_token_is_only_written_on_success(monkeypatch):
    from core import db
    uid = f"apple-{uuid.uuid4()}"
    run_async(db.users.insert_one({"id": uid, "apple_sub": "001.x.0"}))
    try:
        async def failed(_code):
            return None
        monkeypatch.setattr(apple_revoke, "exchange_code_for_refresh_token", failed)
        run_async(auth_routes.store_apple_refresh_token(uid, "code"))
        doc = run_async(db.users.find_one({"id": uid}))
        assert "apple_refresh_token" not in doc, "a failed exchange wrote to the account"

        async def worked(_code):
            return "r3fr3sh"
        monkeypatch.setattr(apple_revoke, "exchange_code_for_refresh_token", worked)
        run_async(auth_routes.store_apple_refresh_token(uid, "code"))
        doc = run_async(db.users.find_one({"id": uid}))
        assert doc["apple_refresh_token"] == "r3fr3sh"
    finally:
        run_async(db.users.delete_one({"id": uid}))


def test_deletion_revokes_before_it_deletes():
    """Order matters: the token lives on the user document, so revoking after
    the delete would have nothing to read."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "routes", "auth_routes.py")).read()
    body = source[source.index("async def delete_account"):]
    revoke_at = body.index("revoke_refresh_token")
    delete_at = body.index("db.users.delete_one")
    assert revoke_at < delete_at, "the account is deleted before its Apple token is revoked"


def test_deletion_still_succeeds_when_revocation_fails(monkeypatch):
    """Apple's own guidance: complete the deletion whether or not the token
    could be revoked. A user must never be unable to delete their account
    because a third party is down."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "routes", "auth_routes.py")).read()
    body = source[source.index("async def delete_account"):]
    revoke_line = [ln for ln in body.splitlines() if "revoke_refresh_token" in ln][0]
    assert "raise" not in revoke_line
    # And the function has no raise at all after the revocation attempt.
    assert "raise" not in body[body.index("revoke_refresh_token"):]


def test_the_private_key_is_not_committed_anywhere():
    """The .p8 downloads exactly once and cannot be re-downloaded; a leaked one
    has to be revoked in the developer account and every deployment updated."""
    import subprocess
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    tracked = subprocess.run(["git", "grep", "-l", "BEGIN PRIVATE KEY"],
                             cwd=root, capture_output=True, text=True)
    hits = [line for line in tracked.stdout.splitlines()
            if not line.startswith("backend/tests/")]
    assert not hits, f"a private key appears in tracked files: {hits}"


# --- this deployment, right now -------------------------------------------

def test_revocation_is_configured_in_this_deployment():
    """Pinned because the symptom of a missing value is invisible: deletion
    still succeeds, and only a log line says Apple was never told. Verified
    against Apple's live endpoints when these were installed — /auth/revoke
    answered 200 and /auth/token answered `invalid_grant` (not
    `invalid_client`), which is Apple confirming the team id, key id and
    private key all match."""
    assert apple_revoke.TEAM_ID, "APPLE_TEAM_ID is unset — Apple tokens will never be revoked"
    assert len(apple_revoke.KEY_ID) == 10, "an Apple key id is exactly 10 characters"
    assert apple_revoke.KEY_ID.isalnum(), "an Apple key id has no punctuation — check it isn't a password"
    assert apple_revoke.PRIVATE_KEY.startswith("-----BEGIN PRIVATE KEY-----"), \
        "APPLE_PRIVATE_KEY is not a PEM — paste the whole .p8, first and last lines included"
    assert apple_revoke.configured() is True


def test_the_installed_private_key_is_a_usable_es256_key():
    """A wrong-curve or RSA key produces a client secret Apple rejects as
    `invalid_client`, which looks identical to a wrong team id."""
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(apple_revoke.PRIVATE_KEY.encode(), password=None)
    assert key.curve.name == "secp256r1", "Apple signs with ES256 (P-256)"
