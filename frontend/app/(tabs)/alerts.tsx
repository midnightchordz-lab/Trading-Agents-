import React, { useCallback, useState } from "react";
import { View, Text, Pressable, FlatList, RefreshControl, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import * as Haptics from "expo-haptics";
import { Trash, BellRinging, BellSlash, ArrowUp, ArrowDown } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api } from "@/src/api";
import { useAlerts, PriceAlert } from "@/src/alerts";
import { ScreenHeader } from "@/src/components/ScreenHeader";

function fmt(n?: number | null, currency?: string): string {
  if (n == null || Number.isNaN(n)) return "—";
  const v = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency && currency !== "USD" ? `${v} ${currency}` : `$${v}`;
}

export default function AlertsScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { items, remove, clearTriggered, evaluate } = useAlerts();
  const [refreshing, setRefreshing] = useState(false);
  const [prices, setPrices] = useState<Record<string, number | null>>({});

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const symbols = Array.from(new Set(items.filter((a) => !a.triggered).map((a) => a.symbol)));
    const results = await Promise.all(
      symbols.map(async (s) => {
        try {
          const q = await api.quote(s);
          return [s, q.price] as const;
        } catch {
          return [s, null] as const;
        }
      }),
    );
    const map: Record<string, number | null> = {};
    results.forEach(([s, p]) => {
      map[s] = p;
      if (p != null) evaluate(s, p);
    });
    setPrices((prev) => ({ ...prev, ...map }));
    setRefreshing(false);
  }, [items, evaluate]);

  useFocusEffect(
    useCallback(() => {
      if (items.some((a) => !a.triggered)) refresh();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [items.length]),
  );

  const sorted = [...items].sort((a, b) => Number(a.triggered) - Number(b.triggered));
  const triggeredCount = items.filter((a) => a.triggered).length;

  const renderItem = useCallback(
    ({ item }: { item: PriceAlert }) => {
      const now = prices[item.symbol];
      const barColor = item.triggered ? colors.brand : item.label === "STOP LOSS" ? colors.error : colors.success;
      return (
        <Pressable
          testID={`alert-row-${item.symbol}`}
          onPress={() => {
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
            router.push(`/(tabs)`);
          }}
          style={[styles.row, { borderLeftWidth: 5, borderLeftColor: barColor }]}
        >
          <View style={styles.rowMain}>
            <View style={styles.rowTop}>
              <Text style={styles.rowSymbol}>{item.symbol}</Text>
              <View style={[styles.tag, { backgroundColor: barColor }]}>
                <Text style={styles.tagText}>{item.label}</Text>
              </View>
              {item.triggered ? (
                <View style={styles.firedTag}>
                  <BellRinging size={11} color={colors.onSurfaceInverse} weight="fill" />
                  <Text style={styles.firedText}>FIRED</Text>
                </View>
              ) : null}
            </View>
            <View style={styles.condRow}>
              {item.direction === "above" ? (
                <ArrowUp size={13} color={colors.onSurfaceTertiary} weight="bold" />
              ) : (
                <ArrowDown size={13} color={colors.onSurfaceTertiary} weight="bold" />
              )}
              <Text style={styles.condText}>
                {item.direction === "above" ? "Rises to" : "Falls to"} {fmt(item.price, item.currency)}
              </Text>
              {!item.triggered && now != null ? (
                <Text style={styles.nowText}> · now {fmt(now, item.currency)}</Text>
              ) : null}
            </View>
          </View>
          <Pressable
            testID={`alert-delete-${item.symbol}`}
            onPress={() => {
              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
              remove(item.id);
            }}
            hitSlop={10}
            style={styles.trashBtn}
          >
            <Trash size={18} color={colors.onSurfaceTertiary} weight="bold" />
          </Pressable>
        </Pressable>
      );
    },
    [prices, remove, router],
  );

  return (
    <View style={styles.root}>
      <ScreenHeader
        title="PRICE ALERTS"
        subtitle={`// ${items.length} ALERT${items.length === 1 ? "" : "S"} · ${triggeredCount} FIRED`}
        insetsTop={insets.top}
        right={
          triggeredCount > 0 ? (
            <Pressable testID="clear-triggered" onPress={clearTriggered} hitSlop={8} style={styles.clearBtn}>
              <Text style={styles.clearText}>CLEAR FIRED</Text>
            </Pressable>
          ) : undefined
        }
      />

      {items.length === 0 ? (
        <View style={styles.emptyWrap}>
          <View style={styles.emptyBox}>
            <BellSlash size={40} color={colors.onSurface} weight="regular" />
            <Text style={styles.emptyTitle}>NO ALERTS SET</Text>
            <Text style={styles.emptyBody}>
              Open any analysis and tap the bell next to a TARGET or STOP LOSS to get pinged when price hits it.
            </Text>
          </View>
        </View>
      ) : (
        <FlatList
          data={sorted}
          keyExtractor={(x) => x.id}
          renderItem={renderItem}
          contentContainerStyle={{ paddingBottom: spacing.xl }}
          ItemSeparatorComponent={() => <View style={styles.sep} />}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.onSurface} />}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  clearBtn: { paddingHorizontal: spacing.sm, paddingVertical: 6, borderWidth: 1.5, borderColor: "#FFFFFF" },
  clearText: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5, color: "#FFFFFF" },

  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  rowMain: { flex: 1 },
  rowTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  rowSymbol: { fontFamily: fonts.monoBold, fontSize: 16, color: colors.onSurface },
  tag: { paddingHorizontal: spacing.sm, paddingVertical: 2 },
  tagText: { fontFamily: fonts.monoBold, fontSize: 9, letterSpacing: 0.5, color: "#FFFFFF" },
  firedTag: { flexDirection: "row", alignItems: "center", gap: 3, backgroundColor: colors.surfaceInverse, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  firedText: { fontFamily: fonts.monoBold, fontSize: 9, letterSpacing: 0.5, color: colors.onSurfaceInverse },
  condRow: { flexDirection: "row", alignItems: "center", marginTop: 5, gap: 3 },
  condText: { fontFamily: fonts.mono, fontSize: 11.5, color: colors.onSurfaceTertiary },
  nowText: { fontFamily: fonts.mono, fontSize: 11.5, color: colors.onSurface },
  trashBtn: { padding: 2 },
  sep: { height: BORDER, backgroundColor: colors.borderStrong },

  emptyWrap: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  emptyBox: { borderWidth: BORDER, borderColor: colors.borderStrong, padding: spacing.xl, alignItems: "center", gap: spacing.sm, width: "100%" },
  emptyTitle: { fontFamily: fonts.display, fontSize: 22, color: colors.onSurface, marginTop: spacing.sm },
  emptyBody: { fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.onSurfaceTertiary, textAlign: "center" },
});
