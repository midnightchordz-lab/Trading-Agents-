"""Verify the real reported account returns its phone as identity."""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ["JWT_SECRET"]
db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]


def test_real_reported_account_identity_is_phone():
    uid = "638b2999-2866-47a6-bd37-22e80453bc4d"
    user = db.users.find_one({"id": uid})
    assert user is not None, "Reported user missing"
    print("DB state:", {k: user.get(k) for k in ["phone", "email", "billing_email", "identity_type", "google_sub", "apple_sub"]})
    token = au.create_session_token(uid, JWT_SECRET)
    headers = {"Authorization": f"Bearer {token}"}
    body = requests.get(f"{BASE}/auth/me", headers=headers, timeout=20).json()
    print("auth/me:", body)
    assert body["identity"] == "+918291026526", body
    assert body["identity_type"] == "phone", body
    assert body["email"] is None, body
