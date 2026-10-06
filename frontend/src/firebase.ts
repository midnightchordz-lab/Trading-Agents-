// Crashlytics + Analytics, via React Native Firebase. Everything here is a
// safe no-op when Firebase isn't there: Expo Go (no native module), a build
// made before google-services.json / GoogleService-Info.plist were added
// (module linked, but no default app), or web (see firebase.web.ts).
//
// Privacy rules, matching what the consent screen promises:
// - Analytics stays OFF (firebase.json) until the account has given consent;
//   `setTelemetryUser` switches it on, and sign-out switches it back off.
// - No advertising ID: collection is disabled in firebase.json, the Android
//   AD_ID permission is blocked in app.json, and iOS is built without AdSupport.
// - Never send a ticker or anything that identifies a person in an event —
//   the consent screen says searched tickers are never linked to an identity.
//   Only the internal account id is attached, and only to crash reports.

type Mods = {
  analytics: typeof import("@react-native-firebase/analytics");
  crashlytics: typeof import("@react-native-firebase/crashlytics");
};

let cached: Mods | null | undefined;

function load(): Mods | null {
  if (cached !== undefined) return cached;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const app = require("@react-native-firebase/app") as typeof import("@react-native-firebase/app");
    if (app.getApps().length === 0) {
      cached = null; // native module present, but no config file in this build
    } else {
      cached = {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        analytics: require("@react-native-firebase/analytics"),
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        crashlytics: require("@react-native-firebase/crashlytics"),
      };
    }
  } catch {
    cached = null; // Expo Go / a build without the native module linked
  }
  return cached;
}

function safely(fn: (m: Mods) => unknown): void {
  const m = load();
  if (!m) return;
  try {
    const r = fn(m);
    if (r && typeof (r as Promise<unknown>).catch === "function") {
      (r as Promise<unknown>).catch(() => {});
    }
  } catch {
    // Telemetry must never break the app.
  }
}

/** Called once the session is known. Before consent (or signed out), crash
 *  reports stay anonymous and Analytics stays off. */
export function setTelemetryUser(userId: string | null, consented: boolean): void {
  const on = !!userId && consented;
  safely(({ analytics, crashlytics }) => {
    crashlytics.setUserId(crashlytics.getCrashlytics(), on && userId ? userId : "");
    analytics.setAnalyticsCollectionEnabled(analytics.getAnalytics(), on);
  });
}

export function trackScreen(pathname: string): void {
  // Route templates only (e.g. /analysis/[id] arrives as the concrete path,
  // so collapse ids) — keeps ids and symbols out of Analytics.
  const name = pathname.replace(/^\/analysis\/.+$/, "/analysis/[id]") || "/";
  safely(({ analytics }) =>
    analytics.logScreenView(analytics.getAnalytics(), { screen_name: name, screen_class: name })
  );
}

type EventName = "analysis_started" | "compare_started" | "portfolio_optimized" | "portfolio_imported" | "wallet_topup";

export function trackEvent(name: EventName, params?: Record<string, string | number>): void {
  safely(({ analytics }) => analytics.logEvent(analytics.getAnalytics(), name, params));
}
