import React from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { colors, fonts, spacing, BORDER, verdictColors } from "@/src/theme";
import type { Timeframes, TimeframeCall } from "@/src/api";

// Shows the desk's short/medium/long-term calls side by side — a stock can
// legitimately be a BUY this week and a SELL over the next year. Tapping a
// tab expands that horizon's thesis and levels.

const ORDER: (keyof Timeframes)[] = ["short_term", "medium_term", "long_term"];
export const HORIZON_LABEL: Record<keyof Timeframes, string> = {
  short_term: "1-2 WEEKS",
  medium_term: "1-3 MONTHS",
  long_term: "6-12 MONTHS",
};
const SHORT_LABEL = HORIZON_LABEL;

function fmt(n: number | null, currency?: string): string {
  if (n == null) return "—";
  const v = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency && currency !== "USD" ? `${v} ${currency}` : `$${v}`;
}

export function TimeframesCard({
  timeframes,
  currency,
  active,
  onChange,
}: {
  timeframes: Timeframes;
  currency?: string;
  active: keyof Timeframes;
  onChange: (key: keyof Timeframes) => void;
}) {
  const call: TimeframeCall = timeframes[active];
  const { bg, fg } = verdictColors(call.decision);
  const isFallback = !!call.thesis?.toLowerCase().includes("unavailable");

  return (
    <View testID="timeframes-card" style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.headerText}>MULTI-HORIZON VIEW</Text>
      </View>

      <View style={styles.tabRow}>
        {ORDER.map((key) => {
          const isActive = key === active;
          const tabColors = verdictColors(timeframes[key].decision);
          return (
            <Pressable
              key={key}
              testID={`horizon-tab-${key}`}
              onPress={() => onChange(key)}
              style={[styles.tab, isActive && { backgroundColor: tabColors.bg }]}
            >
              <Text style={[styles.tabLabel, isActive && { color: tabColors.fg }]}>{SHORT_LABEL[key]}</Text>
              <Text style={[styles.tabDecision, isActive && { color: tabColors.fg }]}>{timeframes[key].decision}</Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.body}>
        <View style={styles.decisionRow}>
          <View style={[styles.decisionBadge, { backgroundColor: bg }]}>
            <Text style={[styles.decisionText, { color: fg }]}>{call.decision}</Text>
          </View>
          <Text style={styles.confidence}>{call.confidence}% CONFIDENCE</Text>
        </View>

        {call.thesis ? <Text style={styles.thesis}>{call.thesis}</Text> : null}

        {call.target_price != null || call.stop_loss != null ? (
          <View style={styles.levelsRow}>
            <View style={styles.levelBox}>
              <Text style={styles.levelLabel}>TARGET</Text>
              <Text style={[styles.levelValue, { color: colors.success }]}>{fmt(call.target_price, currency)}</Text>
            </View>
            <View style={styles.levelBox}>
              <Text style={styles.levelLabel}>STOP LOSS</Text>
              <Text style={[styles.levelValue, { color: colors.error }]}>{fmt(call.stop_loss, currency)}</Text>
            </View>
          </View>
        ) : null}

        {call.target_price != null || call.stop_loss != null ? (
          <Text style={styles.chartHint}>These levels are drawn on the chart below.</Text>
        ) : null}

        {isFallback ? (
          <Text style={styles.fallbackNote}>Horizon-specific levels weren't available for this run — showing the primary verdict.</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, marginTop: spacing.md },
  header: { backgroundColor: colors.surfaceInverse, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  headerText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurfaceInverse },
  tabRow: { flexDirection: "row", borderBottomWidth: BORDER, borderBottomColor: colors.borderStrong },
  tab: {
    flex: 1,
    alignItems: "center",
    paddingVertical: spacing.sm,
    borderEndWidth: 1,
    borderEndColor: colors.border,
  },
  tabLabel: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.onSurfaceTertiary },
  tabDecision: { fontFamily: fonts.monoBold, fontSize: 11, color: colors.onSurface, marginTop: 2 },
  body: { padding: spacing.md },
  decisionRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginBottom: spacing.sm },
  decisionBadge: { paddingHorizontal: spacing.sm, paddingVertical: 4 },
  decisionText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1 },
  confidence: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary },
  thesis: { fontFamily: fonts.displayReg, fontSize: 13, color: colors.onSurface, lineHeight: 18, marginBottom: spacing.sm },
  levelsRow: { flexDirection: "row", gap: spacing.md },
  levelBox: { flex: 1 },
  levelLabel: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.onSurfaceTertiary },
  levelValue: { fontFamily: fonts.mono, fontSize: 13, marginTop: 2 },
  chartHint: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, marginTop: spacing.sm, letterSpacing: 0.3 },
  fallbackNote: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, marginTop: spacing.sm },
});
