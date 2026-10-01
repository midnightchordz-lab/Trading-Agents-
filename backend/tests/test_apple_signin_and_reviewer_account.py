"""Sign in with Apple, and the store-reviewer demo account.

WHY BOTH LANDED TOGETHER: an App Store reviewer could not get into this app at
all. Sign-in was a one-time code sent to a phone or an inbox the reviewer does
not have, and the Apple button said "APPLE — SOON" — which is itself a
rejection, because an app offering Google sign-in MUST offer Sign in with Apple.

THE APPLE AUDIENCE IS THE WHOLE GAME: for a NATIVE iOS sign-in Apple sets the
token's `aud` to the app's BUNDLE IDENTIFIER, not to a Services ID (that is the
web/redirect flow only). The same sign-in inside Expo Go carries
`host.exp.Exponent`, because Expo Go is a different app. An audience list
missing either one turns every sign-in into a 401 with nothing in the log to
explain it, so it is a list and both entries are asserted here.
"""
import json
import os
import sys
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import base64url_encode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402
import deps  # noqa: E402
from routes import auth_routes  # noqa: E402

BUNDLE_ID = "com.emergent.tradeagentapp.kht259"
EXPO_GO_ID = "host.exp.Exponent"


# --- a synthetic Apple: our own key pair, published as a JWKS --------------

@pytest.fixture(scope="module")
def apple():
    """Stands in for Apple's signing key, so a real token can be minted and
    verified without a device or a network call."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()

    def b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64url_encode(raw).decode()

    jwks = [{"kty": "RSA", "kid": "test-key-1", "use": "sig", "alg": "RS256",
             "n": b64(numbers.n), "e": b64(numbers.e)}]
    return {"key": key, "jwks": jwks}


def mint(apple, **overrides) -> str:
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": BUNDLE_ID,
        "sub": f"001234.{uuid.uuid4().hex}.0000",
        "email": "someone@privaterelay.appleid.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, apple["key"], algorithm="RS256",
                      headers={"kid": "test-key-1"})


AUDIENCES = [BUNDLE_ID, EXPO_GO_ID]


def test_a_native_ios_token_verifies(apple):
    """`aud` is the bundle identifier — the case that matters on a real phone."""
    claims = au.verify_apple_id_token(mint(apple), AUDIENCES, apple["jwks"])
    assert claims and claims["sub"]
    assert claims["email"] == "someone@privaterelay.appleid.com"


def test_an_expo_go_token_verifies(apple):
    """Expo Go mints tokens for itself. Without this entry every sign-in
    during development is a 401 that looks like a code bug."""
    claims = au.verify_apple_id_token(mint(apple, aud=EXPO_GO_ID), AUDIENCES, apple["jwks"])
    assert claims and claims["sub"]


def test_a_single_audience_string_is_still_accepted(apple):
    """The signature used to take one string; a deployment that still sets the
    old APPLE_SERVICES_ID must not break."""
    assert au.verify_apple_id_token(mint(apple), BUNDLE_ID, apple["jwks"])


def test_someone_elses_audience_is_refused(apple):
    """A token minted for a DIFFERENT app is a valid Apple token — it just
    isn't for us. Accepting it would let any app's token sign in here."""
    assert au.verify_apple_id_token(mint(apple, aud="com.someone.else"), AUDIENCES, apple["jwks"]) is None


def test_no_configured_audience_refuses_everything(apple):
    """An empty list must not mean "skip the check"."""
    assert au.verify_apple_id_token(mint(apple), [], apple["jwks"]) is None
    assert au.verify_apple_id_token(mint(apple), "", apple["jwks"]) is None
    assert au.verify_apple_id_token(mint(apple), None, apple["jwks"]) is None


def test_a_wrong_issuer_is_refused(apple):
    assert au.verify_apple_id_token(
        mint(apple, iss="https://evil.example.com"), AUDIENCES, apple["jwks"]) is None


def test_an_expired_token_is_refused(apple):
    assert au.verify_apple_id_token(
        mint(apple, exp=int(time.time()) - 60, iat=int(time.time()) - 600),
        AUDIENCES, apple["jwks"]) is None


def test_a_token_signed_by_the_wrong_key_is_refused(apple):
    """The attack this all exists to stop: a self-signed token claiming to be
    someone's Apple identity."""
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({"iss": "https://appleid.apple.com", "aud": BUNDLE_ID,
                        "sub": "001234.attacker.0000", "iat": int(time.time()),
                        "exp": int(time.time()) + 600},
                       impostor, algorithm="RS256", headers={"kid": "test-key-1"})
    assert au.verify_apple_id_token(token, AUDIENCES, apple["jwks"]) is None


def test_an_unknown_kid_is_refused(apple):
    token = jwt.encode({"iss": "https://appleid.apple.com", "aud": BUNDLE_ID,
                        "sub": "x", "exp": int(time.time()) + 600},
                       apple["key"], algorithm="RS256", headers={"kid": "not-apples"})
    assert au.verify_apple_id_token(token, AUDIENCES, apple["jwks"]) is None


@pytest.mark.parametrize("junk", ["", "not.a.token", "a.b", "...", "null"])
def test_garbage_never_raises(junk, apple):
    assert au.verify_apple_id_token(junk, AUDIENCES, apple["jwks"]) is None


def test_a_token_with_no_email_still_verifies(apple):
    """Apple sends the address only on the FIRST authorisation, and private
    relay can withhold it entirely. `sub` is the identity, not the email."""
    token = jwt.encode({"iss": "https://appleid.apple.com", "aud": BUNDLE_ID,
                        "sub": "001234.norelay.0000", "iat": int(time.time()),
                        "exp": int(time.time()) + 600},
                       apple["key"], algorithm="RS256", headers={"kid": "test-key-1"})
    claims = au.verify_apple_id_token(token, AUDIENCES, apple["jwks"])
    assert claims["sub"] == "001234.norelay.0000"
    assert claims["email"] is None


# --- configuration, which is where this feature actually fails -------------

def test_both_audiences_are_configured():
    assert BUNDLE_ID in deps.APPLE_AUDIENCES, \
        "the native iOS audience (the bundle identifier) is missing — every sign-in on a real phone would 401"
    assert EXPO_GO_ID in deps.APPLE_AUDIENCES, \
        "host.exp.Exponent is missing — Apple sign-in cannot be tested in Expo Go"


def test_the_configured_bundle_id_matches_the_app():
    """If these two ever disagree, the symptom is a 401 the logs cannot
    explain, so it is asserted rather than trusted."""
    app_json = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..",
                                           "frontend", "app.json")))
    ios = app_json["expo"]["ios"]
    assert ios["bundleIdentifier"] in deps.APPLE_AUDIENCES
    assert ios.get("usesAppleSignIn") is True, \
        "the iOS entitlement is off, so the native button cannot work in a build"


def test_expo_apple_authentication_plugin_is_registered():
    """The expo-apple-authentication config plugin MUST appear in the
    app.json plugins array. Without it the build process may silently omit
    the `com.apple.developer.applesignin` entitlement and the
    `CFBundleAllowMixedLocalizations` Info.plist key — both needed for
    Apple Sign-In to work on a real device. `ios.usesAppleSignIn: true`
    alone is not sufficient: that flag triggers a warning, not the actual
    entitlement write, if the plugin is absent from the list.

    This is the most likely reason the first App Store review failed."""
    app_json = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..",
                                           "frontend", "app.json")))
    plugins = app_json["expo"].get("plugins", [])
    # plugins entries can be strings or [string, options] arrays.
    plugin_names = [p if isinstance(p, str) else p[0] for p in plugins]
    assert "expo-apple-authentication" in plugin_names, (
        "expo-apple-authentication is missing from app.json plugins — "
        "the build will lack the Apple Sign-In entitlement on a real device"
    )


def test_the_app_no_longer_says_apple_is_coming_soon():
    """Shipping "APPLE — SOON" next to a working Google button is itself an App
    Store rejection."""
    login = open(os.path.join(os.path.dirname(__file__), "..", "..",
                              "frontend", "src", "components", "LoginScreen.tsx")).read()
    assert "APPLE — SOON" not in login
    assert "AppleAuthenticationButton" in login, \
        "Apple's own button component is required — a styled Pressable is a rejection"
    assert "isAvailableAsync" in login, \
        "the button must be gated, or Android and web render something that cannot work"


def test_the_endpoint_no_longer_advertises_a_services_id():
    source = open(os.path.join(os.path.dirname(__file__), "..", "routes", "auth_routes.py")).read()
    assert "APPLE_SERVICES_ID" not in source


# --- the reviewer demo account --------------------------------------------

def test_the_reviewer_account_is_configured():
    assert auth_routes.review_identifier(), \
        "no reviewer demo account — a reviewer cannot receive a one-time code"


def test_the_reviewer_identifier_matches_however_it_is_typed():
    configured = auth_routes.review_identifier()
    assert auth_routes.is_review_login(configured)
    # The same inbox in a different case is the same account.
    upper = au.normalize_identifier(auth_routes.REVIEW_IDENTIFIER.upper())[1]
    assert auth_routes.is_review_login(upper)


@pytest.mark.parametrize("other", [
    "someone@else.com",
    "+919800000099",
    "",
    None,
])
def test_nobody_else_gets_the_fixed_code(other):
    assert auth_routes.is_review_login(other) is False


def test_a_near_miss_identifier_is_not_the_reviewer():
    """A different address that merely looks similar must not match — this is
    what would turn one demo account into a general bypass."""
    configured = auth_routes.review_identifier()
    assert auth_routes.is_review_login(configured + "x") is False
    assert auth_routes.is_review_login("x" + configured) is False


def test_it_is_off_unless_both_variables_are_set(monkeypatch):
    monkeypatch.setattr(auth_routes, "REVIEW_IDENTIFIER", "")
    assert auth_routes.review_identifier() is None
    monkeypatch.setattr(auth_routes, "REVIEW_IDENTIFIER", "appreview@example.com")
    monkeypatch.setattr(auth_routes, "REVIEW_OTP", "")
    assert auth_routes.review_identifier() is None, "an empty code must not be accepted as a code"
    monkeypatch.setattr(auth_routes, "REVIEW_OTP", "12")
    assert auth_routes.review_identifier() is None, "a 2-character code is not a code"


def test_the_reviewer_is_not_an_admin():
    """An admin account is never billed, so a reviewer using one would never
    see the purchase flow Apple wants to inspect — and it would hand a
    published credential real privileges."""
    from deps import ADMIN_IDENTIFIERS
    configured = auth_routes.review_identifier()
    assert configured not in ADMIN_IDENTIFIERS


def test_verify_has_no_special_case_for_the_reviewer():
    """The whole safety argument: the request endpoint stores the fixed code's
    hash where a random one would go, and verify is untouched — so expiry, the
    attempt cap and the single-use claim all still apply. A branch in verify
    would be a real bypass."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "routes", "auth_routes.py")).read()
    verify = source[source.index("async def auth_otp_verify"):]
    verify = verify[:verify.index("class GoogleSession")]
    for leak in ("REVIEW_OTP", "is_review_login", "review_login"):
        assert leak not in verify, f"auth_otp_verify references {leak} — the bypass has moved into verify"


def test_the_fixed_code_is_never_in_a_response_or_a_log():
    source = open(os.path.join(os.path.dirname(__file__), "..", "routes", "auth_routes.py")).read()
    request_fn = source[source.index("async def auth_otp_request"):source.index("async def auth_otp_verify")]
    assert "REVIEW_OTP" in request_fn  # it is used…
    # …but only to hash it.
    assert "au.hash_otp(REVIEW_OTP)" in request_fn
    for leak in ('"debug_otp": REVIEW_OTP', "f\"{REVIEW_OTP}", "{REVIEW_OTP}"):
        assert leak not in request_fn, "the fixed code is being echoed or logged"


def test_review_credentials_not_committed_to_git():
    """The reviewer identifier and OTP must never appear together in any
    tracked file — that is what turned commit 0acddda into a public
    credential. A single field in isolation is fine (the identifier is in
    test_credentials.md as documentation); what is dangerous is the pair.

    This test reads the LIVE values from the env so it will catch a
    rotation that accidentally re-embeds a new code in source too."""
    import subprocess
    identifier = auth_routes.REVIEW_IDENTIFIER
    otp = auth_routes.REVIEW_OTP
    if not identifier or not otp:
        pytest.skip("reviewer account not configured")
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    # Search committed content (--cached searches the index; without a ref
    # git grep searches the working tree, which includes .env — we want
    # the committed/staged snapshot, which is what a reader of the public
    # repo sees).
    result = subprocess.run(
        ["git", "grep", "-l", otp],
        cwd=root, capture_output=True, text=True,
    )
    hits = [f for f in result.stdout.splitlines()
            if not f.startswith("backend/.env") and not f.startswith(".env")]
    assert not hits, (
        f"REVIEW_OTP appears in tracked file(s): {hits} — "
        "rotate the code immediately and remove it from those files"
    )
