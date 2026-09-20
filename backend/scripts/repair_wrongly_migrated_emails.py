#!/usr/bin/env python3
"""Undo the damage the old billing-email migration could have done.

THE DAMAGE: the first version of `migrate_unverified_billing_email` moved
`users.email` to `billing_email` for ANY user with a phone, an email and no
Google/Apple id. A legacy EMAIL-OTP account that also carried a stray phone
(the same pre-fix payment path could write one) matched that filter — and for
those accounts the email was the verified sign-in identity. Removing it locked
the person out of their own history and wallet: their next email sign-in
created a fresh, empty account.

THE EVIDENCE THIS USES: `otp_requests` rows are never deleted, and a row is
`verified: True` only after that identifier completed a sign-in. So a user
with `identity_type: "phone"`, no `email`, and a VERIFIED OTP row for the
address now sitting in `billing_email` is someone who really did sign in with
that email. Nobody else is touched.

DRY RUN BY DEFAULT. It prints counts and internal user ids — never an address —
and changes nothing until `--apply`. It never runs at startup; a data repair
that runs itself on every boot is how the original problem happened.

    python scripts/repair_wrongly_migrated_emails.py            # report only
    python scripts/repair_wrongly_migrated_emails.py --apply    # make changes

CONFLICTS ARE SKIPPED, NOT MERGED: if another account already holds that email,
restoring it would create two accounts with the same identity. Those are listed
for a human to decide; merging accounts silently is not this script's business.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth as au  # noqa: E402
from core import db  # noqa: E402


async def find_candidates():
    """Users whose verified email was taken away by the old migration."""
    repairable, conflicts = [], []
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
        (conflicts if holder else repairable).append(
            {"id": doc["id"], "restore_to": billing, "held_by": holder["id"] if holder else None})
    return repairable, conflicts


async def main(apply: bool):
    repairable, conflicts = await find_candidates()
    print(f"scanned users with identity_type=phone and a billing_email but no email")
    print(f"  repairable (verified OTP exists for that address): {len(repairable)}")
    print(f"  skipped, another account already holds the address: {len(conflicts)}")
    for row in repairable:
        print(f"  REPAIRABLE user_id={row['id']}")
    for row in conflicts:
        print(f"  CONFLICT    user_id={row['id']} address already on user_id={row['held_by']}")

    if not apply:
        print("\nDRY RUN — nothing was changed. Re-run with --apply to restore the accounts above.")
        return

    restored = 0
    for row in repairable:
        result = await db.users.update_one(
            # Re-checked at write time: if anything about the account changed
            # since the scan, skip it rather than overwrite.
            {"id": row["id"], "identity_type": "phone", "billing_email": {"$ne": None},
             "$or": [{"email": None}, {"email": {"$exists": False}}]},
            {"$set": {"email": row["restore_to"], "identity_type": "email"},
             "$unset": {"billing_email": ""}},
        )
        restored += result.modified_count
    print(f"\nrestored {restored} of {len(repairable)} accounts; {len(conflicts)} conflicts left untouched")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually write the changes (default is a dry run)")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
