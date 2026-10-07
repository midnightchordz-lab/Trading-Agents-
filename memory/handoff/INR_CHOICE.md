# INR for every Indian user, whatever they signed in with (branch `fix/inr-choice`)

Builds on `feature/portfolio-import` @ `2bbf88f`. Fetch and fast-forward:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git fix/inr-choice
git merge --ff-only FETCH_HEAD
```
Do not force-push, rebase or rewrite history. Save to GitHub from the UI.

## Why Indian email users still saw USD / cards-only

1. **No way to choose.** The backend has offered `currency_options` since the
   two-currency work, but the app never rendered them. The currency came only
   from a guess (+91 phone, else device time zone / locale). Email sign-ups
   have no phone, so a VPN, a phone set to UTC, or web gave USD.
2. **Locks from abandoned checkouts.** `/pay/order` claims the currency the
   moment a checkout OPENS. Anyone who tapped a pack on an older build (which
   guessed from the phone's language region, i.e. US/GB for "English (US)")
   was locked to USD forever without ever paying. Detection fixes can't undo
   an existing lock.

## What changed

- `backend/routes/payments.py`
  - `currency_change_blocker(key, wallet_doc, target)`: a wallet's currency
    may change while (a) balance is 0, (b) no payment was ever credited
    (`wallet_ledger` empty for it — refunds claw back in the original
    currency), (c) no unexpired `created` payment link in the current currency.
  - `POST /api/wallet/currency {device_id, currency}` (rate-limited like
    `/pay/order`). 400 for unsupported currency, 409 with a plain-English
    reason when blocked. The write is conditional on balance still <= 0 in
    the same operation.
  - `/wallet/balance` adds `currency_changeable`.
- `frontend/src/api.ts`: `setWalletCurrency`, `currency_changeable` type.
- `frontend/src/components/WalletCard.tsx`: Android / web only (never iOS —
  Apple IAP), when `currency_changeable && payments_live`: a two-button row
  "₹ INR · UPI / cards" | "$ USD · cards" above the packs. Default stays the
  detected one; tapping switches and refreshes packs/prices. Disappears once
  money reaches the wallet. Hidden during launch-free (no top-up UI then).
- `backend/tests/test_currency_change.py`: 8 live-server tests (email user
  picks INR, abandoned-checkout lock undone, back and forth while empty,
  balance never relabelled, payment history blocks, open link blocks until
  expiry, bad currency 400, same currency no-op).

## Verify

```bash
cd /app/backend && sudo supervisorctl restart backend && sleep 4
python -m pytest -n 0 -q tests/test_currency_change.py tests/test_currency_by_region.py \
  tests/test_two_currency.py tests/test_currency_race_and_refunds.py tests/test_wallet.py
```
Report the summary line and any failure names. Then, as a test EMAIL user on
the web preview's Wallet card: the INR/USD row shows; tap INR -> packs become
₹99 / ₹199 / ₹499; open a ₹99 checkout and confirm the Razorpay page offers
UPI. Do not pay.

Production note (report counts only, never user data): how many wallets have
`currency: "USD"`, balance 0 and no `wallet_ledger` rows — these are the
users who can now switch.

No iOS build.
