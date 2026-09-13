import { Platform } from "react-native";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import { api } from "@/src/api";
import { getWalletDeviceId } from "@/src/wallet";

// Emergent managed Google sign-in. The frontend only ever handles the
// one-time `session_id` from the redirect and hands it to our own backend,
// which exchanges it for one of our session tokens.
const AUTH_URL = "https://auth.emergentagent.com/";

WebBrowser.maybeCompleteAuthSession();

const consumed = new Set<string>();

function redirectUrl(): string {
  if (Platform.OS === "web") return window.location.origin + "/";
  return Linking.createURL("");
}

/** session_id can arrive in the hash OR the query string. */
function extractSessionId(url?: string | null): string | null {
  if (!url) return null;
  const m = url.match(/[?#&]session_id=([^&#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Exchanges a session_id for our own token. Returns the token, or null. */
async function exchange(sessionId: string): Promise<string | null> {
  if (consumed.has(sessionId)) return null;
  consumed.add(sessionId);
  const res = await api.googleSession(sessionId, await getWalletDeviceId());
  return res.token;
}

/**
 * Picks up a Google redirect that already happened (web page load, or a cold
 * deep link on native). Returns our session token if one was completed.
 */
export async function consumeGoogleRedirect(): Promise<string | null> {
  let sessionId: string | null = null;
  if (Platform.OS === "web") {
    sessionId = extractSessionId(window.location.hash) || extractSessionId(window.location.search);
  } else {
    sessionId = extractSessionId(await Linking.getInitialURL());
  }
  if (!sessionId) return null;
  const token = await exchange(sessionId);
  if (token && Platform.OS === "web") {
    // Strip only session_id, keep everything else on the URL.
    const clean = window.location.href.replace(/([?#&])session_id=[^&#]*/, "$1").replace(/[?#&]$/, "");
    window.history.replaceState(window.history.state, "", clean);
  }
  return token;
}

/**
 * Starts the Google flow. On web this navigates away and never resolves with
 * a token — the redirect is picked up by consumeGoogleRedirect() on reload.
 */
export async function startGoogleSignIn(): Promise<string | null> {
  const redirect = redirectUrl();
  const authUrl = `${AUTH_URL}?redirect=${encodeURIComponent(redirect)}`;

  if (Platform.OS === "web") {
    window.location.href = authUrl;
    return null;
  }

  // Native: the deep link can come back via the result, the url listener, or
  // a cold start — Android often reports "dismiss" on a successful login.
  let fromListener: string | null = null;
  const sub = Linking.addEventListener("url", (e) => {
    fromListener = e.url;
  });
  try {
    const result = await WebBrowser.openAuthSessionAsync(authUrl, redirect);
    const url =
      (result as any)?.url || fromListener || (await Linking.getInitialURL()) || null;
    const sessionId = extractSessionId(url);
    if (!sessionId) return null;
    return await exchange(sessionId);
  } finally {
    sub.remove();
  }
}
