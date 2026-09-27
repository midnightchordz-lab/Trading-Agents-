"""Adversarial audit of the reviewer demo account, Apple sign-in endpoint,
and the .gitignore negation defence.

The reviewer account is the most dangerous thing added this iteration — a fixed
code for one configured identifier. This suite tries to turn it into a general
OTP bypass:
- send the reviewer's fixed code from a different identifier
- send a wrong code and confirm it's rejected the same as any other identifier
- use the reviewer code twice (single-use claim must hold)
- confirm nothing in the /request response leaks the code
- confirm review_identifier() is None when unconfigured

Plus Apple sign-in endpoint (rejection paths only — endpoint fetches real
Apple JWKS, so cannot mint an accepted token) and .gitignore negation guard.

Tests are written to be isolated: each test uses its own X-Forwarded-For via
conftest so per-IP rate limits from unrelated tests don't leak in.
"""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import load_dotenv
from jwt.utils import base64url_encode
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402
import deps  # noqa: E402
from routes import auth_routes  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"

_mongo = MongoClient(os.environ["MONGO_URL"])
_db = _mongo[os.environ.get("DB_NAME", "test_database")]


def _clear_otp_records_for(identifier: str) -> None:
    """Remove any prior OTP requests for this identifier so a per-identifier
    rate limit from an earlier test doesn't interfere."""
    _id_type, normalized = au.normalize_identifier(identifier)
    if normalized:
        _db.otp_requests.delete_many({"identifier": normalized})


# ---------------------------------------------------------------------------
# Reviewer demo account — end-to-end through the running backend
# ---------------------------------------------------------------------------

REVIEW_IDENTIFIER = os.environ["REVIEW_IDENTIFIER"]
REVIEW_OTP = os.environ["REVIEW_OTP"]


def test_reviewer_otp_request_returns_sent_true_with_no_code_leaked():
    _clear_otp_records_for(REVIEW_IDENTIFIER)
    res = requests.post(f"{BASE}/auth/otp/request",
                        json={"identifier": REVIEW_IDENTIFIER}, timeout=10)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("sent") is True
    # Not the code, not "debug_otp", not the raw REVIEW_OTP string — anywhere.
    raw = res.text
    assert REVIEW_OTP not in raw, "the fixed code leaked in the response body"
    assert "debug_otp" not in body, "debug_otp must not appear for the reviewer flow"
    assert "otp" not in body, "the OTP field must not appear at all"


def test_reviewer_end_to_end_verify_returns_a_working_token():
    _clear_otp_records_for(REVIEW_IDENTIFIER)
    r1 = requests.post(f"{BASE}/auth/otp/request",
                       json={"identifier": REVIEW_IDENTIFIER}, timeout=10)
    assert r1.status_code == 200, r1.text
    device_id = f"reviewer-test-{uuid.uuid4()}"
    r2 = requests.post(f"{BASE}/auth/otp/verify",
                       json={"identifier": REVIEW_IDENTIFIER,
                             "otp": REVIEW_OTP,
                             "device_id": device_id}, timeout=10)
    assert r2.status_code == 200, r2.text
    token = r2.json().get("token")
    assert token, "no token returned from verify"

    # /auth/me works with the token
    me = requests.get(f"{BASE}/auth/me",
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["email"] == au.normalize_identifier(REVIEW_IDENTIFIER)[1]

    # Wallet: not admin, standard 10 free credits
    wal = requests.get(f"{BASE}/wallet/balance",
                       headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert wal.status_code == 200, wal.text
    wbody = wal.json()
    assert wbody.get("is_admin") is False, "reviewer must NOT be admin"
    # `free_credits_remaining` may live under different key names; check for both
    remaining = wbody.get("free_credits_remaining")
    if remaining is None:
        remaining = wbody.get("free_credits")
    assert remaining == 10, f"expected 10 free credits, got {remaining} ({wbody})"


def test_a_different_identifier_cannot_sign_in_with_the_fixed_code():
    """The whole point: the fixed code must not be a general bypass."""
    other = f"outsider-{uuid.uuid4().hex}@example.com"
    _clear_otp_records_for(other)
    # Request a code for a different identifier (a real OTP is issued for it —
    # not the reviewer code — so this simulates an attacker knowing REVIEW_OTP
    # and trying it on their own address).
    r1 = requests.post(f"{BASE}/auth/otp/request",
                       json={"identifier": other}, timeout=10)
    # 200 (email delivery may or may not be configured — we only care about
    # what /verify does).
    assert r1.status_code in (200, 502), r1.text
    r2 = requests.post(f"{BASE}/auth/otp/verify",
                       json={"identifier": other, "otp": REVIEW_OTP}, timeout=10)
    assert r2.status_code in (400, 429), \
        f"a foreign account accepted the fixed code! {r2.status_code} {r2.text}"


def test_a_wrong_code_for_the_reviewer_identifier_is_rejected():
    _clear_otp_records_for(REVIEW_IDENTIFIER)
    r1 = requests.post(f"{BASE}/auth/otp/request",
                       json={"identifier": REVIEW_IDENTIFIER}, timeout=10)
    assert r1.status_code == 200
    r2 = requests.post(f"{BASE}/auth/otp/verify",
                       json={"identifier": REVIEW_IDENTIFIER, "otp": "000000"}, timeout=10)
    assert r2.status_code == 400, r2.text
    detail = (r2.json().get("detail") or "").lower()
    assert "incorrect" in detail or "code" in detail


def test_reviewer_code_is_single_use():
    """After verifying once, the same issued record must not verify again —
    the atomic verified:False -> True claim must hold."""
    _clear_otp_records_for(REVIEW_IDENTIFIER)
    r1 = requests.post(f"{BASE}/auth/otp/request",
                       json={"identifier": REVIEW_IDENTIFIER}, timeout=10)
    assert r1.status_code == 200
    r2a = requests.post(f"{BASE}/auth/otp/verify",
                        json={"identifier": REVIEW_IDENTIFIER, "otp": REVIEW_OTP}, timeout=10)
    assert r2a.status_code == 200, r2a.text
    # Second verify with the same code (no new /request) must fail — the
    # record was marked verified, so `find_one({verified: False})` returns None.
    r2b = requests.post(f"{BASE}/auth/otp/verify",
                        json={"identifier": REVIEW_IDENTIFIER, "otp": REVIEW_OTP}, timeout=10)
    assert r2b.status_code == 400, \
        f"reviewer code was replayable: {r2b.status_code} {r2b.text}"


def test_five_wrong_attempts_lock_out_the_reviewer_identifier_too():
    """The 5-attempt cap in verify must apply unchanged — the reviewer code is
    stored via the same path, so is_review_login is irrelevant here."""
    _clear_otp_records_for(REVIEW_IDENTIFIER)
    r1 = requests.post(f"{BASE}/auth/otp/request",
                       json={"identifier": REVIEW_IDENTIFIER}, timeout=10)
    assert r1.status_code == 200
    # 5 wrong tries → 4 x 400 then 5th escalates to 429 (from the $inc/AFTER
    # check inside verify).
    statuses = []
    for _ in range(5):
        r = requests.post(f"{BASE}/auth/otp/verify",
                          json={"identifier": REVIEW_IDENTIFIER, "otp": "111111"}, timeout=10)
        statuses.append(r.status_code)
    assert 429 in statuses, f"attempt cap did not trigger; statuses={statuses}"
    # After lockout, even the right code must not work on this record.
    r_right = requests.post(f"{BASE}/auth/otp/verify",
                            json={"identifier": REVIEW_IDENTIFIER, "otp": REVIEW_OTP}, timeout=10)
    assert r_right.status_code in (400, 429), \
        f"right code accepted AFTER lockout: {r_right.status_code} {r_right.text}"


def test_review_identifier_is_none_when_unconfigured(monkeypatch):
    """OFF unless BOTH env vars set. Verified via monkeypatch — the running
    server keeps its real values."""
    monkeypatch.setattr(auth_routes, "REVIEW_IDENTIFIER", "")
    assert auth_routes.review_identifier() is None
    assert auth_routes.is_review_login("anything@example.com") is False

    monkeypatch.setattr(auth_routes, "REVIEW_IDENTIFIER", "appreview@example.com")
    monkeypatch.setattr(auth_routes, "REVIEW_OTP", "")
    assert auth_routes.review_identifier() is None
    monkeypatch.setattr(auth_routes, "REVIEW_OTP", "12")
    assert auth_routes.review_identifier() is None, "3-char code must be rejected"
    monkeypatch.setattr(auth_routes, "REVIEW_OTP", "1234")
    assert auth_routes.review_identifier() is not None, "4+ char code should be accepted"


def test_review_off_means_is_review_login_returns_false_for_everything(monkeypatch):
    monkeypatch.setattr(auth_routes, "REVIEW_IDENTIFIER", "")
    for candidate in ["appreview@tradeagent.app", "someone@else.com", "+919999999999", "", None]:
        assert auth_routes.is_review_login(candidate) is False


# ---------------------------------------------------------------------------
# Apple sign-in endpoint — endpoint fetches real Apple JWKS, so only rejections
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic_apple():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key


def _mint(private_key, **overrides) -> str:
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": "com.emergent.tradeagentapp.kht259",
        "sub": f"001234.{uuid.uuid4().hex}.0000",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256",
                      headers={"kid": "attacker-key"})


def _assert_apple_reject(resp):
    """Endpoint should reject with 401. 502 acceptable only if Apple's JWKS
    host is unreachable from the container."""
    assert resp.status_code in (401, 502), \
        f"unexpected accept: {resp.status_code} {resp.text}"
    if resp.status_code == 502:
        pytest.skip("Apple JWKS host unreachable from container — cannot assert 401")


def test_apple_endpoint_no_longer_returns_501():
    res = requests.post(f"{BASE}/auth/apple", json={"token": "not.a.token"}, timeout=15)
    assert res.status_code != 501, res.text


def test_apple_rejects_junk_token():
    res = requests.post(f"{BASE}/auth/apple", json={"token": "clearly-not-a-jwt"}, timeout=15)
    _assert_apple_reject(res)


def test_apple_rejects_token_signed_by_a_different_key(synthetic_apple):
    tok = _mint(synthetic_apple)
    res = requests.post(f"{BASE}/auth/apple", json={"token": tok}, timeout=15)
    _assert_apple_reject(res)


def test_apple_rejects_token_with_someone_elses_audience(synthetic_apple):
    tok = _mint(synthetic_apple, aud="com.someone.else.app")
    res = requests.post(f"{BASE}/auth/apple", json={"token": tok}, timeout=15)
    _assert_apple_reject(res)


def test_apple_audiences_contains_bundle_id_and_expo_go():
    assert "com.emergent.tradeagentapp.kht259" in deps.APPLE_AUDIENCES, \
        f"bundle id missing from APPLE_AUDIENCES: {deps.APPLE_AUDIENCES}"
    assert "host.exp.Exponent" in deps.APPLE_AUDIENCES, \
        f"host.exp.Exponent missing from APPLE_AUDIENCES: {deps.APPLE_AUDIENCES}"


def test_frontend_app_json_has_uses_apple_sign_in():
    import json
    app_json = json.load(open(
        Path(__file__).parent.parent.parent / "frontend" / "app.json"))
    assert app_json["expo"]["ios"].get("usesAppleSignIn") is True
    assert app_json["expo"]["ios"]["bundleIdentifier"] == "com.emergent.tradeagentapp.kht259"


# ---------------------------------------------------------------------------
# Regression: normal OTP flow for a non-reviewer email is unchanged
# ---------------------------------------------------------------------------

def test_normal_otp_request_for_random_email_still_works():
    random_email = f"nobody-{uuid.uuid4().hex}@example.com"
    _clear_otp_records_for(random_email)
    res = requests.post(f"{BASE}/auth/otp/request",
                        json={"identifier": random_email}, timeout=15)
    # Never 5xx (bar 502/503 which are acceptable transient states),
    # and definitely not the reviewer bypass.
    assert res.status_code in (200, 400, 429, 502, 503), res.text
    if res.status_code == 200:
        body = res.json()
        assert body.get("sent") is True
        assert "debug_otp" not in body, "AUTH_DEBUG_RETURN_OTP should be off in prod-like"
    # And REVIEW_OTP must never appear in any response for a non-reviewer.
    assert REVIEW_OTP not in res.text


def test_google_auth_session_route_is_still_present():
    """Google sign-in must not have been disturbed."""
    res = requests.post(f"{BASE}/auth/session",
                        json={"session_id": "bogus-session-id"}, timeout=15)
    # Bogus session id → 401/502, never 404 or 501.
    assert res.status_code in (401, 502), \
        f"Google /auth/session unexpectedly responds {res.status_code}: {res.text}"


# ---------------------------------------------------------------------------
# .gitignore negation guard
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent


def _git_status_ignored(paths):
    """Return the set of paths git says are ignored (`!!` prefix)."""
    done = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"] + paths,
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return {line[3:] for line in done.stdout.splitlines() if line.startswith("!!")}


def test_env_files_not_git_ignored_baseline():
    ignored = _git_status_ignored(["backend/.env", "frontend/.env"])
    assert "backend/.env" not in ignored, \
        "backend/.env is git-ignored — deploy will ship without live keys"
    assert "frontend/.env" not in ignored, \
        "frontend/.env is git-ignored — deploy will ship without frontend config"


def test_appending_star_env_to_root_gitignore_does_not_win():
    """The negation is in backend/.gitignore and frontend/.gitignore. Even if
    something appends `*.env` to the root .gitignore, the subdirectory
    negation must still take precedence."""
    root_gitignore = REPO_ROOT / ".gitignore"
    original = root_gitignore.read_bytes()
    try:
        with open(root_gitignore, "ab") as fh:
            fh.write(b"\n*.env\n")
        ignored = _git_status_ignored(["backend/.env", "frontend/.env"])
        assert "backend/.env" not in ignored, \
            "appending `*.env` to root .gitignore bypassed the subdirectory negation"
        assert "frontend/.env" not in ignored, \
            "appending `*.env` to root .gitignore bypassed the subdirectory negation"
    finally:
        # Always restore the root .gitignore exactly as we found it.
        root_gitignore.write_bytes(original)


# ---------------------------------------------------------------------------
# The fixed reviewer code never appears in any tracked source file
# ---------------------------------------------------------------------------

def test_review_otp_not_hardcoded_in_any_tracked_file():
    """The value lives only in backend/.env (which is negated but only present
    on the deploy image, not tracked as content in the way test reports are).
    A grep across tracked files must find it nowhere."""
    done = subprocess.run(
        ["git", "grep", "-lF", REVIEW_OTP],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    # git grep exits 1 when there are no matches — the expected outcome.
    hits = [line for line in done.stdout.splitlines() if line.strip()]
    # Exclude backend/.env itself if it happens to be tracked (it is, via the
    # negation) — that's the ONE legitimate location.
    hits = [h for h in hits if h not in ("backend/.env", "frontend/.env")]
    assert not hits, f"REVIEW_OTP appears in tracked files: {hits}"
