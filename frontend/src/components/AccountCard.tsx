import React, { useState } from "react";
import { View, Text, Pressable, StyleSheet, Linking, ActivityIndicator } from "react-native";
import { SignOut, Trash } from "phosphor-react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { useAuth } from "@/src/auth";
import { api } from "@/src/api";

const PRIVACY_URL = "https://tradingagents.in/privacy.html";

export function AccountCard() {
  const { user, signOut } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
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

  const onDelete = async () => {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      setDeleteError(null);
      setTimeout(() => setConfirmingDelete(false), 5000);
      return;
    }
    setDeleting(true);
    try {
      await api.deleteAccount();
      // The account is gone — the stored token is dead server-side too.
      await signOut();
    } catch (e: any) {
      setDeleteError(e?.message || "Couldn't delete the account. Try again.");
    } finally {
      setDeleting(false);
      setConfirmingDelete(false);
    }
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

      <View style={styles.footerRow}>
        <Pressable
          testID="privacy-policy-link"
          onPress={() => Linking.openURL(PRIVACY_URL)}
          style={styles.privacyLink}
          hitSlop={8}
        >
          <Text style={styles.privacyLinkText}>Privacy Policy</Text>
        </Pressable>
        <Pressable
          testID="delete-account-button"
          onPress={onDelete}
          disabled={deleting}
          style={styles.deleteBtn}
          hitSlop={8}
        >
          {deleting ? (
            <ActivityIndicator size="small" color={colors.error} />
          ) : (
            <>
              <Trash size={13} color={colors.error} weight="bold" />
              <Text style={styles.deleteText}>
                {confirmingDelete ? "TAP AGAIN TO DELETE" : "DELETE ACCOUNT"}
              </Text>
            </>
          )}
        </Pressable>
      </View>
      {confirmingDelete && !deleting ? (
        <Text style={styles.confirmNote}>
          This permanently deletes your account and wallet balance. Paid receipts are kept for our records.
        </Text>
      ) : null}
      {deleteError ? (
        <Text testID="delete-account-error" style={styles.deleteError}>
          {deleteError}
        </Text>
      ) : null}
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
  footerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingHorizontal: spacing.md,
  },
  privacyLink: { paddingVertical: spacing.sm, alignItems: "center", minHeight: 44, justifyContent: "center" },
  privacyLinkText: {
    fontFamily: fonts.mono,
    fontSize: 11,
    color: colors.onSurfaceTertiary,
    textDecorationLine: "underline",
  },
  deleteBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    minHeight: 44,
    justifyContent: "center",
  },
  deleteText: { fontFamily: fonts.monoBold, fontSize: 10.5, letterSpacing: 0.8, color: colors.error },
  deleteError: {
    fontFamily: fonts.mono,
    fontSize: 10,
    color: colors.error,
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.md,
  },
  confirmNote: {
    fontFamily: fonts.mono,
    fontSize: 9.5,
    color: colors.onSurfaceTertiary,
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.md,
  },
});
