import React, { useCallback, useState } from "react";
import { View, Text, Pressable, FlatList, RefreshControl, ActivityIndicator, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import * as Haptics from "expo-haptics";
import { Trash, CaretRight, FolderOpen } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER, verdictColors, accentAt } from "@/src/theme";
import { api, Analysis } from "@/src/api";
import { ScreenHeader } from "@/src/components/ScreenHeader";

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "2-digit" }) +
      " · " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return "";
  }
}

function StatusPill({ item }: { item: Analysis }) {
  if (item.status === "completed" && item.verdict) {
    const { bg, fg } = verdictColors(item.verdict.decision);
    return (
      <View style={[styles.pill, { backgroundColor: bg }]}>
        <Text style={[styles.pillText, { color: fg }]}>{item.verdict.decision}</Text>
      </View>
    );
  }
  if (item.status === "running") {
    return (
      <View style={[styles.pill, styles.pillRunning]}>
        <Text style={[styles.pillText, { color: colors.onSurface }]}>RUN {item.current_step}/{item.total_steps}</Text>
      </View>
    );
  }
  return (
    <View style={[styles.pill, { backgroundColor: colors.error }]}>
      <Text style={[styles.pillText, { color: colors.onError }]}>FAILED</Text>
    </View>
  );
}

export default function HistoryScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [items, setItems] = useState<Analysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.history();
      setItems(r.results || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const onDelete = useCallback(
    async (id: string) => {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      setItems((prev) => prev.filter((x) => x.id !== id));
      try {
        await api.remove(id);
      } catch {
        load();
      }
    },
    [load]
  );

  const renderItem = useCallback(
    ({ item, index }: { item: Analysis; index: number }) => (
      <Pressable
        testID={`history-row-${item.symbol}`}
        onPress={() => router.push(`/analysis/${item.id}`)}
        style={[styles.row, { borderStartWidth: 5, borderStartColor: item.verdict ? verdictColors(item.verdict.decision).bg : accentAt(index) }]}
      >
        <View style={styles.rowMain}>
          <Text style={styles.rowSymbol}>{item.symbol}</Text>
          <Text style={styles.rowName} numberOfLines={1}>
            {item.name}
          </Text>
          <Text style={styles.rowDate}>{fmtDate(item.created_at)}</Text>
        </View>
        <StatusPill item={item} />
        <Pressable
          testID={`history-delete-${item.symbol}`}
          onPress={() => onDelete(item.id)}
          hitSlop={10}
          style={styles.trashBtn}
        >
          <Trash size={18} color={colors.onSurfaceTertiary} weight="bold" />
        </Pressable>
        <CaretRight size={16} color={colors.onSurface} weight="bold" />
      </Pressable>
    ),
    [router, onDelete]
  );

  return (
    <View style={styles.root}>
      <ScreenHeader title="DECISION LOG" subtitle={`// ${items.length} ANALYSES ON RECORD`} insetsTop={insets.top} />

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.onSurface} />
        </View>
      ) : items.length === 0 ? (
        <View style={styles.emptyWrap}>
          <View style={styles.emptyBox}>
            <FolderOpen size={40} color={colors.onSurface} weight="regular" />
            <Text style={styles.emptyTitle}>NO LOGS FOUND</Text>
            <Text style={styles.emptyBody}>{"Run an analysis from the ANALYZE tab and the desk's verdicts land here."}</Text>
          </View>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(x) => x.id}
          renderItem={renderItem}
          contentContainerStyle={{ paddingBottom: spacing.xl }}
          ItemSeparatorComponent={() => <View style={styles.sep} />}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
              }}
              tintColor={colors.onSurface}
            />
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: BORDER,
    borderBottomColor: colors.borderStrong,
  },
  brand: { fontFamily: fonts.display, fontSize: 28, color: colors.onSurface, letterSpacing: -1 },
  tagline: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2, letterSpacing: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },

  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  rowMain: { flex: 1 },
  rowSymbol: { fontFamily: fonts.monoBold, fontSize: 16, color: colors.onSurface },
  rowName: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  rowDate: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, marginTop: 4 },
  sep: { height: BORDER, backgroundColor: colors.borderStrong },

  pill: { paddingHorizontal: spacing.sm, paddingVertical: 4, borderWidth: 1.5, borderColor: colors.borderStrong },
  pillRunning: { backgroundColor: colors.surfaceSecondary },
  pillText: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 0.5 },
  trashBtn: { padding: 2 },

  emptyWrap: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  emptyBox: {
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    padding: spacing.xl,
    alignItems: "center",
    gap: spacing.sm,
    width: "100%",
  },
  emptyTitle: { fontFamily: fonts.display, fontSize: 22, color: colors.onSurface, marginTop: spacing.sm },
  emptyBody: { fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.onSurfaceTertiary, textAlign: "center" },
});
