import { Platform } from "react-native";
import { api, IapPack } from "@/src/api";

// Apple In-App Purchase, through RevenueCat.
//
// Why iOS is different: App Store Review guideline 3.1.1 requires digital
// content consumed inside the app — wallet credit that buys AI analyses — to
// be sold through Apple's own In-App Purchase. Razorpay stays for Android and
// Web, where that rule doesn't apply.
//
// `react-native-purchases` is a native module, so it does not exist in Expo Go
// or on web. It is therefore required lazily, inside try/catch: an unavailable
// module must degrade to "purchases off on this build", never crash the app on
// the screen that renders the wallet.

type PurchasesModule = typeof import("react-native-purchases").default;

let cachedModule: PurchasesModule | null | undefined;
let configuredFor: string | null = null;

function loadPurchases(): PurchasesModule | null {
  if (cachedModule !== undefined) return cachedModule;
  if (Platform.OS !== "ios") {
    cachedModule = null;
    return null;
  }
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    cachedModule = require("react-native-purchases").default as PurchasesModule;
  } catch {
    // Expo Go / a build without the native module linked.
    cachedModule = null;
  }
  return cachedModule;
}

/** True only on an iOS build that actually has StoreKit available. */
export function iapSupported(): boolean {
  return Platform.OS === "ios" && loadPurchases() != null;
}

export type IapState = { available: boolean; packs: IapPack[]; reason?: string };

/**
 * Prepares the iOS purchase path for one signed-in account. Idempotent, and
 * tied to `user:<id>` — the same key the backend's webhook credits — because
 * RevenueCat's App User ID is the ONLY thing that says whose wallet a purchase
 * belongs to. Configuring before sign-in (or with a shared id) would alias
 * accounts and mis-credit real money.
 */
export async function prepareIap(userId: string): Promise<IapState> {
  const Purchases = loadPurchases();
  if (!Purchases) {
    return {
      available: false,
      packs: [],
      reason: Platform.OS === "ios" ? "needs_native_build" : "not_ios",
    };
  }
  let config;
  try {
    config = await api.getIapConfig();
  } catch {
    return { available: false, packs: [], reason: "config_unavailable" };
  }
  if (!config.enabled || !config.ios_api_key) {
    return { available: false, packs: [], reason: "not_configured" };
  }
  const appUserID = `user:${userId}`;
  if (configuredFor !== appUserID) {
    await Purchases.configure({ apiKey: config.ios_api_key, appUserID });
    configuredFor = appUserID;
  }
  return { available: true, packs: config.packs };
}

/** Thrown-and-swallowed marker for the user tapping Cancel in the StoreKit sheet. */
export function isUserCancelled(e: unknown): boolean {
  return !!(e as { userCancelled?: boolean })?.userCancelled;
}

/**
 * Runs the StoreKit purchase. Returns when Apple has taken the payment —
 * which is NOT when the wallet grows: the balance only moves once RevenueCat's
 * signed webhook reaches our backend, so the caller must poll the wallet
 * rather than add anything optimistically.
 */
export async function buyIapPack(productId: string): Promise<void> {
  const Purchases = loadPurchases();
  if (!Purchases) throw new Error("In-app purchases aren't available on this build.");
  const products = await Purchases.getProducts([productId]);
  const product = products.find((p) => p.identifier === productId) || products[0];
  if (!product) throw new Error("This pack isn't available from the App Store right now.");
  await Purchases.purchaseStoreProduct(product);
}

/** Called on sign-out so the next account doesn't inherit the previous one's id. */
export async function resetIap(): Promise<void> {
  const Purchases = loadPurchases();
  if (!Purchases || !configuredFor) return;
  try {
    await Purchases.logOut();
  } catch {
    // Anonymous already, or never configured — nothing to undo.
  }
  configuredFor = null;
}
