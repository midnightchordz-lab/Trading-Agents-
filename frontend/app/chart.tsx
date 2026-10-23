import React, { useEffect, useRef, useState } from "react";
import { View, Text, Pressable, ScrollView, ActivityIndicator, TextInput, StyleSheet, Alert } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import * as Haptics from "expo-haptics";
import { ImageSquare, CheckCircle, RadioButton } from "phosphor-react-native";

import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api, ChartReadResult, SearchResult } from "@/src/api";
import { trackEvent } from "@/src/firebase";
import { getWalletDeviceId } from "@/src/wallet";
import { getCurrentLanguage } from "@/src/i18n";
import { ScreenHeader } from "@/src/components/ScreenHeader";

// "Analyse a chart": upload a screenshot of any price chart, see what the AI
// read off it, confirm which asset it is, then run the normal desk — on real
// data, with the screenshot as extra context and a "your chart vs the data"
// check on the result. The image is read once on the server and not stored.

type Phase = "pick" | "reading" | "result";
type Pick = { symbol: string; name?: string | null };

export default function ChartScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("pick");
  const [result, setResult] = useState<ChartReadResult | null>(null);
  const [choice, setChoice] = useState<Pick | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [starting, setStarting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const q = query.trim();
    if (!q) return;
    timer.current = setTimeout(async () => {
      try {
        setResults((await api.search(q)).results || []);
      } catch {
        setResults([]);
      }
    }, 350);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query]);

  const pick = async () => {
    let picked;
    try {
      picked = await DocumentPicker.getDocumentAsync({
        type: ["image/png", "image/jpeg", "image/webp"],
        copyToCacheDirectory: true,
        multiple: false,
      });
    } catch {
      Alert.alert("Couldn't open your photos", "Your device didn't allow picking an image.");
      return;
    }
    if (picked.canceled || !picked.assets?.[0]) return;
    const asset = picked.assets[0];
    setPhase("reading");
    try {
      const res = await api.chartRead({
        uri: asset.uri,
        name: asset.name || "chart.png",
        file: (asset as { file?: Blob }).file ?? null,
      });
      setResult(res);
      setChoice(res.candidates[0] ? { symbol: res.candidates[0].symbol, name: res.candidates[0].name } : null);
      setQuery("");
      setResults([]);
      setPhase("result");
      trackEvent("chart_read", { is_chart: res.is_chart ? 1 : 0 });
    } catch (e: any) {
      setPhase("pick");
      Alert.alert("Couldn't read that chart", e?.message || "Try again.");
    }
  };

  const run = async () => {
    if (!choice || !result?.reading_id || starting) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setStarting(true);
    try {
      const res = await api.analyze(
        choice.symbol,
        choice.name || undefined,
        getCurrentLanguage(),
        await getWalletDeviceId(),
        result.reading_id
      );
      trackEvent("analysis_started", { source: "chart" });
      router.replace(`/analysis/${res.id}`);
    } catch (e: any) {
      const msg: string = e?.message || "Something went wrong. Try again.";
      Alert.alert(msg.toLowerCase().includes("insufficient balance") ? "Add funds to continue" : "Couldn't start the analysis", msg);
    } finally {
      setStarting(false);
    }
  };

  const r = result?.reading;
  const facts: [string, string][] = r
    ? ([
        ["Timeframe", r.timeframe || ""],
        ["Trend", r.trend || ""],
        ["Patterns", r.patterns.join(", ")],
        ["Support", r.support.map((x) => x.toLocaleString("en-IN")).join(", ")],
        ["Resistance", r.resistance.map((x) => x.toLocaleString("en-IN")).join(", ")],
        ["Indicators", Object.entries(r.indicators).map(([k, v]) => `${k} ${v}`).join(", ")],
      ] as [string, string][]).filter(([, v]) => v)
    : [];

  return (
    <View style={styles.root}>
      <ScreenHeader title="ANALYSE A CHART" subtitle="// UPLOAD A SCREENSHOT" insetsTop={insets.top} onBack={() => router.back()} />
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: insets.bottom + spacing.xxl }} keyboardShouldPersistTaps="handled">
        {phase === "pick" ? (
          <>
            <Pressable testID="chart-pick" onPress={pick} style={styles.pickBox}>
              <ImageSquare size={36} color={colors.onSurface} weight="bold" />
              <Text style={styles.pickTitle}>CHOOSE A CHART SCREENSHOT</Text>
              <Text style={styles.pickSub}>From Zerodha, Groww, TradingView or anywhere · PNG, JPG or WebP · max 5 MB</Text>
            </Pressable>
            <Text style={styles.note}>
              Crop out balances, P&amp;L, names or client IDs before uploading. The image is read once and never stored.
            </Text>
            <Text style={styles.note}>
              Reading the chart is free. Running the desk on it costs the same as any analysis.
            </Text>
          </>
        ) : null}

        {phase === "reading" ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.onSurface} />
            <Text style={styles.readingText}>Reading your chart…</Text>
          </View>
        ) : null}

        {phase === "result" && result && !result.is_chart ? (
          <View>
            <Text testID="chart-not-chart" style={styles.warn}>
              {result.message || "This doesn't look like a price chart."}
            </Text>
            <Pressable onPress={() => setPhase("pick")} style={styles.secondaryBtn}>
              <Text style={styles.secondaryText}>TRY ANOTHER IMAGE</Text>
            </Pressable>
          </View>
        ) : null}

        {phase === "result" && result?.is_chart && r ? (
          <View>
            <Text style={styles.sectionTitle}>WHAT WE SAW ON YOUR CHART</Text>
            <View style={styles.card}>
              {facts.map(([k, v]) => (
                <View key={k} style={styles.factRow}>
                  <Text style={styles.factKey}>{k}</Text>
                  <Text style={styles.factVal}>{v}</Text>
                </View>
              ))}
              {r.observations ? <Text style={styles.obs}>{r.observations}</Text> : null}
              <Text style={styles.small}>Read by AI from the image. The desk checks it against real prices.</Text>
            </View>

            <Text style={styles.sectionTitle}>WHICH ASSET IS THIS?</Text>
            {result.candidates.map((c) => {
              const on = choice?.symbol === c.symbol;
              return (
                <Pressable
                  key={c.symbol}
                  testID={`chart-candidate-${c.symbol}`}
                  onPress={() => setChoice({ symbol: c.symbol, name: c.name })}
                  style={[styles.option, on && styles.optionOn]}
                >
                  {on ? (
                    <CheckCircle size={18} color={colors.onSurface} weight="fill" />
                  ) : (
                    <RadioButton size={18} color={colors.onSurfaceTertiary} />
                  )}
                  <View style={{ flex: 1 }}>
                    <Text style={styles.optSymbol}>{c.symbol}</Text>
                    <Text style={styles.optName}>{[c.name, c.exchange].filter(Boolean).join(" · ")}</Text>
                  </View>
                </Pressable>
              );
            })}
            <TextInput
              testID="chart-search"
              value={query}
              onChangeText={setQuery}
              placeholder={result.candidates.length ? "Not listed? Search…" : "Search the asset, e.g. RELIANCE"}
              placeholderTextColor={colors.onSurfaceTertiary}
              autoCapitalize="characters"
              style={styles.search}
            />
            {query.trim()
              ? results.slice(0, 5).map((s) => (
                  <Pressable
                    key={s.symbol}
                    onPress={() => {
                      setChoice({ symbol: s.symbol, name: s.name });
                      setQuery("");
                      setResults([]);
                    }}
                    style={styles.option}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={styles.optSymbol}>{s.symbol}</Text>
                      <Text style={styles.optName}>{s.name}</Text>
                    </View>
                  </Pressable>
                ))
              : null}

            <Pressable
              testID="chart-run"
              onPress={run}
              disabled={!choice || starting}
              style={[styles.primaryBtn, (!choice || starting) && { opacity: 0.45 }]}
            >
              {starting ? (
                <ActivityIndicator color={colors.onSurfaceInverse} />
              ) : (
                <Text style={styles.primaryText}>{choice ? `RUN THE DESK ON ${choice.symbol}` : "PICK THE ASSET FIRST"}</Text>
              )}
            </Pressable>
            <Pressable onPress={() => setPhase("pick")} hitSlop={8}>
              <Text style={styles.link}>Use a different image</Text>
            </Pressable>
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  pickBox: { borderWidth: BORDER, borderColor: colors.borderStrong, borderStyle: "dashed", alignItems: "center", padding: spacing.xl, gap: spacing.sm },
  pickTitle: { fontFamily: fonts.monoBold, fontSize: 13, letterSpacing: 1.5, color: colors.onSurface },
  pickSub: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, textAlign: "center" },
  note: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: spacing.md, lineHeight: 16 },
  center: { alignItems: "center", marginTop: spacing.xxl, gap: spacing.md },
  readingText: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurface },
  warn: { fontFamily: fonts.mono, fontSize: 13, color: colors.onSurface, marginBottom: spacing.md },
  sectionTitle: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1.5, color: colors.onSurfaceTertiary, marginTop: spacing.lg, marginBottom: spacing.sm },
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, padding: spacing.md, gap: spacing.xs },
  factRow: { flexDirection: "row", gap: spacing.md },
  factKey: { fontFamily: fonts.monoBold, fontSize: 11, color: colors.onSurfaceTertiary, width: 88 },
  factVal: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurface, flex: 1 },
  obs: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurface, marginTop: spacing.sm, lineHeight: 17 },
  small: { fontFamily: fonts.mono, fontSize: 10, color: colors.onSurfaceTertiary, marginTop: spacing.sm },
  option: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderWidth: 1, borderColor: colors.border, padding: spacing.md, marginBottom: spacing.sm, minHeight: 48 },
  optionOn: { borderColor: colors.borderStrong, borderWidth: BORDER },
  optSymbol: { fontFamily: fonts.monoBold, fontSize: 14, color: colors.onSurface },
  optName: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceTertiary, marginTop: 2 },
  search: { borderWidth: 1, borderColor: colors.border, padding: spacing.md, fontFamily: fonts.mono, fontSize: 13, color: colors.onSurface, marginBottom: spacing.sm },
  primaryBtn: { backgroundColor: colors.surfaceInverse, paddingVertical: spacing.md, alignItems: "center", marginTop: spacing.lg, minHeight: 48, justifyContent: "center" },
  primaryText: { fontFamily: fonts.monoBold, fontSize: 13, letterSpacing: 1.5, color: colors.onSurfaceInverse },
  secondaryBtn: { borderWidth: BORDER, borderColor: colors.borderStrong, paddingVertical: spacing.md, alignItems: "center" },
  secondaryText: { fontFamily: fonts.monoBold, fontSize: 12, letterSpacing: 1.5, color: colors.onSurface },
  link: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary, textAlign: "center", marginTop: spacing.md, textDecorationLine: "underline" },
});
