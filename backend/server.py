"""FastAPI application wiring.

Deliberately thin: configuration and the Mongo handle live in core.py, the
endpoints in routes/, the agent pipeline in pipeline.py and the market feeds in
market_data.py. This file only assembles them and owns the startup work that
has to happen exactly once per process.
"""
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

import razorpay_pay as rzp
from core import client, db, logger
from routes import analysis, auth_routes, market, payments, portfolio

app = FastAPI()

for module in (market, analysis, auth_routes, payments, portfolio):
    app.include_router(module.api_router)


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
    allow_methods=["*"],
    allow_headers=["*"],
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
