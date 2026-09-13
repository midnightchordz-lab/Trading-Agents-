import React, { useState } from "react";
import { View, Text, TextInput, Pressable, ActivityIndicator, StyleSheet, Alert } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GoogleLogo } from "phosphor-react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api } from "@/src/api";
import { setStoredToken } from "@/src/auth";
import { startGoogleSignIn } from "@/src/googleAuth";
import { getWalletDeviceId } from "@/src/wallet";

// Phone/email OTP login, required before the app renders. Codes go out by
// email (Emergent managed email) or text (Twilio). Google runs through
// Emergent managed auth; Apple is still waiting on an Apple Services ID.

type Step = "identifier" | "otp";

export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
  const insets = useSafeAreaInsets();
  const [step, setStep] = useState<Step>("identifier");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [debugOtp, setDebugOtp] = useState<string | null>(null);

  const signInWithGoogle = async () => {
    setGoogleLoading(true);
    try {
      const token = await startGoogleSignIn();
      if (token) {
        await setStoredToken(token);
        onAuthenticated();
      }
    } catch (e: any) {
      Alert.alert("Google sign-in failed", e?.message || "Try again.");
    } finally {
      setGoogleLoading(false);
    }
  };

  const requestCode = async () => {
    if (!identifier.trim()) {
      Alert.alert("Enter your email address or phone number");
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
          <Text style={styles.label}>PHONE OR EMAIL</Text>
          <TextInput
            testID="login-identifier-input"
            value={identifier}
            onChangeText={setIdentifier}
            placeholder="you@example.com or +1 415 555 0134"
            placeholderTextColor={colors.onSurfaceTertiary}
            autoCapitalize="none"
            keyboardType="email-address"
            style={styles.input}
          />
          <Pressable testID="login-send-code" onPress={requestCode} disabled={loading} style={styles.primaryBtn}>
            {loading ? <ActivityIndicator color={colors.onSurfaceInverse} /> : <Text style={styles.primaryBtnText}>SEND CODE</Text>}
          </Pressable>
          <Text style={styles.hint}>
            We&apos;ll send a 6-digit code by email, or by text if you enter a number with its country code.
          </Text>
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
            <Text style={styles.linkText}>Use a different phone or email</Text>
          </Pressable>
        </View>
      )}

      <View style={styles.divider} />

      <Pressable
        testID="login-google"
        onPress={signInWithGoogle}
        disabled={googleLoading}
        style={[styles.socialBtn, styles.googleBtn]}
      >
        {googleLoading ? (
          <ActivityIndicator color={colors.onSurface} />
        ) : (
          <>
            <GoogleLogo size={16} color={colors.onSurface} weight="bold" />
            <Text style={styles.googleBtnText}>Continue with Google</Text>
          </>
        )}
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
  googleBtn: {
    flexDirection: "row",
    justifyContent: "center",
    gap: spacing.sm,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    minHeight: 48,
  },
  googleBtnText: { fontFamily: fonts.monoBold, fontSize: 12.5, color: colors.onSurface },
});
