import React, { useState } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { useTranslation } from "react-i18next";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { SUPPORTED_LANGUAGES, setLanguage, getCurrentLanguage, LanguageCode } from "@/src/i18n";

export function LanguagePicker() {
  const { t } = useTranslation();
  const [current, setCurrent] = useState<LanguageCode>(getCurrentLanguage());
  const [saving, setSaving] = useState<LanguageCode | null>(null);

  const pick = async (code: LanguageCode) => {
    if (code === current) return;
    setSaving(code);
    try {
      await setLanguage(code);
      setCurrent(code);
    } finally {
      setSaving(null);
    }
  };

  return (
    <View testID="language-picker" style={styles.card}>
      <Text style={styles.title}>{t("settings.language")}</Text>
      <Text style={styles.hint}>{t("settings.language_hint")}</Text>
      <View style={styles.list}>
        {SUPPORTED_LANGUAGES.map((l) => {
          const active = l.code === current;
          return (
            <Pressable
              key={l.code}
              testID={`language-option-${l.code}`}
              onPress={() => pick(l.code)}
              style={[styles.row, active && styles.rowActive]}
            >
              <Text style={[styles.native, active && styles.nativeActive]}>{l.nativeLabel}</Text>
              <Text style={[styles.english, active && styles.englishActive]}>{l.label}</Text>
              {active ? <Text style={styles.check}>✓</Text> : null}
              {saving === l.code ? <Text style={styles.check}>…</Text> : null}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, marginTop: spacing.xl },
  title: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    letterSpacing: 1,
    color: colors.onSurfaceInverse,
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  hint: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, padding: spacing.md, paddingBottom: 0 },
  list: { padding: spacing.md, gap: spacing.sm },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.sm,
  },
  rowActive: { borderColor: colors.brand, backgroundColor: colors.surfaceSecondary },
  native: { fontFamily: fonts.displayMed, fontSize: 14, color: colors.onSurface },
  nativeActive: { color: colors.brand },
  english: { flex: 1, fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary },
  englishActive: { color: colors.onSurface },
  check: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.brand },
});
