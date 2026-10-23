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

  useEffect(() => {
    (async () => {
      // The helper is typed for scalars but JSON-encodes anything; the Portfolio
      // tab stores the holdings array the same way.
      setHoldings(
        ((await storage.getItem(KEY_HOLDINGS, null)) as unknown as
          | Holding[]
          | null) || [],
      );
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
              Add your holdings on the Portfolio tab first — the health check
              reads them from there.
            </Text>
          </View>
        ) : (
          <View style={styles.card}>
            <Text style={styles.body}>
              Checks your {holdings.length} holding
              {holdings.length > 1 ? "s" : ""}
              {cash ? " and cash" : ""} against the past year of prices. Free ·
              no AI · takes a few seconds.
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
        <Text style={styles.cardTitle}>DIVERSIFICATION</Text>
        <View style={styles.scoreRow}>
          <Text style={[styles.score, { color: tone }]}>{d.score}</Text>
          <View style={{ flex: 1 }}>
            <Text style={styles.scoreLabel}>{d.label.toUpperCase()}</Text>
            <Text style={styles.small}>
              {money(r.base_currency, r.invested_value)} invested
              {r.cash ? ` + ${money(r.base_currency, r.cash)} cash` : ""}
              {r.days_analysed ? ` · ${r.days_analysed} trading days` : ""}
            </Text>
          </View>
        </View>
        <Bar
          label="Number of real bets"
          value={d.parts.breadth}
          note={`behaves like ~${r.concentration.effective_holdings.toFixed(0)} equal holdings`}
        />
        <Bar
          label="Move independently"
          value={d.parts.independence}
          note={
            r.avg_correlation != null
              ? `avg correlation ${r.avg_correlation.toFixed(2)}`
              : "not enough history"
          }
        />
        <Bar
          label="Spread across sectors"
          value={d.parts.sectors}
          note={
            r.sectors[0]
              ? `largest: ${r.sectors[0].sector} ${pct(r.sectors[0].weight)}`
              : ""
          }
        />
        <Bar
          label="Asset classes"
          value={d.parts.asset_classes}
          note={r.asset_mix
            .filter((a) => a.bucket !== "cash")
            .map((a) => a.label)
            .join(", ")}
        />
      </View>

      {r.observations.length ? (
        <View testID="health-observations" style={styles.card}>
          <Text style={styles.cardTitle}>WHAT WE SEE</Text>
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
            How much of your portfolio's daily movement was just the{" "}
            {r.benchmark.name}.
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
            <Stat label="BETA" value={r.market.beta.toFixed(2)} />
            <Stat label="R²" value={r.market.r_squared.toFixed(2)} />
            <Stat label="CORRELATION" value={r.market.correlation.toFixed(2)} />
          </View>
        </View>
      ) : null}

      {r.risk_return ? (
        <View testID="health-sharpe" style={styles.card}>
          <Text style={styles.cardTitle}>RETURN PER UNIT OF RISK</Text>
          <View style={styles.tableHead}>
            <Text style={[styles.th, { flex: 1.3 }]}> </Text>
            <Text style={styles.th}>YOU</Text>
            <Text style={styles.th}>{r.benchmark.name.toUpperCase()}</Text>
          </View>
          <Row
            label="Yearly return"
            a={pct(r.risk_return.annual_return, 1)}
            b={
              r.benchmark_risk_return
                ? pct(r.benchmark_risk_return.annual_return, 1)
                : "—"
            }
          />
          <Row
            label="Volatility"
            a={pct(r.risk_return.volatility, 1)}
            b={
              r.benchmark_risk_return
                ? pct(r.benchmark_risk_return.volatility, 1)
                : "—"
            }
          />
          <Row
            label="Sharpe ratio"
            a={r.risk_return.sharpe?.toFixed(2) ?? "—"}
            b={r.benchmark_risk_return?.sharpe?.toFixed(2) ?? "—"}
          />
          <Row
            label="Worst fall"
            a={pct(r.risk_return.max_drawdown, 1)}
            b={
              r.benchmark_risk_return
                ? pct(r.benchmark_risk_return.max_drawdown, 1)
                : "—"
            }
          />
          <Text style={styles.small}>
            Your current holdings, held for the past year at today's weights.
            Sharpe uses a {pct(r.risk_free_rate, 1)} risk-free rate.
          </Text>
        </View>
      ) : null}

      {r.frontier ? <FrontierCard f={r.frontier} /> : null}

      {r.clusters.length ? (
        <View testID="health-clusters" style={styles.card}>
          <Text style={styles.cardTitle}>MOVE TOGETHER</Text>
          {r.clusters.map((c, i) => (
            <View key={i} style={styles.listRow}>
              <Text style={[styles.rowMain, { flex: 1 }]}>
                {c.symbols.join(" · ")}
              </Text>
              <Text style={styles.rowSide}>
                {pct(c.weight)} · ρ {c.avg_correlation.toFixed(2)}
              </Text>
            </View>
          ))}
          <Text style={styles.small}>
            Holdings whose prices moved closely together over the past year
            (correlation 0.7 or more) — in practice, one bet.
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
          <Text style={styles.cardTitle}>SECTORS</Text>
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
          <Text style={styles.cardTitle}>LIQUIDITY</Text>
          {r.liquidity
            .filter((l) => l.flag)
            .map((l) => (
              <View key={l.symbol} style={styles.listRow}>
                <Text style={[styles.rowMain, { flex: 1 }]}>{l.symbol}</Text>
                <Text style={styles.rowSide}>
                  {money(r.base_currency, l.avg_daily_value)}/day · you{" "}
                  {pct(l.position_share_of_day, 1)}
                </Text>
              </View>
            ))}
          <Text style={styles.small}>
            Average daily traded value over the last 20 sessions, and your
            position as a share of it.
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
        Based on the past year's prices. These are observations about your
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
        EFFICIENT FRONTIER · YOUR OWN HOLDINGS
      </Text>
      <Text style={styles.small}>
        The line is the best past-year return reachable at each level of risk by
        mixing only the stocks you already own. The dot is your mix.
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
            RISK (VOLATILITY) →
          </SvgText>
          <SvgText
            x={10}
            y={H / 2}
            fontSize={9}
            fill={colors.onSurfaceTertiary}
            textAnchor="middle"
            transform={`rotate(-90 10 ${H / 2})`}
          >
            RETURN →
          </SvgText>
        </Svg>
      </View>
      <View style={styles.statsRow}>
        <Stat label="YOUR RISK" value={pct(f.you.volatility, 1)} />
        <Stat label="YOUR RETURN" value={pct(f.you.return, 1)} />
        <Stat
          label="GAP AT SAME RISK"
          value={
            f.return_gap_at_same_risk != null
              ? `${(f.return_gap_at_same_risk * 100).toFixed(1)} pts`
              : "—"
          }
        />
      </View>
      <Text style={styles.small}>
        Hindsight: the frontier is drawn from what already happened and changes
        every year. It is not a forecast or a target mix.
      </Text>
    </View>
  );
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
