import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  TextInput,
  Pressable,
  ScrollView,
  ActivityIndicator,
  StyleSheet,
  Keyboard,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { KeyboardStickyView } from "react-native-keyboard-controller";
import * as Haptics from "expo-haptics";
import { MagnifyingGlass, X, ArrowRight, CaretRight } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER, changeColor } from "@/src/theme";
import { api, Quote, SearchResult } from "@/src/api";
import { QuoteCard } from "@/src/components/QuoteCard";
import { Sparkline } from "@/src/components/Sparkline";

export default function AnalyzeScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  const [selected, setSelected] = useState<{ symbol: string; name: string } | null>(null);
  const [selectedQuote, setSelectedQuote] = useState<Quote | null>(null);
  const [loadingQuote, setLoadingQuote] = useState(false);

  const [trending, setTrending] = useState<Quote[]>([]);
  const [loadingTrending, setLoadingTrending] = useState(true);

  const [submitting, setSubmitting] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.trending();
        setTrending(r.results || []);
      } catch {
        setTrending([]);
      } finally {
        setLoadingTrending(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    if (q.length < 1) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    debounceRef.current = setTimeout(async () => {
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
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  const fetchQuote = useCallback(async (symbol: string) => {
    setLoadingQuote(true);
    setSelectedQuote(null);
    try {
      const q = await api.quote(symbol);
      setSelectedQuote(q);
    } catch {
      setSelectedQuote(null);
    } finally {
      setLoadingQuote(false);
    }
  }, []);

  const onSelectResult = useCallback(
    (r: SearchResult) => {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      setSelected({ symbol: r.symbol, name: r.name });
      setResults([]);
      setQuery(r.symbol);
      Keyboard.dismiss();
      fetchQuote(r.symbol);
    },
    [fetchQuote]
  );

  const onSelectTrending = useCallback((q: Quote) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setSelected({ symbol: q.symbol, name: q.name });
    setSelectedQuote(q);
    setResults([]);
    setQuery(q.symbol);
    Keyboard.dismiss();
  }, []);

  const clearSelection = useCallback(() => {
    setSelected(null);
    setSelectedQuote(null);
    setQuery("");
    setResults([]);
  }, []);

  const onExecute = useCallback(async () => {
    if (!selected || submitting) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setSubmitting(true);
    try {
      const res = await api.analyze(selected.symbol, selected.name);
      router.push(`/analysis/${res.id}`);
    } catch {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setSubmitting(false);
    }
  }, [selected, submitting, router]);

  const showResults = query.trim().length > 0 && (results.length > 0 || searching);

  return (
    <View style={styles.root}>
      {/* Sticky header */}
      <View style={[styles.header, { paddingTop: insets.top + spacing.sm }]}>
        <Text style={styles.brand}>TRADINGAGENTS</Text>
        <Text style={styles.tagline}>{"// MULTI-AGENT EQUITY DESK"}</Text>
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* Search bar */}
        <View style={styles.searchBar}>
          <MagnifyingGlass size={18} color={colors.onSurface} weight="bold" />
          <TextInput
            testID="ticker-search-input"
            style={styles.searchInput}
            value={query}
            onChangeText={(t) => {
              setQuery(t);
              if (selected) setSelected(null);
            }}
            placeholder="SEARCH TICKER — AAPL, BTC-USD…"
            placeholderTextColor="#9CA3AF"
            autoCapitalize="characters"
            autoCorrect={false}
            returnKeyType="search"
          />
          {query.length > 0 ? (
            <Pressable testID="clear-search-button" onPress={clearSelection} hitSlop={10}>
              <X size={18} color={colors.onSurface} weight="bold" />
            </Pressable>
          ) : null}
        </View>

        {showResults ? (
          <View style={styles.resultsBox}>
            {searching ? (
              <View style={styles.resultsLoading}>
                <ActivityIndicator color={colors.onSurface} />
                <Text style={styles.mutedMono}>SCANNING MARKETS…</Text>
              </View>
            ) : (
              results.map((r, i) => (
                <Pressable
                  key={`${r.symbol}-${i}`}
                  testID={`search-result-${r.symbol}`}
                  onPress={() => onSelectResult(r)}
                  style={[styles.resultRow, i < results.length - 1 && styles.resultDivider]}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.resultSymbol}>{r.symbol}</Text>
                    <Text style={styles.resultName} numberOfLines={1}>
                      {r.name}
                    </Text>
                  </View>
                  <Text style={styles.resultExchange}>{r.exchange || r.type || ""}</Text>
                  <CaretRight size={16} color={colors.onSurfaceTertiary} weight="bold" />
                </Pressable>
              ))
            )}
          </View>
        ) : (
          <>
            {/* Selected ticker preview */}
            {selected ? (
              <View style={styles.section}>
                <Text style={styles.sectionLabel}>SELECTED TARGET</Text>
                {loadingQuote ? (
                  <View style={styles.previewLoading}>
                    <ActivityIndicator color={colors.onSurface} />
                  </View>
                ) : selectedQuote ? (
                  <QuoteCard quote={selectedQuote} />
                ) : (
                  <View style={styles.noQuoteBox}>
                    <Text style={styles.selectedSymbol}>{selected.symbol}</Text>
                    <Text style={styles.mutedMono}>
                      Live quote unavailable — the desk will still analyze qualitatively.
                    </Text>
                  </View>
                )}
              </View>
            ) : null}

            {/* Trending grid */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>{selected ? "OR PICK ANOTHER" : "TRENDING TARGETS"}</Text>
              {loadingTrending ? (
                <View style={styles.previewLoading}>
                  <ActivityIndicator color={colors.onSurface} />
                </View>
              ) : (
                <View style={styles.grid}>
                  {trending.map((q) => {
                    const cColor = changeColor(q.changePercent);
                    const active = selected?.symbol === q.symbol;
                    return (
                      <Pressable
                        key={q.symbol}
                        testID={`trending-${q.symbol}`}
                        onPress={() => onSelectTrending(q)}
                        style={[styles.gridCard, active && styles.gridCardActive]}
                      >
                        <View style={styles.gridTop}>
                          <Text style={[styles.gridSymbol, active && { color: colors.onSurfaceInverse }]}>
                            {q.symbol}
                          </Text>
                          <Sparkline data={q.sparkline} color={active ? colors.onSurfaceInverse : cColor} width={46} height={20} strokeWidth={1.5} />
                        </View>
                        <Text
                          style={[styles.gridName, active && { color: colors.onSurfaceInverse }]}
                          numberOfLines={1}
                        >
                          {q.name}
                        </Text>
                        <View style={styles.gridBottom}>
                          <Text style={[styles.gridPrice, active && { color: colors.onSurfaceInverse }]}>
                            {q.currency && q.currency !== "USD" ? "" : "$"}
                            {q.price?.toLocaleString("en-US", { maximumFractionDigits: 2 }) ?? "—"}
                          </Text>
                          <Text style={[styles.gridChange, { color: active ? colors.onSurfaceInverse : cColor }]}>
                            {(q.changePercent ?? 0) >= 0 ? "+" : ""}
                            {q.changePercent?.toFixed(2) ?? "0.00"}%
                          </Text>
                        </View>
                      </Pressable>
                    );
                  })}
                </View>
              )}
            </View>
          </>
        )}
      </ScrollView>

      {/* Sticky CTA */}
      <KeyboardStickyView offset={{ closed: 0, opened: spacing.sm }}>
        <View style={[styles.ctaWrap, { paddingBottom: insets.bottom + spacing.sm }]}>
          <Pressable
            testID="execute-analysis-button"
            onPress={onExecute}
            disabled={!selected || submitting}
            style={[styles.cta, (!selected || submitting) && styles.ctaDisabled]}
          >
            {submitting ? (
              <ActivityIndicator color={colors.onSurfaceInverse} />
            ) : (
              <>
                <Text style={[styles.ctaText, !selected && styles.ctaTextDisabled]}>
                  {selected ? `EXECUTE ANALYSIS · ${selected.symbol}` : "SELECT A TICKER"}
                </Text>
                {selected ? <ArrowRight size={20} color={colors.onSurfaceInverse} weight="bold" /> : null}
              </>
            )}
          </Pressable>
        </View>
      </KeyboardStickyView>
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
    backgroundColor: colors.surface,
  },
  brand: { fontFamily: fonts.display, fontSize: 28, color: colors.onSurface, letterSpacing: -1 },
  tagline: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2, letterSpacing: 1 },
  scroll: { flex: 1 },
  scrollContent: { padding: spacing.lg, paddingBottom: spacing.xl },

  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.md,
    height: 52,
  },
  searchInput: { flex: 1, fontFamily: fonts.monoMed, fontSize: 14, color: colors.onSurface, height: "100%" },

  resultsBox: {
    marginTop: spacing.md,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
  },
  resultsLoading: { flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.lg },
  resultRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.md },
  resultDivider: { borderBottomWidth: 1.5, borderBottomColor: colors.border },
  resultSymbol: { fontFamily: fonts.monoBold, fontSize: 15, color: colors.onSurface },
  resultName: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  resultExchange: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary },

  section: { marginTop: spacing.xl },
  sectionLabel: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    letterSpacing: 1.5,
    color: colors.onSurface,
    marginBottom: spacing.md,
  },
  previewLoading: { padding: spacing.xl, alignItems: "center" },
  mutedMono: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary },
  noQuoteBox: { borderWidth: BORDER, borderColor: colors.borderStrong, padding: spacing.lg, gap: spacing.sm },
  selectedSymbol: { fontFamily: fonts.display, fontSize: 24, color: colors.onSurface },

  grid: { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between" },
  gridCard: {
    width: "48%",
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  gridCardActive: { backgroundColor: colors.surfaceInverse },
  gridTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  gridSymbol: { fontFamily: fonts.displayMed, fontSize: 15, color: colors.onSurface },
  gridName: { fontFamily: fonts.mono, fontSize: 9.5, color: colors.onSurfaceTertiary, marginTop: 4 },
  gridBottom: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between", marginTop: spacing.sm },
  gridPrice: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  gridChange: { fontFamily: fonts.monoMed, fontSize: 11 },

  ctaWrap: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    borderTopWidth: BORDER,
    borderTopColor: colors.borderStrong,
    backgroundColor: colors.surface,
  },
  cta: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    backgroundColor: colors.surfaceInverse,
    height: 56,
  },
  ctaDisabled: { backgroundColor: colors.surfaceTertiary },
  ctaText: { fontFamily: fonts.monoBold, fontSize: 14, letterSpacing: 1, color: colors.onSurfaceInverse },
  ctaTextDisabled: { color: colors.onSurfaceTertiary },
});
