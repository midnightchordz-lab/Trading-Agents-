import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  Pressable,
  ScrollView,
  ActivityIndicator,
  StyleSheet,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import Svg, { Circle, Line, Polyline, Text as SvgText } from "react-native-svg";

import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api, HealthBucket, HealthReport } from "@/src/api";
import { storage } from "@/src/utils/storage";
import { trackEvent } from "@/src/firebase";
import { ScreenHeader } from "@/src/components/ScreenHeader";

// Portfolio Health Check: what the portfolio IS over the past year —
// diversification, how much of it is just the market, return per unit of risk,
// where it sits against the efficient frontier of its own holdings, drift from
// the user's own targets, liquidity. Observations only: nothing here says what
// to buy, sell or change, and the frontier shows points, never the mix behind them.

type Holding = { symbol: string; quantity: string; avgPrice: string };

// Same keys as the Portfolio tab, which owns the holdings.
const KEY_HOLDINGS = "portfolio:holdings";
const KEY_CASH = "portfolio:cash";
const KEY_TARGETS = "portfolio:targets";

const BUCKETS: { id: HealthBucket; label: string }[] = [
  { id: "in_equity", label: "India equity" },
  { id: "us_equity", label: "US equity" },
  { id: "other_equity", label: "Other equity" },
  { id: "debt", label: "Debt & bonds" },
  { id: "gold", label: "Gold" },
  { id: "crypto", label: "Crypto" },
  { id: "cash", label: "Cash" },
];

const pct = (n: number, d = 0) => `${(n * 100).toFixed(d)}%`;

function money(base: string, n: number) {
  const sym = base === "INR" ? "₹" : "$";
  if (base === "INR") {
    if (n >= 1e7) return `${sym}${(n / 1e7).toFixed(1)} Cr`;
    if (n >= 1e5) return `${sym}${(n / 1e5).toFixed(1)} L`;
  } else if (n >= 1e6) return `${sym}${(n / 1e6).toFixed(1)}M`;
  return `${sym}${Math.round(n).toLocaleString("en-IN")}`;
}

export default function HealthScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [cash, setCash] = useState(0);
  const [targets, setTargets] = useState<Partial<Record<HealthBucket, string>>>(
    {},
  );
  const [editingTargets, setEditingTargets] = useState(false);
  const [report, setReport] = useState<HealthReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fundCount, setFundCount] = useState(0);

  useEffect(() => {
    (async () => {
      // The helper is typed for scalars but JSON-encodes anything; the Portfolio
      // tab stores the holdings array the same way.
      const all =
        ((await storage.getItem(KEY_HOLDINGS, null)) as unknown as
          | Holding[]
          | null) || [];
      // Mutual funds ("MF:<code>") have no daily price history from Yahoo, so
      // the health check covers stocks only for now and says so.
      const stocks = all.filter((h) => !h.symbol.startsWith("MF:"));
      setHoldings(stocks);
      setFundCount(all.length - stocks.length);
      setCash(Number(await storage.getItem(KEY_CASH, "0")) || 0);
      setTargets(
        ((await storage.getItem(KEY_TARGETS, null)) as unknown as Partial<
          Record<HealthBucket, string>
        > | null) || {},
      );
    })();
  }, []);

  const targetTotal = BUCKETS.reduce(
    (s, b) => s + (Number(targets[b.id]) || 0),
    0,
  );
  const targetsValid = targetTotal === 0 || Math.abs(targetTotal - 100) <= 1;

  const run = useCallback(async () => {
    if (!holdings.length) return;
    if (!targetsValid) {
      setError(
        `Your targets add up to ${targetTotal}% — make them add up to 100%, or clear them.`,
      );
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const t: Partial<Record<HealthBucket, number>> = {};
      for (const b of BUCKETS)
        if (Number(targets[b.id])) t[b.id] = Number(targets[b.id]);
      const res = await api.portfolioHealth({
        holdings: holdings
          .map((h) => ({ symbol: h.symbol, quantity: Number(h.quantity) }))
          .filter((h) => h.quantity > 0),
        cash,
        targets: targetTotal ? t : null,
      });
      setReport(res);
      trackEvent("health_check", {
        holdings: holdings.length,
        score: res.diversification.score,
      });
    } catch (e: any) {
      setError(e?.message || "Couldn't run the health check. Try again.");
    } finally {
      setLoading(false);
    }
  }, [holdings, cash, targets, targetsValid, targetTotal]);

  const saveTargets = (next: Partial<Record<HealthBucket, string>>) => {
    setTargets(next);
    storage.setItem(KEY_TARGETS, next as unknown as string);
  };

  return (
    <View style={styles.screen}>
      <ScreenHeader
        title="HEALTH CHECK"
        subtitle="// WHAT YOUR PORTFOLIO IS"
        insetsTop={insets.top}
        onBack={() => router.back()}
      />
      <ScrollView
        contentContainerStyle={{
          padding: spacing.lg,
          paddingBottom: insets.bottom + spacing.xxl,
        }}
      >
        {!holdings.length ? (
          <View style={styles.card}>
            <Text style={styles.body}>
              {fundCount
                ? "The health check covers stocks only for now. Add at least one stock on the Portfolio tab."
                : "Add your holdings on the Portfolio tab first — the health check reads them from there."}
            </Text>
          </View>
        ) : (
          <View style={styles.card}>
            <Text style={styles.body}>
              Checks your {holdings.length} holding
              {holdings.length > 1 ? "s" : ""}
              {cash ? " and cash" : ""} against the past year of prices. Free ·
              no AI · takes a few seconds.
              {fundCount
                ? ` Your ${fundCount} mutual fund${fundCount > 1 ? "s are" : " is"} not included yet.`
                : ""}
            </Text>
            <Pressable
              onPress={() => setEditingTargets((v) => !v)}
              style={styles.linkRow}
            >
              <Text style={styles.link}>
                {editingTargets
                  ? "DONE"
                  : targetTotal
                    ? `YOUR TARGET MIX · ${targetTotal}%`
                    : "SET YOUR TARGET MIX (OPTIONAL)"}
              </Text>
            </Pressable>
            {editingTargets ? (
              <View testID="targets-editor" style={styles.targets}>
                <Text style={styles.small}>
                  Your own plan, e.g. 60% India equity, 20% US, 10% gold, 10%
                  cash. We show how far you are from it.
                </Text>
                {BUCKETS.map((b) => (
                  <View key={b.id} style={styles.targetRow}>
                    <Text style={styles.targetLabel}>{b.label}</Text>
                    <TextInput
                      testID={`target-${b.id}`}
                      value={targets[b.id] ?? ""}
                      onChangeText={(t) =>
                        saveTargets({
                          ...targets,
                          [b.id]: t.replace(/[^0-9.]/g, ""),
                        })
                      }
                      keyboardType="decimal-pad"
                      placeholder="0"
                      placeholderTextColor={colors.onSurfaceTertiary}
                      style={styles.targetInput}
                    />
                    <Text style={styles.small}>%</Text>
                  </View>
                ))}
                <Text
                  style={[
                    styles.small,
                    {
                      color: targetsValid
                        ? colors.onSurfaceTertiary
                        : colors.error,
                    },
                  ]}
                >
                  Total {targetTotal}%
                  {targetsValid ? "" : " — needs to be 100%"}
                </Text>
                {targetTotal ? (
                  <Pressable onPress={() => saveTargets({})}>
                    <Text style={styles.link}>CLEAR TARGETS</Text>
                  </Pressable>
                ) : null}
              </View>
            ) : null}
            <Pressable
              testID="run-health"
              onPress={run}
              style={styles.runBtn}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color={colors.onSurfaceInverse} />
              ) : (
                <Text style={styles.runText}>
                  {report ? "RUN AGAIN" : "RUN HEALTH CHECK"}
                </Text>
              )}
            </Pressable>
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>
        )}

        {report ? <Report r={report} /> : null}
      </ScrollView>
    </View>
  );
}

function Report({ r }: { r: HealthReport }) {
  const d = r.diversification;
  const tone =
    d.score >= 75
      ? colors.success
      : d.score >= 50
        ? colors.warning
        : colors.error;
  return (
    <>
      <View testID="health-score" style={styles.card}>
        <Text style={styles.cardTitle}>HOW SPREAD OUT IS YOUR MONEY?</Text>
        <View style={styles.scoreRow}>
          <Text style={[styles.score, { color: tone }]}>{d.score}</Text>
          <View style={{ flex: 1 }}>
            <Text style={styles.scoreLabel}>{(SCORE_WORDS[d.label] || d.label).toUpperCase()}</Text>
            <Text style={styles.small}>
              {money(r.base_currency, r.invested_value)} invested
              {r.cash ? ` + ${money(r.base_currency, r.cash)} cash` : ""}
              {r.days_analysed ? ` · ${r.days_analysed} trading days` : ""}
            </Text>
          </View>
        </View>
        <Bar
          label="How many separate bets"
          value={d.parts.breadth}
          note={`acts like about ${r.concentration.effective_holdings.toFixed(0)} equal stocks`}
        />
        <Bar
          label="Do they rise and fall separately?"
          value={d.parts.independence}
          note={
            r.avg_correlation != null
              ? linkWord(r.avg_correlation)
              : "not enough history"
          }
        />
        <Bar
          label="Spread across industries"
          value={d.parts.sectors}
          note={
            r.sectors[0]
              ? `biggest: ${r.sectors[0].sector} ${pct(r.sectors[0].weight)}`
              : ""
          }
        />
        <Bar
          label="Types of investment"
          value={d.parts.asset_classes}
          note={r.asset_mix
            .filter((a) => a.bucket !== "cash")
            .map((a) => a.label)
            .join(", ")}
        />
      </View>

      {r.observations.length ? (
        <View testID="health-observations" style={styles.card}>
          <Text style={styles.cardTitle}>WHAT STANDS OUT</Text>
          {r.observations.map((o, i) => (
            <Text key={i} style={styles.obs}>
              • {o}
            </Text>
          ))}
        </View>
      ) : null}

      {r.market ? (
        <View testID="health-market" style={styles.card}>
          <Text style={styles.cardTitle}>MARKET VS YOUR PICKS</Text>
          <Text style={styles.small}>
            How much of your portfolio&apos;s daily ups and downs simply followed the{" "}
            {r.benchmark.name}, and how much came from the stocks you picked.
          </Text>
          <View style={styles.split}>
            <View
              style={[
                styles.splitPart,
                {
                  flex: Math.max(r.market.r_squared, 0.02),
                  backgroundColor: colors.brand,
                },
              ]}
            />
            <View
              style={[
                styles.splitPart,
                {
                  flex: Math.max(1 - r.market.r_squared, 0.02),
                  backgroundColor: colors.surfaceTertiary,
                },
              ]}
            />
          </View>
          <View style={styles.splitLegend}>
            <Text style={styles.small}>MARKET {pct(r.market.r_squared)}</Text>
            <Text style={styles.small}>
              YOUR PICKS {pct(1 - r.market.r_squared)}
            </Text>
          </View>
          <View style={styles.statsRow}>
            <Stat
              label={`WHEN THE ${r.benchmark.name.toUpperCase()} MOVES 1%, YOU MOVE`}
              value={`${Math.abs(r.market.beta).toFixed(1)}%`}
            />
          </View>
        </View>
      ) : null}

      {r.risk_return ? (
        <View testID="health-sharpe" style={styles.card}>
          <Text style={styles.cardTitle}>GAINS VS THE BUMPS · PAST YEAR</Text>
          <View style={styles.tableHead}>
            <Text style={[styles.th, { flex: 1.3 }]}> </Text>
            <Text style={styles.th}>YOU</Text>
            <Text style={styles.th}>{r.benchmark.name.toUpperCase()}</Text>
          </View>
          <Row
            label="Gain in the year"
            a={pct(r.risk_return.annual_return, 1)}
            b={
              r.benchmark_risk_return
                ? pct(r.benchmark_risk_return.annual_return, 1)
                : "—"
            }
          />
          <Row
            label="Typical yearly swing"
            a={pct(r.risk_return.volatility, 1)}
            b={
              r.benchmark_risk_return
                ? pct(r.benchmark_risk_return.volatility, 1)
                : "—"
            }
          />
          <Row
            label="Gain for the bumps (higher is better)"
            a={r.risk_return.sharpe?.toFixed(2) ?? "—"}
            b={r.benchmark_risk_return?.sharpe?.toFixed(2) ?? "—"}
          />
          <Row
            label="Biggest drop from a high"
            a={pct(r.risk_return.max_drawdown, 1)}
            b={
              r.benchmark_risk_return
                ? pct(r.benchmark_risk_return.max_drawdown, 1)
                : "—"
            }
          />
          <Text style={styles.small}>
            Your stocks as they are today, as if you had held them for the past
            year. &quot;Gain for the bumps&quot; (the Sharpe ratio) shows how much you earned
            for the ups and downs you sat through, after what a safe deposit
            paying {pct(r.risk_free_rate, 1)} would have earned.
          </Text>
        </View>
      ) : null}

      {r.frontier ? <FrontierCard f={r.frontier} /> : null}

      {r.clusters.length ? (
        <View testID="health-clusters" style={styles.card}>
          <Text style={styles.cardTitle}>STOCKS THAT MOVE AS ONE</Text>
          {r.clusters.map((c, i) => (
            <View key={i} style={styles.listRow}>
              <Text style={[styles.rowMain, { flex: 1 }]}>
                {c.symbols.map(shortName).join(" · ")}
              </Text>
              <Text style={styles.rowSide}>
                {pct(c.weight)} of your money
              </Text>
            </View>
          ))}
          <Text style={styles.small}>
            These rose and fell almost together over the past year, so in
            practice they act like one bet.
          </Text>
        </View>
      ) : null}

      <View testID="health-mix" style={styles.card}>
        <Text style={styles.cardTitle}>
          {r.drift.length ? "YOUR MIX VS YOUR TARGET" : "ASSET MIX"}
        </Text>
        {r.drift.length
          ? r.drift.map((x) => (
              <View key={x.bucket} style={styles.listRow}>
                <Text style={[styles.rowMain, { flex: 1 }]}>{x.label}</Text>
                <Text
                  style={[styles.rowSide, x.flag && { color: colors.warning }]}
                >
                  {pct(x.actual)} vs {pct(x.target)} ({x.drift >= 0 ? "+" : ""}
                  {(x.drift * 100).toFixed(0)})
                </Text>
              </View>
            ))
          : r.asset_mix.map((a) => (
              <Bar
                key={a.bucket}
                label={a.label}
                value={a.weight}
                note={pct(a.weight)}
              />
            ))}
      </View>

      {r.sectors.length ? (
        <View testID="health-sectors" style={styles.card}>
          <Text style={styles.cardTitle}>INDUSTRIES</Text>
          {r.sectors.slice(0, 8).map((s) => (
            <Bar
              key={s.sector}
              label={s.sector}
              value={s.weight}
              note={pct(s.weight)}
            />
          ))}
        </View>
      ) : null}

      {r.liquidity.some((l) => l.flag) ? (
        <View testID="health-liquidity" style={styles.card}>
          <Text style={styles.cardTitle}>EASY TO SELL?</Text>
          {r.liquidity
            .filter((l) => l.flag)
            .map((l) => (
              <View key={l.symbol} style={styles.listRow}>
                <Text style={[styles.rowMain, { flex: 1 }]}>{shortName(l.symbol)}</Text>
                <Text style={styles.rowSide}>
                  {money(r.base_currency, l.avg_daily_value)} traded a day · yours{" "}
                  {pct(l.position_share_of_day, 1)} of that
                </Text>
              </View>
            ))}
          <Text style={styles.small}>
            How much of each stock changes hands on a normal day, and how big
            your holding is next to that. Thinly traded stocks can be slow or
            costly to sell.
          </Text>
        </View>
      ) : null}

      {r.skipped.length ? (
        <Text style={styles.note}>
          Not included:{" "}
          {r.skipped
            .map((s) => `${s.symbol} (${s.reason.toLowerCase()})`)
            .join(", ")}
        </Text>
      ) : null}
      <Text style={styles.note}>
        Based on the past year&apos;s prices. These are observations about your
        portfolio, not advice to buy, sell or change anything.
      </Text>
    </>
  );
}

function FrontierCard({ f }: { f: NonNullable<HealthReport["frontier"]> }) {
  const W = 300;
  const H = 180;
  const P = 28;
  const pts = [...f.curve, f.you];
  const xs = pts.map((p) => p.volatility);
  const ys = pts.map((p) => p.return);
  const [x0, x1] = [Math.min(...xs) * 0.9, Math.max(...xs) * 1.1 || 1];
  const [y0, y1] = [Math.min(...ys, 0), Math.max(...ys) * 1.1 || 1];
  const sx = (v: number) => P + ((v - x0) / (x1 - x0 || 1)) * (W - P * 1.5);
  const sy = (v: number) => H - P - ((v - y0) / (y1 - y0 || 1)) * (H - P * 1.5);
  const curve = [...f.curve]
    .sort((a, b) => a.volatility - b.volatility)
    .map((p) => `${sx(p.volatility)},${sy(p.return)}`)
    .join(" ");
  return (
    <View testID="health-frontier" style={styles.card}>
      <Text style={styles.cardTitle}>
        COULD THE SAME STOCKS HAVE DONE BETTER?
      </Text>
      <Text style={styles.small}>
        The line shows the best gain a different split of the stocks you already
        own could have made last year, for each level of ups and downs. The dot
        is your split: the further below the line, the more a different split
        would have earned for the same bumps.
      </Text>
      <View style={{ alignItems: "center", paddingVertical: spacing.sm }}>
        <Svg width={W} height={H}>
          <Line
            x1={P}
            y1={H - P}
            x2={W - P / 2}
            y2={H - P}
            stroke={colors.border}
          />
          <Line x1={P} y1={P / 2} x2={P} y2={H - P} stroke={colors.border} />
          {y0 < 0 ? (
            <Line
              x1={P}
              y1={sy(0)}
              x2={W - P / 2}
              y2={sy(0)}
              stroke={colors.border}
              strokeDasharray="3,3"
            />
          ) : null}
          <Polyline
            points={curve}
            fill="none"
            stroke={colors.brand}
            strokeWidth={2}
          />
          <Circle
            cx={sx(f.you.volatility)}
            cy={sy(f.you.return)}
            r={6}
            fill={colors.warning}
            stroke={colors.onSurface}
          />
          <SvgText
            x={W / 2}
            y={H - 6}
            fontSize={9}
            fill={colors.onSurfaceTertiary}
            textAnchor="middle"
          >
            MORE UPS AND DOWNS →
          </SvgText>
          <SvgText
            x={10}
            y={H / 2}
            fontSize={9}
            fill={colors.onSurfaceTertiary}
            textAnchor="middle"
            transform={`rotate(-90 10 ${H / 2})`}
          >
            MORE GAIN →
          </SvgText>
        </Svg>
      </View>
      <View style={styles.statsRow}>
        <Stat label="YOUR YEARLY SWING" value={`±${pct(f.you.volatility, 1)}`} />
        <Stat label="YOUR GAIN" value={pct(f.you.return, 1)} />
        <Stat
          label="MISSED GAIN, SAME BUMPS"
          value={
            f.return_gap_at_same_risk != null
              ? `${(f.return_gap_at_same_risk * 100).toFixed(1)}%`
              : "—"
          }
        />
      </View>
      <Text style={styles.small}>
        Hindsight: this is drawn from what already happened and changes every
        year. It is not a forecast or a split to copy.
      </Text>
    </View>
  );
}

// RELIANCE.NS -> RELIANCE: the exchange suffix means nothing to most people.
function shortName(symbol: string) {
  return symbol.replace(/\.(NS|BO)$/, "");
}

const SCORE_WORDS: Record<string, string> = {
  Concentrated: "Mostly in a few stocks",
  "Moderately spread": "Fairly spread out",
  "Well spread": "Well spread out",
};

// The average correlation between holdings, in words.
function linkWord(c: number) {
  if (c >= 0.7) return "mostly move together";
  if (c >= 0.4) return "often move together";
  if (c >= 0.2) return "somewhat linked";
  return "mostly move on their own";
}

function Bar({
  label,
  value,
  note,
}: {
  label: string;
  value: number;
  note?: string;
}) {
  return (
    <View style={styles.barRow}>
      <View style={styles.barHead}>
        <Text style={styles.rowMain}>{label}</Text>
        {note ? (
          <Text style={styles.rowSide} numberOfLines={1}>
            {note}
          </Text>
        ) : null}
      </View>
      <View style={styles.barTrack}>
        <View
          style={[
            styles.barFill,
            { width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` },
          ]}
        />
      </View>
    </View>
  );
}

function Row({ label, a, b }: { label: string; a: string; b: string }) {
  return (
    <View style={styles.tableRow}>
      <Text style={[styles.rowMain, { flex: 1.3 }]}>{label}</Text>
      <Text style={styles.td}>{a}</Text>
      <Text style={styles.td}>{b}</Text>
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
  card: {
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    marginBottom: spacing.lg,
    paddingBottom: spacing.sm,
  },
  cardTitle: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    letterSpacing: 1,
    color: colors.onSurfaceInverse,
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    marginBottom: spacing.sm,
  },
  body: {
    fontFamily: fonts.mono,
    fontSize: 12,
    color: colors.onSurface,
    padding: spacing.md,
    paddingBottom: spacing.sm,
  },
  small: {
    fontFamily: fonts.mono,
    fontSize: 10,
    color: colors.onSurfaceTertiary,
    paddingHorizontal: spacing.md,
    marginTop: 4,
  },
  link: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    color: colors.brand,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  linkRow: { paddingVertical: spacing.xs },
  targets: { paddingBottom: spacing.sm },
  targetRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: 4,
    gap: spacing.sm,
  },
  targetLabel: {
    flex: 1,
    fontFamily: fonts.mono,
    fontSize: 12,
    color: colors.onSurface,
  },
  targetInput: {
    width: 64,
    fontFamily: fonts.mono,
    fontSize: 12,
    color: colors.onSurface,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    textAlign: "right",
  },
  runBtn: {
    backgroundColor: colors.surfaceInverse,
    margin: spacing.md,
    marginBottom: spacing.xs,
    paddingVertical: spacing.md,
    alignItems: "center",
  },
  runText: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    letterSpacing: 1,
    color: colors.onSurfaceInverse,
  },
  error: {
    fontFamily: fonts.mono,
    fontSize: 11,
    color: colors.error,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.xs,
  },
  scoreRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    gap: spacing.md,
    marginBottom: spacing.sm,
  },
  score: { fontFamily: fonts.monoBold, fontSize: 44 },
  scoreLabel: {
    fontFamily: fonts.monoBold,
    fontSize: 14,
    color: colors.onSurface,
  },
  obs: {
    fontFamily: fonts.mono,
    fontSize: 12,
    lineHeight: 18,
    color: colors.onSurface,
    paddingHorizontal: spacing.md,
    paddingVertical: 4,
  },
  split: {
    flexDirection: "row",
    height: 14,
    marginHorizontal: spacing.md,
    marginTop: spacing.sm,
  },
  splitPart: { height: 14 },
  splitLegend: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingEnd: spacing.md,
  },
  statsRow: {
    flexDirection: "row",
    borderTopWidth: 1,
    borderTopColor: colors.border,
    marginTop: spacing.sm,
  },
  stat: {
    flex: 1,
    padding: spacing.sm,
    borderEndWidth: 1,
    borderEndColor: colors.border,
  },
  statLabel: {
    fontFamily: fonts.monoBold,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
  },
  statValue: {
    fontFamily: fonts.mono,
    fontSize: 14,
    color: colors.onSurface,
    marginTop: 2,
  },
  tableHead: {
    flexDirection: "row",
    paddingHorizontal: spacing.md,
    paddingBottom: 4,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  th: {
    flex: 1,
    fontFamily: fonts.monoBold,
    fontSize: 10,
    color: colors.onSurfaceTertiary,
    textAlign: "right",
  },
  tableRow: {
    flexDirection: "row",
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  td: {
    flex: 1,
    fontFamily: fonts.mono,
    fontSize: 12,
    color: colors.onSurface,
    textAlign: "right",
  },
  listRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    gap: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  rowMain: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    color: colors.onSurface,
  },
  rowSide: {
    fontFamily: fonts.mono,
    fontSize: 11,
    color: colors.onSurfaceTertiary,
    flexShrink: 1,
  },
  barRow: { paddingHorizontal: spacing.md, paddingVertical: 4 },
  barHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: spacing.sm,
  },
  barTrack: {
    height: 6,
    backgroundColor: colors.surfaceTertiary,
    marginTop: 3,
  },
  barFill: { height: 6, backgroundColor: colors.brand },
  note: {
    fontFamily: fonts.mono,
    fontSize: 10,
    color: colors.onSurfaceTertiary,
    marginBottom: spacing.md,
    paddingHorizontal: spacing.xs,
  },
});
