import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import * as Localization from "expo-localization";
import { storage } from "@/src/utils/storage";

import en from "@/src/i18n/locales/en.json";
import hi from "@/src/i18n/locales/hi.json";
import es from "@/src/i18n/locales/es.json";
import zh from "@/src/i18n/locales/zh.json";

export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "English", nativeLabel: "English" },
  { code: "hi", label: "Hindi", nativeLabel: "हिन्दी" },
  { code: "es", label: "Spanish", nativeLabel: "Español" },
  { code: "zh", label: "Mandarin Chinese", nativeLabel: "中文" },
] as const;

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
  await i18n.changeLanguage(lang);
}

export async function setLanguage(code: LanguageCode): Promise<void> {
  await storage.setItem(KEY_LANGUAGE, code);
  await i18n.changeLanguage(code);
}

export function getCurrentLanguage(): LanguageCode {
  return (i18n.language as LanguageCode) || "en";
}

export default i18n;
