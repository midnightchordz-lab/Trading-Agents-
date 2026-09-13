import { storage } from "@/src/utils/storage";

// A local, anonymous device identity used only to key the wallet balance.
// No auth exists in this app; this is the same lightweight pattern used
// elsewhere (e.g. the watchlist), scoped specifically to billing so it
// isn't tied to any other (possibly-absent) feature.

const KEY_WALLET_DEVICE_ID = "wallet:device_id";

export async function getWalletDeviceId(): Promise<string> {
  const existing = await storage.getItem<string>(KEY_WALLET_DEVICE_ID, "");
  if (existing) return existing;
  const id = `wdev_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  await storage.setItem(KEY_WALLET_DEVICE_ID, id);
  return id;
}
