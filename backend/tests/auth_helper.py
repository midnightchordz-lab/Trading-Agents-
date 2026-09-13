"""Shared auth header for tests that hit endpoints now requiring a login.

Creates a throwaway user directly in Mongo and signs a session token with the
same JWT secret the server uses — no OTP round-trip needed (and none possible
now that AUTH_DEBUG_RETURN_OTP is off).
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au

load_dotenv(Path(__file__).parent.parent / ".env")

_USER_ID = f"test-{uuid.uuid4()}"
_JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ.get("DB_NAME", "test_database")]
_db.users.insert_one({
    "id": _USER_ID,
    "phone": None,
    "email": f"{_USER_ID}@example.com",
    "google_sub": None,
    "apple_sub": None,
    "created_at": datetime.now(timezone.utc).isoformat(),
})
# Fund the test user's wallet so priced actions work whether or not wallet
# enforcement is switched on.
_db.wallets.update_one(
    {"device_id": f"user:{_USER_ID}"},
    {"$set": {"device_id": f"user:{_USER_ID}", "balance_usd": 100.0}},
    upsert=True,
)

USER_ID = _USER_ID
TOKEN = au.create_session_token(_USER_ID, _JWT_SECRET)
AUTH_HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
