import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { useTranslation } from "react-i18next";
import { colors, fonts, spacing, BORDER, verdictColors, changeColor } from "@/src/theme";
import { Sparkline } from "@/src/components/Sparkline";
import { Quote, Verdict } from "@/src/api";

function fmt(n?: number | null, dp = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

// Fixed-width brutalist card, designed to be captured to a PNG for sharing.
export function ShareCard({
  symbol,
  name,
  verdict,
  quote,
}: {
  symbol: string;
  name: string;
  verdict: Verdict;
  quote: Quote | null;
}) {
  const { t, i18n } = useTranslation();
  const { bg, fg } = verdictColors(verdict.decision);
  const cColor = changeColor(quote?.changePercent);
  const date = new Date().toLocaleDateString(i18n.language || "en", {
    month: "short",
    day: "2-digit",
    year: "numeric",
  });

  return (
    <View style={styles.card}>
      <View style={styles.brandRow}>
        <Text style={styles.brand}>TRADINGAGENTS</Text>
        <Text style={styles.brandSub}>{t("share.ai_desk")}</Text>
      </View>

      <View style={styles.body}>
        <Text style={styles.symbol}>{symbol}</Text>
        <Text style={styles.name} numberOfLines={1}>
          {name}
        </Text>

        {quote && quote.price != null ? (
          <View style={styles.priceRow}>
            <Text style={styles.price}>
              {quote.currency && quote.currency !== "USD" ? "" : "$"}
              {fmt(quote.price)}
            </Text>
            <View style={[styles.changeChip, { backgroundColor: cColor }]}>
              <Text style={styles.changeText}>
                {(quote.changePercent ?? 0) >= 0 ? "▲" : "▼"} {fmt(quote.changePercent)}%
              </Text>
            </View>
          </View>
        ) : null}

        {quote && quote.sparkline?.length > 1 ? (
          <View style={styles.spark}>
            <Sparkline data={quote.sparkline} color={cColor} width={286} height={40} strokeWidth={2} />
          </View>
        ) : null}
      </View>

      <View style={[styles.verdictBlock, { backgroundColor: bg }]}>
        <Text style={[styles.verdictLabel, { color: fg }]}>{t("share.desk_says")}</Text>
        <Text style={[styles.verdictDecision, { color: fg }]}>
          {t(`verdict.${verdict.decision.toLowerCase()}`, { defaultValue: verdict.decision })}
        </Text>
        <View style={styles.confRow}>
          <Text style={[styles.confText, { color: fg }]}>{t("share.confidence")}</Text>
          <Text style={[styles.confVal, { color: fg }]}>{verdict.confidence}%</Text>
        </View>
        <View style={[styles.confTrack, { borderColor: fg }]}>
          <View style={[styles.confFill, { backgroundColor: fg, width: `${Math.max(2, Math.min(100, verdict.confidence))}%` }]} />
        </View>
      </View>

      <View style={styles.thesisWrap}>
        <Text style={styles.thesisLabel}>{t("share.thesis")}</Text>
        <Text style={styles.thesis} numberOfLines={4}>
          {verdict.summary}
        </Text>
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>
          {date} · {t("share.agents_analysis")}
        </Text>
        <Text style={styles.footerText}>{t("share.disclaimer")}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { width: 330, backgroundColor: colors.surface, borderWidth: 3, borderColor: colors.borderStrong },
  brandRow: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: colors.surfaceInverse,
  },
  brand: { fontFamily: fonts.display, fontSize: 16, color: colors.onSurfaceInverse, letterSpacing: -0.5 },
  brandSub: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceInverse, letterSpacing: 1, opacity: 0.7 },

  body: { padding: spacing.lg },
  symbol: { fontFamily: fonts.display, fontSize: 34, color: colors.onSurface, letterSpacing: -1 },
  name: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  priceRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.md },
  price: { fontFamily: fonts.monoBold, fontSize: 20, color: colors.onSurface },
  changeChip: { paddingHorizontal: spacing.sm, paddingVertical: 3 },
  changeText: { fontFamily: fonts.monoBold, fontSize: 12, color: "#FFFFFF" },
  spark: { marginTop: spacing.md, alignItems: "center" },

  verdictBlock: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: BORDER, borderBottomWidth: BORDER, borderColor: colors.borderStrong },
  verdictLabel: { fontFamily: fonts.monoMed, fontSize: 10, letterSpacing: 1 },
  verdictDecision: { fontFamily: fonts.display, fontSize: 52, lineHeight: 56, letterSpacing: -1 },
  confRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end", marginTop: spacing.xs },
  confText: { fontFamily: fonts.mono, fontSize: 11, letterSpacing: 1 },
  confVal: { fontFamily: fonts.monoBold, fontSize: 16 },
  confTrack: { height: 8, borderWidth: 1.5, marginTop: 6 },
  confFill: { height: "100%" },

  thesisWrap: { padding: spacing.lg },
  thesisLabel: { fontFamily: fonts.monoBold, fontSize: 9, letterSpacing: 1.5, color: colors.onSurfaceTertiary, marginBottom: spacing.xs },
  thesis: { fontFamily: fonts.mono, fontSize: 11.5, lineHeight: 17, color: colors.onSurface },

  footer: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderTopWidth: BORDER,
    borderColor: colors.borderStrong,
  },
  footerText: { fontFamily: fonts.mono, fontSize: 8, color: colors.onSurfaceTertiary, letterSpacing: 0.5 },
});
