import React, { useState } from "react";
import { View, Text, TextInput, Pressable, ActivityIndicator, StyleSheet, Alert } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api } from "@/src/api";
import { setStoredToken } from "@/src/auth";
import { getWalletDeviceId } from "@/src/wallet";

// Phone/email OTP login, required before the app renders. Google/Apple
// buttons are shown but disabled — real sign-in needs a Google OAuth
// client ID and Apple Services ID configured server-side first (see the
// integration notes in the spec). Nothing here fakes that working.

type Step = "identifier" | "otp";

export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
  const insets = useSafeAreaInsets();
  const [step, setStep] = useState<Step>("identifier");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [debugOtp, setDebugOtp] = useState<string | null>(null);

  const requestCode = async () => {
    if (!identifier.trim()) {
      Alert.alert("Enter your email address");
      return;
    }
    setLoading(true);
    try {
      const deviceId = await getWalletDeviceId();
      const res = await api.requestOtp(identifier.trim(), deviceId);
      setDebugOtp(res.debug_otp || null);
      setStep("otp");
    } catch (e: any) {
      Alert.alert("Couldn't send code", e?.message || "Try again.");
    } finally {
      setLoading(false);
    }
  };

  const verifyCode = async () => {
    if (otp.trim().length < 4) {
      Alert.alert("Enter the code sent to you");
      return;
    }
    setLoading(true);
    try {
      const deviceId = await getWalletDeviceId();
      const res = await api.verifyOtp(identifier.trim(), otp.trim(), deviceId);
      await setStoredToken(res.token);
      onAuthenticated();
    } catch (e: any) {
      Alert.alert("Couldn't verify code", e?.message || "Check the code and try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={[styles.screen, { paddingTop: insets.top + spacing.xl }]}>
      <Text style={styles.title}>TRADINGAGENTS</Text>
      <Text style={styles.subtitle}>Sign in to continue</Text>

      {step === "identifier" ? (
        <View style={styles.card}>
          <Text style={styles.label}>EMAIL ADDRESS</Text>
          <TextInput
            testID="login-identifier-input"
            value={identifier}
            onChangeText={setIdentifier}
            placeholder="you@example.com"
            placeholderTextColor={colors.onSurfaceTertiary}
            autoCapitalize="none"
            keyboardType="email-address"
            style={styles.input}
          />
          <Pressable testID="login-send-code" onPress={requestCode} disabled={loading} style={styles.primaryBtn}>
            {loading ? <ActivityIndicator color={colors.onSurfaceInverse} /> : <Text style={styles.primaryBtnText}>SEND CODE</Text>}
          </Pressable>
          <Text style={styles.hint}>We&apos;ll email you a 6-digit code. Phone sign-in is coming soon.</Text>
        </View>
      ) : (
        <View style={styles.card}>
          <Text style={styles.label}>ENTER CODE SENT TO {identifier}</Text>
          <TextInput
            testID="login-otp-input"
            value={otp}
            onChangeText={setOtp}
            placeholder="6-digit code"
            placeholderTextColor={colors.onSurfaceTertiary}
            keyboardType="number-pad"
            maxLength={6}
            style={styles.input}
          />
          {debugOtp ? <Text style={styles.debugNote}>DEV MODE — code: {debugOtp} (remove before real launch)</Text> : null}
          <Pressable testID="login-verify-code" onPress={verifyCode} disabled={loading} style={styles.primaryBtn}>
            {loading ? <ActivityIndicator color={colors.onSurfaceInverse} /> : <Text style={styles.primaryBtnText}>VERIFY</Text>}
          </Pressable>
          <Pressable onPress={() => setStep("identifier")} hitSlop={8}>
            <Text style={styles.linkText}>Use a different email</Text>
          </Pressable>
        </View>
      )}

      <View style={styles.divider} />

      <Pressable disabled style={[styles.socialBtn, styles.socialBtnDisabled]}>
        <Text style={styles.socialBtnText}>Continue with Google — coming soon</Text>
      </Pressable>
      <Pressable disabled style={[styles.socialBtn, styles.socialBtnDisabled]}>
        <Text style={styles.socialBtnText}>Continue with Apple — coming soon</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: spacing.lg },
  title: { fontFamily: fonts.display, fontSize: 22, color: colors.onSurface, letterSpacing: 1 },
  subtitle: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary, marginTop: 4, marginBottom: spacing.xl },
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, padding: spacing.lg },
  label: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5, color: colors.onSurfaceTertiary, marginBottom: spacing.sm },
  input: {
    fontFamily: fonts.mono,
    fontSize: 14,
    color: colors.onSurface,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    marginBottom: spacing.md,
  },
  debugNote: { fontFamily: fonts.mono, fontSize: 10, color: colors.warning, marginBottom: spacing.md },
  hint: { fontFamily: fonts.mono, fontSize: 10, lineHeight: 15, color: colors.onSurfaceTertiary, marginTop: spacing.xs },
  primaryBtn: { backgroundColor: colors.brand, paddingVertical: spacing.md, alignItems: "center", marginBottom: spacing.sm },
  primaryBtnText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurfaceInverse },
  linkText: { fontFamily: fonts.mono, fontSize: 11, color: colors.brand, textAlign: "center", marginTop: spacing.xs },
  divider: { height: 1, backgroundColor: colors.border, marginVertical: spacing.xl },
  socialBtn: { borderWidth: 1, borderColor: colors.border, paddingVertical: spacing.md, alignItems: "center", marginBottom: spacing.sm },
  socialBtnDisabled: { opacity: 0.5 },
  socialBtnText: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary },
});
