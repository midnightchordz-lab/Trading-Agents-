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
import { useFocusEffect, useRouter } from "expo-router";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { storage } from "@/src/utils/storage";
import { useWatchlist } from "@/src/watchlist";
import { api, FundSearchResult, PortfolioReport, SearchResult } from "@/src/api";
import { ImportHoldings, ImportedHolding } from "@/src/components/ImportHoldings";
import { trackEvent } from "@/src/firebase";

// Portfolio tab: manual holdings + watchlist quick-add, then compare the
// user's weights with a model mix from PyPortfolioOpt (POST
// /api/portfolio/optimize). The result is described, never prescribed: labels
// say how a weight compares with the model, not what to buy or sell. Holdings persist locally;
// nothing here touches the analysis pipeline — the backend endpoint only
// *reads* cached verdicts when "Use agent views" is on.

// A mutual fund is stored as symbol "MF:<AMFI scheme code>" with its name;
// quantity is units and avgPrice the average NAV paid.
type Holding = { symbol: string; quantity: string; avgPrice: string; name?: string };

const isFund = (symbol: string) => symbol.startsWith("MF:");
type Objective = "hrp" | "max_sharpe" | "min_volatility";

const KEY_HOLDINGS = "portfolio:holdings";
const KEY_CASH = "portfolio:cash";
// Same cap as POST /api/portfolio/optimize, which rejects longer lists.
const MAX_HOLDINGS = 50;

const OBJECTIVES: { id: Objective; label: string; blurb: string }[] = [
  { id: "hrp", label: "BALANCED", blurb: "Spreads the ups and downs evenly across your stocks. Makes no guess about future returns." },
  { id: "max_sharpe", label: "BEST FOR RISK", blurb: "The split that earned the most for its ups and downs over the past year." },
  { id: "min_volatility", label: "STEADIEST", blurb: "The split that went up and down the least over the past year." },
];

function fmtPct(n: number) {
  return `${(n * 100).toFixed(1)}%`;
}

// 0.178 -> "18": how many of every 100 invested, a number anyone can picture.
function per100(n: number) {
  return `${Math.round(n * 100)}`;
}

// RELIANCE.NS -> RELIANCE: the exchange suffix means nothing to most people.
function shortName(symbol: string) {
  return symbol.replace(/\.(NS|BO)$/, "");
}

// Plain labels for the backend's relation values (which stay unchanged for
// older app builds and the no-advice tests).
const RELATION_LABELS: Record<string, string> = {
  "ABOVE MODEL": "MORE THAN MIX",
  "BELOW MODEL": "LESS THAN MIX",
  "IN LINE": "ABOUT THE SAME",
  "NOT IN MODEL": "NOT IN MIX",
};

// One sentence for the top of the results: the biggest gaps either way.
function summarize(actions: any[]): string {
  const gap = (a: any) => a.current_weight - a.suggested_weight;
  const more = actions.filter((a) => a.action === "ABOVE MODEL" || a.action === "NOT IN MODEL").sort((a, b) => gap(b) - gap(a));
  const less = actions.filter((a) => a.action === "BELOW MODEL").sort((a, b) => gap(a) - gap(b));
  if (!more.length && !less.length) return "Your split is close to the sample mix for every stock.";
  const parts = [];
  if (more.length) parts.push(`more money in ${more.slice(0, 2).map((a) => shortName(a.symbol)).join(" and ")}`);
  if (less.length) parts.push(`less in ${less.slice(0, 2).map((a) => shortName(a.symbol)).join(" and ")}`);
  return `Compared with the sample mix, you have ${parts.join(", and ")}.`;
}

const CURRENCY_SYMBOLS: Record<string, string> = { USD: "$", INR: "\u20b9", GBP: "\u00a3", EUR: "\u20ac" };

function currencySymbol(code?: string | null) {
  return CURRENCY_SYMBOLS[(code || "").toUpperCase()] || (code ? `${code} ` : "");
}

// ₹3,74,000 for rupees (Indian grouping), $1,234 otherwise; paise/cents only
// on small amounts where they matter.
function money(currency: string | null | undefined, n: number) {
  const small = Math.abs(n) < 1000;
  const body = Math.abs(n).toLocaleString((currency || "").toUpperCase() === "INR" ? "en-IN" : "en-US", {
    minimumFractionDigits: small ? 2 : 0,
    maximumFractionDigits: small ? 2 : 0,
  });
  return `${n < 0 ? "-" : ""}${currencySymbol(currency)}${body}`;
}

function signedPct(n: number) {
  return `${n >= 0 ? "+" : ""}${(n * 100).toFixed(1)}%`;
}

function shortDate(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export default function PortfolioScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { items: watchlist } = useWatchlist();
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [cash, setCash] = useState("0");
  const [objective, setObjective] = useState<Objective>("hrp");
  const [useAgentViews, setUseAgentViews] = useState(false);
  const [form, setForm] = useState<Holding>({ symbol: "", quantity: "", avgPrice: "" });
  const [mode, setMode] = useState<"stock" | "fund">("stock");
  const [fundQuery, setFundQuery] = useState("");
  const [fundResults, setFundResults] = useState<FundSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [analyzingMissing, setAnalyzingMissing] = useState<string | null>(null); // symbol currently being analyzed
  // The portfolio report (POST /api/portfolio/report): every stock and fund
  // priced, with its type and a short "what we see" line. Kept separate from
  // `result` so it shows whether or not the comparison has been run.
  const [report, setReport] = useState<PortfolioReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

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

  // Fund search by name, from AMFI's list of every scheme.
  useEffect(() => {
    const q = fundQuery.trim();
    // Nothing to fetch: the list is hidden by `fundListOpen` below instead of
    // being cleared here, so this effect never sets state synchronously.
    if (mode !== "fund" || q.length < 2 || isFund(form.symbol)) return;
    let cancelled = false;
    const t = setTimeout(() => {
      setSearching(true);
      api
        .fundSearch(q)
        .then((r) => {
          if (!cancelled) setFundResults(r.results.slice(0, 8));
        })
        .catch(() => {
          if (!cancelled) setFundResults([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [fundQuery, mode, form.symbol]);

  const fundListOpen = mode === "fund" && fundQuery.trim().length >= 2 && !isFund(form.symbol);

  const pickFund = (r: FundSearchResult) => {
    setForm((f) => ({ ...f, symbol: r.symbol, name: r.name }));
    setFundQuery(r.name);
    setFundResults([]);
  };

  const switchMode = (next: "stock" | "fund") => {
    setMode(next);
    setForm({ symbol: "", quantity: "", avgPrice: "" });
    setFundQuery("");
    setSearchResults([]);
    setFundResults([]);
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
    // For a fund the form asks for the amount invested (what statements show)
    // and stores the average NAV paid, so the maths is the same as a stock's.
    const avgPrice = mode === "fund" ? Number(form.avgPrice) / quantity : Number(form.avgPrice);
    if (mode === "fund" && !isFund(symbol)) {
      Alert.alert("Pick a fund", "Search for the fund by name and pick it from the list.");
      return;
    }
    if (!symbol || !quantity || !avgPrice || !isFinite(avgPrice)) {
      Alert.alert(
        "Missing details",
        mode === "fund" ? "Fund, units and amount invested are required." : "Symbol, quantity and average price are required."
      );
      return;
    }
    if (holdings.some((h) => h.symbol === symbol)) {
      Alert.alert("Already added", `${form.name || symbol} is already in the portfolio.`);
      return;
    }
    persist([
      ...holdings,
      { symbol, quantity: String(quantity), avgPrice: String(avgPrice), ...(form.name ? { name: form.name } : {}) },
    ]);
    setForm({ symbol: "", quantity: "", avgPrice: "" });
    setFundQuery("");
    setSearchResults([]);
  };

  // Rows confirmed in the import preview; ImportHoldings has already dropped
  // duplicates and anything over MAX_HOLDINGS.
  const addImported = (list: ImportedHolding[]) => {
    if (list.length) persist([...holdings, ...list]);
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
  // The comparison works from daily stock prices, so funds sit it out.
  const stockHoldings = holdings.filter((h) => !isFund(h.symbol));
  const fundCount = holdings.length - stockHoldings.length;

  const runOptimize = async () => {
    if (stockHoldings.length < 2) {
      Alert.alert("Add more stocks", "Comparing your split needs at least 2 stocks.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const body = {
        holdings: stockHoldings.map((h) => ({
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
      trackEvent("portfolio_optimized", { objective, holdings: body.holdings.length });
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
    const order: Record<string, number> = { "NOT IN MODEL": 0, "ABOVE MODEL": 1, "BELOW MODEL": 2, "IN LINE": 3 };
    return [...result.actions].sort((a: any, b: any) => (order[a.action] ?? 9) - (order[b.action] ?? 9));
  }, [result]);

  // --- Portfolio report -------------------------------------------------
  const holdingsKey = useMemo(
    () => JSON.stringify(holdings.map((h) => [h.symbol, h.quantity, h.avgPrice])),
    [holdings]
  );

  const refreshReport = useCallback(async () => {
    const list: Holding[] = JSON.parse(holdingsKey).map(([symbol, quantity, avgPrice]: string[]) => ({
      symbol,
      quantity,
      avgPrice,
    }));
    if (list.length === 0) {
      setReport(null);
      return;
    }
    setReportLoading(true);
    try {
      const res = await api.portfolioReport({
        holdings: list.map((h) => ({ symbol: h.symbol, quantity: Number(h.quantity), avg_price: Number(h.avgPrice) })),
        cash: 0,
      });
      setReport(res);
      setReportError(null);
    } catch (e: any) {
      // Keep the last good report on screen rather than blanking it.
      setReportError(e?.message || "Couldn't refresh prices.");
    } finally {
      setReportLoading(false);
    }
  }, [holdingsKey]);

  useEffect(() => {
    refreshReport();
  }, [refreshReport]);

  useFocusEffect(
    useCallback(() => {
      refreshReport();
    }, [refreshReport])
  );

  const reportRows = useMemo(() => {
    const bySymbol = Object.fromEntries((report?.rows || []).map((r) => [r.symbol, r]));
    const order = (report?.rows || []).map((r) => r.symbol);
    const sorted = [...holdings].sort((a, b) => {
      const ia = order.indexOf(a.symbol);
      const ib = order.indexOf(b.symbol);
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
    });
    return sorted.map((h) => ({ h, r: bySymbol[h.symbol] }));
  }, [holdings, report]);

  return (
    <View style={styles.screen}>
      <ScreenHeader title="PORTFOLIO" subtitle="Your stocks, profit & loss and how it's split" />
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: insets.bottom + spacing.xxl }}
        refreshControl={
          <RefreshControl refreshing={reportLoading} onRefresh={refreshReport} tintColor={colors.onSurface} />
        }
      >
        {/* Add holding */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>ADD HOLDING</Text>
          <View style={styles.modeRow}>
            {(["stock", "fund"] as const).map((m) => (
              <Pressable
                key={m}
                testID={`add-mode-${m}`}
                onPress={() => switchMode(m)}
                style={[styles.modeChip, mode === m && styles.modeChipOn]}
              >
                <Text style={[styles.modeChipText, mode === m && styles.modeChipTextOn]}>
                  {m === "stock" ? "STOCK" : "MUTUAL FUND"}
                </Text>
              </Pressable>
            ))}
          </View>
          {mode === "stock" ? (
            <>
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
            </>
          ) : (
            <>
              <TextInput
                testID="fund-search"
                placeholder="SEARCH FUND NAME (e.g. PARAG PARIKH FLEXI)"
                value={fundQuery}
                onChangeText={(t) => {
                  setFundQuery(t);
                  if (isFund(form.symbol)) setForm((f) => ({ ...f, symbol: "", name: undefined }));
                }}
                style={styles.input}
                placeholderTextColor={colors.onSurfaceTertiary}
              />
              {fundListOpen && fundResults.length > 0 ? (
                <View style={styles.fundDropdown}>
                  {fundResults.map((r) => (
                    <Pressable key={r.symbol} onPress={() => pickFund(r)} style={styles.searchRow}>
                      <Text style={styles.fundName} numberOfLines={2}>
                        {r.name}
                      </Text>
                      <Text style={styles.searchName} numberOfLines={1}>
                        {r.type} · NAV ₹{r.nav} ({r.nav_date})
                      </Text>
                    </Pressable>
                  ))}
                </View>
              ) : searching && fundListOpen ? (
                <ActivityIndicator color={colors.onSurface} style={{ padding: spacing.sm }} />
              ) : null}
              <View style={[styles.formRow, { marginTop: spacing.sm }]}>
                <TextInput
                  placeholder="UNITS"
                  value={form.quantity}
                  onChangeText={(t) => setForm((f) => ({ ...f, quantity: t.replace(/[^0-9.]/g, "") }))}
                  keyboardType="decimal-pad"
                  style={[styles.input, { flex: 1 }]}
                  placeholderTextColor={colors.onSurfaceTertiary}
                />
                <TextInput
                  placeholder="AMOUNT INVESTED ₹"
                  value={form.avgPrice}
                  onChangeText={(t) => setForm((f) => ({ ...f, avgPrice: t.replace(/[^0-9.]/g, "") }))}
                  keyboardType="decimal-pad"
                  style={[styles.input, { flex: 1.4 }]}
                  placeholderTextColor={colors.onSurfaceTertiary}
                />
                <Pressable testID="add-fund" onPress={addHolding} style={styles.addBtn}>
                  <Text style={styles.addBtnText}>ADD</Text>
                </Pressable>
              </View>
              <Text style={styles.hint}>Units and amount invested are on your fund statement or app.</Text>
            </>
          )}
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
          <ImportHoldings
            existingSymbols={holdings.map((h) => h.symbol)}
            maxHoldings={MAX_HOLDINGS}
            onAdd={addImported}
          />
        </View>

        {/* Portfolio report: every stock and fund, priced, with what we see */}
        {holdings.length > 0 ? (
          <View testID="portfolio-report" style={styles.card}>
            <View style={styles.plHeaderRow}>
              <Text style={styles.cardTitle}>YOUR PORTFOLIO ({holdings.length})</Text>
              {reportLoading ? <ActivityIndicator size="small" color={colors.onSurfaceTertiary} /> : null}
            </View>
            {report?.totals ? (
              <View testID="report-summary" style={styles.summaryBox}>
                <View style={styles.summaryRow}>
                  <Text style={styles.plTotalLabel}>INVESTED</Text>
                  <Text style={styles.plTotalValue}>{money(report.totals.currency, report.totals.invested)}</Text>
                </View>
                <View style={styles.summaryRow}>
                  <Text style={styles.plTotalLabel}>WORTH NOW</Text>
                  <Text style={styles.plTotalValue}>{money(report.totals.currency, report.totals.value)}</Text>
                </View>
                <View style={styles.summaryRow}>
                  <Text style={styles.plTotalLabel}>RETURN</Text>
                  <Text
                    style={[
                      styles.plTotalValue,
                      { color: report.totals.gain >= 0 ? colors.success : colors.error },
                    ]}
                  >
                    {report.totals.gain_pct != null ? `${signedPct(report.totals.gain_pct)} · ` : ""}
                    {report.totals.gain >= 0 ? "+" : ""}
                    {money(report.totals.currency, report.totals.gain)}
                  </Text>
                </View>
                <Text style={styles.summaryCount}>
                  {report.summary.up} up · {report.summary.down} down
                </Text>
              </View>
            ) : report?.mixed_currencies ? (
              <Text testID="pl-mixed-note" style={styles.hint}>
                Holdings are priced in more than one currency, so a single total would be meaningless.
              </Text>
            ) : null}
            {reportError ? <Text style={styles.hint}>{reportError}</Text> : null}
            {reportRows.map(({ h, r }) => {
              const up = (r?.gain ?? 0) >= 0;
              return (
                <View key={h.symbol} testID={`report-row-${h.symbol}`} style={styles.reportRow}>
                  <View style={styles.reportHead}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.reportName} numberOfLines={2}>
                        {r?.name || h.name || shortName(h.symbol)}
                      </Text>
                      <Text style={styles.holdingDetail}>
                        {r?.type || (isFund(h.symbol) ? "Mutual fund" : shortName(h.symbol))}
                      </Text>
                    </View>
                    {r?.gain_pct != null ? (
                      <Text style={[styles.reportPct, { color: up ? colors.success : colors.error }]}>
                        {signedPct(r.gain_pct)}
                      </Text>
                    ) : null}
                  </View>
                  <Text style={styles.reportMoney}>
                    {r
                      ? `${money(r.currency || "INR", r.invested)} → ${r.value != null ? money(r.currency, r.value) : "—"}`
                      : `${h.quantity} @ ${h.avgPrice}`}
                  </Text>
                  {r?.notes?.length || r?.outlook ? (
                    <Text style={styles.reportNotes}>
                      {[
                        ...(r?.notes || []),
                        ...(r?.outlook ? [`AI desk: ${r.outlook.label} (${shortDate(r.outlook.date)})`] : []),
                      ].join(" · ")}
                    </Text>
                  ) : null}
                  <Pressable onPress={() => removeHolding(h.symbol)} hitSlop={8} style={styles.reportRemove}>
                    <Text style={styles.removeText}>REMOVE</Text>
                  </Pressable>
                </View>
              );
            })}
            <Text style={styles.reportFoot}>
              Company size (large / mid / small-cap) is approximate, from market value. Fund prices are the latest
              NAV from AMFI. &quot;AI desk&quot; is our analysts&apos; latest outlook on that stock — what the numbers
              show, not advice to buy or sell.
            </Text>
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

        {/* Health check — observations about what the portfolio is */}
        {holdings.length > 0 ? (
          <Pressable testID="open-health" onPress={() => router.push("/health")} style={styles.healthBtn}>
            <View style={{ flex: 1 }}>
              <Text style={styles.healthTitle}>HEALTH CHECK</Text>
              <Text style={styles.healthSub}>How spread out your money is, how it did against the market, and how bumpy the ride was · free</Text>
            </View>
            <Text style={styles.healthTitle}>{"→"}</Text>
          </Pressable>
        ) : null}

        {/* Controls */}
        {stockHoldings.length > 0 && stockHoldings.length < 2 ? (
          <Text style={styles.hint}>Add at least one more stock to compare your split with a sample mix.</Text>
        ) : null}
        {fundCount > 0 && stockHoldings.length >= 2 ? (
          <Text style={styles.hint}>Mutual funds aren&apos;t part of the split comparison yet — it uses your stocks.</Text>
        ) : null}
        {stockHoldings.length >= 2 ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>HOW IS YOUR MONEY SPLIT?</Text>
            <Text style={styles.objBlurb}>
              We work out a sample split of the same stocks from their past prices, then show where yours is different.
              Pick the kind of sample:
            </Text>
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
              <Text style={styles.toggleText}>Also use our AI desk&apos;s latest read on these stocks, where there is one</Text>
            </Pressable>

            <Pressable onPress={runOptimize} style={styles.runBtn} disabled={loading}>
              {loading ? <ActivityIndicator color={colors.onSurfaceInverse} /> : <Text style={styles.runBtnText}>COMPARE MY SPLIT</Text>}
            </Pressable>
            {error ? <Text style={styles.errorText}>{error}</Text> : null}
          </View>
        ) : null}

        {/* Results */}
        {result ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>YOUR SPLIT VS THE SAMPLE MIX</Text>
            <Text style={styles.summary}>{summarize(result.actions || [])}</Text>
            {result.missing_agent_view_for?.length ? (
              <View style={styles.missingBox}>
                <Text style={styles.note}>
                  The AI desk hasn&apos;t looked at {result.missing_agent_view_for.map(shortName).join(", ")} yet, so for
                  those the sample uses past prices only.
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
                      ANALYSE THESE ({result.missing_agent_view_for.length}) & COMPARE AGAIN
                    </Text>
                  )}
                </Pressable>
              </View>
            ) : null}
            {actionsSorted.map((a: any) => {
              const { bg, fg } = RELATION_COLORS[a.action] || RELATION_COLORS["IN LINE"];
              return (
                <View key={a.symbol} style={styles.actionRow}>
                  <View style={styles.actionHead}>
                    <Text style={styles.actionSymbol}>{shortName(a.symbol)}</Text>
                    <View style={[styles.actionBadge, { backgroundColor: bg }]}>
                      <Text style={[styles.actionBadgeText, { color: fg }]}>{RELATION_LABELS[a.action] || a.action}</Text>
                    </View>
                  </View>
                  <Text style={styles.actionWeights}>
                    You: {per100(a.current_weight)} of every 100 invested · Sample mix: {per100(a.suggested_weight)}
                  </Text>
                </View>
              );
            })}
            <View style={styles.statsRow}>
              <Stat label="SAMPLE MIX: PAST YEARLY GAIN" value={fmtPct(result.expected_return)} />
              <Stat label="SAMPLE MIX: TYPICAL YEARLY SWING" value={`±${fmtPct(result.volatility)}`} />
            </View>
            {result.dropped_symbols?.length ? (
              <Text style={styles.note}>Left out (not enough price history): {result.dropped_symbols.map(shortName).join(", ")}</Text>
            ) : null}
            <Text style={styles.note}>
              The sample mix is maths on past prices of the stocks you already own. It shows how your split differs
              from it — not what to buy or sell, and not investment advice.
            </Text>
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

// Neutral colours on purpose: buy/sell green and red would read as advice.
const RELATION_COLORS: Record<string, { bg: string; fg: string }> = {
  "ABOVE MODEL": { bg: colors.warning, fg: colors.onWarning },
  "BELOW MODEL": { bg: colors.info, fg: colors.onInfo },
  "IN LINE": { bg: colors.surfaceTertiary, fg: colors.onSurface },
  "NOT IN MODEL": { bg: colors.surfaceInverse, fg: colors.onSurfaceInverse },
};

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
  modeRow: { flexDirection: "row", gap: spacing.sm, marginBottom: spacing.sm },
  modeChip: { flex: 1, paddingVertical: spacing.sm, alignItems: "center", borderWidth: BORDER, borderColor: colors.borderStrong },
  modeChipOn: { backgroundColor: colors.surfaceInverse },
  modeChipText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurface },
  modeChipTextOn: { color: colors.onSurfaceInverse },
  fundDropdown: { borderWidth: 1, borderColor: colors.border, marginTop: spacing.xs },
  fundName: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  summaryBox: { padding: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, gap: spacing.xs },
  summaryRow: { flexDirection: "row", justifyContent: "space-between" },
  summaryCount: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary },
  reportRow: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border, gap: 2 },
  reportHead: { flexDirection: "row", alignItems: "flex-start", gap: spacing.sm },
  reportName: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  reportPct: { fontFamily: fonts.monoBold, fontSize: 14 },
  reportMoney: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurface },
  reportNotes: { fontFamily: fonts.mono, fontSize: 11, lineHeight: 16, color: colors.onSurfaceTertiary },
  reportRemove: { alignSelf: "flex-end" },
  reportFoot: { fontFamily: fonts.mono, fontSize: 10, lineHeight: 15, color: colors.onSurfaceTertiary, padding: spacing.md, borderTopWidth: 1, borderTopColor: colors.border },
  actionRow: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    gap: spacing.xs,
  },
  actionHead: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  summary: { fontFamily: fonts.mono, fontSize: 13, lineHeight: 19, color: colors.onSurface, padding: spacing.md },
  actionBadge: { paddingHorizontal: spacing.sm, paddingVertical: 4, minWidth: 96, alignItems: "center" },
  actionBadgeText: { fontFamily: fonts.monoBold, fontSize: 10 },
  actionSymbol: { flex: 1, fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  actionWeights: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary },
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
  healthBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surfaceSecondary,
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  healthTitle: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurface },
  healthSub: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, marginTop: 2 },
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
