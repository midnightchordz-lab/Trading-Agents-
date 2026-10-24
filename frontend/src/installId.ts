import { Platform } from "react-native";
import * as Application from "expo-application";
import * as SecureStore from "expo-secure-store";

// A device identity that survives what the wallet's random device id does
// not: reinstalling the app or clearing its data. The backend uses it for one
// thing only — giving each phone ONE set of free runs, however many accounts
// are made on it. It is sent as the X-Install-Id header and stored only as a
// keyed hash on the server.
//
// Android: the system's ANDROID_ID, fixed per phone and app signing key; it
// changes only on a factory reset.
// iOS: a random id kept in the Keychain, which iOS keeps across a reinstall
// (THIS_DEVICE_ONLY: never synced to the user's other devices).
// Web: none. The backend treats a missing id as "not yet known" and holds the
// free runs until the account signs in from the phone app.

const KEYCHAIN_KEY = "install_id";

let cached: Promise<string | null> | null = null;

async function readInstallId(): Promise<string | null> {
  try {
    if (Platform.OS === "android") {
      const id = Application.getAndroidId();
      return id ? `android:${id}` : null;
    }
    if (Platform.OS === "ios") {
      const opts = { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY };
      let id = await SecureStore.getItemAsync(KEYCHAIN_KEY, opts);
      if (!id) {
        id = (await Application.getIosIdForVendorAsync()) || `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
        await SecureStore.setItemAsync(KEYCHAIN_KEY, id, opts);
      }
      return `ios:${id}`;
    }
  } catch {}
  return null;
}

export function getInstallId(): Promise<string | null> {
  if (!cached) cached = readInstallId();
  return cached;
}
