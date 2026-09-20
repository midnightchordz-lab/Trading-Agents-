"""Where every rate limit for this app is declared, in one file.

Each one is a FastAPI dependency attached to its route, so a route's own code
is untouched and the limit is visible next to the path it protects. Every value
is an env var, so a limit can be retuned in production without a code change.

SIZING RULE: authenticated limits are at least 3x the real peak the app itself
produces, measured from the frontend rather than guessed —
  * `app/analysis/[id].tsx:77`  polls an analysis every 1500ms  -> 40 req/min
  * `app/compare.tsx:215`       polls TWO analyses every 1500ms -> 80 req/min
  * `app/analysis/[id].tsx:130` refreshes the quote every 30s   ->  2 req/min
  * `components/WalletCard.tsx` polls pay status 12x at 2500ms  -> ~24 req/min
The busiest authenticated minute the product can generate is the Compare screen
at ~80, so the polling group is 240/min. A limit a real user can reach is not a
security control, it is an outage.

NEVER LIMITED, deliberately: `/health` (the platform's readiness probe — a 429
there takes the deployment down), `/api/pay/webhook` and
`/api/pay/iap/webhook`. Those two are authenticated by signature/shared secret,
cannot be flooded by an anonymous caller, and carry money events: a dropped
webhook is a customer who paid and wasn't credited. Their idempotency logic is
untouched.
"""
from typing import Optional

from fastapi import Depends, Request

import rate_limit as rl
from deps import client_ip, require_user

# --- Per-endpoint limits (requests, window in seconds) ---------------------
OTP_REQUEST_PER_MIN = rl.env_int("RL_OTP_REQUEST_PER_MIN", 3)
OTP_VERIFY_PER_10MIN = rl.env_int("RL_OTP_VERIFY_PER_10MIN", 30)
AUTH_EXCHANGE_PER_MIN = rl.env_int("RL_AUTH_EXCHANGE_PER_MIN", 30)
ANALYZE_PER_MIN = rl.env_int("RL_ANALYZE_PER_MIN", 6)
ANALYZE_MAX_CONCURRENT = rl.env_int("RL_ANALYZE_MAX_CONCURRENT", 3)
POLL_PER_MIN = rl.env_int("RL_POLL_PER_MIN", 240)
PAY_ORDER_PER_MIN = rl.env_int("RL_PAY_ORDER_PER_MIN", 10)
PAY_CALLBACK_PER_MIN = rl.env_int("RL_PAY_CALLBACK_PER_MIN", 60)
PAY_CONFIG_PER_MIN = rl.env_int("RL_PAY_CONFIG_PER_MIN", 30)
NEWS_LLM_PER_MIN = rl.env_int("RL_NEWS_LLM_PER_MIN", 10)
NEWS_LLM_GLOBAL_PER_MIN = rl.env_int("RL_NEWS_LLM_GLOBAL_PER_MIN", 60)
MARKET_PER_MIN = rl.env_int("RL_MARKET_PER_MIN", 120)
PORTFOLIO_PER_MIN = rl.env_int("RL_PORTFOLIO_PER_MIN", 10)


class PerIp:
    """For endpoints with no session. Keyed on the TRUSTED client address —
    see deps.client_ip; keying on anything the caller can choose would let them
    choose their own bucket."""

    def __init__(self, bucket: str, limit: int, window: int = 60, fail_closed: bool = False):
        self.bucket, self.limit, self.window, self.fail_closed = bucket, limit, window, fail_closed

    async def __call__(self, request: Request) -> None:
        await rl.enforce(self.bucket, client_ip(request), self.limit, self.window,
                         fail_closed=self.fail_closed)


class PerUser:
    """For endpoints behind a session. Keyed on the USER ID, never the IP: a
    company office, a university or a carrier NAT is one address shared by
    hundreds of real people, and an IP-keyed limit there means the busiest
    person locks out everyone else. Falls back to the IP only when there is no
    session at all (AUTH_REQUIRED off), since then there is nothing else to
    key on.

    `require_user` is the same dependency the routes themselves declare, so
    FastAPI resolves it once per request — this adds no extra database read.
    """

    def __init__(self, bucket: str, limit: int, window: int = 60):
        self.bucket, self.limit, self.window = bucket, limit, window

    async def __call__(self, request: Request,
                       user: Optional[dict] = Depends(require_user)) -> None:
        raw_key = f"user:{user['id']}" if user else client_ip(request)
        await rl.enforce(self.bucket, raw_key, self.limit, self.window)


# --- The dependencies, one per protected route ----------------------------
# Auth. The OTP request limiter FAILS CLOSED (see rate_limit) because those
# requests spend real money.
limit_otp_request = PerIp("otp_request", OTP_REQUEST_PER_MIN, 60, fail_closed=True)
limit_otp_verify = PerIp("otp_verify", OTP_VERIFY_PER_10MIN, 600)
limit_auth_exchange = PerIp("auth_exchange", AUTH_EXCHANGE_PER_MIN)

# Analysis. `analyze` is the expensive one (a real multi-agent LLM run).
limit_analyze = PerUser("analyze", ANALYZE_PER_MIN)
# One shared bucket across every screen's polling, so the ceiling is on what a
# user actually does rather than on each path separately.
limit_poll = PerUser("poll", POLL_PER_MIN)

# Payments. The callback is keyed per IP because it arrives from Razorpay's
# servers, not from our users.
limit_pay_order = PerUser("pay_order", PAY_ORDER_PER_MIN)
limit_pay_callback = PerIp("pay_callback", PAY_CALLBACK_PER_MIN)
limit_pay_config = PerIp("pay_config", PAY_CONFIG_PER_MIN)

# Public market data: one shared bucket for the whole group, since they hit the
# same upstream (Yahoo) and the point is to protect that quota.
limit_market = PerIp("market", MARKET_PER_MIN)
limit_portfolio = PerIp("portfolio", PORTFOLIO_PER_MIN)
