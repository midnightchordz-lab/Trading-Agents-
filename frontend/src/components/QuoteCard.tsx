import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, fonts, spacing, BORDER, changeColor } from "@/src/theme";
import { Sparkline } from "@/src/components/Sparkline";
import { Quote } from "@/src/api";

function fmt(n?: number | null, dp = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function QuoteCard({ quote }: { quote: Quote }) {
  const up = (quote.changePercent ?? 0) >= 0;
  const cColor = changeColor(quote.changePercent);

  return (
    <View testID="quote-card" style={styles.card}>
      <View style={styles.topRow}>
        <View style={{ flex: 1 }}>
          <Text style={styles.symbol}>{quote.symbol}</Text>
          <Text style={styles.name} numberOfLines={1}>
            {quote.name}
          </Text>
        </View>
        <View style={styles.sparkWrap}>
          <Sparkline data={quote.sparkline} color={cColor} width={110} height={44} />
        </View>
      </View>

      <View style={styles.priceRow}>
        <Text style={styles.price}>
          {quote.currency && quote.currency !== "USD" ? "" : "$"}
          {fmt(quote.price)}
        </Text>
        <View style={[styles.changeBox, { backgroundColor: cColor }]}>
          <Text style={styles.changeText}>
            {up ? "▲" : "▼"} {fmt(Math.abs(quote.change ?? 0))} ({fmt(quote.changePercent)}%)
          </Text>
        </View>
      </View>

      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>52W LOW</Text>
          <Text style={styles.statValue}>{fmt(quote.fiftyTwoWeekLow)}</Text>
        </View>
        <View style={[styles.stat, styles.statMid]}>
          <Text style={styles.statLabel}>52W HIGH</Text>
          <Text style={styles.statValue}>{fmt(quote.fiftyTwoWeekHigh)}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>EXCHANGE</Text>
          <Text style={styles.statValue} numberOfLines={1}>
            {quote.exchange || "—"}
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
  },
  topRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: spacing.lg,
    paddingBottom: spacing.sm,
  },
  symbol: { fontFamily: fonts.display, fontSize: 26, color: colors.onSurface, letterSpacing: -0.5 },
  name: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  sparkWrap: { justifyContent: "center", alignItems: "flex-end" },
  priceRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.lg,
  },
  price: { fontFamily: fonts.monoBold, fontSize: 24, color: colors.onSurface },
  changeBox: { paddingHorizontal: spacing.sm, paddingVertical: spacing.xs },
  changeText: { fontFamily: fonts.monoBold, fontSize: 12, color: "#FFFFFF" },
  statsRow: {
    flexDirection: "row",
    borderTopWidth: BORDER,
    borderTopColor: colors.borderStrong,
  },
  stat: { flex: 1, padding: spacing.sm, paddingVertical: spacing.md },
  statMid: {
    borderLeftWidth: BORDER,
    borderRightWidth: BORDER,
    borderColor: colors.borderStrong,
  },
  statLabel: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceTertiary, letterSpacing: 0.5 },
  statValue: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface, marginTop: 3 },
});
