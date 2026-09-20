"""FastAPI application wiring.

Deliberately thin: configuration and the Mongo handle live in core.py, the
endpoints in routes/, the agent pipeline in pipeline.py and the market feeds in
market_data.py. This file only assembles them and owns the startup work that
has to happen exactly once per process.
"""
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

import auth as au
import rate_limit as ratelimit
import razorpay_pay as rzp
from core import client, db, logger, now_iso
from email_repair import apply_email_repair, find_email_repair_candidates
from routes import analysis, auth_routes, market, payments, portfolio

app = FastAPI()

for module in (market, analysis, auth_routes, payments, portfolio):
    app.include_router(module.api_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """422 with the field and the reason, and nothing else.

    FastAPI's default handler echoes the offending input back, which turned a
    rejected NaN / Infinity into a 500: those values are valid JSON to send but
    cannot be serialized into a JSON response. Reporting only loc/msg/type
    fixes that and stops arbitrary request content bouncing back to the caller.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": [
            {"loc": [str(part) for part in err.get("loc", ())],
             "msg": str(err.get("msg", "")),
             "type": str(err.get("type", ""))}
            for err in exc.errors()
        ]},
    )


@app.get("/health")
async def health():
    """Platform readiness probe. Deliberately app-level (not under /api) and
    DB-free so a slow Mongo can't make the container look dead."""
    return {"status": "ok"}




app.add_middleware(
    CORSMiddleware,
    # Sessions travel as bearer tokens, never cookies, so credentialed
    # cross-origin requests are never needed — and "*" with credentials is a
    # combination browsers reject anyway.
    allow_credentials=False,
    allow_origins=["*"],
    # Only the verbs and headers this API actually uses. The app sends nothing
    # but a bearer token and JSON, so a browser has no reason to be allowed to
    # preflight anything else.
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()


@app.on_event("startup")
async def migrate_legacy_wallet_field():
    """One-time: wallet rows written before the field rename carry
    `balance_usd`, which reads as $0 today. Takes the higher of the two so
    no row with a real balance can read as empty, then drops the old field."""
    try:
        migrated = 0
        async for doc in db.wallets.find({"balance_usd": {"$exists": True}}):
            merged = max(float(doc.get("balance") or 0), float(doc.get("balance_usd") or 0))
            await db.wallets.update_one(
                {"_id": doc["_id"]},
                {"$set": {"balance": merged}, "$unset": {"balance_usd": ""}},
            )
            migrated += 1
        if migrated:
            logger.info(f"migrated {migrated} legacy wallet rows (balance_usd -> balance)")
    except Exception as e:
        logger.warning(f"legacy wallet migration failed: {e}")


MIGRATION_NAME = "billing_email_v2"


@app.on_event("startup")
async def migrate_unverified_billing_email():
    """One-time: unverified billing emails sitting in the VERIFIED email field.

    Before the SEC-001 fix, `/pay/order` wrote the email a user typed for their
    Razorpay receipt straight onto `users.email` — the field the sign-in
    identity and the admin allowlist both read. A phone-signup account could
    therefore end up displaying an address it never verified (the bug reported
    here: "signed in as <someone's gmail>" after signing in with a phone
    number). New payments store it under `billing_email`; this moves the
    already-written ones there.

    WHY THE SELECTION IS NARROWER THAN IT LOOKS: the original version matched
    any user with a phone, an email and no Google/Apple id. That also matches a
    LEGACY EMAIL-OTP account which happens to carry a stray phone (the same
    pre-fix payment path could write one), and for those the email is the
    VERIFIED sign-in identity. Stripping it silently locked the person out of
    their own account — their next email sign-in created a fresh empty one.

    So the email is only moved when the evidence says the phone is the real
    identity: a verified OTP for the phone, and NO verified OTP for the email
    (checked in both stored-lowercase and canonical form, since Gmail dots and
    +tags are collapsed when an OTP is requested). `otp_requests` rows are
    never deleted, so that evidence is always available. Anything ambiguous is
    skipped and counted — when in doubt, leave the account alone.
    """
    try:
        if await db.migrations.find_one({"name": MIGRATION_NAME}):
            return
        moved = 0
        skipped_ambiguous = 0
        async for doc in db.users.find({
            # An account that has been through any sign-in since the fix has
            # this field set, and its identity is already recorded properly.
            "identity_type": {"$exists": False},
            "phone": {"$ne": None},
            "email": {"$ne": None},
            "google_sub": None,
            "apple_sub": None,
        }):
            email = (doc.get("email") or "").strip().lower()
            _, canonical = au.normalize_identifier(email)
            candidates = [e for e in {email, canonical} if e]
            phone_verified = await db.otp_requests.find_one(
                {"identifier": doc["phone"], "verified": True}, {"_id": 1})
            email_verified = await db.otp_requests.find_one(
                {"identifier": {"$in": candidates}, "verified": True}, {"_id": 1}) if candidates else None
            if not phone_verified or email_verified:
                skipped_ambiguous += 1
                continue
            await db.users.update_one(
                {"_id": doc["_id"]},
                {"$set": {"billing_email": doc.get("billing_email") or doc["email"],
                          "identity_type": "phone"},
                 "$unset": {"email": ""}},
            )
            moved += 1
        # Counts only — never an email, phone or id.
        logger.info(f"billing email migration complete: moved={moved} skipped_ambiguous={skipped_ambiguous}")
        await db.migrations.update_one({"name": MIGRATION_NAME},
                                       {"$set": {"name": MIGRATION_NAME, "completed_at": now_iso()}},
                                       upsert=True)
    except Exception as e:
        logger.warning(f"billing email migration failed: {e}")


EMAIL_REPAIR_MODE = os.environ.get("EMAIL_REPAIR", "").strip().lower()


@app.on_event("startup")
async def run_gated_email_repair():
    """The email repair, run from inside the container — off unless asked for.

    The production database is only reachable from the deployed pod, and the
    repair must not become an admin endpoint (a privacy test forbids wiring
    `require_admin` to a route). So it runs here, and ONLY when EMAIL_REPAIR is
    set in the deployment secrets:

        EMAIL_REPAIR=dryrun   report counts and internal user ids, change nothing
        EMAIL_REPAIR=apply    restore the accounts the dry run listed

    Unset — which is every normal boot — this is a no-op. Remove the variable
    once the log has been read; a data repair that runs itself on every boot is
    how the original problem happened. `apply` is safe to leave set by accident
    only in the sense that it is idempotent: the second run finds nothing,
    because a restored account no longer matches the scan.
    """
    if EMAIL_REPAIR_MODE not in ("dryrun", "apply"):
        return
    try:
        repairable, conflicts = await find_email_repair_candidates()
        # Counts and internal ids only — never an email address.
        logger.warning(
            f"EMAIL_REPAIR[{EMAIL_REPAIR_MODE}] scan: repairable={len(repairable)} "
            f"conflicts={len(conflicts)}")
        for row in repairable:
            logger.warning(f"EMAIL_REPAIR repairable user_id={row['id']}")
        for row in conflicts:
            logger.warning(
                f"EMAIL_REPAIR conflict user_id={row['id']} address already on user_id={row['held_by']}")
        if EMAIL_REPAIR_MODE == "dryrun":
            logger.warning("EMAIL_REPAIR dry run — nothing was changed. "
                           "Set EMAIL_REPAIR=apply to restore the accounts above.")
            return
        restored = await apply_email_repair(repairable)
        logger.warning(f"EMAIL_REPAIR applied: restored={restored} of {len(repairable)}; "
                       f"{len(conflicts)} conflicts left untouched. "
                       "Remove the EMAIL_REPAIR variable now.")
    except Exception as e:
        # Never let a repair stop the API from starting.
        logger.error(f"EMAIL_REPAIR failed: {e}")


async def guarded_index(label: str, coro_factory) -> bool:
    """Build ONE index, and let the rest carry on if it fails.

    Every index below used to sit in a single try/except that logged a warning,
    so one failure skipped every later line — including the limiter's unique
    index (what makes rate limiting atomic) and its TTL index (what stops the
    collection growing forever). The app still started, looking healthy.
    Same indexes, same options, same order; each one is just isolated now, and
    a failure is an ERROR naming the index rather than one anonymous warning.
    """
    try:
        await coro_factory()
        return True
    except Exception as e:
        logger.error(f"INDEX SETUP FAILED for {label}: {e} — continuing with the remaining indexes")
        return False


@app.on_event("startup")
async def ensure_payment_indexes():
    """Unique indexes are what keep a top-up from being credited twice."""
    # Payment-link records carry no order id until the customer starts paying,
    # so these must be sparse — the old non-sparse unique index would reject
    # every record after the first one missing the field. Kept first, and kept
    # as it was.
    try:
        existing = await db.payments.index_information()
        if "razorpay_order_id_1" in existing and not existing["razorpay_order_id_1"].get("sparse"):
            await db.payments.drop_index("razorpay_order_id_1")
    except Exception as e:
        logger.error(f"INDEX SETUP FAILED dropping the old razorpay_order_id index: {e}")

    await guarded_index("payments.razorpay_order_id",
                        lambda: db.payments.create_index("razorpay_order_id", unique=True, sparse=True))
    await guarded_index("payments.razorpay_payment_link_id",
                        lambda: db.payments.create_index("razorpay_payment_link_id", unique=True, sparse=True))
    await guarded_index("payments.reference_id",
                        lambda: db.payments.create_index("reference_id", unique=True, sparse=True))
    await guarded_index("wallet_ledger.payment_id",
                        lambda: db.wallet_ledger.create_index("payment_id", unique=True))
    await guarded_index("webhook_events.event_id",
                        lambda: db.webhook_events.create_index("event_id", unique=True))
    # Private per-account history reads on these two.
    await guarded_index("analyses.owner_hash", lambda: db.analyses.create_index("owner_hash"))
    await guarded_index("analyses.viewer_hashes", lambda: db.analyses.create_index("viewer_hashes"))
    # Signup abuse guard reads these on every new account.
    await guarded_index("free_credit_grants.device_id",
                        lambda: db.free_credit_grants.create_index("device_id"))
    await guarded_index("free_credit_grants.ip+created_at",
                        lambda: db.free_credit_grants.create_index([("ip", 1), ("created_at", -1)]))
    await guarded_index("free_credit_grants.created_at",
                        lambda: db.free_credit_grants.create_index([("created_at", -1)]))
    # Every OTP request counts the last hour globally (the only defence against
    # a distributed pump), so that count must not be a collection scan.
    await guarded_index("otp_requests.created_at",
                        lambda: db.otp_requests.create_index([("created_at", -1)]))
    await guarded_index("otp_requests.identifier_type+created_at",
                        lambda: db.otp_requests.create_index([("identifier_type", 1), ("created_at", -1)]))
    await guarded_index("otp_requests.ip+created_at",
                        lambda: db.otp_requests.create_index([("ip", 1), ("created_at", -1)]))
    # Rate limiting: the unique (key, window_start) index is what makes the
    # counter atomic, and the TTL index is what stops the collection growing
    # with traffic.
    await ratelimit.ensure_indexes()
    # And this one, checked ahead of them: an identifier that already had an
    # account doesn't get a second free grant after deletion.
    await guarded_index("free_credit_tombstones.hash",
                        lambda: db.free_credit_tombstones.create_index("hash", unique=True))


    if rzp.payments_configured():
        if rzp.key_id_malformed():
            logger.error(
                "RAZORPAY_KEY_ID does not look like a key id (expected rzp_live_… / rzp_test_…) "
                "— the key SECRET was probably pasted into it; top-ups will fail with "
                "'Authentication failed'"
            )
        logger.info(f"razorpay ready ({'LIVE' if rzp.is_live_mode() else 'test'} mode)")
        # Prove the keys actually authenticate. A deployed image carrying stale
        # keys otherwise looks fine until a customer taps top-up and gets a 502.
        try:
            # Probe the payment-links API specifically, because that is what
            # checkout actually calls. Evidence for why it matters: with a
            # RETIRED key pair, `GET /payments` answered 200 while
            # `GET /payment_links` answered 401 "api key ... has expired" —
            # so the old probe reported healthy credentials while every
            # customer's top-up failed.
            await rzp.razorpay_request("GET", "/payment_links?count=1")
            rzp.CREDENTIALS_OK = True
            logger.info("razorpay credentials authenticated")
        except rzp.RazorpayError as e:
            rzp.CREDENTIALS_OK = False
            logger.error(f"RAZORPAY CREDENTIALS REJECTED [{e.code}]: {e.description} — top-ups will fail")
        except Exception as e:
            logger.warning(f"razorpay credential check skipped: {e}")
