import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, Pressable, StyleSheet, Alert, Platform, Modal, ActivityIndicator } from "react-native";
import { WebView } from "react-native-webview";
import { useFocusEffect } from "expo-router";
import { colors, fonts, spacing, BORDER, TERMINAL } from "@/src/theme";
import { api, WalletBalance } from "@/src/api";
import { getWalletDeviceId } from "@/src/wallet";

// Real Razorpay top-ups. The app never sees the key secret and never credits
// anything itself: it asks the backend for an order, opens the backend-hosted
// checkout page, then polls the backend, which verifies with Razorpay before
// crediting.

export function WalletCard() {
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [wallet, setWallet] = useState<WalletBalance | null>(null);
  const [busy, setBusy] = useState(false);
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);
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

  const topUp = async (amount: number) => {
    if (!deviceId) return;
    setBusy(true);
    try {
      const order = await api.createTopupOrder(deviceId, amount);
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
      Alert.alert("Couldn't start checkout", e?.message || "Try again.");
    }
  };

  const closeCheckout = () => {
    setCheckoutUrl(null);
    const orderId = pendingOrder.current;
    if (orderId) settle(orderId);
  };

  const symbol = wallet?.symbol || "$";
  const price = wallet?.prices?.full_analysis;
  const packs = wallet?.packs || [];

  return (
    <View testID="wallet-card" style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>WALLET</Text>
        <Text style={styles.balance}>
          {wallet == null ? "—" : `${symbol}${wallet.balance.toFixed(2)}`}
        </Text>
      </View>

      {price ? (
        <Text style={styles.priceNote}>
          {`Full analysis: ${symbol}${price.toFixed(2)} · re-checking an unchanged verdict is free`}
        </Text>
      ) : (
        <Text style={styles.priceNote}>Usage-based pricing isn&apos;t active in this build yet.</Text>
      )}

      <View style={styles.topUpRow}>
        {packs.map((amt) => (
          <Pressable
            key={amt}
            disabled={busy || !wallet?.payments_live}
            onPress={() => topUp(amt)}
            style={[styles.topUpBtn, (busy || !wallet?.payments_live) && styles.topUpBtnDisabled]}
          >
            <Text style={styles.topUpText}>{`+${symbol}${amt.toFixed(0)}`}</Text>
          </Pressable>
        ))}
      </View>
      {busy ? (
        <View style={styles.busyRow}>
          <ActivityIndicator size="small" color={colors.onSurfaceTertiary} />
          <Text style={styles.busyText}>Confirming payment…</Text>
        </View>
      ) : null}
      <Text style={styles.placeholderNote}>
        {wallet?.payments_live
          ? "Secure payment by Razorpay. Cards, UPI and netbanking."
          : "Payments aren't switched on yet."}
      </Text>

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
