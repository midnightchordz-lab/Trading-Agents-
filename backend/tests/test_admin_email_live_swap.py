"""LIVE env-swap test: change ADMIN_IDENTIFIERS to an email, restart
backend, verify email-only user gets admin bypass, then restore.

Isolated so it's easy to skip / run once.
"""
import os
import sys
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / "frontend" / ".env")
import auth as au  # noqa: E402
import pytest  # noqa: E402

# This test rewrites backend/.env and RESTARTS the backend mid-run, which
# breaks any test executing in parallel with it (they hit a dead server).
# Opt in explicitly: RUN_ENV_SWAP_TEST=1 python -m pytest tests/test_admin_email_live_swap.py -p no:xdist
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ENV_SWAP_TEST") != "1",
    reason="restarts the backend; run on its own with RUN_ENV_SWAP_TEST=1",
)

BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL")
            or os.environ["PUBLIC_BASE_URL"]).rstrip("/")
BACKEND_ENV = Path("/app/backend/.env")
_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ.get("DB_NAME", "test_database")]
JWT_SECRET = os.environ["JWT_SECRET"]


def _swap_admin_line(new_value: str):
    text = BACKEND_ENV.read_text()
    lines = []
    replaced = False
    for line in text.splitlines():
        if line.startswith("ADMIN_IDENTIFIERS="):
            lines.append(f"ADMIN_IDENTIFIERS={new_value}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append(f"ADMIN_IDENTIFIERS={new_value}")
    BACKEND_ENV.write_text("\n".join(lines) + "\n")


def _restart_backend():
    subprocess.run(["sudo", "supervisorctl", "restart", "backend"],
                   check=True, capture_output=True)
    # wait for it to come back up
    for _ in range(30):
        try:
            r = requests.get(f"{BASE_URL}/api/", timeout=2)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("backend didn't come back within 30s")


def test_admin_via_email_env_driven():
    admin_email = "ops-live-swap@example.com"
    original = "+918446307145"
    try:
        _swap_admin_line(admin_email)
        _restart_backend()

        # Fresh user whose only identifier is the admin email
        uid = f"test-{uuid.uuid4()}"
        _db.users.insert_one({
            "id": uid, "phone": None, "email": admin_email,
            "google_sub": None, "apple_sub": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _db.wallets.update_one(
            {"device_id": f"user:{uid}"},
            {"$set": {"device_id": f"user:{uid}", "balance": 0.0}},
            upsert=True,
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}",
        }

        # 1) wallet/balance should mark them admin
        rb = requests.get(f"{BASE_URL}/api/wallet/balance", headers=headers, timeout=10)
        assert rb.status_code == 200, rb.text
        body = rb.json()
        assert body["is_admin"] is True, body
        assert body["enforcement_enabled"] is False, body

        # 2) analyze with $0 balance must NOT be billed
        ra = requests.post(f"{BASE_URL}/api/analyze",
                           headers=headers,
                           json={"symbol": "NVDA", "language": "en"},
                           timeout=30)
        assert ra.status_code == 200, ra.text
        rb2 = ra.json()
        assert rb2["billed"] is False, rb2
        assert rb2["admin_bypass"] is True, rb2

        # cleanup
        _db.users.delete_many({"id": uid})
        _db.wallets.delete_many({"device_id": f"user:{uid}"})
    finally:
        _swap_admin_line(original)
        _restart_backend()
