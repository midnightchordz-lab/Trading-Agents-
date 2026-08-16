import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { View, Text, Pressable, ScrollView, ActivityIndicator, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as Haptics from "expo-haptics";
import { CaretLeft, CaretDown, CaretRight, Warning } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api, Analysis, AgentMessageT } from "@/src/api";
import { QuoteCard } from "@/src/components/QuoteCard";
import { AgentMessage } from "@/src/components/AgentMessage";
import { VerdictBlock } from "@/src/components/VerdictBadge";
import { PHASE_LABEL, PHASE_ORDER, PhaseKey } from "@/src/agents";

type Tab = "debate" | "verdict";

function money(n: number | null, currency?: string): string {
  if (n == null) return "—";
  const prefix = currency && currency !== "USD" ? "" : "$";
  return prefix + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export default function AnalysisScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [tab, setTab] = useState<Tab>("debate");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ decision: true });
  const [blink, setBlink] = useState(true);

  const scrollRef = useRef<ScrollView>(null);
  const prevCount = useRef(0);
  const prevStatus = useRef<string | undefined>(undefined);
  const initedTab = useRef(false);

  // Poll analysis until it stops running.
  useEffect(() => {
    if (!id) return;
    let interval: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;
    const poll = async () => {
      try {
        const a = await api.getAnalysis(id);
        if (cancelled) return;
        setAnalysis(a);
        if (a.status !== "running" && interval) {
          clearInterval(interval);
          interval = null;
        }
      } catch {
        // keep trying
      }
    };
    poll();
    interval = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [id]);

  // Blinking terminal cursor.
  useEffect(() => {
    const t = setInterval(() => setBlink((b) => !b), 480);
    return () => clearInterval(t);
  }, []);

  // Initial tab + reactions to new messages / completion.
  useEffect(() => {
    if (!analysis) return;
    if (!initedTab.current) {
      initedTab.current = true;
      setTab(analysis.status === "completed" ? "verdict" : "debate");
    }
    const count = analysis.messages.length;
    if (count > prevCount.current && prevCount.current > 0) {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      if (tab === "debate") {
        requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
      }
    }
    prevCount.current = count;
    if (prevStatus.current === "running" && analysis.status === "completed") {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      setTab("verdict");
    }
    prevStatus.current = analysis.status;
  }, [analysis, tab]);

  const grouped = useMemo(() => {
    const map: Record<string, AgentMessageT[]> = {};
    (analysis?.messages || []).forEach((m) => {
      (map[m.phase] = map[m.phase] || []).push(m);
    });
    return map;
  }, [analysis]);

  const toggle = useCallback((phase: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setExpanded((e) => ({ ...e, [phase]: !e[phase] }));
  }, []);

  if (!analysis) {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={colors.onSurface} />
        <Text style={styles.loadingText}>LOADING DESK…</Text>
      </View>
    );
  }

  const running = analysis.status === "running";
  const currentAgent = analysis.messages[analysis.messages.length - 1]?.agent;

  return (
    <View style={styles.root}>
      {/* Header */}
      <View style={[styles.header, { paddingTop: insets.top + spacing.sm }]}>
        <View style={styles.headerRow}>
          <Pressable testID="back-button" onPress={() => router.back()} hitSlop={12} style={styles.backBtn}>
            <CaretLeft size={22} color={colors.onSurface} weight="bold" />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={styles.headerSymbol}>{analysis.symbol}</Text>
            <Text style={styles.headerName} numberOfLines={1}>
              {analysis.name}
            </Text>
          </View>
          <View style={[styles.statusPill, running ? styles.statusRunning : analysis.status === "error" ? styles.statusError : styles.statusDone]}>
            <Text style={[styles.statusText, { color: running || analysis.status === "error" ? colors.onSurface : colors.onSurfaceInverse }]}>
              {running ? `${analysis.current_step}/${analysis.total_steps}` : analysis.status === "error" ? "FAILED" : "DONE"}
            </Text>
          </View>
        </View>
      </View>

      {/* Segmented control */}
      <View style={styles.segment}>
        {(["debate", "verdict"] as Tab[]).map((t, i) => {
          const active = tab === t;
          return (
            <Pressable
              key={t}
              testID={`tab-segment-${t}`}
              onPress={() => setTab(t)}
              style={[styles.segBtn, i === 0 && styles.segDivider, active && styles.segActive]}
            >
              <Text style={[styles.segText, { color: active ? colors.onSurfaceInverse : colors.onSurface }]}>
                {t === "debate" ? "LIVE DEBATE" : "VERDICT"}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <ScrollView
        ref={scrollRef}
        style={styles.scroll}
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: insets.bottom + spacing.xxl }}
        showsVerticalScrollIndicator={false}
      >
        {analysis.quote ? (
          <View style={{ marginBottom: spacing.lg }}>
            <QuoteCard quote={analysis.quote} />
          </View>
        ) : null}

        {analysis.status === "error" ? (
          <View style={styles.errorBox}>
            <Warning size={28} color={colors.onError} weight="bold" />
            <Text style={styles.errorTitle}>ANALYSIS FAILED</Text>
            <Text style={styles.errorBody}>The desk could not complete this run. Head back and try again.</Text>
          </View>
        ) : tab === "debate" ? (
          <DebateView analysis={analysis} running={running} blink={blink} currentAgent={currentAgent} />
        ) : (
          <VerdictView analysis={analysis} grouped={grouped} expanded={expanded} toggle={toggle} running={running} />
        )}
      </ScrollView>
    </View>
  );
}

function DebateView({
  analysis,
  running,
  blink,
  currentAgent,
}: {
  analysis: Analysis;
  running: boolean;
  blink: boolean;
  currentAgent?: string;
}) {
  let lastPhase: string | null = null;
  return (
    <View>
      {analysis.messages.length === 0 && running ? (
        <View style={styles.awaiting}>
          <Text style={styles.awaitingText}>[ AWAITING_INPUT{blink ? " _" : ""} ]</Text>
          <Text style={styles.awaitingSub}>Spinning up the analyst team…</Text>
        </View>
      ) : null}

      {analysis.messages.map((m, i) => {
        const showDivider = m.phase !== lastPhase;
        lastPhase = m.phase;
        return (
          <View key={m.id}>
            {showDivider ? (
              <View style={styles.phaseDivider}>
                <View style={styles.phaseLine} />
                <Text style={styles.phaseText}>{PHASE_LABEL[m.phase as PhaseKey]}</Text>
                <View style={styles.phaseLine} />
              </View>
            ) : null}
            <AgentMessage message={m} index={i} />
          </View>
        );
      })}

      {running ? (
        <View style={styles.thinkingBox}>
          <ActivityIndicator size="small" color={colors.onSurface} />
          <Text style={styles.thinkingText}>
            STEP {analysis.current_step}/{analysis.total_steps} · {currentAgent ? `${currentAgent.toUpperCase()} → ` : ""}THINKING{blink ? "█" : " "}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

function VerdictView({
  analysis,
  grouped,
  expanded,
  toggle,
  running,
}: {
  analysis: Analysis;
  grouped: Record<string, AgentMessageT[]>;
  expanded: Record<string, boolean>;
  toggle: (p: string) => void;
  running: boolean;
}) {
  const verdict = analysis.verdict;
  const currency = analysis.quote?.currency;

  if (!verdict) {
    return (
      <View style={styles.awaiting}>
        <Text style={styles.awaitingText}>[ AWAITING_VERDICT ]</Text>
        <Text style={styles.awaitingSub}>
          {running ? "The committee is still deliberating. Watch it live in the DEBATE tab." : "No verdict recorded."}
        </Text>
      </View>
    );
  }

  return (
    <View>
      <VerdictBlock decision={verdict.decision} confidence={verdict.confidence} />

      <View style={styles.statsGrid}>
        <View style={styles.statCell}>
          <Text style={styles.statLabel}>TARGET</Text>
          <Text style={styles.statValue}>{money(verdict.target_price, currency)}</Text>
        </View>
        <View style={[styles.statCell, styles.statCellMid]}>
          <Text style={styles.statLabel}>STOP-LOSS</Text>
          <Text style={styles.statValue}>{money(verdict.stop_loss, currency)}</Text>
        </View>
        <View style={styles.statCell}>
          <Text style={styles.statLabel}>HORIZON</Text>
          <Text style={styles.statValue} numberOfLines={1}>
            {verdict.time_horizon}
          </Text>
        </View>
      </View>

      <View style={styles.summaryBox}>
        <Text style={styles.summaryLabel}>THESIS</Text>
        <Text style={styles.summaryText}>{verdict.summary}</Text>
      </View>

      {verdict.key_risks?.length ? (
        <View style={styles.risksBox}>
          <Text style={styles.summaryLabel}>KEY RISKS</Text>
          {verdict.key_risks.map((r, i) => (
            <View key={i} style={styles.riskRow}>
              <Text style={styles.riskBullet}>▸</Text>
              <Text style={styles.riskText}>{r}</Text>
            </View>
          ))}
        </View>
      ) : null}

      <Text style={styles.transcriptLabel}>FULL TRANSCRIPT</Text>
      {PHASE_ORDER.map((phase) => {
        const msgs = grouped[phase];
        if (!msgs || msgs.length === 0) return null;
        const open = !!expanded[phase];
        return (
          <View key={phase} style={styles.accordion}>
            <Pressable testID={`accordion-${phase}`} onPress={() => toggle(phase)} style={[styles.accHeader, open && styles.accHeaderOpen]}>
              <Text style={[styles.accTitle, open && { color: colors.onSurfaceInverse }]}>{PHASE_LABEL[phase]}</Text>
              <View style={styles.accRight}>
                <Text style={[styles.accCount, open && { color: colors.onSurfaceInverse }]}>{msgs.length}</Text>
                {open ? (
                  <CaretDown size={16} color={colors.onSurfaceInverse} weight="bold" />
                ) : (
                  <CaretRight size={16} color={colors.onSurface} weight="bold" />
                )}
              </View>
            </Pressable>
            {open ? (
              <View style={styles.accBody}>
                {msgs.map((m) => (
                  <AgentMessage key={m.id} message={m} animate={false} />
                ))}
              </View>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { alignItems: "center", justifyContent: "center", gap: spacing.md },
  loadingText: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary, letterSpacing: 1 },

  header: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: BORDER,
    borderBottomColor: colors.borderStrong,
  },
  headerRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  backBtn: { width: 34, height: 34, borderWidth: BORDER, borderColor: colors.borderStrong, alignItems: "center", justifyContent: "center" },
  headerSymbol: { fontFamily: fonts.display, fontSize: 22, color: colors.onSurface, letterSpacing: -0.5 },
  headerName: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.onSurfaceTertiary, marginTop: 1 },
  statusPill: { paddingHorizontal: spacing.sm, paddingVertical: 5, borderWidth: 1.5, borderColor: colors.borderStrong },
  statusRunning: { backgroundColor: colors.surfaceSecondary },
  statusDone: { backgroundColor: colors.success, borderColor: colors.success },
  statusError: { backgroundColor: colors.warning },
  statusText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 0.5 },

  segment: { flexDirection: "row", borderBottomWidth: BORDER, borderBottomColor: colors.borderStrong },
  segBtn: { flex: 1, height: 44, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  segDivider: { borderRightWidth: BORDER, borderRightColor: colors.borderStrong },
  segActive: { backgroundColor: colors.surfaceInverse },
  segText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1 },

  scroll: { flex: 1 },

  awaiting: { paddingVertical: spacing.xxl, alignItems: "center", gap: spacing.sm },
  awaitingText: { fontFamily: fonts.monoBold, fontSize: 16, letterSpacing: 2, color: colors.onSurface },
  awaitingSub: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary },

  phaseDivider: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginBottom: spacing.md, marginTop: spacing.sm },
  phaseLine: { flex: 1, height: BORDER, backgroundColor: colors.borderStrong },
  phaseText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurface },

  thinkingBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    borderStyle: "dashed",
    padding: spacing.md,
    marginTop: spacing.xs,
  },
  thinkingText: { flex: 1, fontFamily: fonts.monoMed, fontSize: 11, color: colors.onSurface, letterSpacing: 0.5 },

  statsGrid: { flexDirection: "row", borderWidth: BORDER, borderTopWidth: 0, borderColor: colors.borderStrong },
  statCell: { flex: 1, padding: spacing.md },
  statCellMid: { borderLeftWidth: BORDER, borderRightWidth: BORDER, borderColor: colors.borderStrong },
  statLabel: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceTertiary, letterSpacing: 0.5 },
  statValue: { fontFamily: fonts.monoBold, fontSize: 14, color: colors.onSurface, marginTop: 4 },

  summaryBox: { borderWidth: BORDER, borderTopWidth: 0, borderColor: colors.borderStrong, padding: spacing.lg },
  summaryLabel: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 1.5, color: colors.onSurfaceTertiary, marginBottom: spacing.sm },
  summaryText: { fontFamily: fonts.mono, fontSize: 13, lineHeight: 20, color: colors.onSurface },

  risksBox: { borderWidth: BORDER, borderTopWidth: 0, borderColor: colors.borderStrong, padding: spacing.lg },
  riskRow: { flexDirection: "row", gap: spacing.sm, marginBottom: spacing.xs },
  riskBullet: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.error },
  riskText: { flex: 1, fontFamily: fonts.mono, fontSize: 12.5, lineHeight: 19, color: colors.onSurfaceTertiary },

  transcriptLabel: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurface, marginTop: spacing.xl, marginBottom: spacing.md },
  accordion: { marginBottom: spacing.md, borderWidth: BORDER, borderColor: colors.borderStrong },
  accHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md, backgroundColor: colors.surface },
  accHeaderOpen: { backgroundColor: colors.surfaceInverse },
  accTitle: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurface },
  accRight: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  accCount: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary },
  accBody: { padding: spacing.md, borderTopWidth: 1.5, borderTopColor: colors.border },

  errorBox: { borderWidth: BORDER, borderColor: colors.error, backgroundColor: colors.error, padding: spacing.xl, alignItems: "center", gap: spacing.sm },
  errorTitle: { fontFamily: fonts.display, fontSize: 22, color: colors.onError },
  errorBody: { fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.onError, textAlign: "center" },
});
