import React, { useEffect, useState } from "react";
import { View, Text, Pressable, Image, ActivityIndicator, StyleSheet } from "react-native";
import * as Haptics from "expo-haptics";
import { Newspaper, ArrowUpRight } from "phosphor-react-native";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api, NewsItem } from "@/src/api";
import { openExternalUrl } from "@/src/utils/openExternalUrl";

const SENTIMENT_STYLE: Record<string, { label: string; bg: string; fg: string }> = {
  BULLISH: { label: "BULLISH", bg: colors.success, fg: colors.onSuccess },
  BEARISH: { label: "BEARISH", bg: colors.error, fg: colors.onError },
  NEUTRAL: { label: "NEUTRAL", bg: colors.surfaceTertiary, fg: colors.onSurfaceTertiary },
};

function SentimentTag({ value }: { value?: NewsItem["sentiment"] }) {
  const s = SENTIMENT_STYLE[value || ""];
  if (!s) return null;
  return (
    <View style={[styles.tag, { backgroundColor: s.bg }]}>
      <Text style={[styles.tagText, { color: s.fg }]}>{s.label}</Text>
    </View>
  );
}

function relTime(unix?: number | null): string {
  if (!unix) return "";
  const secs = Math.floor(Date.now() / 1000) - unix;
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export function NewsList({ symbol }: { symbol: string }) {
  const [items, setItems] = useState<NewsItem[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setFailed(false);
    api
      .news(symbol)
      .then((d) => {
        if (!cancelled) setItems(d.results || []);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const open = (url: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    // Headline links come from Yahoo, so the scheme is checked before opening.
    openExternalUrl(url);
  };

  return (
    <View testID="news-list" style={styles.wrap}>
      <View style={styles.header}>
        <Newspaper size={16} color={colors.onSurface} weight="bold" />
        <Text style={styles.headerText}>LATEST HEADLINES</Text>
      </View>

      {items === null && !failed ? (
        <View style={styles.state}>
          <ActivityIndicator color={colors.onSurface} />
        </View>
      ) : failed || (items && items.length === 0) ? (
        <View style={styles.state}>
          <Text style={styles.stateText}>No recent headlines for {symbol}.</Text>
        </View>
      ) : (
        (items || []).slice(0, 8).map((n, i) => (
          <Pressable
            key={`${n.link}-${i}`}
            testID={`news-item-${i}`}
            onPress={() => open(n.link)}
            style={[styles.row, i > 0 && styles.rowBorder]}
          >
            {n.thumbnail ? (
              <Image source={{ uri: n.thumbnail }} style={styles.thumb} />
            ) : (
              <View style={[styles.thumb, styles.thumbFallback]}>
                <Newspaper size={18} color={colors.onSurfaceTertiary} />
              </View>
            )}
            <View style={styles.rowBody}>
              <Text style={styles.title} numberOfLines={3}>
                {n.title}
              </Text>
              <View style={styles.metaRow}>
                <SentimentTag value={n.sentiment} />
                <Text style={styles.meta} numberOfLines={1}>
                  {n.publisher || "News"}
                  {n.published ? ` · ${relTime(n.published)}` : ""}
                </Text>
                <ArrowUpRight size={13} color={colors.onSurfaceTertiary} weight="bold" />
              </View>
            </View>
          </Pressable>
        ))
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    marginTop: spacing.lg,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: BORDER,
    borderBottomColor: colors.borderStrong,
    backgroundColor: colors.surfaceSecondary,
  },
  headerText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurface },
  state: { padding: spacing.xl, alignItems: "center" },
  stateText: { fontFamily: fonts.mono, fontSize: 11.5, color: colors.onSurfaceTertiary },
  row: { flexDirection: "row", gap: spacing.md, padding: spacing.md },
  rowBorder: { borderTopWidth: 1, borderTopColor: colors.border },
  thumb: { width: 58, height: 58, backgroundColor: colors.surfaceTertiary },
  thumbFallback: { alignItems: "center", justifyContent: "center" },
  rowBody: { flex: 1, justifyContent: "space-between" },
  title: { fontFamily: fonts.monoMed, fontSize: 12.5, lineHeight: 18, color: colors.onSurface },
  metaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.xs, marginTop: spacing.xs },
  meta: { flex: 1, fontFamily: fonts.mono, fontSize: 9.5, color: colors.onSurfaceTertiary, letterSpacing: 0.3 },
  tag: { paddingHorizontal: 5, paddingVertical: 2 },
  tagText: { fontFamily: fonts.monoBold, fontSize: 8.5, letterSpacing: 0.6 },
});
