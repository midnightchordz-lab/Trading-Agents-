import React, { useEffect, useState } from "react";
import { View, Text, Pressable, ActivityIndicator, StyleSheet } from "react-native";
import * as Haptics from "expo-haptics";
import { colors, fonts, spacing, BORDER, changeColor } from "@/src/theme";
import { Sparkline } from "@/src/components/Sparkline";
import { Quote, ChartData, api } from "@/src/api";
import { useTranslation } from "react-i18next";

const RANGES = ["1D", "1W", "1M", "1Y"];

function fmt(n?: number | null, dp = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function QuoteCard({ quote, showRanges = false }: { quote: Quote; showRanges?: boolean }) {
  const { t } = useTranslation();
  const [range, setRange] = useState<string>("1M");
  const [chart, setChart] = useState<ChartData | null>(null);
  const [loadingChart, setLoadingChart] = useState(false);

  useEffect(() => {
    setRange("1M");
    setChart(null);
  }, [quote.symbol]);

  useEffect(() => {
    let cancelled = false;
    if (!showRanges || range === "1M") {
      setChart(null);
      return;
    }
    setLoadingChart(true);
    api
      .chart(quote.symbol, range)
      .then((d) => {
        if (!cancelled) setChart(d);
      })
      .catch(() => {
        if (!cancelled) setChart(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingChart(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range, showRanges, quote.symbol]);

  const usingChart = showRanges && range !== "1M" && !!chart && chart.points.length > 1;
  const points = usingChart ? chart!.points : quote.sparkline;
  const dispPct = usingChart ? chart!.changePercent : quote.changePercent;
  const dispChange = usingChart ? chart!.change : quote.change;
  const up = (dispPct ?? 0) >= 0;
  const cColor = changeColor(dispPct);

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
          {loadingChart ? (
            <ActivityIndicator color={colors.onSurface} />
          ) : (
            <Sparkline data={points} color={cColor} width={110} height={44} />
          )}
        </View>
      </View>

      <View style={styles.priceRow}>
        <Text style={styles.price}>
          {quote.currency && quote.currency !== "USD" ? "" : "$"}
          {fmt(quote.price)}
        </Text>
        <View style={[styles.changeBox, { backgroundColor: cColor }]}>
          <Text style={styles.changeText}>
            {up ? "▲" : "▼"} {fmt(Math.abs(dispChange ?? 0))} ({fmt(dispPct)}%)
          </Text>
        </View>
      </View>

      {quote.fiftyTwoWeekLow != null && quote.fiftyTwoWeekHigh != null && quote.price != null && quote.fiftyTwoWeekHigh > quote.fiftyTwoWeekLow ? (
        <View style={styles.rangeBarWrap}>
          <View style={styles.rangeBarLabels}>
            <Text style={styles.rangeBarLabel}>{fmt(quote.fiftyTwoWeekLow)}</Text>
            <Text style={styles.rangeBarLabel}>{fmt(quote.fiftyTwoWeekHigh)}</Text>
          </View>
          <View style={styles.rangeBarTrack}>
            <View
              style={[
                styles.rangeBarDot,
                {
                  left: `${Math.max(0, Math.min(100, ((quote.price - quote.fiftyTwoWeekLow) / (quote.fiftyTwoWeekHigh - quote.fiftyTwoWeekLow)) * 100))}%`,
                },
              ]}
            />
          </View>
        </View>
      ) : null}

      {showRanges ? (
        <View style={styles.rangeRow}>
          {RANGES.map((r, i) => {
            const active = range === r;
            return (
              <Pressable
                key={r}
                testID={`chart-range-${r}`}
                onPress={() => {
                  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                  setRange(r);
                }}
                style={[styles.rangeChip, i < RANGES.length - 1 && styles.rangeDivider, active && styles.rangeActive]}
              >
                <Text style={[styles.rangeText, { color: active ? colors.onSurfaceInverse : colors.onSurface }]}>{r}</Text>
              </Pressable>
            );
          })}
        </View>
      ) : null}

      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>{t("quote.fifty_two_week_low")}</Text>
          <Text style={styles.statValue}>{fmt(quote.fiftyTwoWeekLow)}</Text>
        </View>
        <View style={[styles.stat, styles.statMid]}>
          <Text style={styles.statLabel}>{t("quote.fifty_two_week_high")}</Text>
          <Text style={styles.statValue}>{fmt(quote.fiftyTwoWeekHigh)}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>{t("quote.exchange")}</Text>
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
  rangeBarWrap: { paddingHorizontal: spacing.lg, paddingBottom: spacing.md },
  rangeBarLabels: { flexDirection: "row", justifyContent: "space-between", marginBottom: 4 },
  rangeBarLabel: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceTertiary },
  rangeBarTrack: { height: 4, backgroundColor: colors.borderStrong, borderRadius: 2 },
  rangeBarDot: {
    position: "absolute",
    top: -3,
    width: 3,
    height: 10,
    backgroundColor: colors.onSurface,
    marginLeft: -1.5,
  },
  rangeRow: {
    flexDirection: "row",
    marginHorizontal: spacing.lg,
    marginBottom: spacing.lg,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
  },
  rangeChip: { flex: 1, paddingVertical: spacing.sm, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  rangeDivider: { borderRightWidth: 1.5, borderRightColor: colors.borderStrong },
  rangeActive: { backgroundColor: colors.brand },
  rangeText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1 },
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
