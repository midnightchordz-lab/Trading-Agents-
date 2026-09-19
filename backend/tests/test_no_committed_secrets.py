"""No credential may live in a tracked file.

A testing agent once pasted the Razorpay key id AND secret into its own report
(`test_reports/iteration_15.json`), which is committed — so the secret was in
the repository, and in its history, for anyone with read access. This test is
the guard that stops it happening again silently: it scans every git-tracked
file both for secret-SHAPED strings and for the ACTUAL live values currently in
`backend/.env`.

`backend/.env` itself is untracked and is where every credential belongs.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
ENV_PATH = REPO / "backend" / ".env"

# Values that are not credentials even though they sit in the same file: they
# are non-secret configuration and DO legitimately appear in code and tests.
NOT_SECRET = {
    "MONGO_URL", "DB_NAME", "PUBLIC_BASE_URL", "CORS_ORIGINS", "EMAIL_FROM_NAME",
    "EMAIL_FROM", "TWILIO_FROM_NUMBER", "ADMIN_IDENTIFIERS", "AUTH_REQUIRED_ENABLED",
    "AUTH_DEBUG_RETURN_OTP", "WALLET_ENFORCEMENT_ENABLED", "LAUNCH_FREE_UNTIL",
    "APPLE_SERVICES_ID", "PUBLIC_HOST_SUFFIXES", "REVENUECAT_IOS_KEY",
}

# Shapes that are credentials wherever they appear.
SECRET_PATTERNS = {
    "Razorpay key id": re.compile(r"rzp_(?:test|live)_[A-Za-z0-9]{10,}"),
    "OpenAI key": re.compile(r"sk-[A-Za-z0-9_-]{24,}"),
    "Twilio account sid": re.compile(r"\bAC[a-f0-9]{32}\b"),
    "Twilio auth token": re.compile(r"\bSK[a-f0-9]{32}\b"),
    "Emergent key": re.compile(r"sk-emergent-[A-Za-z0-9]{10,}"),
}

SKIP_DIRS = ("frontend/node_modules", "frontend/.metro-cache", "frontend/yarn.lock",
             "frontend/package-lock.json", "backend/tests/test_no_committed_secrets.py")


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    files = []
    for rel in out.stdout.splitlines():
        if any(rel.startswith(skip) or rel == skip for skip in SKIP_DIRS):
            continue
        path = REPO / rel
        if path.is_file() and path.stat().st_size < 4_000_000:
            files.append(path)
    return files


def read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


@pytest.fixture(scope="module")
def corpus():
    return [(p, read(p)) for p in tracked_files()]


@pytest.mark.parametrize("label", sorted(SECRET_PATTERNS))
def test_no_secret_shaped_strings_in_tracked_files(corpus, label):
    pattern = SECRET_PATTERNS[label]
    hits = []
    for path, text in corpus:
        for match in pattern.findall(text):
            hits.append(f"{path.relative_to(REPO)}: {match[:12]}…")
    assert not hits, f"{label} found in tracked files: {hits}"


def test_no_live_env_value_in_any_tracked_file(corpus):
    """The strongest form of the check: the real values, right now."""
    if not ENV_PATH.exists():
        pytest.skip("backend/.env not present")
    values = {
        k: v for k, v in dotenv_values(ENV_PATH).items()
        if v and len(v) >= 12 and k not in NOT_SECRET
    }
    assert values, "no credentials found in backend/.env — the check would be vacuous"
    leaks = []
    for path, text in corpus:
        for name, value in values.items():
            if value in text:
                leaks.append(f"{name} in {path.relative_to(REPO)}")
    assert not leaks, f"credentials committed: {leaks}"


def test_env_file_itself_is_not_tracked():
    out = subprocess.run(["git", "ls-files", "backend/.env", "frontend/.env"],
                         cwd=REPO, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"env files must stay untracked: {out.stdout}"


def test_no_credential_reaches_the_mobile_app(corpus):
    """Anything the app can read, a user can read.

    The Expo bundle is public by definition, so a credential referenced from
    `frontend/` is a published credential — `EXPO_PUBLIC_*` values especially,
    since Metro inlines them into the JavaScript. The one key the app IS given
    (RevenueCat's iOS SDK key, fetched from /api/pay/iap/config) is public by
    design: it ships inside every App Store binary and can only start a
    purchase, never read or move money.
    """
    if not ENV_PATH.exists():
        pytest.skip("backend/.env not present")
    secrets = {
        k: v for k, v in dotenv_values(ENV_PATH).items()
        if v and len(v) >= 12 and k not in NOT_SECRET
    }
    leaks = []
    for path, text in corpus:
        rel = str(path.relative_to(REPO))
        if not rel.startswith("frontend/"):
            continue
        for name, value in secrets.items():
            if value in text:
                leaks.append(f"{name} in {rel}")
    assert not leaks, f"backend credentials referenced from the app: {leaks}"


def test_frontend_env_publishes_nothing_secret():
    """Every EXPO_PUBLIC_* value is compiled into the downloadable bundle."""
    fe = REPO / "frontend" / ".env"
    if not fe.exists():
        pytest.skip("frontend/.env not present")
    published = {k: v for k, v in dotenv_values(fe).items() if k.startswith("EXPO_PUBLIC_")}
    for name, value in published.items():
        assert value, f"{name} is empty"
        # A URL is fine. A key is not.
        assert value.startswith("http"), f"{name} looks like more than a URL: {name}={value[:6]}…"
    for pattern in SECRET_PATTERNS.values():
        for name, value in published.items():
            assert not pattern.search(value or ""), f"{name} contains a credential"


def test_debug_switches_are_off():
    """`AUTH_DEBUG_RETURN_OTP` returns the one-time code in the API response —
    it exists for local UI testing and turns sign-in into a formality if it is
    ever left on."""
    if not ENV_PATH.exists():
        pytest.skip("backend/.env not present")
    env = dotenv_values(ENV_PATH)
    assert (env.get("AUTH_DEBUG_RETURN_OTP") or "false").lower() == "false"
    assert (env.get("AUTH_REQUIRED_ENABLED") or "").lower() == "true"
    assert (env.get("WALLET_ENFORCEMENT_ENABLED") or "").lower() == "true"


def test_the_scan_actually_reads_files(corpus):
    """Guards the guard: a broken `git ls-files` or an over-eager skip list
    would make every assertion above pass by scanning nothing."""
    assert len(corpus) > 50
    assert any(str(p).endswith("backend/routes/payments.py") for p, _ in corpus)
    assert any(text.strip() for _, text in corpus)
