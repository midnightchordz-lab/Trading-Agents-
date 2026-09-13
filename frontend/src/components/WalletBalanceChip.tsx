import React, { useEffect, useState } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { fonts, spacing, TERMINAL } from "@/src/theme";
import { api } from "@/src/api";
import { getWalletDeviceId } from "@/src/wallet";

// Compact, always-visible balance indicator for the Analyze screen's
// header — the screen where the balance actually gets spent. Tapping it
// jumps to the Agents tab, where full top-up management already lives.
// Silent on failure: this is a convenience readout, not a blocking check —
// the real balance/402 enforcement happens server-side on /analyze.
export function WalletBalanceChip() {
  const router = useRouter();
  const [balance, setBalance] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const deviceId = await getWalletDeviceId();
        const res = await api.getWalletBalance(deviceId);
        // With pricing switched off the balance is never spent, so showing
        // "$0.00" here would be misleading — stay hidden until it matters.
        if (!res.enforcement_enabled) return;
        if (!cancelled) setBalance(res.balance_usd);
      } catch {
        // wallet enforcement may be off, or the fetch failed — stay quiet
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (balance == null) return null;

  return (
    <Pressable testID="wallet-balance-chip" onPress={() => router.push("/agents")} style={styles.chip}>
      <View style={styles.dot} />
      <Text style={styles.text}>${balance.toFixed(2)}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 0.5,
    borderColor: TERMINAL.line,
    borderRadius: 8,
    paddingHorizontal: spacing.sm,
    height: 34,
  },
  dot: { width: 5, height: 5, borderRadius: 2.5, backgroundColor: TERMINAL.lime },
  text: { fontFamily: fonts.mono, fontSize: 12, color: TERMINAL.lime },
});
