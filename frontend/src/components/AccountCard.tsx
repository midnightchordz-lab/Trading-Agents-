import React, { useState } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { SignOut } from "phosphor-react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { useAuth } from "@/src/auth";

export function AccountCard() {
  const { user, signOut } = useAuth();
  const [confirming, setConfirming] = useState(false);
  if (!user) return null;

  const identity = user.email || user.phone || "Signed in";

  // Inline two-tap confirm instead of Alert.alert, which is a no-op on web.
  const onPress = () => {
    if (confirming) {
      signOut();
      return;
    }
    setConfirming(true);
    setTimeout(() => setConfirming(false), 4000);
  };

  return (
    <View testID="account-card" style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>ACCOUNT</Text>
      </View>
      <View style={styles.body}>
        <View style={{ flex: 1 }}>
          <Text style={styles.identityLabel}>SIGNED IN AS</Text>
          <Text testID="account-identity" style={styles.identity} numberOfLines={1}>
            {identity}
          </Text>
        </View>
        <Pressable testID="sign-out-button" onPress={onPress} style={styles.signOutBtn} hitSlop={8}>
          <SignOut size={15} color={colors.onError} weight="bold" />
          <Text style={styles.signOutText}>{confirming ? "TAP AGAIN" : "SIGN OUT"}</Text>
        </Pressable>
      </View>
      {confirming ? <Text style={styles.confirmNote}>Tap again to sign out — you&apos;ll need a new code to get back in.</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, marginTop: spacing.md },
  headerRow: {
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  label: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurfaceInverse },
  body: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.md },
  identityLabel: { fontFamily: fonts.mono, fontSize: 9, letterSpacing: 0.8, color: colors.onSurfaceTertiary },
  identity: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface, marginTop: 2 },
  signOutBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    backgroundColor: colors.error,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    minHeight: 44,
  },
  signOutText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 0.8, color: colors.onError },
  confirmNote: {
    fontFamily: fonts.mono,
    fontSize: 9.5,
    color: colors.onSurfaceTertiary,
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.md,
  },
});
