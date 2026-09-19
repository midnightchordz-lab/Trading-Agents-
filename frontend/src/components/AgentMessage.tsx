import React from "react";
import { View, Text, StyleSheet } from "react-native";
import Animated, { FadeInDown } from "react-native-reanimated";
import { colors, fonts, spacing, BORDER, sentimentColor, PHASE_COLORS } from "@/src/theme";
import { AgentMessageT } from "@/src/api";
import { tagLabel } from "@/src/agents";

function sentimentLabel(s?: string | null): string | null {
  if (s === "bullish") return "BULLISH";
  if (s === "bearish") return "BEARISH";
  if (s === "neutral") return "NEUTRAL";
  return null;
}

export function AgentMessage({
  message,
  index = 0,
  animate = true,
}: {
  message: AgentMessageT;
  index?: number;
  animate?: boolean;
}) {
  const sColor = sentimentColor(message.sentiment as any);
  const sLabel = sentimentLabel(message.sentiment);
  const isDecision = message.phase === "decision";
  const accent = message.sentiment ? sColor : PHASE_COLORS[message.phase] || colors.borderStrong;

  const body = (
    <View
      testID={`agent-message-${message.tag}`}
      style={[styles.card, { borderStartWidth: 5, borderStartColor: accent }, isDecision && styles.decisionCard]}
    >
      <View style={styles.headerRow}>
        <View style={styles.tagBox}>
          <Text style={styles.tagText}>[ {tagLabel(message.tag)} ]</Text>
        </View>
        {sLabel ? (
          <View style={styles.signalWrap}>
            <View style={[styles.dot, { backgroundColor: sColor }]} />
            <Text style={[styles.signalText, { color: sColor }]}>{sLabel}</Text>
          </View>
        ) : null}
      </View>
      <Text style={styles.agentName}>{message.agent}</Text>
      <Text style={styles.content}>{message.content}</Text>
    </View>
  );

  if (!animate) return body;
  return (
    <Animated.View entering={FadeInDown.duration(320).delay(Math.min(index * 40, 240))}>
      {body}
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  decisionCard: {
    backgroundColor: colors.surfaceSecondary,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  tagBox: {
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  tagText: {
    fontFamily: fonts.monoBold,
    fontSize: 10,
    letterSpacing: 0.5,
    color: colors.onSurfaceInverse,
  },
  signalWrap: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  dot: { width: 8, height: 8 },
  signalText: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5 },
  agentName: {
    fontFamily: fonts.displayMed,
    fontSize: 15,
    color: colors.onSurface,
    marginTop: spacing.sm,
    marginBottom: spacing.xs,
  },
  content: {
    fontFamily: fonts.mono,
    fontSize: 12.5,
    lineHeight: 19,
    color: colors.onSurfaceTertiary,
  },
});
