"""The way back to the app after paying.

THE BUG (owner's screenshot): the confirmation page said "Added ₹99 to your
wallet. You can close this and return to the app." and gave the customer
nothing to tap. That sentence is not a way back. Checkout deliberately opens in
the SYSTEM browser rather than a WebView — a WebView cannot launch the UPI app
intent, so Razorpay hides UPI/GPay inside one — and an Android Chrome Custom
Tab cannot be closed by the app: expo-web-browser's `dismissBrowser` is
iOS-only. So the customer's only exit was the browser's own small ✕.

THE FIX: the app sends its own deep link (`Linking.createURL('/')`, which is
`frontend://` in a build and `exp://…` in Expo Go) with the top-up; it is
stored on the payment and rendered as a real button on the callback page.

WHAT THESE TESTS GUARD: that the button is actually there in every outcome, and
that the URL — which is client-supplied and rendered into a page anyone can
reach — can never turn that page into an open redirect or an XSS.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import razorpay_pay as rzp  # noqa: E402
from routes.payments import safe_return_url  # noqa: E402

DEEP_LINK = "frontend:///"


# --- the button exists -----------------------------------------------------

def test_the_success_page_has_a_tappable_way_back():
    html = rzp.result_html("Added ₹99 to your wallet.", ok=True, return_url=DEEP_LINK)
    assert f'href="{DEEP_LINK}"' in html, "no link back to the app — this is the reported bug"
    assert "Return to the app" in html


@pytest.mark.parametrize("message,ok", [
    ("Payment wasn't completed — nothing was charged.", False),
    ("We couldn't verify that payment.", False),
    ("Payment received — we're still confirming it.", True),
    ("That payment didn't go through — nothing was charged.", False),
])
def test_every_outcome_has_a_way_back_not_just_success(message, ok):
    """Being stranded on a FAILED payment is worse: the customer wants to get
    back and try again."""
    html = rzp.result_html(message, ok=ok, return_url=DEEP_LINK)
    assert f'href="{DEEP_LINK}"' in html


def test_without_a_deep_link_there_is_still_a_close_button():
    """Web checkout is a script-opened popup, where `window.close()` works, and
    older payments have no stored return url. Neither may render a dead page."""
    html = rzp.result_html("Added $5 to your wallet.", ok=True)
    assert "window.close()" in html
    assert "<button" in html


def test_the_button_is_big_enough_to_tap():
    """44px is the platform minimum; this is a one-action page on a phone."""
    html = rzp.result_html("Added ₹99 to your wallet.", ok=True, return_url=DEEP_LINK)
    assert "min-height:48px" in html and "min-width:200px" in html


def test_the_link_works_without_javascript():
    """The onclick is an optimisation for the popup case. The href is the
    actual way back, so a browser that ran no script still has one."""
    html = rzp.result_html("Added ₹99 to your wallet.", ok=True, return_url=DEEP_LINK)
    anchor = html[html.index("<a id=\"back\""):]
    assert 'href="frontend:///"' in anchor


def test_the_old_dead_end_wording_is_gone():
    html = rzp.result_html("Added ₹99 to your wallet.", ok=True, return_url=DEEP_LINK)
    assert "You can close this and return to the app." not in html


# --- the url is client-supplied, so it is not trusted ----------------------

@pytest.mark.parametrize("url", [
    "frontend:///",
    "frontend://wallet",
    "exp://192.168.1.5:8081/--/",
    "exp+frontend://expo-development-client/",
    "myapp://path?x=1&y=2",
])
def test_a_real_app_deep_link_is_accepted(url):
    assert safe_return_url(url) == url


@pytest.mark.parametrize("url", [
    "https://evil.example.com",
    "http://evil.example.com",
    "HTTPS://evil.example.com",
    "  https://evil.example.com  ",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "vbscript:msgbox",
])
def test_anything_a_browser_would_follow_off_device_is_refused(url):
    """The callback page is reachable by anyone with a link id, so a stored
    http(s) url would make it a redirector to an attacker's page — and a
    `javascript:` one would run on it."""
    assert safe_return_url(url) is None


@pytest.mark.parametrize("url", [
    'frontend:///" onmouseover="alert(1)',
    "frontend:///'><script>alert(1)</script>",
    "frontend:///\n<script>",
    "frontend:/// with spaces",
    "://no-scheme",
    "nocolonslashes",
    "f://" + "x" * 300,
    "",
    None,
])
def test_a_malformed_or_injected_url_is_dropped(url):
    assert safe_return_url(url) is None


def test_a_refused_url_leaves_a_page_that_still_works():
    """Dropping the value must degrade to the no-link page, never render it."""
    assert safe_return_url("https://evil.example.com") is None
    html = rzp.result_html("Added ₹99 to your wallet.", ok=True,
                           return_url=safe_return_url("https://evil.example.com"))
    assert "evil.example.com" not in html
    assert "window.close()" in html


def test_a_quote_in_a_url_that_did_pass_is_still_escaped():
    """Belt and braces: `result_html` escapes with quote=True, so even if the
    validator were ever loosened the attribute cannot be broken out of."""
    html = rzp.result_html("ok", ok=True, return_url='frontend:///?q="x"')
    assert '"x"' not in html
    assert "&quot;x&quot;" in html


def test_the_message_is_still_escaped():
    html = rzp.result_html("<script>alert(1)</script>", ok=True, return_url=DEEP_LINK)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
