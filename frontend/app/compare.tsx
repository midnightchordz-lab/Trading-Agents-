import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, TextInput, Pressable, ActivityIndicator, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { MagnifyingGlass, X, ArrowsLeftRight } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER, verdictColors, changeColor, CTA_GRADIENT } from "@/src/theme";
import { api, Analysis, SearchResult } from "@/src/api";
import { ScreenHeader } from "@/src/components/ScreenHeader";

type Sel = { symbol: string; name: string };

function SlotPicker({
  label,
  value,
  onPick,
  onClear,
}: {
  label: string;
  value: Sel | null;
  onPick: (s: Sel) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const ref = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (ref.current) clearTimeout(ref.current);
    const q = query.trim();
    if (q.length < 1) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    ref.current = setTimeout(async () => {
      try {
        const r = await api.search(q);
        setResults(r.results || []);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => {
      if (ref.current) clearTimeout(ref.current);
    };
  }, [query]);

  return (
    <View style={styles.slot}>
      <Text style={styles.slotLabel}>{label}</Text>
      {value ? (
        <View style={styles.slotSelected}>
          <View style={{ flex: 1 }}>
            <Text style={styles.slotSymbol}>{value.symbol}</Text>
            <Text style={styles.slotName} numberOfLines={1}>
              {value.name}
            </Text>
          </View>
          <Pressable testID={`compare-clear-${label}`} onPress={onClear} hitSlop={10}>
            <X size={18} color={colors.onSurface} weight="bold" />
          </Pressable>
        </View>
      ) : (
        <>
          <View style={styles.searchBar}>
            <MagnifyingGlass size={16} color={colors.onSurface} weight="bold" />
            <TextInput
              testID={`compare-search-${label}`}
              style={styles.searchInput}
              value={query}
              onChangeText={setQuery}
              placeholder="SEARCH TICKER…"
              placeholderTextColor="#9CA3AF"
              autoCapitalize="characters"
              autoCorrect={false}
              returnKeyType="search"
            />
          </View>
          {query.trim().length > 0 ? (
            <View style={styles.resultsBox}>
              {searching ? (
                <View style={styles.resultsLoading}>
                  <ActivityIndicator color={colors.onSurface} />
                </View>
              ) : (
                results.slice(0, 5).map((r, i) => (
                  <Pressable
                    key={`${r.symbol}-${i}`}
                    testID={`compare-result-${r.symbol}`}
                    onPress={() => {
                      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      onPick({ symbol: r.symbol, name: r.name });
                      setQuery("");
                      setResults([]);
                    }}
                    style={[styles.resultRow, i < Math.min(results.length, 5) - 1 && styles.resultDivider]}
                  >
                    <Text style={styles.resultSymbol}>{r.symbol}</Text>
                    <Text style={styles.resultName} numberOfLines={1}>
                      {r.name}
                    </Text>
                  </Pressable>
                ))
              )}
            </View>
          ) : null}
        </>
      )}
    </View>
  );
}

function ResultColumn({ data, fallback }: { data: Analysis | null; fallback: Sel }) {
  const v = data?.verdict;
  const q = data?.quote;
  return (
    <View style={styles.col}>
      <Text style={styles.colSymbol}>{data?.symbol || fallback.symbol}</Text>
      <Text style={styles.colName} numberOfLines={1}>
        {data?.name || fallback.name}
      </Text>

      {q ? (
        <View style={styles.colPriceRow}>
          <Text style={styles.colPrice}>
            {q.currency && q.currency !== "USD" ? "" : "$"}
            {q.price?.toLocaleString("en-US", { maximumFractionDigits: 2 }) ?? "—"}
          </Text>
          <Text style={[styles.colChange, { color: changeColor(q.changePercent) }]}>
            {(q.changePercent ?? 0) >= 0 ? "+" : ""}
            {q.changePercent?.toFixed(2) ?? "0.00"}%
          </Text>
        </View>
      ) : (
        <View style={{ height: spacing.lg }} />
      )}

      {data?.status === "error" ? (
        <View style={[styles.colVerdict, { backgroundColor: colors.error }]}>
          <Text style={[styles.colDecision, { color: colors.onError }]}>FAILED</Text>
        </View>
      ) : v ? (
        (() => {
          const { bg, fg } = verdictColors(v.decision);
          return (
            <View style={[styles.colVerdict, { backgroundColor: bg }]}>
              <Text style={[styles.colDecision, { color: fg }]}>{v.decision}</Text>
              <Text style={[styles.colConf, { color: fg }]}>{v.confidence}% CONF</Text>
            </View>
          );
        })()
      ) : (
        <View style={styles.colRunning}>
          <ActivityIndicator color={colors.onSurface} />
          <Text style={styles.colRunningText}>{data ? `STEP ${data.current_step}/${data.total_steps}` : "QUEUED"}</Text>
        </View>
      )}

      {v ? (
        <View style={styles.colMeta}>
          <Text style={styles.colMetaLabel}>HORIZON</Text>
          <Text style={styles.colMetaValue} numberOfLines={1}>
            {v.time_horizon}
          </Text>
          <Text style={[styles.colMetaLabel, { marginTop: spacing.sm }]}>TARGET</Text>
          <Text style={styles.colMetaValue}>
            {v.target_price != null ? (q?.currency && q.currency !== "USD" ? "" : "$") + v.target_price : "—"}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

export default function CompareScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [slotA, setSlotA] = useState<Sel | null>(null);
  const [slotB, setSlotB] = useState<Sel | null>(null);
  const [idA, setIdA] = useState<string | null>(null);
  const [idB, setIdB] = useState<string | null>(null);
  const [aData, setAData] = useState<Analysis | null>(null);
  const [bData, setBData] = useState<Analysis | null>(null);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    if (!idA || !idB) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      try {
        const [a, b] = await Promise.all([api.getAnalysis(idA), api.getAnalysis(idB)]);
        if (cancelled) return;
        setAData(a);
        setBData(b);
        if (a.status !== "running" && b.status !== "running") {
          if (interval) clearInterval(interval);
          setComparing(false);
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        }
      } catch {
        // keep polling
      }
    };
    poll();
    interval = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [idA, idB]);

  const run = useCallback(async () => {
    if (!slotA || !slotB || comparing) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setComparing(true);
    setAData(null);
    setBData(null);
    try {
      const [ra, rb] = await Promise.all([
        api.analyze(slotA.symbol, slotA.name),
        api.analyze(slotB.symbol, slotB.name),
      ]);
      setIdA(ra.id);
      setIdB(rb.id);
    } catch {
      setComparing(false);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    }
  }, [slotA, slotB, comparing]);

  const reset = useCallback(() => {
    setIdA(null);
    setIdB(null);
    setAData(null);
    setBData(null);
    setSlotA(null);
    setSlotB(null);
    setComparing(false);
  }, []);

  const started = !!(idA && idB);
  const bothDone = aData?.verdict && bData?.verdict;

  // Desk lean: signed conviction score.
  let lean: string | null = null;
  if (bothDone && aData?.verdict && bData?.verdict) {
    const score = (v: { decision: string; confidence: number }) =>
      (v.decision === "BUY" ? 1 : v.decision === "SELL" ? -1 : 0) * v.confidence;
    const sa = score(aData.verdict);
    const sb = score(bData.verdict);
    lean = sa === sb ? "TOSS-UP" : sa > sb ? aData.symbol : bData.symbol;
  }

  return (
    <View style={styles.root}>
      <ScreenHeader title="COMPARE" subtitle="// TWO TICKERS · ONE DESK" insetsTop={insets.top} onBack={() => router.back()} />

      <KeyboardAwareScrollView
        style={styles.scroll}
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: insets.bottom + spacing.xxl }}
        keyboardShouldPersistTaps="handled"
        bottomOffset={20}
        showsVerticalScrollIndicator={false}
      >
        {!started ? (
          <>
            <SlotPicker label="SIDE A" value={slotA} onPick={setSlotA} onClear={() => setSlotA(null)} />
            <View style={styles.vsWrap}>
              <View style={styles.vsBox}>
                <ArrowsLeftRight size={18} color={colors.onSurfaceInverse} weight="bold" />
                <Text style={styles.vsText}>VS</Text>
              </View>
            </View>
            <SlotPicker label="SIDE B" value={slotB} onPick={setSlotB} onClear={() => setSlotB(null)} />

            <Pressable
              testID="run-comparison-button"
              onPress={run}
              disabled={!slotA || !slotB || comparing}
              style={styles.runBtn}
            >
              <LinearGradient
                colors={!slotA || !slotB ? ["#D4D4D8", "#D4D4D8"] : (CTA_GRADIENT as unknown as string[])}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 0 }}
                style={styles.runGrad}
              >
                <Text style={[styles.runText, (!slotA || !slotB) && { color: colors.onSurfaceTertiary }]}>
                  {slotA && slotB ? "RUN COMPARISON" : "PICK TWO TICKERS"}
                </Text>
              </LinearGradient>
            </Pressable>
          </>
        ) : (
          <>
            {lean ? (
              <View style={styles.leanBox}>
                <Text style={styles.leanLabel}>DESK LEANS</Text>
                <Text style={styles.leanValue}>{lean === "TOSS-UP" ? "TOSS-UP" : `→ ${lean}`}</Text>
              </View>
            ) : (
              <View style={styles.leanBox}>
                <Text style={styles.leanLabel}>THE DESK IS DELIBERATING</Text>
                <Text style={styles.leanValueSmall}>Both analyses run in parallel…</Text>
              </View>
            )}

            <View style={styles.columns}>
              <ResultColumn data={aData} fallback={slotA!} />
              <View style={styles.colGap} />
              <ResultColumn data={bData} fallback={slotB!} />
            </View>

            <Pressable testID="new-comparison-button" onPress={reset} style={styles.resetBtn}>
              <Text style={styles.resetText}>NEW COMPARISON</Text>
            </Pressable>
          </>
        )}
      </KeyboardAwareScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingBottom: spacing.md, borderBottomWidth: BORDER, borderBottomColor: colors.borderStrong },
  headerRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  backBtn: { width: 34, height: 34, borderWidth: BORDER, borderColor: colors.borderStrong, alignItems: "center", justifyContent: "center" },
  brand: { fontFamily: fonts.display, fontSize: 26, color: colors.onSurface, letterSpacing: -1 },
  tagline: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2, letterSpacing: 1 },
  scroll: { flex: 1 },

  slot: { marginBottom: spacing.md },
  slotLabel: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurface, marginBottom: spacing.sm },
  slotSelected: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    padding: spacing.md,
  },
  slotSymbol: { fontFamily: fonts.monoBold, fontSize: 16, color: colors.onSurface },
  slotName: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },

  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    paddingHorizontal: spacing.md,
    height: 48,
  },
  searchInput: { flex: 1, fontFamily: fonts.monoMed, fontSize: 14, color: colors.onSurface, height: "100%" },
  resultsBox: { borderWidth: BORDER, borderTopWidth: 0, borderColor: colors.borderStrong },
  resultsLoading: { padding: spacing.md, alignItems: "center" },
  resultRow: { padding: spacing.md },
  resultDivider: { borderBottomWidth: 1.5, borderBottomColor: colors.border },
  resultSymbol: { fontFamily: fonts.monoBold, fontSize: 14, color: colors.onSurface },
  resultName: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.onSurfaceTertiary, marginTop: 2 },

  vsWrap: { alignItems: "center", marginVertical: spacing.xs },
  vsBox: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.surfaceInverse, paddingHorizontal: spacing.md, paddingVertical: spacing.xs },
  vsText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurfaceInverse },

  runBtn: { height: 56, marginTop: spacing.lg, overflow: "hidden" },
  runGrad: { flex: 1, alignItems: "center", justifyContent: "center" },
  runText: { fontFamily: fonts.monoBold, fontSize: 14, letterSpacing: 1, color: colors.onSurfaceInverse },

  leanBox: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surfaceInverse, padding: spacing.lg, marginBottom: spacing.lg },
  leanLabel: { fontFamily: fonts.mono, fontSize: 11, letterSpacing: 1, color: colors.onSurfaceInverse },
  leanValue: { fontFamily: fonts.display, fontSize: 34, color: colors.onSurfaceInverse, marginTop: spacing.xs, letterSpacing: -0.5 },
  leanValueSmall: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceInverse, marginTop: spacing.xs, opacity: 0.8 },

  columns: { flexDirection: "row" },
  colGap: { width: BORDER, backgroundColor: colors.borderStrong },
  col: { flex: 1, borderWidth: BORDER, borderColor: colors.borderStrong, padding: spacing.md },
  colSymbol: { fontFamily: fonts.display, fontSize: 20, color: colors.onSurface, letterSpacing: -0.5 },
  colName: { fontFamily: fonts.mono, fontSize: 9.5, color: colors.onSurfaceTertiary, marginTop: 2 },
  colPriceRow: { marginTop: spacing.sm, marginBottom: spacing.md },
  colPrice: { fontFamily: fonts.monoBold, fontSize: 16, color: colors.onSurface },
  colChange: { fontFamily: fonts.monoMed, fontSize: 12, marginTop: 2 },
  colVerdict: { padding: spacing.md, alignItems: "center" },
  colDecision: { fontFamily: fonts.display, fontSize: 28, letterSpacing: -0.5 },
  colConf: { fontFamily: fonts.monoBold, fontSize: 11, marginTop: 2 },
  colRunning: { paddingVertical: spacing.lg, alignItems: "center", gap: spacing.sm },
  colRunningText: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, letterSpacing: 0.5 },
  colMeta: { marginTop: spacing.md, borderTopWidth: 1.5, borderTopColor: colors.border, paddingTop: spacing.sm },
  colMetaLabel: { fontFamily: fonts.mono, fontSize: 8.5, color: colors.onSurfaceTertiary, letterSpacing: 0.5 },
  colMetaValue: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface, marginTop: 2 },

  resetBtn: { height: 52, alignItems: "center", justifyContent: "center", borderWidth: BORDER, borderColor: colors.borderStrong, marginTop: spacing.lg },
  resetText: { fontFamily: fonts.monoBold, fontSize: 13, letterSpacing: 1, color: colors.onSurface },
});
