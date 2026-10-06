import React, { useMemo, useState } from "react";
import { View, Text, Pressable, ScrollView, Modal, ActivityIndicator, StyleSheet, Alert } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import { colors, fonts, spacing } from "@/src/theme";
import { api, ImportRow } from "@/src/api";
import { trackEvent } from "@/src/firebase";

// "Import from Excel / CSV" for the Portfolio tab. The backend parses the file
// and resolves each stock name to a ticker but saves nothing; this preview is
// where the user confirms which rows become holdings, which then go through
// the same local storage as hand-added ones.

export type ImportedHolding = { symbol: string; quantity: string; avgPrice: string };

type Props = {
  existingSymbols: string[];
  /** The optimizer's cap on holdings (the backend rejects more). */
  maxHoldings: number;
  onAdd: (holdings: ImportedHolding[]) => void;
};

type Choice = { include: boolean; symbol: string | null };

const importable = (r: ImportRow) => r.status === "ok" || r.status === "check";

export function ImportHoldings({ existingSymbols, maxHoldings, onAdd }: Props) {
  const insets = useSafeAreaInsets();
  const [uploading, setUploading] = useState(false);
  const [rows, setRows] = useState<ImportRow[] | null>(null);
  const [choices, setChoices] = useState<Record<number, Choice>>({});

  const existing = useMemo(() => new Set(existingSymbols.map((s) => s.toUpperCase())), [existingSymbols]);
  const capacity = Math.max(0, maxHoldings - existing.size);

  const pick = async () => {
    if (uploading) return;
    let picked;
    try {
      // "*/*": Android often labels CSVs as octet-stream, and a narrower filter
      // greys the user's file out. The backend checks the type and says why.
      picked = await DocumentPicker.getDocumentAsync({ type: "*/*", copyToCacheDirectory: true, multiple: false });
    } catch {
      Alert.alert("Couldn't open files", "Your device didn't allow picking a file.");
      return;
    }
    if (picked.canceled || !picked.assets?.[0]) return;
    const asset = picked.assets[0];
    setUploading(true);
    try {
      const res = await api.portfolioImport({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType,
        file: (asset as { file?: Blob }).file ?? null,
      });
      const initial: Record<number, Choice> = {};
      for (const r of res.rows) {
        initial[r.row] = {
          include: importable(r) && !!r.symbol && !existing.has(r.symbol.toUpperCase()),
          symbol: r.symbol,
        };
      }
      setChoices(initial);
      setRows(res.rows);
    } catch (e: unknown) {
      Alert.alert("Couldn't import that file", (e as Error)?.message || "Try again.");
    } finally {
      setUploading(false);
    }
  };

  const selected = rows ? rows.filter((r) => importable(r) && choices[r.row]?.include && choices[r.row]?.symbol) : [];

  const confirm = () => {
    if (!rows) return;
    const seen = new Set(existing);
    const out: ImportedHolding[] = [];
    let skipped = 0;
    for (const r of selected) {
      const sym = (choices[r.row].symbol as string).toUpperCase();
      if (seen.has(sym) || out.length >= capacity) {
        skipped += 1;
        continue;
      }
      seen.add(sym);
      out.push({ symbol: sym, quantity: String(r.quantity), avgPrice: String(r.avg_price) });
    }
    onAdd(out);
    trackEvent("portfolio_imported", { added: out.length, skipped });
    setRows(null);
    if (skipped > 0) {
      Alert.alert(
        `Added ${out.length}`,
        `${skipped} skipped — already in the portfolio, listed twice, or over the ${maxHoldings}-holding limit.`
      );
    }
  };

  return (
    <>
      <Pressable testID="import-holdings-btn" onPress={pick} style={styles.importBtn} disabled={uploading}>
        {uploading ? (
          <ActivityIndicator color={colors.onSurface} />
        ) : (
          <Text style={styles.importBtnText}>IMPORT FROM EXCEL / CSV</Text>
        )}
      </Pressable>
      <Text style={styles.formatHint}>Columns: Name · Quantity · Avg Price. Zerodha and Groww holdings exports work too.</Text>

      <Modal visible={rows != null} animationType="slide" onRequestClose={() => setRows(null)}>
        <View style={[styles.sheet, { paddingTop: insets.top + spacing.sm, paddingBottom: insets.bottom + spacing.sm }]}>
          <Text style={styles.sheetTitle}>REVIEW IMPORT</Text>
          <Text style={styles.sheetSub}>
            Check each match before adding. Rows marked CONFIRM had more than one possible listing — tap the right one.
          </Text>
          <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: spacing.lg }}>
            {(rows || []).map((r) => {
              const ch = choices[r.row];
              const usable = importable(r);
              const already = !!ch?.symbol && existing.has(ch.symbol.toUpperCase());
              return (
                <View key={r.row} style={[styles.row, !usable && styles.rowDisabled]}>
                  <Pressable
                    disabled={!usable}
                    onPress={() => setChoices((c) => ({ ...c, [r.row]: { ...c[r.row], include: !c[r.row].include } }))}
                    style={styles.rowTop}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: !!ch?.include, disabled: !usable }}
                  >
                    <View style={[styles.checkbox, ch?.include && styles.checkboxOn]}>
                      {ch?.include ? <Text style={styles.checkMark}>✓</Text> : null}
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.rowInput} numberOfLines={1}>
                        {r.input || "(blank)"} <Text style={styles.rowNum}>· row {r.row}</Text>
                      </Text>
                      {usable ? (
                        <Text style={styles.rowDetail}>
                          {'→'} {ch?.symbol} {'·'} {r.quantity} {'×'} {r.avg_price}
                          {already ? '  · already in portfolio' : ''}
                        </Text>
                      ) : (
                        <Text style={styles.rowError}>{r.error}</Text>
                      )}
                    </View>
                    {r.status === "check" ? <Text style={styles.badge}>CONFIRM</Text> : null}
                  </Pressable>
                  {r.status === "check" && r.candidates.length > 1 ? (
                    <View style={styles.candRow}>
                      {r.candidates.map((c) => {
                        const on = ch?.symbol === c.symbol;
                        return (
                          <Pressable
                            key={c.symbol}
                            onPress={() => setChoices((s) => ({ ...s, [r.row]: { include: true, symbol: c.symbol } }))}
                            style={[styles.cand, on && styles.candOn]}
                          >
                            <Text style={[styles.candSym, on && styles.candTextOn]}>{c.symbol}</Text>
                            {c.name ? (
                              <Text style={[styles.candName, on && styles.candTextOn]} numberOfLines={1}>
                                {c.name}
                              </Text>
                            ) : null}
                          </Pressable>
                        );
                      })}
                    </View>
                  ) : null}
                </View>
              );
            })}
          </ScrollView>
          <View style={styles.footer}>
            <Pressable onPress={() => setRows(null)} style={styles.cancelBtn}>
              <Text style={styles.cancelText}>CANCEL</Text>
            </Pressable>
            <Pressable
              testID="import-confirm-btn"
              onPress={confirm}
              disabled={selected.length === 0}
              style={[styles.confirmBtn, selected.length === 0 && { opacity: 0.4 }]}
            >
              <Text style={styles.confirmText}>
                ADD {selected.length} HOLDING{selected.length === 1 ? "" : "S"}
              </Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  importBtn: {
    marginHorizontal: spacing.sm,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    paddingVertical: spacing.sm,
    alignItems: "center",
  },
  importBtnText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 0.5, color: colors.onSurface },
  formatHint: {
    fontFamily: fonts.mono,
    fontSize: 9,
    color: colors.onSurfaceTertiary,
    paddingHorizontal: spacing.sm,
    paddingTop: spacing.xs,
    paddingBottom: spacing.sm,
  },
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: spacing.lg },
  sheetTitle: {
    fontFamily: fonts.monoBold,
    fontSize: 12,
    letterSpacing: 1,
    color: colors.onSurfaceInverse,
    backgroundColor: colors.surfaceInverse,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  sheetSub: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, paddingVertical: spacing.sm },
  row: { borderWidth: 1, borderColor: colors.border, marginBottom: spacing.xs },
  rowDisabled: { opacity: 0.55 },
  rowTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.sm },
  checkbox: { width: 16, height: 16, borderWidth: 1, borderColor: colors.borderStrong, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: colors.brand },
  checkMark: { color: colors.onSurfaceInverse, fontSize: 11, fontFamily: fonts.monoBold },
  rowInput: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  rowNum: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceTertiary },
  rowDetail: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  rowError: { fontFamily: fonts.mono, fontSize: 10, color: colors.error, marginTop: 2 },
  badge: { fontFamily: fonts.monoBold, fontSize: 9, color: colors.onSurface, borderWidth: 1, borderColor: colors.borderStrong, paddingHorizontal: 4, paddingVertical: 2 },
  candRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs, paddingHorizontal: spacing.sm, paddingBottom: spacing.sm },
  cand: { borderWidth: 1, borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 4, maxWidth: "100%" },
  candOn: { backgroundColor: colors.brand, borderColor: colors.brand },
  candSym: { fontFamily: fonts.monoBold, fontSize: 11, color: colors.onSurface },
  candName: { fontFamily: fonts.mono, fontSize: 9, color: colors.onSurfaceTertiary },
  candTextOn: { color: colors.onSurfaceInverse },
  footer: { flexDirection: "row", gap: spacing.sm, paddingTop: spacing.sm },
  cancelBtn: { flex: 1, borderWidth: 1, borderColor: colors.borderStrong, paddingVertical: spacing.md, alignItems: "center" },
  cancelText: { fontFamily: fonts.monoBold, fontSize: 12, color: colors.onSurface },
  confirmBtn: { flex: 2, backgroundColor: colors.surfaceInverse, paddingVertical: spacing.md, alignItems: "center" },
  confirmText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1, color: colors.onSurfaceInverse },
});
