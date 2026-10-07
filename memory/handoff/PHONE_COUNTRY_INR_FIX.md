# PHONE_COUNTRY_INR_FIX.md

Three quick fixes for TradingAgents:
1. The app shows "HTTP …" / raw Twilio text when a phone number is entered.
2. Indian users get a USD, cards-only Razorpay checkout, with no UPI.
3. Phone sign-in needs a country-code picker in front of the number, as MoodFood has.

> **Source of truth:** branch **`fix/phone-and-inr`**, commit **`d0f16aa`**, in `midnightchordz-lab/Trading-Agents-`, based on `feature/portfolio-import` @ `457aace`.
> **Preferred:** `git fetch origin fix/phone-and-inr && git merge origin/fix/phone-and-inr`, then go to §5.
> **Only if you can't merge:** apply §1–§4 exactly.

## Ground rules

- **Change only what is listed.** No refactors, no restyling, no package changes (none are needed).
- **No force-push.** Commit on top of the current head.
- **Never print secrets** (including `REVIEW_OTP`) in chat, commit messages or files.
- **Keep the EMAIL tab working:** the App Store reviewer signs in with email.

---

## 1. New file `frontend/src/region.ts`
```ts
import * as Localization from "expo-localization";

// Where the user is, as best the device can tell, for two decisions: the
// wallet currency hint (INR unlocks UPI / GPay on Razorpay) and the default
// country code on the phone sign-in field.
//
// The first locale's regionCode alone is NOT a location: it is the region of
// the phone's LANGUAGE setting, and a great many people in India run their
// phone in "English (United States)" or "English (United Kingdom)". That
// reported US/GB and sent Indian users a USD, cards-only checkout. The
// device time zone follows where the phone actually is, so it wins.
const INDIA_TIME_ZONES = new Set(["Asia/Kolkata", "Asia/Calcutta"]);

export function detectRegion(): string {
  try {
    const tz = Localization.getCalendars()[0]?.timeZone || "";
    if (INDIA_TIME_ZONES.has(tz)) return "IN";
  } catch {
    // fall through to the locale list
  }
  try {
    const locales = Localization.getLocales();
    if (locales.some((l) => l.regionCode === "IN")) return "IN";
    return locales[0]?.regionCode || "";
  } catch {
    return "";
  }
}

export type Country = { iso: string; name: string; dial: string; flag: string };

// India first: it is most of the audience. Short on purpose — a full picker
// is a search problem; anyone elsewhere can type "+<code>" straight into the
// number field instead.
export const COUNTRIES: Country[] = [
  { iso: "IN", name: "India", dial: "+91", flag: "🇮🇳" },
  { iso: "US", name: "United States", dial: "+1", flag: "🇺🇸" },
  { iso: "GB", name: "United Kingdom", dial: "+44", flag: "🇬🇧" },
  { iso: "AE", name: "UAE", dial: "+971", flag: "🇦🇪" },
  { iso: "SG", name: "Singapore", dial: "+65", flag: "🇸🇬" },
  { iso: "CA", name: "Canada", dial: "+1", flag: "🇨🇦" },
  { iso: "AU", name: "Australia", dial: "+61", flag: "🇦🇺" },
  { iso: "SA", name: "Saudi Arabia", dial: "+966", flag: "🇸🇦" },
  { iso: "QA", name: "Qatar", dial: "+974", flag: "🇶🇦" },
  { iso: "DE", name: "Germany", dial: "+49", flag: "🇩🇪" },
];

export function defaultCountry(region: string = detectRegion()): Country {
  return COUNTRIES.find((c) => c.iso === region) || COUNTRIES[0];
}

/** Builds the E.164 number the backend expects from what was typed.
 *  Returns null when it can't be a phone number for that country. A number
 *  typed with its own "+" is taken as already international. */
export function toE164(country: Country, typed: string): string | null {
  const raw = typed.trim();
  if (raw.startsWith("+")) {
    const full = "+" + raw.slice(1).replace(/\D/g, "");
    return /^\+[1-9]\d{7,14}$/.test(full) ? full : null;
  }
  // Drop spaces/dashes and the trunk "0" people habitually type (098765…).
  let digits = raw.replace(/\D/g, "").replace(/^0+/, "");
  // Someone who typed the country code without "+" (919876543210).
  const cc = country.dial.slice(1);
  if (country.iso === "IN" && digits.length === 12 && digits.startsWith(cc)) digits = digits.slice(2);
  if (country.iso === "IN") return /^[6-9]\d{9}$/.test(digits) ? `+91${digits}` : null;
  const full = `${country.dial}${digits}`;
  return digits.length >= 6 && /^\+[1-9]\d{7,14}$/.test(full) ? full : null;
}
```

## 2. Edits (apply this diff exactly)

What it does:
- **`frontend/src/api.ts`:**
  - The currency `REGION` hint now comes from `detectRegion()`: time zone first. The old code used the phone's *language* region, so "English (US)" phones in India got USD.
  - `j()` shows a plain message by status code whenever an error body isn't our JSON `{detail: string}`, so the app never shows "HTTP 502" or "[object Object]".
- **`frontend/src/components/LoginScreen.tsx`:**
  - PHONE / EMAIL tabs.
  - A country-code button (+91 default) in front of a phone-pad field.
  - The app builds the +E.164 number itself.
  - Verify uses exactly the identifier the code was sent to.
- **`backend/sms.py`:**
  - Maps Twilio error codes, via the exception's `.code`, to plain messages. The raw exception text, which contains "HTTP 400 error" and a twilio.com URL, is logged but never returned.
  - Adds the `INPUT_ERRORS` set.
- **`backend/routes/auth_routes.py`:** number problems → **400**; provider failure → 502.
- **`backend/tests/test_sms.py`:** 5 new tests.

```diff
diff --git a/backend/routes/auth_routes.py b/backend/routes/auth_routes.py
index fe618bc..4b314dd 100644
--- a/backend/routes/auth_routes.py
+++ b/backend/routes/auth_routes.py
@@ -484,7 +484,11 @@ async def auth_otp_request(body: OtpRequest, request: Request):
     else:
         delivered, err = await sms.send_otp_sms(identifier, otp, ttl_minutes, mailer.EMAIL_FROM_NAME)
         if not delivered and not AUTH_DEBUG_RETURN_OTP:
-            raise HTTPException(status_code=502, detail=err or "Couldn't send the text — try again")
+            # A number problem is the user's to fix (400); a provider failure
+            # is ours (502). Hosting proxies may also swap a 502 body for their
+            # own page, so anything the user must act on stays a 400.
+            status = 400 if err in sms.INPUT_ERRORS else 502
+            raise HTTPException(status_code=status, detail=err or "Couldn't send the text — try again")
 
     response = {"identifier": identifier, "identifier_type": id_type, "sent": True}
     if AUTH_DEBUG_RETURN_OTP:
diff --git a/backend/sms.py b/backend/sms.py
index fcbde9c..05b80b7 100644
--- a/backend/sms.py
+++ b/backend/sms.py
@@ -37,6 +37,42 @@ def otp_message(otp: str, ttl_minutes: int, brand: str) -> str:
     return f"{otp} is your {brand} sign-in code. It expires in {ttl_minutes} minutes."
 
 
+# Twilio error code -> what the person typing their number should be told.
+# The exception's own text ("HTTP 400 error: Unable to create record: ...
+# https://www.twilio.com/docs/errors/21211") must never reach the screen.
+_NOT_A_NUMBER = "That doesn't look like a valid mobile number — check it and the country code"
+_COUNTRY_OFF = "We can't send texts to that country yet — sign in with your email address instead"
+_TWILIO_MESSAGES = {
+    21211: _NOT_A_NUMBER,   # invalid 'To' number
+    21614: _NOT_A_NUMBER,   # not a mobile number (landline)
+    21265: _NOT_A_NUMBER,   # 'To' number can't be a short code / invalid format
+    21266: _NOT_A_NUMBER,   # 'To' and 'From' are the same
+    21408: _COUNTRY_OFF,    # geo permissions: region not enabled
+    21612: _COUNTRY_OFF,    # route to this destination unavailable
+    21608: "This number isn't verified on the Twilio trial account — verify it in Twilio, or use email",
+    21610: "This number has opted out of messages from us",
+}
+# Problems with what the user typed (answer 400, so they fix it), as opposed
+# to our provider failing (answer 502, try again later).
+INPUT_ERRORS = {_NOT_A_NUMBER, _COUNTRY_OFF, _TWILIO_MESSAGES[21610],
+                "Enter your number with the country code, like +919876543210"}
+
+
+def _user_message(exc: Exception) -> str:
+    code = getattr(exc, "code", None)
+    if isinstance(code, int) and code in _TWILIO_MESSAGES:
+        return _TWILIO_MESSAGES[code]
+    text = str(exc)
+    for known, message in _TWILIO_MESSAGES.items():
+        if str(known) in text:
+            return message
+    if "unverified" in text.lower():
+        return _TWILIO_MESSAGES[21608]
+    if "not a valid phone number" in text.lower():
+        return _NOT_A_NUMBER
+    return "Couldn't send the text — try again, or use your email address"
+
+
 def _send_sync(to: str, body: str) -> str:
     from twilio.rest import Client  # imported lazily so the module loads without creds
 
@@ -51,19 +87,13 @@ async def send_otp_sms(to: str, otp: str, ttl_minutes: int, brand: str) -> tuple
     if not sms_configured():
         return False, "Text messages aren't available yet — sign in with your email address instead"
     if not is_e164(to):
-        return False, "Enter your number with the country code, like +14155550134"
+        return False, "Enter your number with the country code, like +919876543210"
     try:
         sid = await asyncio.to_thread(_send_sync, to, otp_message(otp, ttl_minutes, brand))
         logger.info(f"OTP SMS queued ({sid})")
         return True, None
     except Exception as e:
-        msg = str(e)
-        logger.error(f"OTP SMS failed: {msg}")
-        # Trial accounts can only text numbers verified in the Twilio console.
-        if "unverified" in msg.lower() or "21608" in msg:
-            return False, "This number isn't verified on the Twilio trial account — verify it in Twilio, or use email"
-        if "21211" in msg or "not a valid phone number" in msg.lower():
-            return False, "That doesn't look like a valid phone number"
-        if "21610" in msg:
-            return False, "This number has opted out of messages from us"
-        return False, "Couldn't send the text — try again, or use your email address"
+        # The full Twilio text goes to the log for us; only the mapped,
+        # plain-language message goes back to the app.
+        logger.error(f"OTP SMS failed (code={getattr(e, 'code', None)}): {e}")
+        return False, _user_message(e)
diff --git a/backend/tests/test_sms.py b/backend/tests/test_sms.py
index b6213e5..a2168ef 100644
--- a/backend/tests/test_sms.py
+++ b/backend/tests/test_sms.py
@@ -1,4 +1,5 @@
 """Tests for the Twilio SMS helper (no network — delivery itself is not called)."""
+import asyncio
 import os
 import sys
 
@@ -40,3 +41,56 @@ def test_bad_number_is_rejected_before_any_network_call():
     delivered, err = asyncio.run(sms.send_otp_sms("4155550134", "123456", 5, "TradingAgents"))
     assert delivered is False
     assert "country code" in err
+
+
+class _TwilioLikeError(Exception):
+    """Shape of twilio.base.exceptions.TwilioRestException: a numeric .code and
+    a str() that carries the raw API text, including a docs URL."""
+
+    def __init__(self, code, text):
+        super().__init__(text)
+        self.code = code
+
+
+def _send_failing_with(monkeypatch, exc):
+    monkeypatch.setattr(sms, "sms_configured", lambda: True)
+
+    def boom(to, body):
+        raise exc
+
+    monkeypatch.setattr(sms, "_send_sync", boom)
+    return asyncio.run(sms.send_otp_sms("+919876543210", "123456", 5, "TradingAgents"))
+
+
+RAW = ("HTTP 400 error: Unable to create record: Permission to send an SMS has not been enabled "
+       "for the region indicated by the 'To' number: +2136751. "
+       "More information: https://www.twilio.com/docs/errors/21408")
+
+
+def test_twilio_text_and_urls_never_reach_the_user(monkeypatch):
+    for code in (21211, 21408, 21614, 21265, 21610, 21608, 99999):
+        delivered, err = _send_failing_with(monkeypatch, _TwilioLikeError(code, RAW))
+        assert delivered is False
+        assert "http" not in err.lower(), err
+        assert "twilio.com" not in err.lower(), err
+        assert "unable to create record" not in err.lower(), err
+
+
+def test_region_not_enabled_points_to_email(monkeypatch):
+    _, err = _send_failing_with(monkeypatch, _TwilioLikeError(21408, RAW))
+    assert "email" in err and err in sms.INPUT_ERRORS
+
+
+def test_invalid_number_is_an_input_error(monkeypatch):
+    _, err = _send_failing_with(monkeypatch, _TwilioLikeError(21211, "HTTP 400 error: ... is not a valid phone number"))
+    assert err in sms.INPUT_ERRORS
+
+
+def test_code_found_in_text_when_exception_has_no_code_attr(monkeypatch):
+    _, err = _send_failing_with(monkeypatch, RuntimeError(RAW))
+    assert err in sms.INPUT_ERRORS and "http" not in err.lower()
+
+
+def test_provider_outage_is_not_blamed_on_the_user(monkeypatch):
+    _, err = _send_failing_with(monkeypatch, _TwilioLikeError(20500, "HTTP 500 error: internal"))
+    assert err not in sms.INPUT_ERRORS and "http" not in err.lower()
diff --git a/frontend/src/api.ts b/frontend/src/api.ts
index 6bdb255..fe25598 100644
--- a/frontend/src/api.ts
+++ b/frontend/src/api.ts
@@ -1,6 +1,6 @@
 // API client for the TradingAgents backend.
 import * as Linking from "expo-linking";
-import * as Localization from "expo-localization";
+import { detectRegion } from "@/src/region";
 import { Platform } from "react-native";
 
 const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;
@@ -9,14 +9,9 @@ const API = `${BASE}/api`;
 // Device region, sent with wallet calls so the backend can pick the right
 // currency for a wallet that hasn't locked one yet (INR for India, because
 // UPI/GPay only exist on INR payment links). The backend decides; this is
-// only a hint, and an unknown region is simply omitted.
-const REGION = (() => {
-  try {
-    return Localization.getLocales()[0]?.regionCode || "";
-  } catch {
-    return "";
-  }
-})();
+// only a hint, and an unknown region is simply omitted. See region.ts for why
+// this is the time zone first and the language setting second.
+const REGION = detectRegion();
 
 // Deep link back into THIS app, sent with a top-up so the payment page can
 // render a "Return to the app" button. `createURL` knows which runtime we are
@@ -295,6 +290,14 @@ export type Analysis = {
   updated_at: string;
 };
 
+function friendlyStatus(status: number): string {
+  if (status === 401) return "Please sign in again.";
+  if (status === 429) return "Too many attempts — wait a minute and try again.";
+  if (status === 413) return "That file is too large.";
+  if (status >= 500) return "Our server had a problem — try again in a moment.";
+  return "Something went wrong — try again.";
+}
+
 async function j<T>(path: string, opts?: RequestInit): Promise<T> {
   const token = authTokenGetter ? await authTokenGetter() : null;
   const res = await fetch(`${API}${path}`, {
@@ -309,10 +312,13 @@ async function j<T>(path: string, opts?: RequestInit): Promise<T> {
     },
   });
   if (!res.ok) {
-    let detail = `HTTP ${res.status}`;
+    // Our API always answers errors as JSON {detail: "..."}. Anything else —
+    // a hosting proxy's HTML error page, a timeout, a validation array — must
+    // not reach the user as "HTTP 502" or "[object Object]".
+    let detail = friendlyStatus(res.status);
     try {
       const body = await res.json();
-      detail = body?.detail || detail;
+      if (typeof body?.detail === "string" && body.detail) detail = body.detail;
     } catch {}
     const error: Error & { status?: number } = new Error(detail);
     // The status, without touching the message: callers match machine-readable
diff --git a/frontend/src/components/LoginScreen.tsx b/frontend/src/components/LoginScreen.tsx
index b514517..dae98b3 100644
--- a/frontend/src/components/LoginScreen.tsx
+++ b/frontend/src/components/LoginScreen.tsx
@@ -1,5 +1,5 @@
 import React, { useEffect, useRef, useState } from "react";
-import { View, Text, TextInput, Pressable, ActivityIndicator, StyleSheet, Alert, Animated, Platform } from "react-native";
+import { View, Text, TextInput, Pressable, ActivityIndicator, StyleSheet, Alert, Animated, Platform, ScrollView } from "react-native";
 import * as AppleAuthentication from "expo-apple-authentication";
 import { useSafeAreaInsets } from "react-native-safe-area-context";
 import { GoogleLogo } from "phosphor-react-native";
@@ -8,6 +8,7 @@ import { api } from "@/src/api";
 import { setStoredToken } from "@/src/auth";
 import { startGoogleSignIn } from "@/src/googleAuth";
 import { getWalletDeviceId } from "@/src/wallet";
+import { COUNTRIES, Country, defaultCountry, toE164 } from "@/src/region";
 
 // Futuristic "agent terminal" look — scoped to the login screen only, per
 // product decision. Dark panel, acid-green accent, monospace throughout,
@@ -31,6 +32,7 @@ const TEXT_DIM = "#66755A";
 const TEXT_MID = "#CBD8BC";
 
 type Step = "identifier" | "otp";
+type Mode = "phone" | "email";
 
 function PulseDot() {
   const opacity = useRef(new Animated.Value(1)).current;
@@ -61,6 +63,13 @@ function CornerBracket({ position }: { position: "tl" | "tr" | "bl" | "br" }) {
 export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
   const insets = useSafeAreaInsets();
   const [step, setStep] = useState<Step>("identifier");
+  const [mode, setMode] = useState<Mode>("phone");
+  const [country, setCountry] = useState<Country>(() => defaultCountry());
+  const [pickerOpen, setPickerOpen] = useState(false);
+  const [phone, setPhone] = useState("");
+  const [email, setEmail] = useState("");
+  // What the code was actually sent to (full +E.164 number or email); verify
+  // must use exactly this, not whatever is in the field now.
   const [identifier, setIdentifier] = useState("");
   const [otp, setOtp] = useState("");
   const [loading, setLoading] = useState(false);
@@ -82,14 +91,31 @@ export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }
   }, []);
 
   const requestCode = async () => {
-    if (!identifier.trim()) {
-      Alert.alert("Enter a phone number or email");
-      return;
+    let target: string;
+    if (mode === "phone") {
+      const full = toE164(country, phone);
+      if (!full) {
+        Alert.alert(
+          "Check your number",
+          country.iso === "IN"
+            ? "Enter your 10-digit mobile number, e.g. 98765 43210."
+            : `Enter your number without the country code — ${country.dial} is added for you.`
+        );
+        return;
+      }
+      target = full;
+    } else {
+      target = email.trim();
+      if (!target) {
+        Alert.alert("Enter your email address");
+        return;
+      }
     }
     setLoading(true);
     try {
       const deviceId = await getWalletDeviceId();
-      const res = await api.requestOtp(identifier.trim(), deviceId);
+      const res = await api.requestOtp(target, deviceId);
+      setIdentifier(res.identifier || target);
       setDebugOtp(res.debug_otp || null);
       setStep("otp");
     } catch (e: any) {
@@ -107,7 +133,7 @@ export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }
     setLoading(true);
     try {
       const deviceId = await getWalletDeviceId();
-      const res = await api.verifyOtp(identifier.trim(), otp.trim(), deviceId);
+      const res = await api.verifyOtp(identifier, otp.trim(), deviceId);
       await setStoredToken(res.token);
       onAuthenticated();
     } catch (e: any) {
@@ -178,18 +204,83 @@ export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }
 
         {step === "identifier" ? (
           <>
+            <View style={styles.modeRow}>
+              {(["phone", "email"] as Mode[]).map((m) => (
+                <Pressable
+                  key={m}
+                  testID={`login-mode-${m}`}
+                  onPress={() => {
+                    setMode(m);
+                    setPickerOpen(false);
+                  }}
+                  style={[styles.modeBtn, mode === m && styles.modeBtnOn]}
+                >
+                  <Text style={[styles.modeText, mode === m && styles.modeTextOn]}>{m === "phone" ? "PHONE" : "EMAIL"}</Text>
+                </Pressable>
+              ))}
+            </View>
             <View style={styles.fieldBlock}>
-              <Text style={styles.fieldLabel}>&gt; PHONE OR EMAIL</Text>
-              <TextInput
-                testID="login-identifier-input"
-                value={identifier}
-                onChangeText={setIdentifier}
-                placeholder="you@example.com"
-                placeholderTextColor={TEXT_DIM}
-                autoCapitalize="none"
-                keyboardType="email-address"
-                style={styles.input}
-              />
+              <Text style={styles.fieldLabel}>&gt; {mode === "phone" ? "MOBILE NUMBER" : "EMAIL"}</Text>
+              {mode === "phone" ? (
+                <>
+                  <View style={styles.phoneRow}>
+                    <Pressable
+                      testID="login-country"
+                      onPress={() => setPickerOpen((v) => !v)}
+                      style={styles.ccBtn}
+                      accessibilityLabel={`Country code ${country.name} ${country.dial}`}
+                    >
+                      <Text style={styles.ccText}>
+                        {country.flag} {country.dial} ▾
+                      </Text>
+                    </Pressable>
+                    <TextInput
+                      testID="login-phone-input"
+                      value={phone}
+                      onChangeText={setPhone}
+                      placeholder={country.iso === "IN" ? "98765 43210" : "Phone number"}
+                      placeholderTextColor={TEXT_DIM}
+                      keyboardType="phone-pad"
+                      textContentType="telephoneNumber"
+                      autoComplete="tel"
+                      style={[styles.input, { flex: 1 }]}
+                    />
+                  </View>
+                  {pickerOpen ? (
+                    <ScrollView style={styles.ccList} nestedScrollEnabled keyboardShouldPersistTaps="handled">
+                      {COUNTRIES.map((c) => (
+                        <Pressable
+                          key={c.iso}
+                          testID={`login-country-${c.iso}`}
+                          onPress={() => {
+                            setCountry(c);
+                            setPickerOpen(false);
+                          }}
+                          style={[styles.ccRow, c.iso === country.iso && styles.ccRowOn]}
+                        >
+                          <Text style={styles.ccRowText}>
+                            {c.flag}  {c.name}
+                          </Text>
+                          <Text style={styles.ccRowDial}>{c.dial}</Text>
+                        </Pressable>
+                      ))}
+                    </ScrollView>
+                  ) : null}
+                </>
+              ) : (
+                <TextInput
+                  testID="login-identifier-input"
+                  value={email}
+                  onChangeText={setEmail}
+                  placeholder="you@example.com"
+                  placeholderTextColor={TEXT_DIM}
+                  autoCapitalize="none"
+                  keyboardType="email-address"
+                  textContentType="emailAddress"
+                  autoComplete="email"
+                  style={styles.input}
+                />
+              )}
             </View>
             <Pressable testID="login-send-code" onPress={requestCode} disabled={loading} style={styles.primaryBtn}>
               {loading ? <ActivityIndicator color={PANEL_BG} /> : <Text style={styles.primaryBtnText}>TRANSMIT CODE →</Text>}
@@ -279,6 +370,26 @@ const styles = StyleSheet.create({
     paddingHorizontal: spacing.sm,
     paddingVertical: spacing.sm,
   },
+  modeRow: { flexDirection: "row", gap: spacing.xs, marginBottom: spacing.sm },
+  modeBtn: { flex: 1, borderWidth: 0.5, borderColor: LINE, borderRadius: 8, paddingVertical: spacing.sm, alignItems: "center" },
+  modeBtnOn: { borderColor: LIME, backgroundColor: FIELD_BG },
+  modeText: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 1, color: TEXT_DIM },
+  modeTextOn: { color: LIME },
+  phoneRow: { flexDirection: "row", gap: spacing.xs },
+  ccBtn: {
+    backgroundColor: FIELD_BG,
+    borderWidth: 0.5,
+    borderColor: LIME,
+    borderRadius: 6,
+    paddingHorizontal: spacing.sm,
+    justifyContent: "center",
+  },
+  ccText: { fontFamily: fonts.mono, fontSize: 13, color: TEXT_BRIGHT },
+  ccList: { maxHeight: 200, marginTop: spacing.xs, borderWidth: 0.5, borderColor: LINE, borderRadius: 6, backgroundColor: FIELD_BG },
+  ccRow: { flexDirection: "row", justifyContent: "space-between", paddingHorizontal: spacing.sm, paddingVertical: spacing.sm },
+  ccRowOn: { backgroundColor: LINE },
+  ccRowText: { fontFamily: fonts.mono, fontSize: 12, color: TEXT_BRIGHT },
+  ccRowDial: { fontFamily: fonts.mono, fontSize: 12, color: LIME },
   otpLabel: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 1, color: TEXT_DIM, textAlign: "center", marginBottom: spacing.sm },
   otpInput: { textAlign: "center", letterSpacing: 6, fontSize: 18, marginBottom: spacing.sm },
   debugNote: { fontFamily: fonts.mono, fontSize: 10, color: LIME, marginBottom: spacing.md },
```

## 3. Append to `memory/PRD.md`
```markdown
## Phone sign-in country code, INR detection, friendly SMS errors (2026-10-07) — branch `fix/phone-and-inr`
- **Country code on phone sign-in**: the login screen now has PHONE / EMAIL tabs. Phone shows a country-code button (🇮🇳 +91 by default; 10 common countries) in front of a phone-pad field; `src/region.ts` `toE164()` builds the +E.164 number (strips spaces, dashes and a leading 0, accepts a pasted `+…` number, accepts `91XXXXXXXXXX` typed without "+", and requires a 6-9-leading 10-digit number for India). Verify uses exactly the identifier the code was sent to. Email tab unchanged (App Store reviewer signs in there).
- **INR for Indian users**: the wallet currency hint came from `getLocales()[0].regionCode`, which is the phone's LANGUAGE region — Indian phones set to English (US/UK) reported US/GB and got USD, cards-only Razorpay links (no UPI). `detectRegion()` now checks the device time zone first (Asia/Kolkata → IN), then any locale with region IN, then the first locale. Backend `suggest_currency` unchanged (+91 sign-in phone or region IN → INR). NOTE: a wallet whose currency is already LOCKED (locked on the first top-up order, even if that link was never paid) stays in that currency by design.
- **No raw HTTP / Twilio text on screen**: `api.ts j()` shows plain messages by status when an error body isn't our JSON `{detail: string}` (proxy HTML pages, timeouts, validation arrays) instead of "HTTP 502" / "[object Object]". `sms.py` maps Twilio error codes (21211/21614/21265/21266 invalid number, 21408/21612 country not enabled → "sign in with email", 21608 trial, 21610 opted out) via the exception's `.code`, never shows the exception text (which carries "HTTP 400 error" and a twilio.com URL); `/auth/otp/request` answers number problems with 400 (user must fix) and provider failures with 502.
- **Verified**: `tests/test_sms.py` 10 passed (5 new; the pre-existing `test_bad_number_is_rejected_before_any_network_call` needs TWILIO_* set, as on the server); `toE164` 11 cases and `detectRegion` (English-US phone in India → IN) checked; eslint/tsc clean for changed files. Not verified: the login UI on a device.
```

## 4. Not in code — tell the owner

- **Already-locked wallets stay as they are.** A wallet's currency locks on its **first top-up order**, even if that link was never paid. Any account that already opened a USD link stays USD by design. If the owner wants a specific test account reset, only do it for a wallet with **balance 0 and no captured payment**, and only when they ask.
- **Twilio console:** Messaging → Settings → Geo permissions: allow only the countries the app serves (at least India). Keep high-risk destinations off; there were SMS-pumping attempts to +213, +224, +255, +20, +218 and +250.

---

## 5. Verify (all must pass)

1. **SMS tests:** `cd backend && pytest tests/test_sms.py`: **10 passed**. The server has the `TWILIO_*` env vars, which one older test needs.
2. **Full backend suite** passes as before.
3. **Bad numbers through the real endpoint:** with a phone number Twilio rejects, `POST /api/auth/otp/request` (for example `+999123456789`, or a country not in Geo permissions) must:
   - return **400**, not 502;
   - have a `detail` that contains **no** "http", "twilio.com" or "Unable to create record".
4. **App lint and types:** `npx eslint src/region.ts src/api.ts src/components/LoginScreen.tsx` is clean, and `npx tsc --noEmit` shows no new errors in these files.
5. **App checks in the preview:**
   - **Default:** Login shows PHONE / EMAIL tabs; PHONE shows **🇮🇳 +91 ▾** for a device in India.
   - **Typing:** entering `98765 43210` sends to `+919876543210`. A leading `0` and a pasted `+91…` both work. An invalid Indian number shows "Enter your 10-digit mobile number".
   - **Picker:** the country list switches the code; for example 🇺🇸 +1 then `4155550134` sends to `+14155550134`.
   - **Email:** the EMAIL tab still signs in the App Store reviewer account.
   - **INR:** on a device set to **English (United States)** with the **India time zone**, a *new* account's wallet shows **₹** packs, and the Razorpay link offers **UPI**.
6. **Commit message:** contains no secret values. Push normally.

## 6. Tell the owner

- **What changed,** with the test results.
- **New build:** the login UI change needs a new Android build. It can ship together with the Firebase and portfolio-import builds if they haven't gone out yet.
