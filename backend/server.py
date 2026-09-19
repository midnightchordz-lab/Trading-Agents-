"""FastAPI application wiring.

Deliberately thin: configuration and the Mongo handle live in core.py, the
endpoints in routes/, the agent pipeline in pipeline.py and the market feeds in
market_data.py. This file only assembles them and owns the startup work that
has to happen exactly once per process.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

import razorpay_pay as rzp
from core import client, db, logger
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

    Only accounts that signed in with a PHONE and have no Google/Apple identity
    are touched — those are exactly the ones whose email cannot have come from
    a verified sign-in. Email-OTP and Google accounts are left alone.
    """
    try:
        moved = 0
        async for doc in db.users.find({
            "phone": {"$ne": None},
            "email": {"$ne": None},
            "google_sub": None,
            "apple_sub": None,
        }):
            await db.users.update_one(
                {"_id": doc["_id"]},
                {"$set": {"billing_email": doc.get("billing_email") or doc["email"],
                          "identity_type": "phone"},
                 "$unset": {"email": ""}},
            )
            moved += 1
        if moved:
            logger.info(f"moved {moved} unverified emails from users.email to billing_email")
    except Exception as e:
        logger.warning(f"billing email migration failed: {e}")


@app.on_event("startup")
async def ensure_payment_indexes():
    """Unique indexes are what keep a top-up from being credited twice."""
    try:
        # Payment-link records carry no order id until the customer starts
        # paying, so these must be sparse — the old non-sparse unique index
        # would reject every record after the first one missing the field.
        existing = await db.payments.index_information()
        if "razorpay_order_id_1" in existing and not existing["razorpay_order_id_1"].get("sparse"):
            await db.payments.drop_index("razorpay_order_id_1")
        await db.payments.create_index("razorpay_order_id", unique=True, sparse=True)
        await db.payments.create_index("razorpay_payment_link_id", unique=True, sparse=True)
        await db.payments.create_index("reference_id", unique=True, sparse=True)
        await db.wallet_ledger.create_index("payment_id", unique=True)
        await db.webhook_events.create_index("event_id", unique=True)
        # Private per-account history reads on these two.
        await db.analyses.create_index("owner_hash")
        await db.analyses.create_index("viewer_hashes")
        # Signup abuse guard reads these two on every new account.
        await db.free_credit_grants.create_index("device_id")
        await db.free_credit_grants.create_index([("ip", 1), ("created_at", -1)])
        # And this one, checked ahead of them: an identifier that already had
        # an account doesn't get a second free grant after deletion.
        await db.free_credit_tombstones.create_index("hash", unique=True)
    except Exception as e:
        logger.warning(f"payment index setup failed: {e}")
    if rzp.payments_configured():
        logger.info(f"razorpay ready ({'LIVE' if rzp.is_live_mode() else 'test'} mode)")
        # Prove the keys actually authenticate. A deployed image carrying stale
        # keys otherwise looks fine until a customer taps top-up and gets a 502.
        try:
            await rzp.razorpay_request("GET", "/payments?count=1")
            logger.info("razorpay credentials authenticated")
        except rzp.RazorpayError as e:
            logger.error(f"RAZORPAY CREDENTIALS REJECTED [{e.code}]: {e.description} — top-ups will fail")
        except Exception as e:
            logger.warning(f"razorpay credential check skipped: {e}")
