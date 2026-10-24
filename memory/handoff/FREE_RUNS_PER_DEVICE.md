# One set of free runs per phone

Branch: `feature/free-runs-per-device`

## The problem
On the same phone, signing up with a new email address gave a fresh free allowance every time:
- the 10 sign-up credits;
- 2 free Trade Reviews;
- launch-free daily runs, if the launch window is on.

The old "one grant per device" check used a random ID the app keeps in its own storage. Clearing app data, reinstalling or using the web app reset it. Trade Review and launch-free runs had no device check at all.

## The fix
- **App** (`frontend/src/installId.ts`, `src/api.ts`): every API call sends an `X-Install-Id` header.
  - Android: `android:<ANDROID_ID>`. This survives a reinstall and "clear data"; only a factory reset changes it.
  - iOS: `ios:<id kept in the Keychain>`. This survives a reinstall.
  - Web: no header.
  - New dependency: `expo-application ~57.0.0`. **It needs a new native build.**
- **Backend** (`backend/free_grants.py`): the decision is made once at sign-up, and it covers all three free allowances.
  - `granted`: the full allowance. The phone's grant is recorded as a keyed hash of the install ID; the raw ID is never stored. A unique index stops two sign-ups at the same moment from both getting one.
  - `withheld`: this phone already had a grant, or an existing check refused it (deleted account's email, throwaway email domain, network or global caps). The account signs in and can pay, but gets 0 credits, no free Trade Reviews and no launch-free runs.
  - `deferred`: no install ID was sent (old app version, web app, or a script). Nothing is granted yet. The first time the account is seen from a phone with the current app (`/auth/me`, `/wallet/balance`, `/analyze`, `/trade-review`), it gets the grant, provided that phone hasn't had one.
- **Launch-free daily runs** now need a signed-in account that wasn't refused a grant. Anonymous callers no longer get them, because they were keyed on a random device ID that a script can make endlessly.
- **Existing accounts are untouched.** They have no `free_grant_state`, so they keep whatever they have.

## What this does not stop
Someone with many phones, or a script that invents install IDs. The existing network cap (5 a day) and global cap (200 an hour) remain the backstop. Google Play Integrity would be the next step if that becomes a problem.

## Tests
- `tests/test_free_runs_per_device.py`: 21 offline tests.
- The sign-up helpers in `tests/test_signup_abuse_and_webhook.py` and `tests/test_iteration_27_adversarial.py` now send a fresh `X-Install-Id`, as the current app does.

## Emergent: please verify
1. Merge `main` and restart the backend; the new unique index on `free_credit_grants.install_hash` is created at startup. Run `cd /app/frontend && yarn install` for `expo-application`.
2. Run `pytest backend/tests/test_free_runs_per_device.py backend/tests/test_trade_review.py backend/tests/test_signup_abuse_and_webhook.py backend/tests/test_findings_4_14.py backend/tests/test_free_credits.py backend/tests/test_launch_free.py`. Report any failure in full.
   - Live tests that create accounts through `/api/auth/otp/verify` **without** an `X-Install-Id` header will now see 0 credits (deferred). That is the intended behaviour; send the header in those helpers.
   - Tests that expect anonymous launch-free runs will fail; that is also intended.
3. With curl:
   - Sign up two new emails with the same header (`X-Install-Id: android:1a2b3c4d5e6f7a8b`). The first should get `free_credits_remaining` 10 and the second 0, with `trade_review_free_used` 2.
   - Sign up a third email with no header and confirm `free_grant_state: "deferred"`. Then call `/api/auth/me` with a new, unused header and confirm it changes to `granted` with 10 credits.
4. The web preview sends no header, so a new account made in the preview will show 0 free credits. That is expected.
5. Clean up test users and grants by ID only. Leave `AUTH_DEBUG_RETURN_OTP=false`, and never print tokens or secrets.

## For the owner
- **New native Android/iOS build needed.** Until users update, new accounts on the old app get no free runs, and get them later from the updated app.
- **Play Console Data safety:** add "Device or other IDs", collected for fraud prevention and not shared. Also update the privacy policy.
