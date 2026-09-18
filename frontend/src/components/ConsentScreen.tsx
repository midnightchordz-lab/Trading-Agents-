import React, { useState } from "react";
import { View, Text, Pressable, StyleSheet, ScrollView, Linking, ActivityIndicator, Alert } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Check } from "phosphor-react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api } from "@/src/api";

// Shown once, immediately after sign-in succeeds, before the app allows
// anything else. India's DPDP Act requires informed, specific, AFFIRMATIVE
// consent presented alongside the data request itself — not implied by
// continued use — so this is a real gate, not a dismissible notice. The
// backend independently rejects `agreed: false` rather than storing it, and
// returns 403 consent_required on /analyze until real consent exists.

const PRIVACY_URL = "https://tradingagents.in/privacy.html";
const TERMS_URL = "https://tradingagents.in/terms.html";

type Props = { onAgreed: () => void };

function Section({ title, lines }: { title: string; lines: string[] }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {lines.map((line) => (
        <View key={line} style={styles.bulletRow}>
          <Text style={styles.bulletDot}>{"\u2022"}</Text>
          <Text style={styles.bulletText}>{line}</Text>
        </View>
      ))}
    </View>
  );
}

export function ConsentScreen({ onAgreed }: Props) {
  const insets = useSafeAreaInsets();
  // Starts unchecked, always. Belt and braces with the backend's own
  // affirmative-only rule — never pre-checked, under any circumstance.
  const [checked, setChecked] = useState(false);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!checked || saving) return;
    setSaving(true);
    try {
      await api.recordConsent();
      onAgreed();
    } catch (e: unknown) {
      Alert.alert("Couldn't save that", (e as Error)?.message || "Check your connection and try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={[styles.screen, { paddingTop: insets.top + spacing.lg }]}>
      <Text style={styles.heading}>BEFORE YOU CONTINUE</Text>
      <Text style={styles.intro}>
        To create your account and let you use TradingAgents, we need your permission to handle some information
        about you. Here&apos;s exactly what that means, in plain terms:
      </Text>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollInner} showsVerticalScrollIndicator>
        <Section
          title="WHAT WE COLLECT"
          lines={[
            "Your phone number or email address, so you can sign in.",
            "If you use Google sign-in: your name, email, and profile picture.",
            "Your wallet balance and free-credit count, so we know what you've paid for and what's still free.",
          ]}
        />
        <Section
          title="WHY WE COLLECT IT"
          lines={[
            "To create and secure your account.",
            "To send you a one-time sign-in code by text or email.",
            "To process wallet top-ups if you choose to buy more analyses.",
            "To keep your free-credit count accurate.",
          ]}
        />
        <Section
          title="WHAT WE DON'T DO"
          lines={[
            "We don't link the stocks, crypto, or other tickers you search to your identity anywhere in our systems.",
            "We don't track your location.",
            "We don't sell your data, and we don't use it for advertising.",
          ]}
        />
        <Section
          title="WHO ELSE SEES IT"
          lines={[
            "Twilio, to deliver text-message sign-in codes.",
            "Our email provider, to deliver email sign-in codes and account emails.",
            "Razorpay, to process payments if you top up your wallet — they handle your actual card or bank details directly; we never see that information ourselves.",
            "Google, only if you choose to sign in with Google.",
          ]}
        />
        <Section
          title="YOUR CHOICES"
          lines={[
            "You can withdraw this consent at any time by deleting your account, in Settings. Deleting your account removes your account record and wallet balance immediately.",
          ]}
        />
        <View style={styles.linkRow}>
          <Pressable testID="consent-privacy-link" onPress={() => Linking.openURL(PRIVACY_URL)} hitSlop={8}>
            <Text style={styles.link}>Privacy Policy</Text>
          </Pressable>
          <Text style={styles.linkSep}>·</Text>
          <Pressable testID="consent-terms-link" onPress={() => Linking.openURL(TERMS_URL)} hitSlop={8}>
            <Text style={styles.link}>Terms of Service</Text>
          </Pressable>
        </View>
      </ScrollView>

      <Pressable
        testID="consent-checkbox"
        accessibilityRole="checkbox"
        accessibilityState={{ checked }}
        onPress={() => setChecked((v) => !v)}
        style={styles.checkRow}
        hitSlop={8}
      >
        <View style={[styles.box, checked && styles.boxChecked]}>
          {checked ? <Check size={14} color={colors.onSuccess} weight="bold" /> : null}
        </View>
        <Text style={styles.checkLabel}>
          I have read this and I agree to TradingAgents collecting and using my information as described above.
        </Text>
      </Pressable>

      <Pressable
        testID="consent-continue"
        onPress={submit}
        disabled={!checked || saving}
        style={[styles.cta, (!checked || saving) && styles.ctaDisabled, { marginBottom: insets.bottom + spacing.lg }]}
      >
        {saving ? (
          <ActivityIndicator size="small" color={colors.onSurfaceInverse} />
        ) : (
          <Text style={styles.ctaText}>CONTINUE</Text>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: spacing.lg },
  heading: {
    fontFamily: fonts.monoBold,
    fontSize: 13,
    letterSpacing: 1.2,
    color: colors.onSurface,
    marginBottom: spacing.sm,
  },
  intro: {
    fontFamily: fonts.displayReg,
    fontSize: 14,
    lineHeight: 20,
    color: colors.onSurfaceTertiary,
    marginBottom: spacing.md,
  },
  scroll: { flex: 1, borderTopWidth: BORDER, borderTopColor: colors.borderStrong },
  scrollInner: { paddingVertical: spacing.md },
  section: { marginBottom: spacing.md },
  sectionTitle: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    letterSpacing: 1,
    color: colors.onSurfaceTertiary,
    marginBottom: spacing.xs,
  },
  bulletRow: { flexDirection: "row", marginBottom: spacing.xs, paddingRight: spacing.sm },
  bulletDot: { fontFamily: fonts.displayReg, fontSize: 13, color: colors.onSurfaceTertiary, marginRight: 6 },
  bulletText: { flex: 1, fontFamily: fonts.displayReg, fontSize: 13, lineHeight: 19, color: colors.onSurface },
  linkRow: { flexDirection: "row", alignItems: "center", marginTop: spacing.xs, minHeight: 44 },
  link: { fontFamily: fonts.mono, fontSize: 11, color: colors.brand, textDecorationLine: "underline" },
  linkSep: {
    fontFamily: fonts.mono,
    fontSize: 11,
    color: colors.onSurfaceTertiary,
    marginHorizontal: spacing.sm,
  },
  checkRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
    paddingVertical: spacing.md,
    borderTopWidth: BORDER,
    borderTopColor: colors.borderStrong,
  },
  box: {
    width: 24,
    height: 24,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 1,
  },
  boxChecked: { backgroundColor: colors.success, borderColor: colors.success },
  checkLabel: { flex: 1, fontFamily: fonts.displayReg, fontSize: 13, lineHeight: 19, color: colors.onSurface },
  cta: {
    minHeight: 52,
    backgroundColor: colors.surfaceInverse,
    alignItems: "center",
    justifyContent: "center",
  },
  ctaDisabled: { backgroundColor: colors.surfaceTertiary },
  ctaText: { fontFamily: fonts.monoBold, fontSize: 13, letterSpacing: 1.5, color: colors.onSurfaceInverse },
});
