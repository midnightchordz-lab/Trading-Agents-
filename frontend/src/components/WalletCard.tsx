import React, { useCallback, useEffect, useState } from "react";
import { View, Text, Pressable, StyleSheet, Alert } from "react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api } from "@/src/api";
import { getWalletDeviceId } from "@/src/wallet";

// PLACEHOLDER top-up: credits the wallet directly with no real payment
// taken. Wire this button to Apple In-App Purchase / Google Play Billing
// (iOS requires IAP for digital goods/services purchased in-app) or
// Stripe (web/Android) with server-side receipt verification before this
// can accept real money — see the integration notes in the accompanying
// spec. Everything else here (balance display, pricing, deduction) is
// real and already wired to the backend.
const TOPUP_AMOUNTS = [5, 10, 25];

export function WalletCard() {
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async (id: string) => {
    try {
      const res = await api.getWalletBalance(id);
      setBalance(res.balance_usd);
      setPrices(res.prices);
    } catch {
      // wallet is supplementary display; fail quietly
    }
  }, []);

  useEffect(() => {
    (async () => {
      const id = await getWalletDeviceId();
      setDeviceId(id);
      refresh(id);
    })();
  }, [refresh]);

  const topUp = async (amount: number) => {
    if (!deviceId) return;
    setLoading(true);
    try {
      await api.topUpWallet(deviceId, amount);
      await refresh(deviceId);
    } catch (e: any) {
      Alert.alert("Couldn't add funds", e?.message || "Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View testID="wallet-card" style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>WALLET</Text>
        <Text style={styles.balance}>{balance == null ? "—" : `$${balance.toFixed(2)}`}</Text>
      </View>

      {prices.full_analysis ? (
        <Text style={styles.priceNote}>
          Full analysis: ${prices.full_analysis.toFixed(2)} · re-checking an unchanged verdict is free
        </Text>
      ) : (
        <Text style={styles.priceNote}>Usage-based pricing isn&apos;t active in this build yet.</Text>
      )}

      <View style={styles.topUpRow}>
        {TOPUP_AMOUNTS.map((amt) => (
          <Pressable key={amt} disabled={loading} onPress={() => topUp(amt)} style={styles.topUpBtn}>
            <Text style={styles.topUpText}>+${amt}</Text>
          </Pressable>
        ))}
      </View>
      <Text style={styles.placeholderNote}>
        Demo top-up — no real payment is taken yet. Real purchases require App Store / Play Store / Stripe setup.
      </Text>
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
  topUpBtn: { flex: 1, borderWidth: 1, borderColor: colors.border, paddingVertical: spacing.sm, alignItems: "center" },
  topUpText: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  placeholderNote: {
    fontFamily: fonts.mono,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
    padding: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
});
