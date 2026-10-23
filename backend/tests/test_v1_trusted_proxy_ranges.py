"""V1: resolve the client address by skipping OUR OWN proxy ranges.

The earlier fix counted `TRUSTED_PROXY_HOPS` entries in from the right. That is
a guess about the topology: if the edge ever adds or removes a proxy, the count
points at the wrong entry — one way every user collapses onto the load
balancer's single address (per-IP limits become global and lock people out),
the other way the value becomes forgeable again.

So the entries our own edge appended are now identified by ADDRESS RANGE
(Cloudflare's published ranges, Google's load balancers, private/loopback
space), and the number of skips is still CAPPED at the known hop count. The cap
is load-bearing: range-skipping alone is forgeable by an attacker hosted INSIDE
Cloudflare or Google Cloud, whose own entry would also be skipped.

The real chain shape, measured against the live edge:
  client sends nothing      -> `<client>,<cloudflare>,<google lb>`
  client sends `1.2.3.4`    -> `1.2.3.4,<client>,<cloudflare>,<google lb>`
Cloudflare INSERTS the connecting address, so the client's true address is
always present and always to the right of anything the caller wrote.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deps  # noqa: E402

CF = "104.22.64.124"      # a real Cloudflare egress address
LB = "34.160.159.238"     # the Google load balancer
CLIENT = "34.16.56.64"     # a real client address, measured


class Req:
    def __init__(self, xff=None, peer="10.79.165.11"):
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = type("C", (), {"host": peer})()


def test_the_measured_honest_chain_yields_the_client():
    assert deps.client_ip(Req(f"{CLIENT},{CF},{LB}")) == CLIENT


def test_a_forged_entry_is_ignored():
    assert deps.client_ip(Req(f"1.2.3.4,{CLIENT},{CF},{LB}")) == CLIENT


def test_a_forged_entry_shaped_like_cloudflare_is_ignored():
    """Sending something that looks like our edge must not shift the answer —
    the cap means only our own appended hops are ever discarded."""
    assert deps.client_ip(Req(f"104.16.0.5,{CLIENT},{CF},{LB}")) == CLIENT


def test_padding_the_header_cannot_move_the_answer():
    forged = ",".join(f"104.16.0.{n}" for n in range(1, 30))
    assert deps.client_ip(Req(f"{forged},{CLIENT},{CF},{LB}")) == CLIENT


def test_a_client_hosted_inside_a_trusted_range_is_still_itself():
    """Someone browsing from a Google Cloud VM has an address in one of our
    trusted ranges. The cap is what stops their entry being skipped — without
    it, their own forged value would be returned."""
    gcp_client = "34.64.5.5"
    assert deps.client_ip(Req(f"9.9.9.9,{gcp_client},{CF},{LB}")) == gcp_client


def test_a_shorter_chain_still_resolves_correctly():
    """The case a hop count gets WRONG: if Cloudflare is bypassed, only the
    load balancer appends, and counting 2 in from the right would have skipped
    past the client. Range-skipping stops at the first entry that is not
    ours."""
    assert deps.client_ip(Req(f"{CLIENT},{LB}")) == CLIENT


def test_junk_in_the_header_is_not_treated_as_our_infrastructure():
    """An unparseable entry is what a forger sends, so it must not be trusted
    and skipped over."""
    assert deps.client_ip(Req(f"notanip,{CLIENT},{CF},{LB}")) == CLIENT
    assert deps._is_trusted_proxy("notanip") is False
    assert deps._is_trusted_proxy("") is False


def test_a_chain_of_only_our_own_hops_falls_back_to_the_socket_peer():
    """The client address never made it into the header, so the only
    unforgeable value left is the peer."""
    assert deps.client_ip(Req(f"{CF},{LB}")) == "10.79.165.11"


def test_a_loopback_caller_may_still_declare_its_address():
    """The in-container test path: nothing outside the container can reach
    127.0.0.1, and it is what lets the suite behave like many clients."""
    assert deps.client_ip(Req("203.0.113.5", peer="127.0.0.1")) == "203.0.113.5"


def test_the_ranges_cover_what_they_claim_to():
    for ours in ("104.22.64.124", "172.64.1.1", "141.101.65.9",   # Cloudflare
                 "35.191.0.7", "34.160.159.238", "130.211.0.5",   # Google LB
                 "10.79.165.11", "127.0.0.1", "192.168.1.1"):     # cluster/private
        assert deps._is_trusted_proxy(ours) is True, ours
    for theirs in ("34.16.56.64", "203.0.113.9", "8.8.8.8", "49.36.1.1"):
        assert deps._is_trusted_proxy(theirs) is False, theirs


def test_extra_ranges_can_be_added_without_a_code_change():
    """If the edge changes, TRUSTED_PROXY_RANGES adapts it in production."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "deps.py")).read()
    assert 'os.environ.get("TRUSTED_PROXY_RANGES"' in source
    assert 'os.environ.get("TRUSTED_PROXY_HOPS"' in source


def test_no_header_uses_the_peer():
    assert deps.client_ip(Req(None, peer="10.79.165.11")) == "10.79.165.11"
    assert deps.client_ip(None) == ""
