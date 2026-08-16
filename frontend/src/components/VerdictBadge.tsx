import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, fonts, spacing, BORDER, verdictColors } from "@/src/theme";

// Big edge-to-edge verdict block for the Report screen.
export function VerdictBlock({
  decision,
  confidence,
}: {
  decision: string;
  confidence: number;
}) {
  const { bg, fg } = verdictColors(decision);
  return (
    <View testID="verdict-block" style={[styles.block, { backgroundColor: bg }]}>
      <Text style={[styles.blockLabel, { color: fg }]}>PORTFOLIO MANAGER VERDICT</Text>
      <Text style={[styles.blockDecision, { color: fg }]}>{decision.toUpperCase()}</Text>
      <View style={styles.confRow}>
        <Text style={[styles.confLabel, { color: fg }]}>CONFIDENCE</Text>
        <Text style={[styles.confValue, { color: fg }]}>{confidence}%</Text>
      </View>
      <View style={[styles.confTrack, { borderColor: fg }]}>
        <View style={[styles.confFill, { backgroundColor: fg, width: `${Math.max(2, Math.min(100, confidence))}%` }]} />
      </View>
    </View>
  );
}

// Small inline verdict tag for history rows / headers.
export function VerdictTag({ decision, small }: { decision: string; small?: boolean }) {
  const { bg, fg } = verdictColors(decision);
  return (
    <View testID={`verdict-tag-${decision}`} style={[styles.tag, { backgroundColor: bg }, small && styles.tagSmall]}>
      <Text style={[styles.tagText, { color: fg }, small && styles.tagTextSmall]}>{decision.toUpperCase()}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  block: {
    padding: spacing.xl,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
  },
  blockLabel: {
    fontFamily: fonts.monoMed,
    fontSize: 11,
    letterSpacing: 1,
  },
  blockDecision: {
    fontFamily: fonts.display,
    fontSize: 64,
    lineHeight: 68,
    letterSpacing: -1,
    marginTop: spacing.xs,
  },
  confRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-end",
    marginTop: spacing.md,
  },
  confLabel: { fontFamily: fonts.mono, fontSize: 12, letterSpacing: 1 },
  confValue: { fontFamily: fonts.monoBold, fontSize: 18 },
  confTrack: {
    height: 10,
    borderWidth: BORDER,
    marginTop: spacing.sm,
  },
  confFill: { height: "100%" },
  tag: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs + 2,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
  },
  tagSmall: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderWidth: 1.5 },
  tagText: { fontFamily: fonts.monoBold, fontSize: 14, letterSpacing: 1 },
  tagTextSmall: { fontSize: 11 },
});
