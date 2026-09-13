"""SMS delivery for login codes, via Twilio.

We generate and hash the OTP ourselves (see auth.py); Twilio only carries the
message. Credentials live in backend/.env — when they're absent the module
reports itself unconfigured and the phone login path stays disabled.
"""
import asyncio
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# server.py imports this before it calls load_dotenv.
load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def sms_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def is_e164(number: str) -> bool:
    """Twilio rejects anything that isn't +<country code><number>."""
    return bool(_E164.match(number or ""))


def otp_message(otp: str, ttl_minutes: int, brand: str) -> str:
    return f"{otp} is your {brand} sign-in code. It expires in {ttl_minutes} minutes."


def _send_sync(to: str, body: str) -> str:
    from twilio.rest import Client  # imported lazily so the module loads without creds

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    msg = client.messages.create(to=to, from_=TWILIO_FROM_NUMBER, body=body)
    return msg.sid


async def send_otp_sms(to: str, otp: str, ttl_minutes: int, brand: str) -> tuple[bool, str | None]:
    """(delivered, user_facing_error). Never raises — the caller turns a failure
    into an HTTP error the login screen can show."""
    if not sms_configured():
        return False, "Text messages aren't available yet — sign in with your email address instead"
    if not is_e164(to):
        return False, "Enter your number with the country code, like +14155550134"
    try:
        sid = await asyncio.to_thread(_send_sync, to, otp_message(otp, ttl_minutes, brand))
        logger.info(f"OTP SMS queued ({sid})")
        return True, None
    except Exception as e:
        msg = str(e)
        logger.error(f"OTP SMS failed: {msg}")
        # Trial accounts can only text numbers verified in the Twilio console.
        if "unverified" in msg.lower() or "21608" in msg:
            return False, "This number isn't verified on the Twilio trial account — verify it in Twilio, or use email"
        if "21211" in msg or "not a valid phone number" in msg.lower():
            return False, "That doesn't look like a valid phone number"
        if "21610" in msg:
            return False, "This number has opted out of messages from us"
        return False, "Couldn't send the text — try again, or use your email address"
