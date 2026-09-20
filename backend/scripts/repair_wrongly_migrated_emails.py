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

from email_repair import apply_email_repair, find_email_repair_candidates  # noqa: E402


async def main(apply: bool):
    repairable, conflicts = await find_email_repair_candidates()
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

    restored = await apply_email_repair(repairable)
    print(f"\nrestored {restored} of {len(repairable)} accounts; {len(conflicts)} conflicts left untouched")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually write the changes (default is a dry run)")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
