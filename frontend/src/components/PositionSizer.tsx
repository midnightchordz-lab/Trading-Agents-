import React, { useEffect, useMemo, useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet } from "react-native";
import { colors, fonts, spacing, BORDER, verdictColors } from "@/src/theme";
import { storage } from "@/src/utils/storage";
import type { Verdict, Quote, Grounding } from "@/src/api";

// Position sizer. Pattern borrowed from ai-hedge-fund's risk stage:
// "conviction requests, risk disposes" — the agents propose entry/target/stop,
// and this deterministic arithmetic decides how much. The LLM never sees or
// influences these numbers. Frontend-only; nothing in the pipeline changes.

const KEY_CAPITAL = "sizer:capital";
const KEY_RISK_PCT = "sizer:risk_pct";
const KEY_MAX_POS_PCT = "sizer:max_pos_pct";

const DEFAULT_CAPITAL = 100000;
const DEFAULT_RISK_PCT = 1; // % of capital risked per trade
const DEFAULT_MAX_POS_PCT = 20; // % of capital in one name

type Props = {
  verdict: Verdict;
  quote: Quote | null | undefined;
  grounding?: Grounding | null;
};

function fmt(n: number, dp = 0): string {
  return n.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function sizePosition(args: {
  capital: number;
  riskPct: number;
  maxPosPct: number;
  price: number;
  stop: number;
  target: number | null;
}) {
  const { capital, riskPct, maxPosPct, price, stop, target } = args;
  const perShareRisk = Math.abs(price - stop);
  if (!(capital > 0) || !(price > 0) || !(perShareRisk > 0)) return null;
  const riskBudget = capital * (riskPct / 100);
  const byRisk = Math.floor(riskBudget / perShareRisk);
  const byCap = Math.floor((capital * (maxPosPct / 100)) / price);
  const qty = Math.max(0, Math.min(byRisk, byCap));
  const clampedBy: "risk" | "position_cap" = byCap < byRisk ? "position_cap" : "risk";
  const notional = qty * price;
  const atRisk = qty * perShareRisk;
  const reward = target != null ? qty * Math.abs(target - price) : null;
  const rr = target != null && perShareRisk > 0 ? Math.abs(target - price) / perShareRisk : null;
  return { qty, notional, atRisk, reward, rr, clampedBy, perShareRisk, riskBudget };
}

export function PositionSizer({ verdict, quote, grounding }: Props) {
  const [capital, setCapital] = useState<string>(String(DEFAULT_CAPITAL));
  const [riskPct, setRiskPct] = useState<string>(String(DEFAULT_RISK_PCT));
  const [maxPosPct, setMaxPosPct] = useState<string>(String(DEFAULT_MAX_POS_PCT));
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      const c = await storage.getItem(KEY_CAPITAL, DEFAULT_CAPITAL);
      const r = await storage.getItem(KEY_RISK_PCT, DEFAULT_RISK_PCT);
      const m = await storage.getItem(KEY_MAX_POS_PCT, DEFAULT_MAX_POS_PCT);
      setCapital(String(c ?? DEFAULT_CAPITAL));
      setRiskPct(String(r ?? DEFAULT_RISK_PCT));
      setMaxPosPct(String(m ?? DEFAULT_MAX_POS_PCT));
      setLoaded(true);
    })();
  }, []);

  useEffect(() => {
    if (!loaded) return;
    const c = Number(capital), r = Number(riskPct), m = Number(maxPosPct);
    if (Number.isFinite(c)) storage.setItem(KEY_CAPITAL, c);
    if (Number.isFinite(r)) storage.setItem(KEY_RISK_PCT, r);
    if (Number.isFinite(m)) storage.setItem(KEY_MAX_POS_PCT, m);
  }, [capital, riskPct, maxPosPct, loaded]);

  const price = quote?.price ?? null;
  const currency = quote?.currency && quote.currency !== "USD" ? quote.currency : "$";
  const isHold = verdict.decision === "HOLD";
  const rejected = grounding?.status === "failed";
  const { bg, fg } = verdictColors(verdict.decision);

  const result = useMemo(() => {
    if (isHold || rejected || price == null || verdict.stop_loss == null) return null;
    return sizePosition({
      capital: Number(capital) || 0,
      riskPct: Math.min(Math.max(Number(riskPct) || 0, 0), 100),
      maxPosPct: Math.min(Math.max(Number(maxPosPct) || 0, 0), 100),
      price,
      stop: verdict.stop_loss,
      target: verdict.target_price,
    });
  }, [capital, riskPct, maxPosPct, price, verdict, isHold, rejected]);

  let blocker: string | null = null;
  if (isHold) blocker = "HOLD verdict — no position to size.";
  else if (rejected) blocker = "Levels were rejected by the grounding check — sizing disabled.";
  else if (price == null) blocker = "No live price available.";
  else if (verdict.stop_loss == null) blocker = "No stop loss in the verdict — cannot size risk.";

  return (
    <View testID="position-sizer" style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.headerText}>POSITION SIZER</Text>
        <Text style={styles.headerSub}>RISK DISPOSES · NOT ADVICE</Text>
      </View>

      <View style={styles.inputs}>
        <Field label={`CAPITAL (${currency})`} value={capital} onChange={setCapital} />
        <Field label="RISK / TRADE %" value={riskPct} onChange={setRiskPct} />
        <Field label="MAX POSITION %" value={maxPosPct} onChange={setMaxPosPct} />
      </View>

      {blocker ? (
        <Text style={styles.blocker}>{blocker}</Text>
      ) : result ? (
        <View>
          <View style={[styles.qtyRow, { backgroundColor: bg }]}>
            <Text style={[styles.qtyLabel, { color: fg }]}>{verdict.decision}</Text>
            <Text style={[styles.qty, { color: fg }]}>{fmt(result.qty)} shares</Text>
          </View>
          <View style={styles.grid}>
            <Stat label="NOTIONAL" value={`${currency === "$" ? "$" : ""}${fmt(result.notional)}${currency !== "$" ? ` ${currency}` : ""}`} />
            <Stat label="AT RISK" value={`${fmt(result.atRisk)} (${((result.atRisk / (Number(capital) || 1)) * 100).toFixed(2)}%)`} tone={colors.error} />
            <Stat label="TO TARGET" value={result.reward != null ? fmt(result.reward) : "—"} tone={colors.success} />
            <Stat label="R : R" value={result.rr != null ? `1 : ${result.rr.toFixed(2)}` : "—"} />
          </View>
          <Text style={styles.note}>
            {result.qty === 0
              ? "Stop is too far for this risk budget — size rounds to zero."
              : result.clampedBy === "position_cap"
                ? `Capped by max position % (risk budget alone allowed more). Per-share risk ${result.perShareRisk.toFixed(2)}.`
                : `Sized by risk budget ${fmt(result.riskBudget)} ÷ per-share risk ${result.perShareRisk.toFixed(2)}.`}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={(t) => onChange(t.replace(/[^0-9.]/g, ""))}
        keyboardType="decimal-pad"
        style={styles.input}
        selectTextOnFocus
      />
    </View>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, tone ? { color: tone } : null]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, marginTop: spacing.md },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceInverse,
  },
  headerText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurfaceInverse },
  headerSub: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceInverse, opacity: 0.7 },
  inputs: { flexDirection: "row", borderBottomWidth: 1, borderBottomColor: colors.border },
  field: { flex: 1, padding: spacing.sm, borderEndWidth: 1, borderEndColor: colors.border },
  fieldLabel: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.onSurfaceTertiary, marginBottom: 2 },
  input: { fontFamily: fonts.mono, fontSize: 14, color: colors.onSurface, borderBottomWidth: BORDER, borderBottomColor: colors.borderStrong, paddingVertical: 2 },
  qtyRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  qtyLabel: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1 },
  qty: { fontFamily: fonts.display, fontSize: 20 },
  grid: { flexDirection: "row", flexWrap: "wrap" },
  stat: { width: "50%", padding: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border },
  statLabel: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.onSurfaceTertiary },
  statValue: { fontFamily: fonts.mono, fontSize: 13, color: colors.onSurface, marginTop: 2 },
  note: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, padding: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border },
  blocker: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, padding: spacing.md },
});
