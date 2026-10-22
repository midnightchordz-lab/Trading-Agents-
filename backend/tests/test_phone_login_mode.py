"""PHONE_LOGIN_MODE: new accounts sign in by email; phone codes go only to
numbers that already have an account ("existing", the default), to nobody
("off"), or to anyone ("on").

Runs the real request handler in-process against an in-memory stand-in for
the two collections it touches, with the SMS sender replaced — so these
tests can never text a real number, even where Twilio is configured.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import routes.auth_routes as ar  # noqa: E402


def _matches(doc, query):
    for key, cond in query.items():
        val = doc.get(key)
        if isinstance(cond, dict):
            if "$gt" in cond and not (val is not None and val > cond["$gt"]):
                return False
            if "$ne" in cond and val == cond["$ne"]:
                return False
        elif val != cond:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction):
        self.docs = sorted(self.docs, key=lambda d: d.get(key), reverse=direction < 0)
        return self

    async def to_list(self, n):
        return self.docs[:n]


class _Coll:
    def __init__(self):
        self.docs = []

    def find(self, query, projection=None):
        return _Cursor([d for d in self.docs if _matches(d, query)])

    async def find_one(self, query, projection=None, **_):
        return next((d for d in self.docs if _matches(d, query)), None)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _matches(d, query))

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


@pytest.fixture
def env(monkeypatch):
    fake = SimpleNamespace(otp_requests=_Coll(), users=_Coll())
    monkeypatch.setattr(ar, "db", fake)
    texts = []

    async def fake_send(to, otp, ttl, brand):
        texts.append(to)
        return True, None

    monkeypatch.setattr(ar.sms, "send_otp_sms", fake_send)
    monkeypatch.setattr(ar.sms, "sms_configured", lambda: True)
    monkeypatch.setattr(ar, "AUTH_DEBUG_RETURN_OTP", False)
    monkeypatch.setattr(ar, "is_review_login", lambda ident: False)
    monkeypatch.delenv("PHONE_LOGIN_MODE", raising=False)
    return SimpleNamespace(db=fake, texts=texts, monkeypatch=monkeypatch)


REQ = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.7"))
NEW = "+919812345678"
OLD = "+919898989898"


def ask(number):
    return asyncio.run(ar.auth_otp_request(ar.OtpRequest(identifier=number), REQ))


def test_default_mode_is_existing():
    os.environ.pop("PHONE_LOGIN_MODE", None)
    assert ar.phone_login_mode() == "existing"
    assert asyncio.run(ar.auth_config()) == {"phone_login": "existing"}


def test_unknown_values_fall_back_to_existing(monkeypatch):
    monkeypatch.setenv("PHONE_LOGIN_MODE", "maybe")
    assert ar.phone_login_mode() == "existing"


def test_existing_mode_texts_only_numbers_that_have_an_account(env):
    env.db.users.docs.append({"id": "u1", "phone": OLD})
    assert ask(OLD)["sent"] is True
    assert env.texts == [OLD]


def test_existing_mode_sends_nothing_to_a_new_number_but_says_the_same(env):
    res = ask(NEW)
    assert res == {"identifier": NEW, "identifier_type": "phone", "sent": True}
    assert env.texts == []
    rec = env.db.otp_requests.docs[-1]
    assert rec["suppressed"] is True and rec["identifier"] == NEW


def test_suppressed_probes_do_not_eat_the_sms_budget(env):
    env.monkeypatch.setattr(ar, "OTP_SMS_GLOBAL_MAX_PER_HOUR", 1)
    ask(NEW)  # suppressed, recorded
    env.db.users.docs.append({"id": "u1", "phone": OLD})
    assert ask(OLD)["sent"] is True  # the real text still goes out
    assert env.texts == [OLD]


def test_off_mode_refuses_phone_with_a_pointer_to_email(env):
    env.monkeypatch.setenv("PHONE_LOGIN_MODE", "off")
    env.db.users.docs.append({"id": "u1", "phone": OLD})
    with pytest.raises(HTTPException) as e:
        ask(OLD)
    assert e.value.status_code == 400 and "email" in e.value.detail
    assert env.texts == []


def test_on_mode_restores_phone_sign_up(env):
    env.monkeypatch.setenv("PHONE_LOGIN_MODE", "on")
    assert ask(NEW)["sent"] is True
    assert env.texts == [NEW]


def test_reviewer_number_always_gets_in(env):
    env.monkeypatch.setenv("PHONE_LOGIN_MODE", "off")
    env.monkeypatch.setattr(ar, "is_review_login", lambda ident: ident == NEW)
    env.monkeypatch.setattr(ar, "REVIEW_OTP", "123456")
    assert ask(NEW)["sent"] is True
    assert env.texts == []  # reviewer codes are never texted


def test_email_is_untouched_in_every_mode(env):
    sent = []

    async def fake_mail(to, otp, ttl):
        sent.append(to)
        return True

    env.monkeypatch.setattr(ar.mailer, "send_otp_email", fake_mail)
    for mode in ("existing", "off", "on"):
        env.monkeypatch.setenv("PHONE_LOGIN_MODE", mode)
        assert ask(f"person.{mode}@example.com")["sent"] is True
    assert len(sent) == 3
