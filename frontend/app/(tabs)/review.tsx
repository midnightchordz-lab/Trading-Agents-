import React, { useState } from "react";
import {
  View,
  Text,
  Pressable,
  ScrollView,
  ActivityIndicator,
  StyleSheet,
  Alert,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";

import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api, ReviewTable, TradeReview } from "@/src/api";
import { trackEvent } from "@/src/firebase";
import { ScreenHeader } from "@/src/components/ScreenHeader";

// Trade Review: the user's own broker statements (PDF) → how they actually
// trade. Parsed on the server in memory; the files, trades and report are not
// stored anywhere (the report lives only in this screen's state). Everything
// shown describes past trades — no suggestions, no buy/sell language.

type Picked = { uri: string; name: string; file: Blob | null };
const MAX_FILES = 3;

function rupees(n: number, signed = true) {
  const sign = n < 0 ? "−" : signed && n > 0 ? "+" : "";
  const a = Math.abs(n);
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return `${sign}₹${Math.round(a).toLocaleString("en-IN")}`;
}
const pct = (n: number | null | undefined) =>
  n == null ? "—" : `${Math.round(n * 100)}%`;
const tone = (n: number) =>
  n > 0 ? colors.success : n < 0 ? colors.error : colors.onSurface;

export default function ReviewScreen() {
  const insets = useSafeAreaInsets();
  const [files, setFiles] = useState<Picked[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<TradeReview | null>(null);

  const pick = async () => {
    let res: DocumentPicker.DocumentPickerResult;
    try {
      res = await DocumentPicker.getDocumentAsync({
        type: "application/pdf",
        multiple: true,
        copyToCacheDirectory: true,
      });
    } catch {
      Alert.alert(
        "Couldn't open files",
        "Your device didn't allow picking a file.",
      );
      return;
    }
    if (res.canceled || !res.assets?.length) return;
    const added = res.assets.map((a) => ({
      uri: a.uri,
      name: a.name,
      file: (a as { file?: Blob }).file ?? null,
    }));
    setFiles((prev) =>
      [
        ...prev,
        ...added.filter((a) => !prev.some((p) => p.name === a.name)),
      ].slice(0, MAX_FILES),
    );
    setError(null);
  };

  const run = async () => {
    if (!files.length) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.tradeReview(files);
      setReport(r);
      trackEvent("trade_review", {
        files: files.length,
        positions: r.positions,
        contracts: r.contracts,
      });
    } catch (e: any) {
      setError(e?.message || "Couldn't review those statements. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.screen}>
      <ScreenHeader title="TRADE REVIEW" subtitle="How you actually trade" />
      <ScrollView
        contentContainerStyle={{
          padding: spacing.lg,
          paddingBottom: insets.bottom + spacing.xxl,
        }}
      >
        <View style={styles.card}>
          <Text style={styles.cardTitle}>UPLOAD YOUR STATEMENTS</Text>
          <Text style={styles.body}>
            See your own patterns from your broker statements: what you win and
            lose on, by size, time of day, trades per day and after a loss. F&O
            and commodity options. Free.
          </Text>
          <Text style={styles.small}>Supported now (PDF):</Text>
          <Text style={styles.small}>• Upstox — Reports → Trade report</Text>
          <Text style={styles.small}>
            • Kotak Neo — Reports → Transaction statement, plus the Gain/Loss
            statement for exact charges
          </Text>
          <Text style={styles.small}>
            Add up to 3 files for the same account. Files are read once and not
            stored.
          </Text>

          {files.map((f) => (
            <View key={f.name} style={styles.fileRow}>
              <Text style={styles.fileName} numberOfLines={1}>
                {f.name}
              </Text>
              <Pressable
                onPress={() =>
                  setFiles((p) => p.filter((x) => x.name !== f.name))
                }
                hitSlop={8}
              >
                <Text style={styles.remove}>REMOVE</Text>
              </Pressable>
            </View>
          ))}
          {files.length < MAX_FILES ? (
            <Pressable
              testID="review-pick"
              onPress={pick}
              style={styles.pickBtn}
            >
              <Text style={styles.pickText}>
                {files.length ? "ADD ANOTHER PDF" : "CHOOSE PDF STATEMENTS"}
              </Text>
            </Pressable>
          ) : null}
          <Pressable
            testID="review-run"
            onPress={run}
            disabled={!files.length || loading}
            style={[
              styles.runBtn,
              (!files.length || loading) && { opacity: 0.5 },
            ]}
          >
            {loading ? (
              <ActivityIndicator color={colors.onSurfaceInverse} />
            ) : (
              <Text style={styles.runText}>
                {report ? "RUN AGAIN" : "RUN REVIEW"}
              </Text>
            )}
          </Pressable>
          {loading ? (
            <Text style={styles.small}>
              Reading the statements — this can take up to a minute for long
              ones.
            </Text>
          ) : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
        </View>

        {report ? <Report r={report} /> : null}
      </ScrollView>
    </View>
  );
}

function Report({ r }: { r: TradeReview }) {
  const m = r.money;
  return (
    <>
      <View testID="review-summary" style={styles.card}>
        <Text style={styles.cardTitle}>
          RESULT{r.period.from ? ` · ${r.period.from} → ${r.period.to}` : ""}
        </Text>
        <Text style={[styles.big, { color: tone(m.net) }]}>
          {rupees(m.net)}
        </Text>
        <Text style={styles.small}>
          {m.charges_known
            ? "After all charges"
            : "Before charges (not in this file)"}{" "}
          ·{" "}
          {r.sources
            .map((s) => s.broker)
            .filter((b, i, a) => a.indexOf(b) === i)
            .join(", ")}
        </Text>
        <View style={styles.grid}>
          <Stat
            label="TRADING P&L"
            value={rupees(m.gross)}
            color={tone(m.gross)}
          />
          <Stat
            label="CHARGES"
            value={m.charges_known ? rupees(m.charges, false) : "—"}
          />
          <Stat label="WIN RATE" value={pct(m.win_rate)} />
          <Stat
            label="₹ WON PER ₹1 LOST"
            value={
              m.profit_factor != null ? `₹${m.profit_factor.toFixed(2)}` : "—"
            }
          />
          <Stat
            label="AVG WIN"
            value={m.avg_win != null ? rupees(m.avg_win) : "—"}
            color={colors.success}
          />
          <Stat
            label="AVG LOSS"
            value={m.avg_loss != null ? rupees(m.avg_loss) : "—"}
            color={colors.error}
          />
          <Stat
            label={m.counted_in === "contracts" ? "CONTRACTS" : "POSITIONS"}
            value={String(m.count)}
          />
          <Stat
            label="GREEN DAYS"
            value={r.active_days ? `${r.green_days} of ${r.active_days}` : "—"}
          />
        </View>
      </View>

      {r.observations.length ? (
        <View testID="review-observations" style={styles.card}>
          <Text style={styles.cardTitle}>WHAT YOUR TRADES SHOW</Text>
          {r.observations.map((o, i) => (
            <Text key={i} style={styles.obs}>
              • {o}
            </Text>
          ))}
        </View>
      ) : null}

      {r.tables.map((t) => (
        <TableCard key={t.id} t={t} />
      ))}

      <View testID="review-tax" style={styles.card}>
        <Text style={styles.cardTitle}>FOR YOUR TAX FILING</Text>
        <View style={styles.row}>
          <Text style={styles.rowLabel}>F&O turnover</Text>
          <Text style={styles.rowValue}>{rupees(r.tax.turnover, false)}</Text>
        </View>
        <View style={styles.row}>
          <Text style={styles.rowLabel}>Net result after charges</Text>
          <Text
            style={[
              styles.rowValue,
              { color: tone(r.tax.net_pnl_after_charges) },
            ]}
          >
            {rupees(r.tax.net_pnl_after_charges)}
          </Text>
        </View>
        <View style={styles.row}>
          <Text style={styles.rowLabel}>
            Charges{r.tax.charges_exact ? "" : " (not in file)"}
          </Text>
          <Text style={styles.rowValue}>
            {r.tax.charges_exact ? rupees(r.tax.charges, false) : "—"}
          </Text>
        </View>
        <Text style={styles.small}>{r.tax.note}</Text>
      </View>

      {r.notes.map((n, i) => (
        <Text key={i} style={styles.note}>
          {n}
        </Text>
      ))}
      {r.open_positions.length ? (
        <Text style={styles.note}>
          Not counted ({r.open_positions.length} not closed in these files):{" "}
          {r.open_positions
            .slice(0, 5)
            .map((o) => `${o.contract.split("|").join(" ")} — ${o.status}`)
            .join("; ")}
        </Text>
      ) : null}
      <Text style={styles.note}>{r.risk_line}</Text>
    </>
  );
}

function TableCard({ t }: { t: ReviewTable }) {
  const showWin = t.rows.some((r) => r.win != null);
  return (
    <View testID={`review-table-${t.id}`} style={styles.card}>
      <Text style={styles.cardTitle}>{t.title}</Text>
      <View style={styles.thead}>
        <Text style={[styles.th, { flex: 2, textAlign: "left" }]}>
          {t.label.toUpperCase()}
        </Text>
        <Text style={styles.th}>N</Text>
        <Text style={[styles.th, { flex: 1.4 }]}>P&L</Text>
        {showWin ? <Text style={styles.th}>WIN</Text> : null}
      </View>
      {t.rows.map((row) => (
        <View key={row.label} style={styles.tr}>
          <View style={{ flex: 2 }}>
            <Text style={styles.tdLabel}>{row.label}</Text>
            {row.extra ? <Text style={styles.tdExtra}>{row.extra}</Text> : null}
          </View>
          <Text style={styles.td}>{row.n}</Text>
          <Text style={[styles.td, { flex: 1.4, color: tone(row.pnl) }]}>
            {rupees(row.pnl)}
          </Text>
          {showWin ? <Text style={styles.td}>{pct(row.win)}</Text> : null}
        </View>
      ))}
      {t.note ? <Text style={styles.small}>{t.note}</Text> : null}
    </View>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, color ? { color } : null]}>{value}</Text>
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
    lineHeight: 18,
    color: colors.onSurface,
    paddingHorizontal: spacing.md,
    marginBottom: spacing.sm,
  },
  small: {
    fontFamily: fonts.mono,
    fontSize: 10,
    lineHeight: 15,
    color: colors.onSurfaceTertiary,
    paddingHorizontal: spacing.md,
    marginTop: 3,
  },
  fileRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginHorizontal: spacing.md,
    marginTop: spacing.sm,
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  fileName: {
    flex: 1,
    fontFamily: fonts.mono,
    fontSize: 12,
    color: colors.onSurface,
  },
  remove: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.error },
  pickBtn: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    marginHorizontal: spacing.md,
    marginTop: spacing.md,
    paddingVertical: spacing.md,
    alignItems: "center",
  },
  pickText: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    letterSpacing: 1,
    color: colors.onSurface,
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
  big: {
    fontFamily: fonts.monoBold,
    fontSize: 32,
    paddingHorizontal: spacing.md,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    borderTopWidth: 1,
    borderTopColor: colors.border,
    marginTop: spacing.sm,
  },
  stat: {
    width: "50%",
    padding: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
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
  obs: {
    fontFamily: fonts.mono,
    fontSize: 12,
    lineHeight: 18,
    color: colors.onSurface,
    paddingHorizontal: spacing.md,
    paddingVertical: 4,
  },
  thead: {
    flexDirection: "row",
    paddingHorizontal: spacing.md,
    paddingBottom: 4,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  th: {
    flex: 1,
    fontFamily: fonts.monoBold,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
    textAlign: "right",
  },
  tr: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  tdLabel: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    color: colors.onSurface,
  },
  tdExtra: {
    fontFamily: fonts.mono,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
  },
  td: {
    flex: 1,
    fontFamily: fonts.mono,
    fontSize: 11,
    color: colors.onSurface,
    textAlign: "right",
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  rowLabel: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurface },
  rowValue: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    color: colors.onSurface,
  },
  note: {
    fontFamily: fonts.mono,
    fontSize: 10,
    color: colors.onSurfaceTertiary,
    marginBottom: spacing.md,
    paddingHorizontal: spacing.xs,
  },
});
