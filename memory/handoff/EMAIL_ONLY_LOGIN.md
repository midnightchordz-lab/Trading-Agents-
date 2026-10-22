# Sign-in: email code or Google only, phone removed (branch `feature/email-only-login`)

Two commits on top of `feature/outlook-labels` @ `de808fa`:
- `d738f62`: email-first, with a phone fallback for existing accounts. You already verified it.
- The next commit: phone removed completely. The owner confirmed there are no live users.

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git feature/email-only-login
git merge --ff-only FETCH_HEAD
```
If `--ff-only` is blocked only by your own test-report auto-commit, a plain
`git merge --no-edit FETCH_HEAD` is fine. Never force-push or rebase.

## What the app offers now

- **Email**: type your email, receive a 6-digit code, enter it. Same backend flow as before.
- **Google**: unchanged.
- **Apple**: iOS only, unchanged. Apple requires it whenever Google sign-in is offered.
- No phone field, no country picker, no PHONE tab, no "use phone" link.
  `LoginScreen.tsx` no longer imports from `region.ts`. `api.authConfig` was removed from the app.

## Backend

- `PHONE_LOGIN_MODE` now defaults to **`off`**. Any phone code request gets a 400:
  "Phone sign-in isn't available — sign in with your email address instead".
  Older builds that still show a PHONE tab therefore cannot send texts either.
- `existing` / `on` are still available if phone is ever brought back. Leave the env var UNSET in production.
- The reviewer identifier is always allowed. It should be an email.
- `GET /api/auth/config` still exists and returns `{"phone_login":"off"}`.
- Tests: `tests/test_phone_login_mode.py` now has 10 tests (default off, phone refused with nothing texted;
  the other modes are tested explicitly). It uses a fake DB and a fake SMS sender, so it never texts anyone.
  `tests/test_otp_ip_spoofing.py` runs its SMS-budget test with `PHONE_LOGIN_MODE=on`.

## Verify

```bash
cd /app/backend && sudo supervisorctl restart backend && sleep 4
python -m pytest -n 0 -q tests/test_phone_login_mode.py tests/test_otp_ip_spoofing.py tests/test_sms.py
curl -s localhost:8001/api/auth/config
```

1. The tests should report 33 passed. The curl should print `{"phone_login":"off"}`.
2. Web preview:
   - The login screen shows ONLY the email field, TRANSMIT CODE and GOOGLE.
   - There's no phone link.
   - Email sign-in works end to end.
3. Make sure `PHONE_LOGIN_MODE` is NOT set in the deployment secrets, so it stays off.
4. Confirm `REVIEW_IDENTIFIER` is an email. Answer "email" or "phone" only.

Optional cleanup, for the owner to decide later: the Twilio env vars can be removed from
deployment once this is live. Nothing calls Twilio while phone sign-in is off.

No iOS build. No secrets in commits or replies.
