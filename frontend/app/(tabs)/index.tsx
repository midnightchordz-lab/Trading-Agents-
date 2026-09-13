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
  Alert,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { KeyboardStickyView } from "react-native-keyboard-controller";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { MagnifyingGlass, X, ArrowRight, CaretRight, Star, ArrowsLeftRight } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER, changeColor, accentAt, CATEGORY_COLORS, accents, CTA_GRADIENT } from "@/src/theme";
import { api, Quote, SearchResult } from "@/src/api";
import { getWalletDeviceId } from "@/src/wallet";
import { useTranslation } from "react-i18next";
import { getCurrentLanguage } from "@/src/i18n";
import { QuoteCard } from "@/src/components/QuoteCard";
import { Sparkline } from "@/src/components/Sparkline";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { useWatchlist } from "@/src/watchlist";

const CATEGORIES = [
  { key: "trending", label: "TRENDING" },
  { key: "stocks", label: "STOCKS" },
  { key: "crypto", label: "CRYPTO" },
  { key: "commodities", label: "COMMODITIES" },
];

export default function AnalyzeScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { items: watchItems, isSaved, toggle: toggleWatch, remove: removeWatch } = useWatchlist();
  const { t } = useTranslation();

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  const [selected, setSelected] = useState<{ symbol: string; name: string } | null>(null);
  const [selectedQuote, setSelectedQuote] = useState<Quote | null>(null);
  const [loadingQuote, setLoadingQuote] = useState(false);

  const [category, setCategory] = useState<string>("trending");
  const [marketData, setMarketData] = useState<Record<string, Quote[]>>({});
  const [loadingCat, setLoadingCat] = useState(true);

  const [submitting, setSubmitting] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadCategory = useCallback(async (cat: string) => {
    setLoadingCat(true);
    try {
      const r = await api.markets(cat);
      setMarketData((prev) => ({ ...prev, [cat]: r.results || [] }));
    } catch {
      setMarketData((prev) => ({ ...prev, [cat]: [] }));
    } finally {
      setLoadingCat(false);
    }
  }, []);

  useEffect(() => {
    loadCategory("trending");
  }, [loadCategory]);

  const onSelectCategory = useCallback(
    (cat: string) => {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      setCategory(cat);
      if (!marketData[cat]) loadCategory(cat);
    },
    [marketData, loadCategory]
  );

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

  const startAnalysis = useCallback(
    async (sym: string, nm?: string) => {
      if (submitting) return;
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      setSubmitting(true);
      try {
        const res = await api.analyze(sym, nm, getCurrentLanguage(), await getWalletDeviceId());
        router.push(`/analysis/${res.id}`);
      } catch (e: any) {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
        if (e?.message?.toLowerCase().includes("insufficient balance")) {
          Alert.alert("Add funds to continue", e.message);
        }
      } finally {
        setSubmitting(false);
      }
    },
    [submitting, router]
  );

  const onExecute = useCallback(() => {
    if (selected) startAnalysis(selected.symbol, selected.name);
  }, [selected, startAnalysis]);

  const showResults = !selected && query.trim().length > 0 && (results.length > 0 || searching);

  return (
    <View style={styles.root}>
      {/* Sticky header */}
      <ScreenHeader
        title={t("analyze.title")}
        subtitle={t("analyze.subtitle")}
        insetsTop={insets.top}
        right={
          <Pressable testID="compare-button" onPress={() => router.push("/compare")} style={styles.compareBtn}>
            <ArrowsLeftRight size={16} color={colors.onSurface} weight="bold" />
            <Text style={styles.compareText}>{t("common.compare")}</Text>
          </Pressable>
        }
      />

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
            placeholder={t("analyze.search_placeholder")}
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
                <View style={styles.sectionHeadRow}>
                  <Text style={[styles.sectionLabel, { marginBottom: 0 }]}>{t("analyze.selected_target")}</Text>
                  <Pressable
                    testID="watchlist-toggle"
                    onPress={() => selected && toggleWatch({ symbol: selected.symbol, name: selected.name })}
                    hitSlop={8}
                    style={[styles.starBtn, isSaved(selected.symbol) && styles.starBtnActive]}
                  >
                    <Star
                      size={16}
                      color={isSaved(selected.symbol) ? colors.onSurfaceInverse : colors.onSurface}
                      weight={isSaved(selected.symbol) ? "fill" : "regular"}
                    />
                    <Text style={[styles.starText, isSaved(selected.symbol) && { color: colors.onSurfaceInverse }]}>
                      {isSaved(selected.symbol) ? t("common.watching") : t("common.watch")}
                    </Text>
                  </Pressable>
                </View>
                {loadingQuote ? (
                  <View style={styles.previewLoading}>
                    <ActivityIndicator color={colors.onSurface} />
                  </View>
                ) : selectedQuote ? (
                  <QuoteCard quote={selectedQuote} showRanges />
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

            {/* Watchlist */}
            {watchItems.length > 0 ? (
              <View style={styles.section}>
                <Text style={styles.sectionLabel}>{t("analyze.watchlist_tap_to_rerun")}</Text>
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.chipRowContent}
                  style={styles.chipRow}
                >
                  {watchItems.map((w) => (
                    <View key={w.symbol} style={styles.watchCard}>
                      <Pressable
                        testID={`watchlist-item-${w.symbol}`}
                        onPress={() => startAnalysis(w.symbol, w.name)}
                        style={styles.watchMain}
                      >
                        <Text style={styles.watchSymbol} numberOfLines={1}>
                          {w.symbol}
                        </Text>
                        <Text style={styles.watchName} numberOfLines={1}>
                          {w.name}
                        </Text>
                      </Pressable>
                      <Pressable
                        testID={`watchlist-remove-${w.symbol}`}
                        onPress={() => removeWatch(w.symbol)}
                        hitSlop={8}
                        style={styles.watchRemove}
                      >
                        <X size={14} color={colors.onSurfaceTertiary} weight="bold" />
                      </Pressable>
                    </View>
                  ))}
                </ScrollView>
              </View>
            ) : null}

            {/* Browse markets */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>{selected ? t("analyze.or_pick_another") : t("analyze.browse_markets")}</Text>
              <View style={styles.chipScrollWrap}>
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.chipRowContent}
                  style={styles.chipRow}
                >
                  {CATEGORIES.map((c) => {
                    const active = category === c.key;
                    const catColor = CATEGORY_COLORS[c.key] || accents.blue;
                    return (
                      <Pressable
                        key={c.key}
                        testID={`category-chip-${c.key}`}
                        onPress={() => onSelectCategory(c.key)}
                        style={[styles.chip, active && { backgroundColor: catColor, borderColor: catColor }]}
                      >
                        <View style={[styles.chipDot, { backgroundColor: active ? "#FFFFFF" : catColor }]} />
                        <Text style={[styles.chipText, { color: active ? "#FFFFFF" : colors.onSurface }]}>{c.label}</Text>
                      </Pressable>
                    );
                  })}
                </ScrollView>
                <LinearGradient
                  colors={["rgba(255,255,255,0)", colors.surface] as unknown as string[]}
                  start={{ x: 0, y: 0 }}
                  end={{ x: 1, y: 0 }}
                  style={styles.chipFade}
                  pointerEvents="none"
                />
              </View>
              {loadingCat && !marketData[category] ? (
                <View style={styles.previewLoading}>
                  <ActivityIndicator color={colors.onSurface} />
                </View>
              ) : (
                <View style={styles.grid}>
                  {(marketData[category] || []).map((q, idx) => {
                    const cColor = changeColor(q.changePercent);
                    const active = selected?.symbol === q.symbol;
                    return (
                      <Pressable
                        key={q.symbol}
                        testID={`trending-${q.symbol}`}
                        onPress={() => onSelectTrending(q)}
                        style={[styles.gridCard, active && styles.gridCardActive]}
                      >
                        <View style={[styles.gridAccent, { backgroundColor: accentAt(idx) }]} />
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

      {/* Sticky CTA — only shown once a ticker is selected */}
      {selected ? (
        <KeyboardStickyView offset={{ closed: 0, opened: spacing.sm }}>
          <View style={[styles.ctaWrap, { paddingBottom: insets.bottom + spacing.sm }]}>
            <Pressable
              testID="execute-analysis-button"
              onPress={onExecute}
              disabled={submitting}
              style={styles.cta}
            >
              <LinearGradient
                colors={submitting ? ["#D4D4D8", "#D4D4D8"] : (CTA_GRADIENT as unknown as string[])}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 0 }}
                style={styles.ctaGrad}
              >
                {submitting ? (
                  <ActivityIndicator color={colors.onSurfaceInverse} />
                ) : (
                  <>
                    <Text style={styles.ctaText}>{`EXECUTE ANALYSIS · ${selected.symbol}`}</Text>
                    <ArrowRight size={20} color={colors.onSurfaceInverse} weight="bold" />
                  </>
                )}
              </LinearGradient>
            </Pressable>
          </View>
        </KeyboardStickyView>
      ) : null}
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
  headerRow: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: spacing.md },
  compareBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.md,
    height: 38,
  },
  compareText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurface },
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
  sectionHeadRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md },
  starBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    paddingHorizontal: spacing.sm,
    height: 32,
  },
  starBtnActive: { backgroundColor: colors.surfaceInverse },
  starText: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5, color: colors.onSurface },
  watchCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    paddingLeft: spacing.md,
    paddingRight: spacing.sm,
    height: 54,
    flexShrink: 0,
  },
  watchMain: { justifyContent: "center", maxWidth: 130 },
  watchSymbol: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  watchName: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceTertiary, marginTop: 2 },
  watchRemove: { padding: 4 },
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

  chipRow: { marginBottom: spacing.md, marginHorizontal: -spacing.lg },
  chipRowContent: { gap: spacing.sm, paddingHorizontal: spacing.lg },
  chipScrollWrap: { position: "relative" },
  chipFade: { position: "absolute", right: 0, top: 0, bottom: spacing.md, width: 28 },
  chip: {
    height: 36,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
    paddingHorizontal: spacing.md,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    flexShrink: 0,
  },
  chipDot: { width: 8, height: 8 },
  chipText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1 },

  grid: { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between" },
  gridCard: {
    width: "48%",
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    padding: spacing.md,
    paddingTop: spacing.md + 4,
    marginBottom: spacing.md,
    overflow: "hidden",
  },
  gridAccent: { position: "absolute", top: 0, left: 0, right: 0, height: 5 },
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
    height: 56,
    overflow: "hidden",
  },
  ctaGrad: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
  },
  ctaText: { fontFamily: fonts.monoBold, fontSize: 14, letterSpacing: 1, color: colors.onSurfaceInverse },
  ctaTextDisabled: { color: colors.onSurfaceTertiary },
});
