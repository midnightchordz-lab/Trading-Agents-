# Email-first sign-in; phone only for existing phone accounts (branch `feature/email-only-login`)

Builds on `feature/outlook-labels` @ `de808fa` (your latest save). Fast-forward:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git feature/email-only-login
git merge --ff-only FETCH_HEAD
```
If `--ff-only` is blocked only by your own test-report auto-commit, a plain
`git merge --no-edit FETCH_HEAD` is fine. Never force-push or rebase.

## Why

Phone OTP was the least reliable sign-in (carrier filtering, Twilio geo
permissions, number errors). It is also the only one that costs money and can be pumped.
New users now sign in by email. People who already signed up with a phone
number must not be locked out.

## Backend: `PHONE_LOGIN_MODE` (deployment env var, default `existing`)

- `existing` (default):
  - A code is texted ONLY to a number that already has an account (`users.phone`).
  - Any other number gets the identical `{"sent": true}` reply. Nothing is sent, and the stored row
    holds the hash of a code nobody is told, marked `suppressed: true`.
  - Per-number and per-IP limits still count these rows. The SMS budget ignores them.
  - Result: no SMS spend, and no way to learn which numbers have accounts.
- `off`: phone codes are refused with a 400: "Phone sign-in isn't available — sign in with your email address instead".
- `on`: the old behaviour.
- The store-reviewer identifier is always allowed, and it is never texted.
- The mode is read per request, so a redeploy with a new value takes effect without an app build.
  Older app builds that still show a PHONE tab obey it too.
- New `GET /api/auth/config` returns `{"phone_login": "existing"|"off"|"on"}`. No auth, nothing secret.

Files: `backend/routes/auth_routes.py`. Tests: new `tests/test_phone_login_mode.py` (9).
It runs the handler in-process with a fake DB and fake SMS sender, so it never texts anyone.
`tests/test_otp_ip_spoofing.py` was updated: the SMS-budget test now runs with
`PHONE_LOGIN_MODE=on`, and its baseline ignores suppressed rows.

## App: `frontend/src/components/LoginScreen.tsx`, `frontend/src/api.ts`

- Opens on EMAIL. Until `/auth/config` answers, or if it fails, only email is shown.
- `on`: EMAIL | PHONE tabs (email first).
- `existing`: no tabs. A small link under the button: "SIGNED UP WITH A PHONE NUMBER? USE PHONE >".
  It toggles to the phone field (with "< BACK TO EMAIL SIGN-IN").
- `off`: email only.
- Google (and Apple on iOS) are unchanged.
- testIDs: `login-identifier-input` (email), `login-phone-input`, `login-country`, `login-phone-fallback` (new link).
  `login-mode-*` only exists in `on` mode.

## Verify

```bash
cd /app/backend && sudo supervisorctl restart backend && sleep 4
python -m pytest -n 0 -q tests/test_phone_login_mode.py tests/test_otp_ip_spoofing.py tests/test_sms.py
curl -s localhost:8001/api/auth/config
```

1. The tests should report 32 passed. The curl should print `{"phone_login":"existing"}`.
2. Web preview:
   - The login screen opens on email with no tabs.
   - The "signed up with a phone number?" link shows the phone field and toggles back.
   - Email sign-in works end to end.
3. DO NOT request codes for real phone numbers.
4. Report a COUNT only, never numbers or emails: how many users have a `phone` set and no `email`.
   Those are the people who rely on the fallback link.
5. Confirm `REVIEW_IDENTIFIER` in the deployment secrets is an EMAIL address (not a phone number),
   so reviewers use the main email field. Report "email" or "phone" only, never the value.

No iOS build. No secrets in commits or replies.
