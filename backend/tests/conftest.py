"""Test-suite wide setup. Two things, both about the suite rather than the app.

1. SPEED. Most modules talk to the running backend over
   `EXPO_PUBLIC_BACKEND_URL`, which is the public preview host — so every
   request left the container, crossed the ingress and came back: 146ms instead
   of 2ms, measured. The suite makes thousands of them (the analysis-polling
   tests alone poll for a minute each), which added up to **110 seconds of pure
   network latency**: 4m22s -> 2m31s, same tests, same assertions.
   It is the SAME backend process either way, so nothing about what is tested
   changes. What the loopback does NOT exercise is the ingress/proxy path, so
   `test_ingress.py` covers that explicitly through the public host, and
   `TEST_VIA_INGRESS=1` sends everything back through it.

2. ONE CLIENT IDENTITY PER TEST. The app is rate limited per client address,
   and every test calling the loopback IS the same address (127.0.0.1). So one
   module's traffic pushed another module over the limit and the failure landed
   somewhere unrelated — `/portfolio/optimize` (10/min per IP) failed in
   test_security_part2 because test_portfolio_api had just used the allowance.
   Each test function is therefore given its own `X-Forwarded-For`, which the
   backend trusts ONLY from a loopback peer (see deps.client_ip), so this shim
   cannot exist in production.
   Tests that need several requests to share a bucket — the per-IP OTP ceiling,
   the signup-abuse throttle, the limiter tests themselves — set the header
   explicitly, and an explicit header is never overwritten.

Both must run before the test modules are imported (they read the env var at
import time); conftest is imported first, so they do.
"""
import hashlib
import os

import pytest
import requests

if not os.environ.get("TEST_VIA_INGRESS"):
    os.environ["EXPO_PUBLIC_BACKEND_URL"] = os.environ.get(
        "TEST_BACKEND_URL", "http://localhost:8001"
    )

_current_test = {"id": "session"}
_real_request = requests.sessions.Session.request


def _ip_for(test_id: str) -> str:
    """A stable, private-range address per test. Deterministic so a rerun of
    the same test lands in the same bucket (a flaky limit is worse than a
    strict one)."""
    digest = hashlib.sha256(test_id.encode()).digest()
    return f"10.{digest[0]}.{digest[1]}.{digest[2] or 1}"


def _request_with_test_ip(self, method, url, **kwargs):
    headers = kwargs.get("headers") or {}
    if not any(k.lower() == "x-forwarded-for" for k in headers):
        headers = {**headers, "X-Forwarded-For": _ip_for(_current_test["id"])}
        kwargs["headers"] = headers
    return _real_request(self, method, url, **kwargs)


requests.sessions.Session.request = _request_with_test_ip


@pytest.fixture(autouse=True)
def _tag_requests_with_the_current_test(request):
    _current_test["id"] = request.node.nodeid
    yield
    _current_test["id"] = "session"
