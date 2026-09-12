import React, { useState } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import type { Grounding } from "@/src/api";

// Shows whether the agents' price levels survived the grounding gate —
// i.e. whether they are consistent with the live quote the app observed.
// Tapping expands the individual checks.

const STATUS: Record<Grounding["status"], { label: string; bg: string; fg: string; blurb: string }> = {
  grounded: { label: "LEVELS VERIFIED", bg: colors.success, fg: colors.onSuccess, blurb: "Target and stop are consistent with the live quote." },
  warning: { label: "LEVELS · CHECK", bg: colors.warning, fg: colors.onWarning, blurb: "Levels are usable but at least one check raised a flag." },
  failed: { label: "LEVELS REJECTED", bg: colors.error, fg: colors.onError, blurb: "Levels contradict the live quote — do not trade off these numbers." },
  unverified: { label: "LEVELS UNVERIFIED", bg: colors.surfaceTertiary, fg: colors.onSurface, blurb: "No live quote was available, so levels could not be checked." },
};

export function GroundingBadge({ grounding }: { grounding: Grounding }) {
  const [open, setOpen] = useState(false);
  const meta = STATUS[grounding.status] ?? STATUS.unverified;
  const flagged = grounding.checks.filter((c) => !c.ok);

  return (
    <View testID="grounding-badge" style={styles.wrap}>
      <Pressable onPress={() => setOpen((v) => !v)} style={[styles.row, { backgroundColor: meta.bg }]}>
        <Text style={[styles.label, { color: meta.fg }]}>{meta.label}</Text>
        <Text style={[styles.count, { color: meta.fg }]}>
          {flagged.length ? `${flagged.length} FLAG${flagged.length > 1 ? "S" : ""}` : `${grounding.checks.length} CHECKS`} {open ? "▲" : "▼"}
        </Text>
      </Pressable>
      <Text style={styles.blurb}>{meta.blurb}</Text>
      {open ? (
        <View style={styles.list}>
          {grounding.checks.map((c) => (
            <View key={c.id} style={styles.item}>
              <Text style={[styles.mark, { color: c.ok ? colors.success : c.severity === "fail" ? colors.error : colors.warning }]}>
                {c.ok ? "✓" : c.severity === "fail" ? "✕" : "!"}
              </Text>
              <Text style={styles.msg}>{c.message}</Text>
            </View>
          ))}
          {grounding.evidence ? (
            <Text style={styles.evidence}>
              Evidence: price {grounding.evidence.price}
              {grounding.evidence.fiftyTwoWeekLow != null && grounding.evidence.fiftyTwoWeekHigh != null
                ? ` · 52w ${grounding.evidence.fiftyTwoWeekLow}–${grounding.evidence.fiftyTwoWeekHigh}`
                : ""}
            </Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, marginTop: spacing.md },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  label: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1 },
  count: { fontFamily: fonts.mono, fontSize: 10 },
  blurb: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  list: { borderTopWidth: 1, borderTopColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, gap: spacing.xs },
  item: { flexDirection: "row", gap: spacing.sm, alignItems: "flex-start" },
  mark: { fontFamily: fonts.monoBold, fontSize: 12, width: 14 },
  msg: { flex: 1, fontFamily: fonts.mono, fontSize: 11, color: colors.onSurface },
  evidence: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, marginTop: spacing.xs },
});
