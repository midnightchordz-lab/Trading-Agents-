import React from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import * as Haptics from "expo-haptics";
import { Bell, BellRinging } from "phosphor-react-native";
import { colors, fonts, spacing, BORDER, verdictColors } from "@/src/theme";
import type { Verdict, Quote } from "@/src/api";
import { useAlerts } from "@/src/alerts";

type Props = {
  verdict: Verdict;
  quote?: Quote | null;
  symbol: string;
  name: string;
};

function fmt(n?: number | null, currency?: string): string {
  if (n == null || Number.isNaN(n)) return "—";
  const v = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency && currency !== "USD" ? `${v} ${currency}` : `$${v}`;
}

function pct(from?: number | null, to?: number | null): string {
  if (from == null || to == null || !from) return "";
  const p = ((to - from) / from) * 100;
  return `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
}

/**
 * Renders the agents' verdict as price levels: the BUY / SELL entry at the
 * live price, the target, and the stop loss. Sits directly under the
 * TradingView chart so the levels read against the live candles.
 */
export function VerdictLevels({ verdict, quote, symbol, name }: Props) {
  const decision = verdict.decision;
  const isHold = decision === "HOLD";
  const price = quote?.price ?? null;
  const currency = quote?.currency;
  const { bg, fg } = verdictColors(decision);
  const { add, remove, findActive, evaluate } = useAlerts();

  // Evaluate any standing alerts against the live price when levels render.
  React.useEffect(() => {
    if (price != null) evaluate(symbol, price);
  }, [symbol, price, evaluate]);

  type Row = {
    label: string;
    value: string;
    delta: string;
    color: string;
    fg: string;
    alert?: { price: number; direction: "above" | "below"; label: string };
  };

  const rows: Row[] = [
    {
      label: isHold ? "HOLD · NO ENTRY" : `${decision} ENTRY`,
      value: fmt(price, currency),
      delta: "LIVE",
      color: bg,
      fg,
    },
    {
      label: "TARGET",
      value: fmt(verdict.target_price, currency),
      delta: pct(price, verdict.target_price),
      color: colors.success,
      fg: colors.onSuccess,
      alert:
        verdict.target_price != null && price != null
          ? { price: verdict.target_price, direction: verdict.target_price >= price ? "above" : "below", label: "TARGET" }
          : undefined,
    },
    {
      label: "STOP LOSS",
      value: fmt(verdict.stop_loss, currency),
      delta: pct(price, verdict.stop_loss),
      color: colors.error,
      fg: colors.onError,
      alert:
        verdict.stop_loss != null && price != null
          ? { price: verdict.stop_loss, direction: verdict.stop_loss <= price ? "below" : "above", label: "STOP LOSS" }
          : undefined,
    },
  ];

  const onToggleAlert = (a: NonNullable<Row["alert"]>) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const existing = findActive(symbol, a.price, a.direction);
    if (existing) {
      remove(existing.id);
    } else {
      add({ symbol, name, label: a.label, price: a.price, direction: a.direction, currency: currency || undefined });
    }
  };

  return (
    <View testID="verdict-levels" style={styles.wrap}>
      {rows.map((r, i) => {
        const set = r.alert ? !!findActive(symbol, r.alert.price, r.alert.direction) : false;
        return (
          <View key={r.label} style={[styles.row, i < rows.length - 1 && styles.rowBorder]}>
            <View style={[styles.swatch, { backgroundColor: r.color }]} />
            <Text style={styles.label}>{r.label}</Text>
            <Text style={styles.value}>{r.value}</Text>
            <View style={[styles.deltaBox, { backgroundColor: r.color }]}>
              <Text style={[styles.delta, { color: r.fg }]}>{r.delta || "—"}</Text>
            </View>
            {r.alert ? (
              <Pressable
                testID={`alert-toggle-${r.label.replace(/\s+/g, "-").toLowerCase()}`}
                onPress={() => onToggleAlert(r.alert!)}
                hitSlop={8}
                style={[styles.bell, set && styles.bellActive]}
              >
                {set ? (
                  <BellRinging size={16} color={colors.onSurfaceInverse} weight="fill" />
                ) : (
                  <Bell size={16} color={colors.onSurface} weight="regular" />
                )}
              </Pressable>
            ) : (
              <View style={styles.bellSpacer} />
            )}
          </View>
        );
      })}
      <Text style={styles.note}>
        Tap the bell to set a price alert · {verdict.time_horizon} horizon · not financial advice
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    borderWidth: BORDER,
    borderTopWidth: 0,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    gap: spacing.sm,
  },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: colors.border },
  swatch: { width: 14, height: 3 },
  label: { flex: 1, fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 0.5, color: colors.onSurface },
  value: { fontFamily: fonts.mono, fontSize: 13, color: colors.onSurface },
  deltaBox: { minWidth: 58, paddingHorizontal: 6, paddingVertical: 2, alignItems: "center" },
  delta: { fontFamily: fonts.monoBold, fontSize: 10 },
  bell: { width: 30, height: 30, borderWidth: 1.5, borderColor: colors.borderStrong, alignItems: "center", justifyContent: "center" },
  bellActive: { backgroundColor: colors.surfaceInverse, borderColor: colors.borderStrong },
  bellSpacer: { width: 30, height: 30 },
  note: {
    fontFamily: fonts.mono,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
});
