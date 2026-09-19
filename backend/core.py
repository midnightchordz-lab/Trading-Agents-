"""Shared process-wide setup: environment, logging, the Mongo handle and the
model configuration.

Every other backend module imports from here and this module imports none of
them, so there is exactly one place the database and the LLM are configured
and no import cycle is possible.
"""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')
MODEL_PROVIDER = "openai"
MODEL_NAME = "gpt-5.4"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("tradingagents")

# Twilio's SDK logs every request URL at INFO, and that URL embeds our account
# SID. The SID is an identifier rather than a secret (the auth token is the
# secret, and is never logged), but it does not belong in a log file that gets
# read, pasted and attached to reports. WARNING still surfaces real failures.
logging.getLogger("twilio.http_client").setLevel(logging.WARNING)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
