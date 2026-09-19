"""Test-suite wide setup.

ONE thing, and it is a speed fix. Most modules here talk to the running
backend over `EXPO_PUBLIC_BACKEND_URL`, which is the public preview host — so
every single request left the container, crossed the ingress and came back:
146ms instead of 2ms, measured. The suite makes thousands of them (the
analysis-polling tests alone poll for a minute each), and it added up to
**110 seconds of pure network latency**: 4m22s -> 2m31s, same 657 tests, same
assertions, nothing skipped.

It is the SAME backend process either way, so nothing about what is tested
changes. What the loopback does NOT exercise is the ingress/proxy path itself,
so `test_ingress.py` covers that explicitly through the public host, and
`TEST_VIA_INGRESS=1` sends everything back through it when that is what you
want to check.

This must run before the test modules are imported, because they read the env
var at import time — conftest is imported first, so it does.
"""
import os

if not os.environ.get("TEST_VIA_INGRESS"):
    os.environ["EXPO_PUBLIC_BACKEND_URL"] = os.environ.get(
        "TEST_BACKEND_URL", "http://localhost:8001"
    )
