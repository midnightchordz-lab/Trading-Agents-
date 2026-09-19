import React, { useEffect, useMemo, useState, useCallback } from "react";
import {
  View,
  Text,
  TextInput,
  Pressable,
  ScrollView,
  ActivityIndicator,
  StyleSheet,
  Alert,
  RefreshControl,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useFocusEffect } from "expo-router";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, fonts, spacing, BORDER, verdictColors } from "@/src/theme";
import { storage } from "@/src/utils/storage";
import { useWatchlist } from "@/src/watchlist";
import { api, Quote, SearchResult } from "@/src/api";

// Portfolio tab: manual holdings + watchlist quick-add, then optimize against
// PyPortfolioOpt via POST /api/portfolio/optimize. Holdings persist locally;
// nothing here touches the analysis pipeline — the backend endpoint only
// *reads* cached verdicts when "Use agent views" is on.

type Holding = { symbol: string; quantity: string; avgPrice: string };
type Objective = "hrp" | "max_sharpe" | "min_volatility";

const KEY_HOLDINGS = "portfolio:holdings";
const KEY_CASH = "portfolio:cash";

const OBJECTIVES: { id: Objective; label: string; blurb: string }[] = [
  { id: "hrp", label: "HRP", blurb: "Hierarchical Risk Parity — no return forecast, more robust." },
  { id: "max_sharpe", label: "MAX SHARPE", blurb: "Best risk-adjusted return given the covariance." },
  { id: "min_volatility", label: "MIN VOL", blurb: "Lowest-volatility mix given the covariance." },
];

function fmtPct(n: number) {
  return `${(n * 100).toFixed(1)}%`;
}

function fmtMoney(symbol: string, n: number) {
  return `${n < 0 ? "-" : ""}${symbol}${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

const CURRENCY_SYMBOLS: Record<string, string> = { USD: "$", INR: "\u20b9", GBP: "\u00a3", EUR: "\u20ac" };

function currencySymbol(code?: string) {
  return CURRENCY_SYMBOLS[(code || "").toUpperCase()] || (code ? `${code} ` : "");
}

export default function PortfolioScreen() {
  const insets = useSafeAreaInsets();
  const { items: watchlist } = useWatchlist();
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [cash, setCash] = useState("0");
  const [objective, setObjective] = useState<Objective>("hrp");
  const [useAgentViews, setUseAgentViews] = useState(false);
  const [form, setForm] = useState<Holding>({ symbol: "", quantity: "", avgPrice: "" });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [analyzingMissing, setAnalyzingMissing] = useState<string | null>(null); // symbol currently being analyzed
  // Live prices for the P/L panel. Kept separate from `result` so a failed or
  // never-run optimization still shows the user what their positions are worth.
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [quotesLoading, setQuotesLoading] = useState(false);

  // Symbol search-as-you-type (mirrors the Analyze tab's search), so the user
  // doesn't need to already know the exact ticker.
  useEffect(() => {
    const q = form.symbol.trim();
    if (q.length < 1) {
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const t = setTimeout(() => {
      api
        .search(q)
        .then((r) => {
          if (!cancelled) setSearchResults(r.results.slice(0, 6));
        })
        .catch(() => {
          if (!cancelled) setSearchResults([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [form.symbol]);

  const pickSearchResult = (r: SearchResult) => {
    setForm((f) => ({ ...f, symbol: r.symbol }));
    setSearchResults([]);
  };

  useEffect(() => {
    (async () => {
      const saved = await storage.getItem<Holding[]>(KEY_HOLDINGS, []);
      const savedCash = await storage.getItem(KEY_CASH, "0");
      if (saved) setHoldings(saved);
      if (savedCash != null) setCash(String(savedCash));
    })();
  }, []);

  const persist = useCallback((next: Holding[]) => {
    setHoldings(next);
    storage.setItem(KEY_HOLDINGS, next);
  }, []);

  const addHolding = () => {
    const symbol = form.symbol.trim().toUpperCase();
    const quantity = Number(form.quantity);
    const avgPrice = Number(form.avgPrice);
    if (!symbol || !quantity || !avgPrice) {
      Alert.alert("Missing details", "Symbol, quantity and average price are required.");
      return;
    }
    if (holdings.some((h) => h.symbol === symbol)) {
      Alert.alert("Already added", `${symbol} is already in the portfolio.`);
      return;
    }
    persist([...holdings, { symbol, quantity: String(quantity), avgPrice: String(avgPrice) }]);
    setForm({ symbol: "", quantity: "", avgPrice: "" });
    setSearchResults([]);
  };

  // Runs the existing /api/analyze -> poll /api/analysis/{id} flow for symbols
  // that have no cached verdict yet, then re-runs the optimizer. Does not add
  // any new endpoint or touch the pipeline — it drives the same flow the
  // Analyze tab already uses, one symbol at a time.
  const analyzeMissing = async () => {
    const missing: string[] = result?.missing_agent_view_for || [];
    if (!missing.length) return;
    for (const symbol of missing) {
      setAnalyzingMissing(symbol);
      try {
        const started = await api.analyze(symbol);
        await new Promise<void>((resolve) => {
          const interval = setInterval(async () => {
            try {
              const a = await api.getAnalysis(started.id);
              if (a.status === "completed" || a.status === "error") {
                clearInterval(interval);
                resolve();
              }
            } catch {
              clearInterval(interval);
              resolve();
            }
          }, 1500);
        });
      } catch {
        // one symbol failing shouldn't block the rest
      }
    }
    setAnalyzingMissing(null);
    await runOptimize();
  };

  const addFromWatchlist = (symbol: string) => {
    if (holdings.some((h) => h.symbol === symbol)) return;
    setForm((f) => ({ ...f, symbol }));
  };

  const removeHolding = (symbol: string) => {
    persist(holdings.filter((h) => h.symbol !== symbol));
    setResult(null);
  };

  const quickAddable = watchlist.filter((w) => !holdings.some((h) => h.symbol === w.symbol));

  const runOptimize = async () => {
    if (holdings.length < 2) {
      Alert.alert("Add more holdings", "Optimization needs at least 2 symbols.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const body = {
        holdings: holdings.map((h) => ({
          symbol: h.symbol,
          quantity: Number(h.quantity),
          avg_price: Number(h.avgPrice),
        })),
        objective,
        use_agent_views: useAgentViews,
        cash: Number(cash) || 0,
      };
      const res = await api.portfolioOptimize(body);
      setResult(res);
    } catch (e: any) {
      setError(e?.message || "Optimization failed. Try a different objective or fewer symbols.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    storage.setItem(KEY_CASH, cash);
  }, [cash]);

  const actionsSorted = useMemo(() => {
    if (!result?.actions) return [];
    const order: Record<string, number> = { SELL: 0, TRIM: 1, ADD: 2, HOLD: 3 };
    return [...result.actions].sort((a: any, b: any) => order[a.action] - order[b.action]);
  }, [result]);

  // --- Live P/L ---------------------------------------------------------
  const symbols = useMemo(() => holdings.map((h) => h.symbol).join(","), [holdings]);

  const refreshQuotes = useCallback(async () => {
    const list = symbols ? symbols.split(",") : [];
    if (list.length === 0) {
      setQuotes({});
      return;
    }
    setQuotesLoading(true);
    const results = await Promise.all(
      list.map((s) =>
        api
          .quote(s)
          .then((q) => [s, q] as const)
          .catch(() => [s, null] as const)
      )
    );
    // A symbol whose quote failed keeps whatever it had rather than flashing
    // to "—": a single upstream hiccup shouldn't blank the whole panel.
    setQuotes((prev) => {
      const next = { ...prev };
      for (const [s, q] of results) if (q) next[s] = q;
      return next;
    });
    setQuotesLoading(false);
  }, [symbols]);

  useEffect(() => {
    refreshQuotes();
  }, [refreshQuotes]);

  useFocusEffect(
    useCallback(() => {
      refreshQuotes();
    }, [refreshQuotes])
  );

  const pl = useMemo(() => {
    const rows = holdings.map((h) => {
      const qty = Number(h.quantity) || 0;
      const avg = Number(h.avgPrice) || 0;
      const q = quotes[h.symbol];
      const price = q?.price ?? null;
      const cost = qty * avg;
      const value = price != null ? qty * price : null;
      const gain = value != null ? value - cost : null;
      return {
        symbol: h.symbol,
        qty,
        avg,
        price,
        cost,
        value,
        gain,
        gainPct: gain != null && cost > 0 ? gain / cost : null,
        dayPct: q?.changePercent ?? null,
        currency: q?.currency,
        symbolPrefix: currencySymbol(q?.currency),
      };
    });
    const priced = rows.filter((r) => r.value != null);
    // Holdings can sit in different currencies (an NSE stock and a US one), and
    // adding those numbers together would be a lie. The total is only shown
    // when every priced holding shares one currency.
    const currencies = new Set(priced.map((r) => (r.currency || "").toUpperCase()));
    const single = currencies.size === 1 ? [...currencies][0] : null;
    const cashNum = Number(cash) || 0;
    const cost = priced.reduce((s, r) => s + r.cost, 0);
    const value = priced.reduce((s, r) => s + (r.value || 0), 0);
    return {
      rows,
      totals:
        single && priced.length > 0
          ? {
              prefix: currencySymbol(single),
              cost,
              value,
              cash: cashNum,
              gain: value - cost,
              gainPct: cost > 0 ? (value - cost) / cost : null,
              mixed: false,
            }
          : priced.length > 0
            ? { mixed: true as const }
            : null,
    };
  }, [holdings, quotes, cash]);

  return (
    <View style={styles.screen}>
      <ScreenHeader title="PORTFOLIO" subtitle="Keep, trim or sell — optimized" />
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: insets.bottom + spacing.xxl }}
        refreshControl={
          <RefreshControl refreshing={quotesLoading} onRefresh={refreshQuotes} tintColor={colors.onSurface} />
        }
      >
        {/* Add holding */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>ADD HOLDING</Text>
          <View style={styles.formRow}>
            <View style={{ flex: 1.4 }}>
              <TextInput
                placeholder="SEARCH SYMBOL"
                value={form.symbol}
                onChangeText={(t) => setForm((f) => ({ ...f, symbol: t.toUpperCase() }))}
                autoCapitalize="characters"
                style={styles.input}
                placeholderTextColor={colors.onSurfaceTertiary}
              />
              {searchResults.length > 0 ? (
                <View style={styles.searchDropdown}>
                  {searchResults.map((r) => (
                    <Pressable key={r.symbol} onPress={() => pickSearchResult(r)} style={styles.searchRow}>
                      <Text style={styles.searchSymbol}>{r.symbol}</Text>
                      <Text style={styles.searchName} numberOfLines={1}>
                        {r.name}
                      </Text>
                    </Pressable>
                  ))}
                </View>
              ) : searching ? (
                <View style={styles.searchDropdown}>
                  <ActivityIndicator color={colors.onSurface} style={{ padding: spacing.sm }} />
                </View>
              ) : null}
            </View>
            <TextInput
              placeholder="QTY"
              value={form.quantity}
              onChangeText={(t) => setForm((f) => ({ ...f, quantity: t.replace(/[^0-9.]/g, "") }))}
              keyboardType="decimal-pad"
              style={[styles.input, { flex: 1 }]}
              placeholderTextColor={colors.onSurfaceTertiary}
            />
            <TextInput
              placeholder="AVG PRICE"
              value={form.avgPrice}
              onChangeText={(t) => setForm((f) => ({ ...f, avgPrice: t.replace(/[^0-9.]/g, "") }))}
              keyboardType="decimal-pad"
              style={[styles.input, { flex: 1.2 }]}
              placeholderTextColor={colors.onSurfaceTertiary}
            />
            <Pressable onPress={addHolding} style={styles.addBtn}>
              <Text style={styles.addBtnText}>ADD</Text>
            </Pressable>
          </View>
          {quickAddable.length > 0 ? (
            <View style={styles.quickAddRow}>
              <Text style={styles.quickAddLabel}>FROM WATCHLIST:</Text>
              <View style={styles.chipRow}>
                {quickAddable.map((w) => (
                  <Pressable key={w.symbol} onPress={() => addFromWatchlist(w.symbol)} style={styles.chip}>
                    <Text style={styles.chipText}>{w.symbol}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          ) : null}
        </View>

        {/* Current holdings */}
        {holdings.length > 0 ? (
          <View testID="live-pl-card" style={styles.card}>
            <View style={styles.plHeaderRow}>
              <Text style={styles.cardTitle}>HOLDINGS ({holdings.length})</Text>
              {quotesLoading ? <ActivityIndicator size="small" color={colors.onSurfaceTertiary} /> : null}
            </View>
            {pl.rows.map((r) => {
              const up = (r.gain ?? 0) >= 0;
              return (
                <View key={r.symbol} style={styles.holdingRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.holdingSymbol}>{r.symbol}</Text>
                    <Text style={styles.holdingDetail}>
                      {r.qty} @ {r.avg}
                      {r.price != null ? ` · NOW ${r.symbolPrefix}${r.price}` : " · PRICE UNAVAILABLE"}
                    </Text>
                  </View>
                  <View style={styles.plNumbers}>
                    {r.value != null ? (
                      <>
                        <Text testID={`pl-value-${r.symbol}`} style={styles.plValue}>
                          {fmtMoney(r.symbolPrefix, r.value)}
                        </Text>
                        <Text
                          testID={`pl-gain-${r.symbol}`}
                          style={[styles.plGain, { color: up ? verdictColors.BUY : verdictColors.SELL }]}
                        >
                          {up ? "+" : ""}
                          {fmtMoney(r.symbolPrefix, r.gain || 0)}
                          {r.gainPct != null ? ` (${up ? "+" : ""}${fmtPct(r.gainPct)})` : ""}
                        </Text>
                      </>
                    ) : (
                      <Text style={styles.plGain}>—</Text>
                    )}
                  </View>
                  <Pressable onPress={() => removeHolding(r.symbol)} hitSlop={8}>
                    <Text style={styles.removeText}>REMOVE</Text>
                  </Pressable>
                </View>
              );
            })}
            {pl.totals ? (
              pl.totals.mixed ? (
                <Text testID="pl-mixed-note" style={styles.hint}>
                  Holdings are priced in more than one currency, so a single total would be meaningless.
                </Text>
              ) : (
                <View testID="pl-total" style={styles.plTotalBox}>
                  <View style={styles.plTotalRow}>
                    <Text style={styles.plTotalLabel}>INVESTED</Text>
                    <Text style={styles.plTotalValue}>{fmtMoney(pl.totals.prefix!, pl.totals.cost!)}</Text>
                  </View>
                  <View style={styles.plTotalRow}>
                    <Text style={styles.plTotalLabel}>MARKET VALUE</Text>
                    <Text style={styles.plTotalValue}>{fmtMoney(pl.totals.prefix!, pl.totals.value!)}</Text>
                  </View>
                  <View style={styles.plTotalRow}>
                    <Text style={styles.plTotalLabel}>TOTAL P/L</Text>
                    <Text
                      style={[
                        styles.plTotalValue,
                        { color: (pl.totals.gain || 0) >= 0 ? verdictColors.BUY : verdictColors.SELL },
                      ]}
                    >
                      {(pl.totals.gain || 0) >= 0 ? "+" : ""}
                      {fmtMoney(pl.totals.prefix!, pl.totals.gain!)}
                      {pl.totals.gainPct != null
                        ? ` (${(pl.totals.gain || 0) >= 0 ? "+" : ""}${fmtPct(pl.totals.gainPct)})`
                        : ""}
                    </Text>
                  </View>
                </View>
              )
            ) : null}
            <View style={styles.cashRow}>
              <Text style={styles.holdingDetail}>UNINVESTED CASH</Text>
              <TextInput
                value={cash}
                onChangeText={(t) => setCash(t.replace(/[^0-9.]/g, ""))}
                keyboardType="decimal-pad"
                style={styles.cashInput}
              />
            </View>
          </View>
        ) : null}

        {/* Controls */}
        {holdings.length > 0 && holdings.length < 2 ? (
          <Text style={styles.hint}>Add at least one more holding to run the optimizer.</Text>
        ) : null}
        {holdings.length >= 2 ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>OPTIMIZE</Text>
            <View style={styles.objRow}>
              {OBJECTIVES.map((o) => {
                const active = objective === o.id;
                return (
                  <Pressable key={o.id} onPress={() => setObjective(o.id)} style={[styles.objChip, active && styles.objChipActive]}>
                    <Text style={[styles.objChipText, active && styles.objChipTextActive]}>{o.label}</Text>
                  </Pressable>
                );
              })}
            </View>
            <Text style={styles.objBlurb}>{OBJECTIVES.find((o) => o.id === objective)?.blurb}</Text>

            <Pressable onPress={() => setUseAgentViews((v) => !v)} style={styles.toggleRow}>
              <View style={[styles.checkbox, useAgentViews && styles.checkboxOn]}>
                {useAgentViews ? <Text style={styles.checkMark}>✓</Text> : null}
              </View>
              <Text style={styles.toggleText}>Use agent views (Black-Litterman) where available</Text>
            </Pressable>

            <Pressable onPress={runOptimize} style={styles.runBtn} disabled={loading}>
              {loading ? <ActivityIndicator color={colors.onSurfaceInverse} /> : <Text style={styles.runBtnText}>RUN OPTIMIZER</Text>}
            </Pressable>
            {error ? <Text style={styles.errorText}>{error}</Text> : null}
          </View>
        ) : null}

        {/* Results */}
        {result ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>SUGGESTED ACTIONS</Text>
            {result.missing_agent_view_for?.length ? (
              <View style={styles.missingBox}>
                <Text style={styles.note}>
                  No cached analysis for {result.missing_agent_view_for.join(", ")} — their weight is based on price
                  history only.
                </Text>
                <Pressable
                  onPress={analyzeMissing}
                  style={styles.analyzeMissingBtn}
                  disabled={analyzingMissing !== null}
                >
                  {analyzingMissing ? (
                    <>
                      <ActivityIndicator color={colors.onSurfaceInverse} size="small" />
                      <Text style={styles.analyzeMissingText}>ANALYZING {analyzingMissing}…</Text>
                    </>
                  ) : (
                    <Text style={styles.analyzeMissingText}>
                      ANALYZE MISSING ({result.missing_agent_view_for.length}) & RE-RUN
                    </Text>
                  )}
                </Pressable>
              </View>
            ) : null}
            {actionsSorted.map((a: any) => {
              const { bg, fg } = verdictColors(a.action === "ADD" ? "BUY" : a.action === "SELL" ? "SELL" : "HOLD");
              return (
                <View key={a.symbol} style={styles.actionRow}>
                  <View style={[styles.actionBadge, { backgroundColor: bg }]}>
                    <Text style={[styles.actionBadgeText, { color: fg }]}>{a.action}</Text>
                  </View>
                  <Text style={styles.actionSymbol}>{a.symbol}</Text>
                  <Text style={styles.actionWeights}>
                    {fmtPct(a.current_weight)} {"→"} {fmtPct(a.suggested_weight)}
                  </Text>
                </View>
              );
            })}
            <View style={styles.statsRow}>
              <Stat label="EXP. RETURN" value={fmtPct(result.expected_return)} />
              <Stat label="VOLATILITY" value={fmtPct(result.volatility)} />
              <Stat label="SHARPE" value={result.sharpe.toFixed(2)} />
            </View>
            {result.dropped_symbols?.length ? (
              <Text style={styles.note}>Skipped (insufficient history): {result.dropped_symbols.join(", ")}</Text>
            ) : null}
            <Text style={styles.note}>Not financial advice — an allocation model, not a guarantee.</Text>
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.surface },
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, marginBottom: spacing.lg },
  cardTitle: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    letterSpacing: 1,
    color: colors.onSurfaceInverse,
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  formRow: { flexDirection: "row", padding: spacing.sm, gap: spacing.xs },
  input: {
    fontFamily: fonts.mono,
    fontSize: 12,
    color: colors.onSurface,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
  },
  addBtn: { backgroundColor: colors.brand, paddingHorizontal: spacing.md, justifyContent: "center" },
  addBtnText: { fontFamily: fonts.monoBold, fontSize: 11, color: colors.onSurfaceInverse },
  quickAddRow: { paddingHorizontal: spacing.sm, paddingBottom: spacing.sm },
  quickAddLabel: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.onSurfaceTertiary, marginBottom: spacing.xs },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  chip: { borderWidth: 1, borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 4 },
  chipText: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurface },
  holdingRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    gap: spacing.sm,
  },
  holdingSymbol: { flex: 1, fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  holdingDetail: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary },
  plHeaderRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingEnd: spacing.md },
  plNumbers: { alignItems: "flex-end", marginEnd: spacing.md },
  plValue: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  plGain: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary },
  plTotalBox: {
    borderTopWidth: BORDER,
    borderTopColor: colors.borderStrong,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    gap: spacing.xs,
  },
  plTotalRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  plTotalLabel: { fontFamily: fonts.mono, fontSize: 10, letterSpacing: 1, color: colors.onSurfaceTertiary },
  plTotalValue: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  removeText: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.error },
  cashRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  cashInput: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurface, borderBottomWidth: 1, borderBottomColor: colors.borderStrong, minWidth: 80, textAlign: "right" },
  objRow: { flexDirection: "row", padding: spacing.sm, gap: spacing.xs },
  objChip: { flex: 1, borderWidth: 1, borderColor: colors.border, paddingVertical: spacing.sm, alignItems: "center" },
  objChipActive: { backgroundColor: colors.brand, borderColor: colors.brand },
  objChipText: { fontFamily: fonts.monoBold, fontSize: 10, color: colors.onSurface },
  objChipTextActive: { color: colors.onSurfaceInverse },
  objBlurb: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, paddingHorizontal: spacing.md, paddingBottom: spacing.sm },
  toggleRow: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingBottom: spacing.md, gap: spacing.sm },
  checkbox: { width: 16, height: 16, borderWidth: 1, borderColor: colors.borderStrong, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: colors.brand },
  checkMark: { color: colors.onSurfaceInverse, fontSize: 11, fontFamily: fonts.monoBold },
  toggleText: { flex: 1, fontFamily: fonts.mono, fontSize: 11, color: colors.onSurface },
  runBtn: { backgroundColor: colors.surfaceInverse, margin: spacing.md, paddingVertical: spacing.md, alignItems: "center" },
  runBtnText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurfaceInverse },
  errorText: { fontFamily: fonts.mono, fontSize: 11, color: colors.error, padding: spacing.md, paddingTop: 0 },
  actionRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    gap: spacing.sm,
  },
  actionBadge: { paddingHorizontal: spacing.sm, paddingVertical: 4, minWidth: 56, alignItems: "center" },
  actionBadgeText: { fontFamily: fonts.monoBold, fontSize: 10 },
  actionSymbol: { flex: 1, fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  actionWeights: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary },
  statsRow: { flexDirection: "row", borderTopWidth: 1, borderTopColor: colors.border },
  stat: { flex: 1, padding: spacing.sm, borderEndWidth: 1, borderEndColor: colors.border },
  statLabel: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.onSurfaceTertiary },
  statValue: { fontFamily: fonts.mono, fontSize: 14, color: colors.onSurface, marginTop: 2 },
  note: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, padding: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border },
  hint: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginBottom: spacing.lg, paddingHorizontal: spacing.xs },
  searchDropdown: {
    position: "absolute",
    top: 44,
    left: 0,
    right: 0,
    zIndex: 10,
    backgroundColor: colors.surface,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
  },
  searchRow: { paddingHorizontal: spacing.sm, paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
  searchSymbol: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  searchName: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary },
  missingBox: { borderTopWidth: 1, borderTopColor: colors.border },
  analyzeMissingBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    backgroundColor: colors.brand,
    marginHorizontal: spacing.sm,
    marginBottom: spacing.sm,
    paddingVertical: spacing.sm,
  },
  analyzeMissingText: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5, color: colors.onSurfaceInverse },
});
