import React, { useCallback, useRef, useState } from "react";
import {
  View,
  Text,
  Pressable,
  ScrollView,
  ActivityIndicator,
  StyleSheet,
  Alert,
  Modal,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import * as Sharing from "expo-sharing";
import ViewShot from "react-native-view-shot";

import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { useFocusEffect } from "expo-router";
import { api, ReviewTable, TradeReview, TradeReviewPricing } from "@/src/api";
import { trackEvent } from "@/src/firebase";
import { ScreenHeader } from "@/src/components/ScreenHeader";

// Trade Review: the user's own broker statements (PDF, Excel or CSV) → how they actually
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
  const [pricing, setPricing] = useState<TradeReviewPricing | null>(null);

  const loadPricing = useCallback(() => {
    api
      .tradeReviewPricing()
      .then(setPricing)
      .catch(() => setPricing(null));
  }, []);
  useFocusEffect(loadPricing);

  // "2 of 2 free reviews left", or the price once they are used.
  const priceLine = !pricing
    ? null
    : !pricing.charged
      ? "Free"
      : pricing.free_left > 0
        ? `${pricing.free_left} of ${pricing.free_runs} free reviews left · then ${pricing.symbol}${pricing.price} per review`
        : `${pricing.symbol}${pricing.price} per review · wallet ${pricing.symbol}${pricing.balance}`;
  const paidRun = !!pricing && pricing.charged && pricing.free_left === 0;

  const pick = async () => {
    let res: DocumentPicker.DocumentPickerResult;
    try {
      res = await DocumentPicker.getDocumentAsync({
        type: "*/*",
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
      loadPricing();
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
            and commodity options.
          </Text>
          {priceLine ? (
            <Text testID="review-price" style={styles.price}>
              {priceLine}
            </Text>
          ) : null}
          <Text style={styles.small}>
            Your tradebook or P&L — PDF, Excel (.xlsx) or CSV:
          </Text>
          <Text style={styles.small}>
            • Zerodha — Console → Reports → Tradebook (and P&L)
          </Text>
          <Text style={styles.small}>• Upstox — Reports → Trade report</Text>
          <Text style={styles.small}>
            • Kotak Neo — Transaction statement, plus the Gain/Loss statement
            for exact charges
          </Text>
          <Text style={styles.small}>
            • Any other broker&apos;s tradebook as .xlsx or CSV
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
                {files.length ? "ADD ANOTHER FILE" : "CHOOSE STATEMENTS"}
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
                {paidRun ? ` · ${pricing!.symbol}${pricing!.price}` : ""}
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
          {report?.billing && !loading ? (
            <Text style={styles.small}>
              {report.billing.free_run
                ? pricing?.charged
                  ? `Free review used · ${report.billing.free_left} left`
                  : "Free review"
                : `${report.billing.symbol}${report.billing.charged} charged from your wallet`}
            </Text>
          ) : null}
        </View>

        {report ? <Report r={report} /> : null}
      </ScrollView>
    </View>
  );
}

function Report({ r }: { r: TradeReview }) {
  const m = r.money;
  const [shareOpen, setShareOpen] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [shareErr, setShareErr] = useState<string | null>(null);
  const shotRef = useRef<React.ElementRef<typeof ViewShot>>(null);

  const doShare = async () => {
    try {
      setSharing(true);
      setShareErr(null);
      const uri = await shotRef.current?.capture?.();
      if (!uri) throw new Error("capture failed");
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, {
          mimeType: "image/png",
          dialogTitle: "My trade review",
        });
        trackEvent("trade_review", { shared: 1 });
      } else {
        setShareErr(
          "Sharing isn't available here — open the app on your phone to share.",
        );
      }
    } catch {
      setShareErr("Couldn't make the image. Please try again.");
    } finally {
      setSharing(false);
    }
  };

  return (
    <>
      <Pressable
        testID="review-share"
        onPress={() => {
          setShareErr(null);
          setShareOpen(true);
        }}
        style={styles.pickBtn}
      >
        <Text style={styles.pickText}>SHARE MY REVIEW (NO AMOUNTS)</Text>
      </Pressable>
      <View style={{ height: spacing.md }} />
      <Modal
        visible={shareOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setShareOpen(false)}
      >
        <View style={styles.backdrop}>
          <ViewShot
            ref={shotRef}
            options={{ format: "png", quality: 1 }}
            style={{ backgroundColor: colors.surface }}
          >
            <ShareCard s={r.share} />
          </ViewShot>
          {shareErr ? <Text style={styles.shareErr}>{shareErr}</Text> : null}
          <View style={styles.modalBtns}>
            <Pressable
              onPress={() => setShareOpen(false)}
              style={[styles.modalBtn, styles.modalGhost]}
            >
              <Text style={styles.modalGhostText}>CLOSE</Text>
            </Pressable>
            <Pressable
              testID="review-share-go"
              onPress={doShare}
              disabled={sharing}
              style={[styles.modalBtn, { backgroundColor: colors.brand }]}
            >
              {sharing ? (
                <ActivityIndicator color={colors.onSurfaceInverse} />
              ) : (
                <Text style={styles.modalGoText}>SHARE</Text>
              )}
            </Pressable>
          </View>
        </View>
      </Modal>

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

      {r.what_if.length ? (
        <View testID="review-whatif" style={styles.card}>
          <Text style={styles.cardTitle}>
            HINDSIGHT · YOUR OWN TRADES, RECALCULATED
          </Text>
          {r.what_if.map((w) => (
            <View key={w.id} style={styles.whatIf}>
              <Text style={styles.tdLabel}>{w.title}</Text>
              <Text style={styles.tdExtra}>{w.detail}</Text>
              <View style={styles.whatIfNums}>
                <Text style={styles.small}>
                  Actual{" "}
                  <Text style={{ color: tone(w.actual) }}>
                    {rupees(w.actual)}
                  </Text>
                </Text>
                <Text style={styles.small}>
                  Recalculated{" "}
                  <Text style={{ color: tone(w.hypothetical) }}>
                    {rupees(w.hypothetical)}
                  </Text>
                </Text>
              </View>
            </View>
          ))}
          <Text style={styles.small}>{r.what_if_note}</Text>
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

// The image people share: ratios and percentages only — no rupee amount ever
// appears on it, so nobody's P&L leaves their phone by accident.
function ShareCard({ s }: { s: TradeReview["share"] }) {
  return (
    <View style={styles.shareCard}>
      <Text style={styles.shareBrand}>TRADINGAGENTS · TRADE REVIEW</Text>
      {s.period.from ? (
        <Text style={styles.shareSub}>
          {s.period.from} → {s.period.to}
        </Text>
      ) : null}
      <View style={styles.shareStats}>
        <View style={styles.shareStat}>
          <Text style={styles.shareNum}>{pct(s.win_rate)}</Text>
          <Text style={styles.shareLabel}>WIN RATE</Text>
        </View>
        <View style={styles.shareStat}>
          <Text style={styles.shareNum}>
            {s.profit_factor != null ? s.profit_factor.toFixed(2) : "—"}
          </Text>
          <Text style={styles.shareLabel}>WON PER 1 LOST</Text>
        </View>
        <View style={styles.shareStat}>
          <Text style={styles.shareNum}>{s.count}</Text>
          <Text style={styles.shareLabel}>{s.counted_in.toUpperCase()}</Text>
        </View>
      </View>
      {s.active_days ? (
        <Text style={styles.shareSub}>
          {s.green_days} green days of {s.active_days}
        </Text>
      ) : null}
      {s.lines.map((l, i) => (
        <Text key={i} style={styles.shareLine}>
          • {l}
        </Text>
      ))}
      <Text style={styles.shareFoot}>
        From my own broker statements · not advice
      </Text>
    </View>
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
  whatIf: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  whatIfNums: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 2,
  },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.md,
    padding: spacing.lg,
  },
  shareErr: {
    fontFamily: fonts.mono,
    fontSize: 11,
    color: colors.onSurfaceInverse,
    textAlign: "center",
  },
  modalBtns: { flexDirection: "row", gap: spacing.md, width: 320 },
  modalBtn: {
    flex: 1,
    height: 48,
    alignItems: "center",
    justifyContent: "center",
  },
  modalGhost: { borderWidth: 1, borderColor: colors.onSurfaceInverse },
  modalGhostText: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    color: colors.onSurfaceInverse,
  },
  modalGoText: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    color: colors.onSurfaceInverse,
  },
  shareCard: {
    width: 320,
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
  },
  shareBrand: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    letterSpacing: 1,
    color: colors.brand,
  },
  shareSub: {
    fontFamily: fonts.mono,
    fontSize: 10,
    color: colors.onSurfaceTertiary,
    marginTop: 4,
  },
  shareStats: {
    flexDirection: "row",
    marginTop: spacing.md,
    marginBottom: spacing.sm,
  },
  shareStat: { flex: 1 },
  shareNum: {
    fontFamily: fonts.monoBold,
    fontSize: 24,
    color: colors.onSurface,
  },
  shareLabel: {
    fontFamily: fonts.monoBold,
    fontSize: 8,
    color: colors.onSurfaceTertiary,
    letterSpacing: 0.5,
  },
  shareLine: {
    fontFamily: fonts.mono,
    fontSize: 11,
    lineHeight: 16,
    color: colors.onSurface,
    marginTop: 6,
  },
  shareFoot: {
    fontFamily: fonts.mono,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
    marginTop: spacing.md,
  },
  price: {
    fontFamily: fonts.monoBold,
    fontSize: 11,
    color: colors.brand,
    paddingHorizontal: spacing.md,
    marginBottom: spacing.sm,
  },
  note: {
    fontFamily: fonts.mono,
    fontSize: 10,
    color: colors.onSurfaceTertiary,
    marginBottom: spacing.md,
    paddingHorizontal: spacing.xs,
  },
});
