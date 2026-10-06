"""Firebase (Crashlytics + Analytics) configuration guards.

These are the settings that keep the app's privacy promises true — the
consent screen says the app never uses data for advertising and never links
searched tickers to an identity — and that keep builds working before the
Firebase config files have been added. Each one is easy to lose silently in a
later edit, so each one is pinned here.
"""
import json
import os
import re

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")


def _read(name):
    with open(os.path.join(FRONTEND, name)) as f:
        return f.read()


def test_analytics_is_off_until_consent_and_never_collects_ad_ids():
    rn = json.loads(_read("firebase.json"))["react-native"]
    assert rn["analytics_auto_collection_enabled"] is False  # switched on only after consent
    assert rn["google_analytics_adid_collection_enabled"] is False
    assert rn["google_analytics_ssaid_collection_enabled"] is False
    assert rn["google_analytics_registration_with_ad_network_enabled"] is False
    assert rn["analytics_default_allow_ad_storage"] is False
    assert rn["analytics_default_allow_ad_user_data"] is False
    assert rn["analytics_default_allow_ad_personalization_signals"] is False


def test_android_ad_id_permission_is_blocked():
    """firebase-analytics merges com.google.android.gms.permission.AD_ID into
    the manifest; blocking it is what lets the Play data-safety form say the
    app doesn't use the advertising ID."""
    app = json.loads(_read("app.json"))["expo"]
    assert "com.google.android.gms.permission.AD_ID" in app["android"].get("blockedPermissions", [])


def test_ios_uses_dynamic_frameworks_for_firebase_spm():
    """React Native Firebase resolves the Firebase Apple SDK with Swift Package
    Manager, which needs dynamic frameworks. It's autolinked whether or not the
    config files exist, so this must be unconditional, in app.json."""
    plugins = json.loads(_read("app.json"))["expo"]["plugins"]
    props = [p[1] for p in plugins if isinstance(p, list) and p[0] == "expo-build-properties"]
    assert props and props[0]["ios"]["useFrameworks"] == "dynamic"


def test_firebase_plugins_are_gated_on_both_config_files():
    """The Firebase config plugins throw when google-services.json or
    GoogleService-Info.plist is missing, so app.config.js must only add them
    when both exist — and app.json must never list them directly."""
    src = _read("app.config.js")
    assert "google-services.json" in src and "GoogleService-Info.plist" in src
    assert re.search(r"if \(!has\(ANDROID_FILE\) \|\| !has\(IOS_FILE\)\) return config;", src)
    plugins = json.loads(_read("app.json"))["expo"]["plugins"]
    names = [p[0] if isinstance(p, list) else p for p in plugins]
    assert not [n for n in names if n.startswith("@react-native-firebase/")]


def test_ios_analytics_is_built_without_ad_id_support():
    src = _read("app.config.js")
    assert re.search(r'"@react-native-firebase/analytics",\s*\{\s*ios:\s*\{\s*withoutAdIdSupport:\s*true', src)


def test_web_build_has_a_no_op_telemetry_module():
    """Metro resolves firebase.web.ts for web; without it the web bundle would
    pull in the native SDK wrapper."""
    native = _read("src/firebase.ts")
    web = _read("src/firebase.web.ts")
    for fn in ("setTelemetryUser", "trackScreen", "trackEvent"):
        assert f"export function {fn}" in native
        assert f"export function {fn}" in web
    assert "@react-native-firebase" not in web


def test_consent_screen_discloses_firebase():
    src = _read("src/components/ConsentScreen.tsx")
    assert "Google Firebase" in src
    assert "Crash reports and app-usage statistics" in src
