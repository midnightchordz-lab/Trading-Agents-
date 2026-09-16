import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  Alert,
  Platform,
  Modal,
  ActivityIndicator,
  KeyboardAvoidingView,
} from "react-native";
import { WebView } from "react-native-webview";
import { useFocusEffect } from "expo-router";
import { colors, fonts, spacing, BORDER, TERMINAL } from "@/src/theme";
import { api, WalletBalance } from "@/src/api";
import { getWalletDeviceId } from "@/src/wallet";

// Real Razorpay top-ups. The app never sees the key secret and never credits
// anything itself: it asks the backend for a Razorpay-hosted payment link,
// opens it, then polls the backend, which verifies with Razorpay before
// crediting.

export function WalletCard() {
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [wallet, setWallet] = useState<WalletBalance | null>(null);
  const [busy, setBusy] = useState(false);
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);
  // Razorpay requires both an email and a phone number on every payment link,
  // but an account only has the one it signed in with — so the missing one is
  // asked for here, once.
  const [needContact, setNeedContact] = useState<{ fields: string[]; amount: number } | null>(null);
  const [emailInput, setEmailInput] = useState("");
  const [phoneInput, setPhoneInput] = useState("");
  const [contactError, setContactError] = useState<string | null>(null);
  const pendingOrder = useRef<string | null>(null);

  const refresh = useCallback(async (id: string) => {
    try {
      setWallet(await api.getWalletBalance(id));
    } catch {
      // supplementary display; fail quietly
    }
  }, []);

  useEffect(() => {
    (async () => {
      const id = await getWalletDeviceId();
      setDeviceId(id);
      refresh(id);
    })();
  }, [refresh]);

  useFocusEffect(
    useCallback(() => {
      if (deviceId) refresh(deviceId);
    }, [deviceId, refresh])
  );

  // Ask the backend whether the payment landed. Polled because the webhook
  // can arrive slightly after the browser returns.
  const settle = useCallback(
    async (orderId: string) => {
      setBusy(true);
      try {
        for (let i = 0; i < 12; i++) {
          const res = await api.getPaymentStatus(orderId);
          if (res.status === "captured") {
            if (deviceId) await refresh(deviceId);
            Alert.alert("Wallet topped up", `Your balance is now ${wallet?.symbol || "$"}${res.balance.toFixed(2)}.`);
            return;
          }
          if (res.status === "failed") {
            Alert.alert("Payment didn't go through", "Nothing was charged. You can try again.");
            return;
          }
          await new Promise((r) => setTimeout(r, 2500));
        }
        if (deviceId) await refresh(deviceId);
      } catch {
        if (deviceId) await refresh(deviceId);
      } finally {
        setBusy(false);
        pendingOrder.current = null;
      }
    },
    [deviceId, refresh, wallet?.symbol]
  );

  const topUp = async (amount: number, contact?: { email?: string; phone?: string }) => {
    if (!deviceId) return;
    setBusy(true);
    try {
      const order = await api.createTopupOrder(deviceId, amount, contact);
      setNeedContact(null);
      pendingOrder.current = order.order_id;
      if (Platform.OS === "web") {
        window.open(order.checkout_url, "razorpay_checkout", "width=480,height=760");
        setBusy(false);
        // The popup is a separate window, so poll from here.
        settle(order.order_id);
      } else {
        setCheckoutUrl(order.checkout_url);
        setBusy(false);
      }
    } catch (e: any) {
      setBusy(false);
      const msg: string = e?.message || "Try again.";
      if (msg.startsWith("contact_required:")) {
        setNeedContact({ fields: msg.split(":")[1].split(","), amount });
        return;
      }
      Alert.alert("Couldn't start checkout", msg);
    }
  };

  const submitContact = () => {
    if (!needContact) return;
    const contact: { email?: string; phone?: string } = {};
    if (needContact.fields.includes("email")) {
      if (!emailInput.trim().includes("@")) {
        setContactError("Enter a valid email address for the receipt.");
        return;
      }
      contact.email = emailInput.trim();
    }
    if (needContact.fields.includes("phone")) {
      if (phoneInput.replace(/\D/g, "").length < 8) {
        setContactError("Enter your phone number with country code, e.g. +1…");
        return;
      }
      contact.phone = phoneInput.trim();
    }
    setContactError(null);
    topUp(needContact.amount, contact);
  };

  const closeCheckout = () => {
    setCheckoutUrl(null);
    const orderId = pendingOrder.current;
    if (orderId) settle(orderId);
  };

  const symbol = wallet?.symbol || "$";
  const price = wallet?.prices?.full_analysis;
  const packs = wallet?.packs || [];
  const freeCredits = wallet?.free_credits_remaining ?? 0;
  const launchFree = wallet?.launch_free_active === true;

  return (
    <View testID="wallet-card" style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>WALLET</Text>
        <Text style={styles.balance}>
          {wallet == null ? "—" : `${symbol}${wallet.balance.toFixed(2)}`}
        </Text>
      </View>

      {launchFree ? (
        <Text style={styles.priceNote}>
          {wallet?.launch_free_daily_remaining != null
            ? `Free during launch — ${wallet.launch_free_daily_remaining} of ${wallet.launch_free_daily_cap ?? 10} analyses left today.`
            : "Every analysis is free during launch — no payment needed."}
        </Text>
      ) : price ? (
        <Text style={styles.priceNote}>
          {freeCredits > 0
            ? `${freeCredits} free ${freeCredits === 1 ? "analysis" : "analyses"} left — no payment needed yet. After that, ${symbol}${price.toFixed(2)} each.`
            : `Full analysis: ${symbol}${price.toFixed(2)} · re-checking an unchanged verdict is free`}
        </Text>
      ) : (
        <Text style={styles.priceNote}>Usage-based pricing isn&apos;t active in this build yet.</Text>
      )}

      {launchFree ? (
        // No top-up buttons at all while the launch window is open: an
        // external purchase path for digital content is the thing Apple
        // rejects, even when nothing is actually being charged.
        <View testID="launch-free-banner" style={styles.launchFreeBox}>
          <Text style={styles.launchFreeText}>FREE DURING LAUNCH</Text>
        </View>
      ) : (
        <View style={styles.topUpRow}>
          {packs.map((amt) => (
            <Pressable
              key={amt}
              testID={`topup-${amt}`}
              disabled={busy || !wallet?.payments_live}
              onPress={() => topUp(amt)}
              style={[styles.topUpBtn, (busy || !wallet?.payments_live) && styles.topUpBtnDisabled]}
            >
              <Text style={styles.topUpText}>{`+${symbol}${amt.toFixed(0)}`}</Text>
            </Pressable>
          ))}
        </View>
      )}
      {busy ? (
        <View style={styles.busyRow}>
          <ActivityIndicator size="small" color={colors.onSurfaceTertiary} />
          <Text style={styles.busyText}>Confirming payment…</Text>
        </View>
      ) : null}
      {launchFree ? null : (
        <Text style={styles.placeholderNote}>
          {wallet?.payments_live
            ? "Secure payment page hosted by Razorpay."
            : "Payments aren't switched on yet."}
        </Text>
      )}

      <Modal visible={!!needContact} animationType="slide" transparent onRequestClose={() => setNeedContact(null)}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.contactBackdrop}
        >
          <View testID="contact-prompt" style={styles.contactCard}>
            <Text style={styles.contactTitle}>ONE-TIME DETAIL</Text>
            <Text style={styles.contactBody}>
              Razorpay needs both an email and a phone number to issue your payment receipt. We only ask once.
            </Text>
            {needContact?.fields.includes("email") ? (
              <View style={styles.fieldBlock}>
                <Text style={styles.fieldLabel}>&gt; EMAIL</Text>
                <TextInput
                  testID="contact-email-input"
                  value={emailInput}
                  onChangeText={setEmailInput}
                  placeholder="you@example.com"
                  placeholderTextColor={colors.onSurfaceTertiary}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="email-address"
                  style={styles.input}
                />
              </View>
            ) : null}
            {needContact?.fields.includes("phone") ? (
              <View style={styles.fieldBlock}>
                <Text style={styles.fieldLabel}>&gt; PHONE (WITH COUNTRY CODE)</Text>
                <TextInput
                  testID="contact-phone-input"
                  value={phoneInput}
                  onChangeText={setPhoneInput}
                  placeholder="+1 555 000 1234"
                  placeholderTextColor={colors.onSurfaceTertiary}
                  keyboardType="phone-pad"
                  style={styles.input}
                />
              </View>
            ) : null}
            {contactError ? (
              <Text testID="contact-error" style={styles.contactError}>
                {contactError}
              </Text>
            ) : null}
            <Pressable testID="contact-continue" onPress={submitContact} disabled={busy} style={styles.contactBtn}>
              {busy ? (
                <ActivityIndicator color={colors.onSurfaceInverse} />
              ) : (
                <Text style={styles.contactBtnText}>CONTINUE TO PAYMENT →</Text>
              )}
            </Pressable>
            <Pressable onPress={() => setNeedContact(null)} hitSlop={8}>
              <Text style={styles.contactCancel}>CANCEL</Text>
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      <Modal visible={!!checkoutUrl} animationType="slide" onRequestClose={closeCheckout}>
        <View style={styles.modalRoot}>
          <View style={styles.modalBar}>
            <Text style={styles.modalTitle}>SECURE CHECKOUT</Text>
            <Pressable onPress={closeCheckout} hitSlop={12}>
              <Text style={styles.modalClose}>CLOSE</Text>
            </Pressable>
          </View>
          {checkoutUrl ? <WebView source={{ uri: checkoutUrl }} style={{ flex: 1 }} /> : null}
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, marginTop: spacing.md },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  label: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurfaceInverse },
  balance: { fontFamily: fonts.monoBold, fontSize: 14, color: colors.onSurfaceInverse },
  priceNote: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, padding: spacing.md, paddingBottom: spacing.sm },
  topUpRow: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.md, paddingBottom: spacing.sm },
  topUpBtn: { flex: 1, borderWidth: 1, borderColor: colors.border, paddingVertical: spacing.sm, alignItems: "center", minHeight: 44, justifyContent: "center" },
  topUpBtnDisabled: { opacity: 0.45 },
  topUpText: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  busyRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingHorizontal: spacing.md, paddingBottom: spacing.sm },
  busyText: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary },
  placeholderNote: {
    fontFamily: fonts.mono,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
    padding: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  modalRoot: { flex: 1, backgroundColor: TERMINAL.bg },
  contactBackdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.55)" },
  contactCard: {
    backgroundColor: colors.surface,
    borderTopWidth: BORDER,
    borderTopColor: colors.borderStrong,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  contactTitle: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurface },
  contactBody: { fontFamily: fonts.mono, fontSize: 11, lineHeight: 16, color: colors.onSurfaceTertiary },
  fieldBlock: { gap: 4, marginTop: spacing.sm },
  fieldLabel: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 0.5, color: colors.onSurfaceTertiary },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    minHeight: 46,
    fontFamily: fonts.mono,
    fontSize: 13,
    color: colors.onSurface,
  },
  launchFreeBox: {
    marginTop: spacing.md,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: TERMINAL.bg,
    paddingVertical: spacing.md,
    alignItems: "center",
  },
  launchFreeText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1.5, color: TERMINAL.lime },
  contactError: { fontFamily: fonts.mono, fontSize: 11, color: colors.error, marginTop: spacing.sm },
  contactBtn: {    marginTop: spacing.md,
    backgroundColor: colors.surfaceInverse,
    minHeight: 48,
    alignItems: "center",
    justifyContent: "center",
  },
  contactBtnText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurfaceInverse },
  contactCancel: {
    fontFamily: fonts.mono,
    fontSize: 11,
    color: colors.onSurfaceTertiary,
    textAlign: "center",
    paddingVertical: spacing.md,
  },
  modalBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xl,
    paddingBottom: spacing.md,
    backgroundColor: TERMINAL.panel,
  },
  modalTitle: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: TERMINAL.textBright },
  modalClose: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: TERMINAL.lime },
});
