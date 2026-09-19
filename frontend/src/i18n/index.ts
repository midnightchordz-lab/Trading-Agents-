import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import * as Localization from "expo-localization";
import { I18nManager } from "react-native";
import { storage } from "@/src/utils/storage";

import en from "@/src/i18n/locales/en.json";
import hi from "@/src/i18n/locales/hi.json";
import es from "@/src/i18n/locales/es.json";
import zh from "@/src/i18n/locales/zh.json";
import ar from "@/src/i18n/locales/ar.json";

export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "English", nativeLabel: "English" },
  { code: "hi", label: "Hindi", nativeLabel: "हिन्दी" },
  { code: "es", label: "Spanish", nativeLabel: "Español" },
  { code: "zh", label: "Mandarin Chinese", nativeLabel: "中文" },
  { code: "ar", label: "Arabic", nativeLabel: "العربية" },
] as const;

/** Languages written right-to-left. Driving layout direction off this list —
 *  rather than off a flag per screen — is what keeps a new RTL language from
 *  needing UI changes. */
export const RTL_LANGUAGES: string[] = ["ar"];

export function isRtlLanguage(code: string): boolean {
  return RTL_LANGUAGES.includes(code);
}

/** Sets the layout direction for a language.
 *
 *  React Native fixes the direction when the view hierarchy is created, so a
 *  change only takes visual effect on the next launch. Returns true when that
 *  restart is needed, so the caller can say so instead of leaving the user
 *  looking at an unchanged screen. */
export function applyLayoutDirection(code: LanguageCode): boolean {
  const wantRtl = isRtlLanguage(code);
  I18nManager.allowRTL(wantRtl);
  if (I18nManager.isRTL === wantRtl) return false;
  I18nManager.forceRTL(wantRtl);
  return true;
}

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];

const KEY_LANGUAGE = "settings:language";

function deviceDefaultLanguage(): LanguageCode {
  const tag = Localization.getLocales()[0]?.languageCode || "en";
  const supported = SUPPORTED_LANGUAGES.map((l) => l.code) as string[];
  return (supported.includes(tag) ? tag : "en") as LanguageCode;
}

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    hi: { translation: hi },
    es: { translation: es },
    zh: { translation: zh },
    ar: { translation: ar },
  },
  lng: "en", // overwritten by initLanguage() below once storage/device locale is read
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  compatibilityJSON: "v4",
});

/** Call once at app startup. Resolves stored preference, else device locale, else English. */
export async function initLanguage(): Promise<void> {
  const stored = await storage.getItem<string>(KEY_LANGUAGE, "");
  const lang = stored || deviceDefaultLanguage();
  // Direction is applied before anything renders; at this point it already
  // matches what the last launch persisted, so nothing flips mid-session.
  applyLayoutDirection(lang);
  await i18n.changeLanguage(lang);
}

/** Returns true when the app has to be relaunched for the layout direction
 *  (LTR <-> RTL) to take effect. Text changes immediately either way. */
export async function setLanguage(code: LanguageCode): Promise<boolean> {
  await storage.setItem(KEY_LANGUAGE, code);
  const needsRestart = applyLayoutDirection(code);
  await i18n.changeLanguage(code);
  return needsRestart;
}

export function getCurrentLanguage(): LanguageCode {
  return (i18n.language as LanguageCode) || "en";
}

export default i18n;
