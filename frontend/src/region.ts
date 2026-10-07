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
