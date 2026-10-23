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


# Twilio error code -> what the person typing their number should be told.
# The exception's own text ("HTTP 400 error: Unable to create record: ...
# https://www.twilio.com/docs/errors/21211") must never reach the screen.
_NOT_A_NUMBER = "That doesn't look like a valid mobile number — check it and the country code"
_COUNTRY_OFF = "We can't send texts to that country yet — sign in with your email address instead"
_TWILIO_MESSAGES = {
    21211: _NOT_A_NUMBER,   # invalid 'To' number
    21614: _NOT_A_NUMBER,   # not a mobile number (landline)
    21265: _NOT_A_NUMBER,   # 'To' number can't be a short code / invalid format
    21266: _NOT_A_NUMBER,   # 'To' and 'From' are the same
    21408: _COUNTRY_OFF,    # geo permissions: region not enabled
    21612: _COUNTRY_OFF,    # route to this destination unavailable
    21608: "This number isn't verified on the Twilio trial account — verify it in Twilio, or use email",
    21610: "This number has opted out of messages from us",
}
# Problems with what the user typed (answer 400, so they fix it), as opposed
# to our provider failing (answer 502, try again later).
INPUT_ERRORS = {_NOT_A_NUMBER, _COUNTRY_OFF, _TWILIO_MESSAGES[21610],
                "Enter your number with the country code, like +919876543210"}


def _user_message(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in _TWILIO_MESSAGES:
        return _TWILIO_MESSAGES[code]
    text = str(exc)
    for known, message in _TWILIO_MESSAGES.items():
        if str(known) in text:
            return message
    if "unverified" in text.lower():
        return _TWILIO_MESSAGES[21608]
    if "not a valid phone number" in text.lower():
        return _NOT_A_NUMBER
    return "Couldn't send the text — try again, or use your email address"


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
        return False, "Enter your number with the country code, like +919876543210"
    try:
        sid = await asyncio.to_thread(_send_sync, to, otp_message(otp, ttl_minutes, brand))
        logger.info(f"OTP SMS queued ({sid})")
        return True, None
    except Exception as e:
        # The full Twilio text goes to the log for us; only the mapped,
        # plain-language message goes back to the app.
        logger.error(f"OTP SMS failed (code={getattr(e, 'code', None)}): {e}")
        return False, _user_message(e)
