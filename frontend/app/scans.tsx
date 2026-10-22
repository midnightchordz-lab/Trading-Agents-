import React, { useCallback, useRef, useState } from "react";
import { View, Text, Pressable, ScrollView, ActivityIndicator, RefreshControl, StyleSheet, Alert } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import * as Haptics from "expo-haptics";
import { CaretDown, CaretRight } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER, changeColor } from "@/src/theme";
import { api, ScanItem, ScanMeta } from "@/src/api";
import { trackEvent } from "@/src/firebase";
import { getWalletDeviceId } from "@/src/wallet";
import { getCurrentLanguage } from "@/src/i18n";
import { ScreenHeader } from "@/src/components/ScreenHeader";

// Stock scanners: factual filters ("RSI below 30", "top gainers today"),
// refreshed by the backend. Every list says how fresh it is — LIVE, 15-MIN
// DELAYED or the time it was computed — because people use this during market
// hours and an old list must never pass for a live one. Tapping a stock runs
// the full desk on it, which always fetches the current price itself.

type Group = "in" | "us" | "crypto";
const GROUPS: { id: Group; label: string }[] = [
  { id: "in", label: "INDIA" },
  { id: "us", label: "US" },
  { id: "crypto", label: "CRYPTO" },
];
const REFRESH_MS = 60_000;
const LIVE_FRESH_MS = 5 * 60_000;

function timeLabel(iso: string): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  const time = d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString("en-IN", { day: "numeric", month: "short" })} ${time}`;
}

function freshness(s: ScanMeta, marketOpen: boolean): { text: string; tone: string } {
  if (!s.as_of) return { text: "NOT YET AVAILABLE", tone: colors.onSurfaceTertiary };
  if (s.kind === "live") {
    const recent = Date.now() - new Date(s.as_of).getTime() < LIVE_FRESH_MS;
    if (marketOpen && recent && !s.delayed) return { text: "LIVE", tone: colors.success };
    if (marketOpen && s.delayed) return { text: `15-MIN DELAYED · ${timeLabel(s.as_of)}`, tone: colors.warning };
    return { text: `AS OF ${timeLabel(s.as_of)}`, tone: colors.onSurfaceTertiary };
  }
  return { text: `UPDATED ${timeLabel(s.as_of)}`, tone: colors.onSurfaceTertiary };
}

export default function ScansScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [group, setGroup] = useState<Group>("in");
  const [scans, setScans] = useState<ScanMeta[] | null>(null);
  const [marketOpen, setMarketOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [items, setItems] = useState<Record<string, ScanItem[]>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const openRef = useRef<string | null>(null);

  const loadItems = useCallback(async (id: string) => {
    setLoadingId(id);
    try {
      const res = await api.scan(id);
      setItems((prev) => ({ ...prev, [id]: res.items }));
    } catch {
      setItems((prev) => ({ ...prev, [id]: [] }));
    } finally {
      setLoadingId(null);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await api.scans();
      setScans(res.scans);
      setMarketOpen(res.india_market_open);
      setError(null);
      if (openRef.current) loadItems(openRef.current);
    } catch (e: any) {
      setError(e?.message || "Couldn't load scans.");
    }
  }, [loadItems]);

  // Refresh while the screen is visible, so a live list keeps up.
  useFocusEffect(
    useCallback(() => {
      load();
      const t = setInterval(load, REFRESH_MS);
      return () => clearInterval(t);
    }, [load])
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const toggle = (id: string) => {
    Haptics.selectionAsync();
    const next = open === id ? null : id;
    setOpen(next);
    openRef.current = next;
    if (next) loadItems(next);
  };

  const analyze = async (it: ScanItem) => {
    if (starting) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setStarting(it.analyze_symbol);
    try {
      const res = await api.analyze(it.analyze_symbol, undefined, getCurrentLanguage(), await getWalletDeviceId());
      trackEvent("analysis_started", { source: "scan" });
      router.push(`/analysis/${res.id}`);
    } catch (e: any) {
      const msg: string = e?.message || "Something went wrong. Try again.";
      Alert.alert(msg.toLowerCase().includes("insufficient balance") ? "Add funds to continue" : "Couldn't start the analysis", msg);
    } finally {
      setStarting(null);
    }
  };

  const visible = (scans || []).filter((s) => s.group === group);
  const sections: { title: string; rows: ScanMeta[] }[] = [
    { title: group === "in" ? `LIVE · NSE ${marketOpen ? "OPEN" : "CLOSED"}` : "", rows: visible.filter((s) => s.kind === "live") },
    { title: "DAILY · FROM CLOSING PRICES", rows: visible.filter((s) => s.kind === "daily") },
  ].filter((s) => s.rows.length > 0);

  return (
    <View style={styles.root}>
      <ScreenHeader title="SCANS" subtitle="// WHAT THE NUMBERS SHOW" insetsTop={insets.top} onBack={() => router.back()} />

      <View style={styles.tabs}>
        {GROUPS.map((g) => (
          <Pressable
            key={g.id}
            testID={`scans-tab-${g.id}`}
            onPress={() => {
              setGroup(g.id);
              setOpen(null);
              openRef.current = null;
            }}
            style={[styles.tab, group === g.id && styles.tabOn]}
          >
            <Text style={[styles.tabText, group === g.id && styles.tabTextOn]}>{g.label}</Text>
          </Pressable>
        ))}
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: insets.bottom + spacing.xxl }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        {scans === null && !error ? <ActivityIndicator color={colors.onSurface} style={{ marginTop: spacing.xl }} /> : null}
        {error ? (
          <Text testID="scans-error" style={styles.note}>
            {error}
          </Text>
        ) : null}

        {sections.map((sec) => (
          <View key={sec.title || "live"} style={styles.section}>
            {sec.title ? <Text style={styles.sectionTitle}>{sec.title}</Text> : null}
            {sec.rows.map((s) => {
              const f = freshness(s, marketOpen);
              const isOpen = open === s.id;
              const rows = items[s.id];
              return (
                <View key={s.id} style={styles.card}>
                  <Pressable testID={`scan-${s.id}`} onPress={() => toggle(s.id)} style={styles.cardHead}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.cardTitle}>{s.title}</Text>
                      <Text style={styles.cardDesc}>{s.description}</Text>
                      <Text style={[styles.fresh, { color: f.tone }]}>{f.text}</Text>
                    </View>
                    <View style={styles.countBox}>
                      <Text style={styles.count}>{s.count}</Text>
                    </View>
                    {isOpen ? (
                      <CaretDown size={16} color={colors.onSurface} weight="bold" />
                    ) : (
                      <CaretRight size={16} color={colors.onSurface} weight="bold" />
                    )}
                  </Pressable>

                  {isOpen ? (
                    <View style={styles.items}>
                      {loadingId === s.id && !rows ? <ActivityIndicator color={colors.onSurface} /> : null}
                      {s.note ? <Text style={styles.note}>{s.note}</Text> : null}
                      {rows && rows.length === 0 && !s.note ? <Text style={styles.note}>Nothing matches right now.</Text> : null}
                      {(rows || []).map((it) => (
                        <Pressable
                          key={it.symbol}
                          testID={`scan-item-${it.symbol}`}
                          onPress={() => analyze(it)}
                          disabled={!!starting}
                          style={styles.item}
                        >
                          <View style={{ flex: 1 }}>
                            <Text style={styles.itemSymbol}>{it.symbol}</Text>
                            <Text style={styles.itemLabel}>{it.label}</Text>
                          </View>
                          <View style={{ alignItems: "flex-end" }}>
                            <Text style={styles.itemPrice}>{it.price != null ? it.price.toLocaleString("en-IN") : "—"}</Text>
                            <Text style={[styles.itemChange, { color: changeColor(it.change_pct) }]}>
                              {it.change_pct != null ? `${it.change_pct >= 0 ? "+" : ""}${it.change_pct.toFixed(2)}%` : ""}
                            </Text>
                          </View>
                          {starting === it.analyze_symbol ? (
                            <ActivityIndicator color={colors.onSurface} style={{ marginStart: spacing.sm }} />
                          ) : (
                            <CaretRight size={14} color={colors.onSurfaceTertiary} style={{ marginStart: spacing.sm }} />
                          )}
                        </Pressable>
                      ))}
                    </View>
                  ) : null}
                </View>
              );
            })}
          </View>
        ))}

        <Text style={styles.footer}>
          Scans list what the numbers show, not what to do. Tap a stock to run the full desk on it — the analysis uses the
          current price, not the scan&apos;s.
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  scroll: { flex: 1 },
  tabs: { flexDirection: "row", borderBottomWidth: BORDER, borderBottomColor: colors.borderStrong },
  tab: { flex: 1, paddingVertical: spacing.md, alignItems: "center" },
  tabOn: { backgroundColor: colors.surfaceInverse },
  tabText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1.5, color: colors.onSurface },
  tabTextOn: { color: colors.onSurfaceInverse },
  section: { marginBottom: spacing.xl },
  sectionTitle: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurfaceTertiary, marginBottom: spacing.sm },
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, marginBottom: spacing.md },
  cardHead: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.md },
  cardTitle: { fontFamily: fonts.display, fontSize: 16, color: colors.onSurface },
  cardDesc: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  fresh: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 1, marginTop: spacing.xs },
  countBox: { minWidth: 32, paddingHorizontal: spacing.sm, paddingVertical: 2, backgroundColor: colors.surfaceInverse, alignItems: "center" },
  count: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurfaceInverse },
  items: { borderTopWidth: 1, borderTopColor: colors.divider, paddingVertical: spacing.xs },
  item: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: 48 },
  itemSymbol: { fontFamily: fonts.monoBold, fontSize: 14, color: colors.onSurface },
  itemLabel: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  itemPrice: { fontFamily: fonts.monoBold, fontSize: 13, color: colors.onSurface },
  itemChange: { fontFamily: fonts.mono, fontSize: 11, marginTop: 2 },
  note: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, padding: spacing.md },
  footer: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, lineHeight: 15 },
});
