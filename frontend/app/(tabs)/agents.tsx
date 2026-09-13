import React from "react";
import { View, Text, ScrollView, Pressable, Linking, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  ChartLineUp,
  Scales,
  ChatCircle,
  Newspaper,
  TrendUp,
  TrendDown,
  Gavel,
  Lightning,
  ShieldWarning,
  Briefcase,
} from "phosphor-react-native";

import { colors, fonts, spacing, BORDER, accents } from "@/src/theme";
import { AGENT_ROSTER, PIPELINE_STEPS } from "@/src/agents";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { LanguagePicker } from "@/src/components/LanguagePicker";
import { WalletCard } from "@/src/components/WalletCard";
import { AccountCard } from "@/src/components/AccountCard";

const ICONS: Record<string, any> = {
  ChartLineUp,
  Scales,
  ChatCircle,
  Newspaper,
  TrendUp,
  TrendDown,
  Gavel,
  Lightning,
  ShieldWarning,
  Briefcase,
};

const TEAMS = ["ANALYST TEAM", "RESEARCH TEAM", "EXECUTION"];

const TEAM_COLORS: Record<string, string> = {
  "ANALYST TEAM": accents.blue,
  "RESEARCH TEAM": accents.violet,
  EXECUTION: accents.teal,
};

export default function AgentsScreen() {
  const insets = useSafeAreaInsets();

  return (
    <View style={styles.root}>
      <ScreenHeader title="THE DESK" subtitle="// 10 AI AGENTS · 1 VERDICT" insetsTop={insets.top} />

      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxl }}
        showsVerticalScrollIndicator={false}
      >
        {/* How it works */}
        <View style={styles.pipeline}>
          <Text style={styles.pipelineTitle}>ANALYSIS PIPELINE</Text>
          {PIPELINE_STEPS.map((s, i) => (
            <View key={i} style={styles.pipeRow}>
              <View style={styles.pipeNum}>
                <Text style={styles.pipeNumText}>{i + 1}</Text>
              </View>
              <Text style={styles.pipeText}>{s}</Text>
            </View>
          ))}
        </View>

        {TEAMS.map((team) => {
          const members = AGENT_ROSTER.filter((a) => a.team === team);
          return (
            <View key={team} style={styles.teamSection}>
              <Text style={styles.teamLabel}>{team}</Text>
              {members.map((a) => {
                const Icon = ICONS[a.icon] || Briefcase;
                return (
                  <View key={a.tag} testID={`agent-card-${a.tag}`} style={styles.agentCard}>
                    <View style={[styles.agentIcon, { backgroundColor: TEAM_COLORS[team] || accents.blue }]}>
                      <Icon size={22} color={colors.onSurfaceInverse} weight="bold" />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.agentName}>{a.name}</Text>
                      <Text style={styles.agentBlurb}>{a.blurb}</Text>
                    </View>
                  </View>
                );
              })}
            </View>
          );
        })}

        <LanguagePicker />

        <WalletCard />

        <AccountCard />

        <View style={styles.disclaimer}>
          <Text style={styles.disclaimerText}>
            [ DISCLAIMER ] TradingAgents is an AI research tool. Outputs are model-generated and NOT financial,
            investment or trading advice. Do your own research.
          </Text>
        </View>

        <View style={styles.credit}>
          <Text style={styles.creditLabel}>BASED ON THE RESEARCH</Text>
          <Text style={styles.creditText}>
            This app&apos;s multi-agent debate architecture (analysts → bull/bear debate → risk manager → trader →
            risk team → portfolio manager) is built on the framework introduced in:
          </Text>
          <Text style={styles.creditCitation}>
            Yijia Xiao, Edward Sun, Di Luo, and Wei Wang.{"\n"}
            &quot;TradingAgents: Multi-Agents LLM Financial Trading Framework.&quot;{"\n"}
            arXiv:2412.20138 (2025).
          </Text>
          <Pressable onPress={() => Linking.openURL("https://arxiv.org/abs/2412.20138")} hitSlop={8}>
            <Text style={styles.creditLink}>arxiv.org/abs/2412.20138 ↗</Text>
          </Pressable>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: BORDER,
    borderBottomColor: colors.borderStrong,
  },
  brand: { fontFamily: fonts.display, fontSize: 28, color: colors.onSurface, letterSpacing: -1 },
  tagline: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2, letterSpacing: 1 },

  pipeline: {
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surfaceInverse,
    padding: spacing.lg,
  },
  pipelineTitle: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1.5, color: colors.onSurfaceInverse, marginBottom: spacing.md },
  pipeRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, marginBottom: spacing.sm },
  pipeNum: { width: 24, height: 24, backgroundColor: colors.onSurfaceInverse, alignItems: "center", justifyContent: "center" },
  pipeNumText: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.surfaceInverse },
  pipeText: { flex: 1, fontFamily: fonts.mono, fontSize: 12.5, color: colors.onSurfaceInverse, lineHeight: 18 },

  teamSection: { marginTop: spacing.xl },
  teamLabel: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurface, marginBottom: spacing.md },
  agentCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  agentIcon: {
    width: 44,
    height: 44,
    backgroundColor: colors.surfaceInverse,
    alignItems: "center",
    justifyContent: "center",
  },
  agentName: { fontFamily: fonts.displayMed, fontSize: 15, color: colors.onSurface },
  agentBlurb: { fontFamily: fonts.mono, fontSize: 11.5, lineHeight: 17, color: colors.onSurfaceTertiary, marginTop: 3 },

  disclaimer: { marginTop: spacing.xl, borderWidth: 1.5, borderColor: colors.border, padding: spacing.md, backgroundColor: colors.surfaceSecondary },
  disclaimerText: { fontFamily: fonts.mono, fontSize: 10.5, lineHeight: 16, color: colors.onSurfaceTertiary },
  credit: { marginTop: spacing.md, borderWidth: 1, borderColor: colors.border, padding: spacing.md, backgroundColor: colors.surface },
  creditLabel: { fontFamily: fonts.monoBold, fontSize: 9.5, letterSpacing: 1, color: colors.onSurfaceTertiary, marginBottom: spacing.xs },
  creditText: { fontFamily: fonts.displayReg, fontSize: 11, lineHeight: 16, color: colors.onSurfaceTertiary, marginBottom: spacing.sm },
  creditCitation: { fontFamily: fonts.mono, fontSize: 10.5, lineHeight: 16, color: colors.onSurface, marginBottom: spacing.sm },
  creditLink: { fontFamily: fonts.monoBold, fontSize: 10.5, color: colors.brand },
});
