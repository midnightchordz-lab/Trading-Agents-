"""The email-repair scan and its write path, shared by every caller.

Kept out of the script itself so the scan and the write can never drift: a dry
run that reports one set of accounts and an `--apply` that writes a different
set would be worse than no tool at all. Two callers use this — the CLI script
in scripts/, and the env-gated one-shot in server.py for the case where the
production database is only reachable from inside the deployed container.
There was briefly an admin-only endpoint too; it was dropped at the owner's
request because it would have wired `require_admin` to a route, which a privacy
test deliberately forbids.

`find_email_repair_candidates` is read-only; `apply_email_repair` is the only
thing here that writes.
"""
import auth as au
from core import db


async def find_email_repair_candidates() -> tuple[list[dict], list[dict]]:
    """Accounts whose VERIFIED email the old migration took away.

    The first version of `migrate_unverified_billing_email` moved `users.email`
    to `billing_email` for any user with a phone, an email and no social id. A
    legacy EMAIL-OTP account carrying a stray phone matched that too — and for
    those the email was the verified sign-in identity, so removing it orphaned
    the account.

    The distinguishing evidence is `otp_requests`, which is never pruned: a row
    is `verified: True` only after that identifier completed a sign-in. An
    account with `identity_type: "phone"`, no `email`, and a verified row for
    the address now in `billing_email` is someone who really did sign in with
    it. Nobody else is a candidate.

    Returns (repairable, conflicts). A conflict is an address another account
    already holds — restoring it would leave two accounts with one identity, so
    a human decides. Neither list contains an email address.
    """
    repairable: list[dict] = []
    conflicts: list[dict] = []
    async for doc in db.users.find({
        "identity_type": "phone",
        "billing_email": {"$ne": None},
        "$or": [{"email": None}, {"email": {"$exists": False}}],
    }):
        billing = (doc.get("billing_email") or "").strip().lower()
        if not billing:
            continue
        _, canonical = au.normalize_identifier(billing)
        candidates = [e for e in {billing, canonical} if e]
        verified = await db.otp_requests.find_one(
            {"identifier": {"$in": candidates}, "verified": True}, {"_id": 1})
        if not verified:
            # No proof this person ever signed in with that address, so it is
            # exactly what the migration was meant to move. Leave it.
            continue
        holder = await db.users.find_one(
            {"email": {"$in": candidates}, "id": {"$ne": doc["id"]}}, {"id": 1})
        row = {"id": doc["id"], "restore_to": billing,
               "held_by": holder["id"] if holder else None}
        (conflicts if holder else repairable).append(row)
    return repairable, conflicts


async def apply_email_repair(repairable: list[dict]) -> int:
    """Restore the verified email onto each account in `repairable`.

    The filter repeats every condition the scan matched on, so an account that
    changed between the scan and the write (a fresh sign-in, a second run of
    this repair) is skipped rather than overwritten. Returns the number of
    accounts actually changed, which is why a second run reports zero.
    """
    restored = 0
    for row in repairable:
        result = await db.users.update_one(
            {"id": row["id"], "identity_type": "phone", "billing_email": {"$ne": None},
             "$or": [{"email": None}, {"email": {"$exists": False}}]},
            {"$set": {"email": row["restore_to"], "identity_type": "email"},
             "$unset": {"billing_email": ""}},
        )
        restored += result.modified_count
    return restored
