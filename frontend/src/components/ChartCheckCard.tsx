import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { ChartCheckRow } from "@/src/api";

// "Your chart vs the data": the screenshot's claims beside the computed
// numbers, so a misread (an RSI from another timeframe, an old screenshot,
// the wrong stock) is visible instead of silently trusted.
export function ChartCheckCard({ rows }: { rows: ChartCheckRow[] }) {
  if (!rows.length) return null;
  const mismatches = rows.filter((r) => !r.agrees).length;
  return (
    <View testID="chart-check" style={styles.card}>
      <Text style={styles.title}>YOUR CHART VS THE DATA</Text>
      <Text style={styles.sub}>
        {mismatches === 0
          ? "Your screenshot agrees with the live data."
          : `${mismatches} difference${mismatches > 1 ? "s" : ""} — the desk used the live data.`}
      </Text>
      <View style={styles.headRow}>
        <Text style={[styles.head, { flex: 1.2 }]}> </Text>
        <Text style={styles.head}>CHART</Text>
        <Text style={styles.head}>DATA</Text>
        <Text style={[styles.head, { flex: 0.4 }]}> </Text>
      </View>
      {rows.map((r) => (
        <View key={r.item} style={styles.rowWrap}>
          <View style={styles.row}>
            <Text style={[styles.item, { flex: 1.2 }]}>{r.item}</Text>
            <Text style={styles.val}>{r.chart}</Text>
            <Text style={styles.val}>{r.data}</Text>
            <Text style={[styles.mark, { color: r.agrees ? colors.success : colors.error }]}>{r.agrees ? "✓" : "≠"}</Text>
          </View>
          {r.note ? <Text style={styles.note}>{r.note}</Text> : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, padding: spacing.md, marginHorizontal: spacing.lg, marginBottom: spacing.md },
  title: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurface },
  sub: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2, marginBottom: spacing.sm },
  headRow: { flexDirection: "row", borderBottomWidth: 1, borderBottomColor: colors.divider, paddingBottom: 4 },
  head: { flex: 1, fontFamily: fonts.monoBold, fontSize: 10, color: colors.onSurfaceTertiary },
  rowWrap: { paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: colors.divider },
  row: { flexDirection: "row", alignItems: "center" },
  item: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  val: { flex: 1, fontFamily: fonts.mono, fontSize: 11, color: colors.onSurface },
  mark: { flex: 0.4, fontFamily: fonts.monoBold, fontSize: 14, textAlign: "right" },
  note: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, marginTop: 2 },
});
