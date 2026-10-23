import * as Linking from "expo-linking";

/** Opens a URL that came from the network (a news link, a payment page).
 *
 *  `Linking.openURL` will happily hand a `javascript:`, `file:` or custom app
 *  scheme to the platform, so a URL we didn't author must be checked before it
 *  is opened. Anything that isn't plain http(s) is dropped.
 */
export async function openExternalUrl(url: string | null | undefined): Promise<boolean> {
  if (!url) return false;
  if (!/^https?:\/\//i.test(url.trim())) return false;
  try {
    await Linking.openURL(url.trim());
    return true;
  } catch {
    return false;
  }
}
