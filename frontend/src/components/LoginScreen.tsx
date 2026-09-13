import React, { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, Pressable, ActivityIndicator, StyleSheet, Alert, Animated } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GoogleLogo } from "phosphor-react-native";
import { fonts, spacing } from "@/src/theme";
import { api } from "@/src/api";
import { setStoredToken } from "@/src/auth";
import { startGoogleSignIn } from "@/src/googleAuth";
import { getWalletDeviceId } from "@/src/wallet";

// Futuristic "agent terminal" look — scoped to the login screen only, per
// product decision. Dark panel, acid-green accent, monospace throughout,
// HUD-style corner brackets. Same OTP login logic as before; visuals only.
// Google sign-in stays live (Emergent managed auth); Apple is still pending
// an Apple Services ID.

const LIME = "#AEFA3C";
const PANEL_BG = "#0B1220";
const FIELD_BG = "#131A0F";
const LINE = "#24301C";
const TEXT_BRIGHT = "#EEF7E0";
const TEXT_DIM = "#66755A";
const TEXT_MID = "#CBD8BC";

type Step = "identifier" | "otp";

function PulseDot() {
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.25, duration: 800, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 1, duration: 800, useNativeDriver: true }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return <Animated.View style={[styles.pulseDot, { opacity }]} />;
}

function CornerBracket({ position }: { position: "tl" | "tr" | "bl" | "br" }) {
  const style = [
    styles.corner,
    position === "tl" && { top: 10, left: 10, borderTopWidth: 1.5, borderLeftWidth: 1.5 },
    position === "tr" && { top: 10, right: 10, borderTopWidth: 1.5, borderRightWidth: 1.5 },
    position === "bl" && { bottom: 10, left: 10, borderBottomWidth: 1.5, borderLeftWidth: 1.5 },
    position === "br" && { bottom: 10, right: 10, borderBottomWidth: 1.5, borderRightWidth: 1.5 },
  ];
  return <View style={style} />;
}

export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
  const insets = useSafeAreaInsets();
  const [step, setStep] = useState<Step>("identifier");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [debugOtp, setDebugOtp] = useState<string | null>(null);

  const requestCode = async () => {
    if (!identifier.trim()) {
      Alert.alert("Enter a phone number or email");
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

  return (
    <View style={[styles.screen, { paddingTop: insets.top + spacing.md, paddingBottom: insets.bottom + spacing.md }]}>
      <View style={styles.panel}>
        <CornerBracket position="tl" />
        <CornerBracket position="tr" />
        <CornerBracket position="bl" />
        <CornerBracket position="br" />

        <View style={styles.header}>
          <View style={styles.statusRow}>
            <PulseDot />
            <Text style={styles.statusText}>AGENT NETWORK ONLINE</Text>
          </View>
          <Text style={styles.title}>TRADINGAGENTS</Text>
          <Text style={styles.subtitle}>{"// IDENTITY VERIFICATION REQUIRED"}</Text>
        </View>

        {step === "identifier" ? (
          <>
            <View style={styles.fieldBlock}>
              <Text style={styles.fieldLabel}>&gt; PHONE OR EMAIL</Text>
              <TextInput
                testID="login-identifier-input"
                value={identifier}
                onChangeText={setIdentifier}
                placeholder="you@example.com"
                placeholderTextColor={TEXT_DIM}
                autoCapitalize="none"
                keyboardType="email-address"
                style={styles.input}
              />
            </View>
            <Pressable testID="login-send-code" onPress={requestCode} disabled={loading} style={styles.primaryBtn}>
              {loading ? <ActivityIndicator color={PANEL_BG} /> : <Text style={styles.primaryBtnText}>TRANSMIT CODE →</Text>}
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.otpLabel}>&gt; ENTER 6-DIGIT KEY SENT TO {identifier}</Text>
            <TextInput
              testID="login-otp-input"
              value={otp}
              onChangeText={setOtp}
              placeholder="------"
              placeholderTextColor={TEXT_DIM}
              keyboardType="number-pad"
              maxLength={6}
              style={[styles.input, styles.otpInput]}
            />
            {debugOtp ? <Text style={styles.debugNote}>DEV MODE — code: {debugOtp} (remove before real launch)</Text> : null}
            <Pressable testID="login-verify-code" onPress={verifyCode} disabled={loading} style={styles.primaryBtn}>
              {loading ? <ActivityIndicator color={PANEL_BG} /> : <Text style={styles.primaryBtnText}>VERIFY →</Text>}
            </Pressable>
            <Pressable onPress={() => setStep("identifier")} hitSlop={8}>
              <Text style={styles.linkText}>&lt; USE A DIFFERENT PHONE OR EMAIL</Text>
            </Pressable>
          </>
        )}

        <View style={styles.dividerRow}>
          <View style={styles.dividerLine} />
          <Text style={styles.dividerText}>ALT ACCESS</Text>
          <View style={styles.dividerLine} />
        </View>

        <View style={styles.socialRow}>
          <Pressable
            testID="login-google"
            onPress={signInWithGoogle}
            disabled={googleLoading}
            style={[styles.socialBtn, styles.googleBtn]}
          >
            {googleLoading ? (
              <ActivityIndicator color={LIME} />
            ) : (
              <>
                <GoogleLogo size={13} color={LIME} weight="bold" />
                <Text style={styles.googleBtnText}>GOOGLE</Text>
              </>
            )}
          </Pressable>
          <Pressable disabled style={styles.socialBtn}>
            <Text style={styles.socialBtnText}>APPLE — SOON</Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#05070B", paddingHorizontal: spacing.lg, justifyContent: "center" },
  panel: { backgroundColor: PANEL_BG, borderRadius: 16, padding: spacing.xl, position: "relative", overflow: "hidden" },
  corner: { position: "absolute", width: 16, height: 16, borderColor: LIME },
  header: { alignItems: "center", marginBottom: spacing.xl },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: spacing.sm },
  pulseDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: LIME },
  statusText: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 1.5, color: LIME },
  title: { fontFamily: fonts.monoBold, fontSize: 20, letterSpacing: 0.5, color: TEXT_BRIGHT },
  subtitle: { fontFamily: fonts.mono, fontSize: 11, color: TEXT_DIM, marginTop: 4 },
  fieldBlock: { borderWidth: 0.5, borderColor: LINE, borderRadius: 10, padding: spacing.md, marginBottom: spacing.md },
  fieldLabel: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 1, color: LIME, marginBottom: spacing.sm },
  input: {
    backgroundColor: FIELD_BG,
    borderWidth: 0.5,
    borderColor: LINE,
    borderRadius: 6,
    color: TEXT_BRIGHT,
    fontFamily: fonts.mono,
    fontSize: 13,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
  },
  otpLabel: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 1, color: TEXT_DIM, textAlign: "center", marginBottom: spacing.sm },
  otpInput: { textAlign: "center", letterSpacing: 6, fontSize: 18, marginBottom: spacing.sm },
  debugNote: { fontFamily: fonts.mono, fontSize: 10, color: LIME, marginBottom: spacing.md },
  primaryBtn: {
    backgroundColor: LIME,
    borderRadius: 8,
    paddingVertical: spacing.md,
    alignItems: "center",
    marginBottom: spacing.md,
  },
  primaryBtnText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: PANEL_BG },
  linkText: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 0.5, color: TEXT_MID, textAlign: "center", marginTop: spacing.xs },
  dividerRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginVertical: spacing.lg },
  dividerLine: { flex: 1, height: 0.5, backgroundColor: LINE },
  dividerText: { fontFamily: fonts.mono, fontSize: 9, letterSpacing: 1, color: "#4A5540" },
  socialRow: { flexDirection: "row", gap: spacing.sm },
  socialBtn: {
    flex: 1,
    borderWidth: 0.5,
    borderColor: LINE,
    borderRadius: 8,
    paddingVertical: spacing.sm,
    alignItems: "center",
    minHeight: 44,
    justifyContent: "center",
  },
  socialBtnText: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 0.5, color: TEXT_MID },
  googleBtn: { flexDirection: "row", gap: 6, borderColor: LIME },
  googleBtnText: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5, color: LIME },
});
