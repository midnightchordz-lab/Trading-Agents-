import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, fonts, spacing, BORDER, accents } from "@/src/theme";
import { computeFearGreed } from "@/src/sentiment";
import type { Analysis } from "@/src/api";

const ZONES = [colors.error, accents.orange, accents.amber, accents.lime, colors.success];

export function FearGreedGauge({ analysis }: { analysis: Analysis }) {
  const { score, label, color, blurb } = computeFearGreed(analysis);
  return (
    <View testID="fear-greed-gauge" style={styles.wrap}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>FEAR & GREED</Text>
        <View style={[styles.badge, { backgroundColor: color }]}>
          <Text style={styles.badgeText}>{label}</Text>
        </View>
      </View>

      <View style={styles.scoreRow}>
        <Text style={[styles.score, { color }]}>{score}</Text>
        <Text style={styles.scoreOutOf}>/100</Text>
        <Text style={styles.blurb} numberOfLines={2}>
          {blurb}
        </Text>
      </View>

      <View style={styles.track}>
        {ZONES.map((c, i) => (
          <View key={i} style={[styles.zone, { backgroundColor: c }, i < ZONES.length - 1 && styles.zoneDivider]} />
        ))}
        <View style={[styles.marker, { left: `${score}%` }]} pointerEvents="none">
          <View style={styles.markerDot} />
        </View>
      </View>

      <View style={styles.scaleRow}>
        <Text style={styles.scaleText}>FEAR</Text>
        <Text style={styles.scaleText}>NEUTRAL</Text>
        <Text style={styles.scaleText}>GREED</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    borderWidth: BORDER,
    borderTopWidth: 0,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    padding: spacing.lg,
  },
  headerRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurfaceTertiary },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 3 },
  badgeText: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5, color: "#FFFFFF" },

  scoreRow: { flexDirection: "row", alignItems: "flex-end", marginTop: spacing.sm, gap: spacing.xs },
  score: { fontFamily: fonts.display, fontSize: 40, letterSpacing: -1 },
  scoreOutOf: { fontFamily: fonts.mono, fontSize: 13, color: colors.onSurfaceTertiary, marginBottom: 8 },
  blurb: { flex: 1, fontFamily: fonts.mono, fontSize: 10.5, lineHeight: 15, color: colors.onSurfaceTertiary, marginLeft: spacing.md, marginBottom: 4 },

  track: { flexDirection: "row", height: 14, marginTop: spacing.md, borderWidth: 1.5, borderColor: colors.borderStrong },
  zone: { flex: 1 },
  zoneDivider: { borderRightWidth: 1, borderRightColor: colors.surface },
  marker: { position: "absolute", top: -6, marginLeft: -7 },
  markerDot: { width: 14, height: 26, backgroundColor: colors.onSurface, borderWidth: 2, borderColor: colors.surface },

  scaleRow: { flexDirection: "row", justifyContent: "space-between", marginTop: spacing.xs },
  scaleText: { fontFamily: fonts.mono, fontSize: 8.5, letterSpacing: 0.5, color: colors.onSurfaceTertiary },
});
