import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { View, Text, Pressable, ScrollView, ActivityIndicator, StyleSheet, Modal } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as Haptics from "expo-haptics";
import * as Sharing from "expo-sharing";
import ViewShot from "react-native-view-shot";
import { CaretDown, CaretRight, Warning, Star, ShareNetwork } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api, Analysis, AgentMessageT, Verdict, Quote } from "@/src/api";
import { QuoteCard } from "@/src/components/QuoteCard";
import { AgentMessage } from "@/src/components/AgentMessage";
import { VerdictBlock } from "@/src/components/VerdictBadge";
import { ShareCard } from "@/src/components/ShareCard";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { TradingViewChart } from "@/src/components/TradingViewChart";
import { VerdictLevels } from "@/src/components/VerdictLevels";
import { FearGreedGauge } from "@/src/components/FearGreedGauge";
import { GroundingBadge } from "@/src/components/GroundingBadge";
import { NewsList } from "@/src/components/NewsList";
import { rangeToInterval, widgetSupports } from "@/src/tv";
import { PHASE_LABEL, PHASE_ORDER, PhaseKey } from "@/src/agents";
import { useWatchlist } from "@/src/watchlist";
import { useAlerts } from "@/src/alerts";

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
  const { isSaved, toggle: toggleWatch } = useWatchlist();
  const { evaluate } = useAlerts();

  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [tab, setTab] = useState<Tab>("debate");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ decision: true });
  const [blink, setBlink] = useState(true);
  const [shareOpen, setShareOpen] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [shareErr, setShareErr] = useState<string | null>(null);

  const scrollRef = useRef<ScrollView>(null);
  const shotRef = useRef<ViewShot>(null);
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

  // Evaluate standing price alerts whenever a fresh live price arrives.
  useEffect(() => {
    if (analysis?.symbol && analysis.quote?.price != null) {
      evaluate(analysis.symbol, analysis.quote.price);
    }
  }, [analysis?.symbol, analysis?.quote?.price, evaluate]);

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

  const onShare = useCallback(() => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setShareErr(null);
    setShareOpen(true);
  }, []);

  const doShare = useCallback(async () => {
    try {
      setSharing(true);
      setShareErr(null);
      const uri = await shotRef.current?.capture?.();
      if (!uri) throw new Error("capture failed");
      const available = await Sharing.isAvailableAsync();
      if (available) {
        await Sharing.shareAsync(uri, { mimeType: "image/png", dialogTitle: `${analysis?.symbol} verdict` });
      } else {
        setShareErr("Sharing isn't available here — open the app on your phone to share.");
      }
    } catch {
      setShareErr("Couldn't generate the image. Please try again.");
    } finally {
      setSharing(false);
    }
  }, [analysis]);

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
      <ScreenHeader
        title={analysis.symbol}
        subtitle={analysis.name}
        insetsTop={insets.top}
        onBack={() => router.back()}
        right={
          <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
            <Pressable
              testID="analysis-watch-toggle"
              onPress={() => toggleWatch({ symbol: analysis.symbol, name: analysis.name })}
              hitSlop={8}
              style={[styles.watchStar, isSaved(analysis.symbol) && styles.watchStarActive]}
            >
              <Star
                size={18}
                color={isSaved(analysis.symbol) ? colors.onSurface : "#FFFFFF"}
                weight={isSaved(analysis.symbol) ? "fill" : "regular"}
              />
            </Pressable>
            <View style={[styles.statusPill, running ? styles.statusRunning : analysis.status === "error" ? styles.statusError : styles.statusDone]}>
              <Text style={[styles.statusText, { color: running || analysis.status === "error" ? colors.onSurface : colors.onSurfaceInverse }]}>
                {running ? `${analysis.current_step}/${analysis.total_steps}` : analysis.status === "error" ? "FAILED" : "DONE"}
              </Text>
            </View>
          </View>
        }
      />

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
            <QuoteCard quote={analysis.quote} showRanges />
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
          <VerdictView analysis={analysis} grouped={grouped} expanded={expanded} toggle={toggle} running={running} onShare={onShare} />
        )}
      </ScrollView>

      <Modal visible={shareOpen} transparent animationType="fade" onRequestClose={() => setShareOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalInner}>
            <ViewShot ref={shotRef} options={{ format: "png", quality: 1 }} style={styles.shotWrap}>
              {analysis.verdict ? (
                <ShareCard symbol={analysis.symbol} name={analysis.name} verdict={analysis.verdict} quote={analysis.quote} />
              ) : null}
            </ViewShot>
            {shareErr ? <Text style={styles.shareErr}>{shareErr}</Text> : null}
            <View style={styles.modalBtns}>
              <Pressable testID="share-close-button" onPress={() => setShareOpen(false)} style={[styles.modalBtn, styles.modalBtnGhost]}>
                <Text style={styles.modalBtnGhostText}>CLOSE</Text>
              </Pressable>
              <Pressable testID="share-image-button" onPress={doShare} disabled={sharing} style={[styles.modalBtn, styles.modalBtnPrimary]}>
                {sharing ? (
                  <ActivityIndicator color={colors.onSurface} />
                ) : (
                  <>
                    <ShareNetwork size={18} color={colors.onSurface} weight="bold" />
                    <Text style={styles.modalBtnPrimaryText}>SHARE IMAGE</Text>
                  </>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
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
  onShare,
}: {
  analysis: Analysis;
  grouped: Record<string, AgentMessageT[]>;
  expanded: Record<string, boolean>;
  toggle: (p: string) => void;
  running: boolean;
  onShare: () => void;
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

      {analysis.grounding ? <GroundingBadge grounding={analysis.grounding} /> : null}

      <FearGreedGauge analysis={analysis} />

      <TvSection
        symbol={analysis.symbol}
        name={analysis.name}
        exchange={analysis.quote?.exchange}
        verdict={verdict}
        chartLevels={analysis.grounding?.status === "failed" ? null : analysis.verdict}
        quote={analysis.quote}
      />

      <NewsList symbol={analysis.symbol} />

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

      <Pressable testID="open-share-button" onPress={onShare} style={styles.shareVerdictBtn}>
        <ShareNetwork size={18} color={colors.onSurfaceInverse} weight="bold" />
        <Text style={styles.shareVerdictText}>SHARE THIS VERDICT</Text>
      </Pressable>

      {analysis.debate ? (
        <View>
          <Text style={styles.transcriptLabel}>ROUND TABLE DEBATE</Text>
          <DebateArg tag="BULL" color={colors.success} text={analysis.debate.bull} />
          <DebateArg tag="BEAR" color={colors.error} text={analysis.debate.bear} />
          <DebateArg tag="FUNDAMENTALS" color={colors.info} text={analysis.debate.fundamentals} />

          <View style={styles.debateVerdict}>
            <Text style={styles.debateVerdictLabel}>DEBATE VERDICT</Text>
            {analysis.debate.agreements.length ? (
              <>
                <Text style={styles.debateSub}>WHERE THEY AGREE</Text>
                {analysis.debate.agreements.map((a, i) => (
                  <View key={`ag-${i}`} style={styles.debateBulletRow}>
                    <Text style={[styles.debateBullet, { color: colors.success }]}>+</Text>
                    <Text style={styles.debateBulletText}>{a}</Text>
                  </View>
                ))}
              </>
            ) : null}
            {analysis.debate.disagreements.length ? (
              <>
                <Text style={[styles.debateSub, { marginTop: spacing.md }]}>WHERE THEY CLASH</Text>
                {analysis.debate.disagreements.map((a, i) => (
                  <View key={`dis-${i}`} style={styles.debateBulletRow}>
                    <Text style={[styles.debateBullet, { color: colors.error }]}>×</Text>
                    <Text style={styles.debateBulletText}>{a}</Text>
                  </View>
                ))}
              </>
            ) : null}
            <View style={styles.debateRec}>
              <Text style={styles.debateRecLabel}>FINAL WORD</Text>
              <Text style={styles.debateRecText}>{analysis.debate.recommendation}</Text>
            </View>
          </View>
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

function TvSection({
  symbol,
  name,
  exchange,
  verdict,
  chartLevels,
  quote,
}: {
  symbol: string;
  name: string;
  exchange?: string;
  verdict: Verdict;
  chartLevels: Verdict | null;
  quote?: Quote | null;
}) {
  const [range, setRange] = useState<string>("1M");
  const ranges = ["1D", "1W", "1M", "1Y"];
  const widgetChart = widgetSupports(symbol);
  return (
    <View style={styles.tvSection}>
      {widgetChart ? (
        <View style={styles.rangeBar}>
          {ranges.map((r, i) => {
            const active = range === r;
            return (
              <Pressable
                key={r}
                testID={`tv-range-${r}`}
                onPress={() => {
                  Haptics.selectionAsync();
                  setRange(r);
                }}
                style={[styles.rangeBtn, i > 0 && styles.rangeDivider, active && styles.rangeActive]}
              >
                <Text style={[styles.rangeText, { color: active ? colors.onSurfaceInverse : colors.onSurface }]}>{r}</Text>
              </Pressable>
            );
          })}
        </View>
      ) : null}
      <TradingViewChart
        symbol={symbol}
        exchange={exchange}
        interval={rangeToInterval(range)}
        theme="light"
        height={widgetChart ? 360 : 460}
        levels={chartLevels}
        livePrice={quote?.price ?? null}
      />
      <VerdictLevels verdict={verdict} quote={quote} symbol={symbol} name={name} />
    </View>
  );
}

function DebateArg({ tag, color, text }: { tag: string; color: string; text: string }) {
  return (
    <View style={[styles.debateArg, { borderLeftWidth: 6, borderLeftColor: color }]}>
      <View style={styles.debateArgHead}>
        <View style={[styles.debateDot, { backgroundColor: color }]} />
        <Text style={styles.debateArgTag}>[ {tag} ]</Text>
      </View>
      <Text style={styles.debateArgText}>{text}</Text>
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
  watchStar: { width: 34, height: 34, borderWidth: BORDER, borderColor: "#FFFFFF", alignItems: "center", justifyContent: "center" },
  watchStarActive: { backgroundColor: "#FFFFFF", borderColor: "#FFFFFF" },
  statusRunning: { backgroundColor: colors.surfaceSecondary },
  statusDone: { backgroundColor: colors.success, borderColor: colors.success },
  statusError: { backgroundColor: colors.warning },
  statusText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 0.5 },

  segment: { flexDirection: "row", borderBottomWidth: BORDER, borderBottomColor: colors.borderStrong },
  segBtn: { flex: 1, height: 44, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  segDivider: { borderRightWidth: BORDER, borderRightColor: colors.borderStrong },
  segActive: { backgroundColor: colors.brand },
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

  tvSection: { marginTop: spacing.lg },
  rangeBar: {
    flexDirection: "row",
    borderWidth: BORDER,
    borderBottomWidth: 0,
    borderColor: colors.borderStrong,
  },
  rangeBtn: { flex: 1, height: 38, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  rangeDivider: { borderLeftWidth: 1.5, borderLeftColor: colors.border },
  rangeActive: { backgroundColor: colors.brand },
  rangeText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1 },


  summaryBox: { borderWidth: BORDER, borderTopWidth: 0, borderColor: colors.borderStrong, padding: spacing.lg },
  summaryLabel: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 1.5, color: colors.onSurfaceTertiary, marginBottom: spacing.sm },
  summaryText: { fontFamily: fonts.mono, fontSize: 13, lineHeight: 20, color: colors.onSurface },

  risksBox: { borderWidth: BORDER, borderTopWidth: 0, borderColor: colors.borderStrong, padding: spacing.lg },
  riskRow: { flexDirection: "row", gap: spacing.sm, marginBottom: spacing.xs },
  riskBullet: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.error },
  riskText: { flex: 1, fontFamily: fonts.mono, fontSize: 12.5, lineHeight: 19, color: colors.onSurfaceTertiary },

  transcriptLabel: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurface, marginTop: spacing.xl, marginBottom: spacing.md },

  debateArg: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, padding: spacing.md, marginBottom: spacing.md },
  debateArgHead: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginBottom: spacing.xs },
  debateDot: { width: 8, height: 8 },
  debateArgTag: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 0.5, color: colors.onSurface },
  debateArgText: { fontFamily: fonts.mono, fontSize: 12.5, lineHeight: 19, color: colors.onSurfaceTertiary },
  debateVerdict: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surfaceSecondary, padding: spacing.lg, marginBottom: spacing.md },
  debateVerdictLabel: { fontFamily: fonts.display, fontSize: 18, color: colors.onSurface, marginBottom: spacing.sm },
  debateSub: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 1, color: colors.onSurfaceTertiary, marginBottom: spacing.xs },
  debateBulletRow: { flexDirection: "row", gap: spacing.sm, marginBottom: 2 },
  debateBullet: { fontFamily: fonts.monoBold, fontSize: 13 },
  debateBulletText: { flex: 1, fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.onSurface },
  debateRec: { marginTop: spacing.md, borderTopWidth: 1.5, borderTopColor: colors.border, paddingTop: spacing.md },
  debateRecLabel: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 1, color: colors.onSurfaceTertiary, marginBottom: spacing.xs },
  debateRecText: { fontFamily: fonts.mono, fontSize: 13, lineHeight: 20, color: colors.onSurface },
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

  shareVerdictBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    height: 54,
    backgroundColor: colors.surfaceInverse,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    marginTop: spacing.lg,
  },
  shareVerdictText: { fontFamily: fonts.monoBold, fontSize: 14, letterSpacing: 1, color: colors.onSurfaceInverse },

  modalBackdrop: { flex: 1, backgroundColor: "rgba(9,9,11,0.9)", alignItems: "center", justifyContent: "center", padding: spacing.lg },
  modalInner: { width: "100%", alignItems: "center", gap: spacing.lg },
  shotWrap: { backgroundColor: colors.surface },
  shareErr: { fontFamily: fonts.mono, fontSize: 11, lineHeight: 16, color: colors.onSurfaceInverse, textAlign: "center", paddingHorizontal: spacing.lg },
  modalBtns: { flexDirection: "row", gap: spacing.md, width: 330 },
  modalBtn: { flex: 1, height: 52, alignItems: "center", justifyContent: "center", flexDirection: "row", gap: spacing.sm },
  modalBtnGhost: { borderWidth: BORDER, borderColor: colors.onSurfaceInverse, backgroundColor: "transparent" },
  modalBtnGhostText: { fontFamily: fonts.monoBold, fontSize: 13, letterSpacing: 1, color: colors.onSurfaceInverse },
  modalBtnPrimary: { backgroundColor: colors.surface },
  modalBtnPrimaryText: { fontFamily: fonts.monoBold, fontSize: 13, letterSpacing: 1, color: colors.onSurface },
});
