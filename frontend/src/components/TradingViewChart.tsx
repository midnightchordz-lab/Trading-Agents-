import React, { useMemo, useState } from "react";
import { View, Text, StyleSheet, Platform, ActivityIndicator } from "react-native";
import { WebView } from "react-native-webview";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { DEFAULT_STUDIES, STUDIES_OVERRIDES, toTradingViewSymbol, widgetSupports, TvTheme } from "@/src/tv";
import { LightweightChart } from "@/src/components/LightweightChart";
import type { Verdict } from "@/src/api";

type Props = {
  /** Yahoo-style symbol from the backend (RELIANCE.NS, BTC-USD, GC=F, AAPL). */
  symbol: string;
  /** Optional exchange hint from search results (NASDAQ / NYSE / NMS / NYQ). */
  exchange?: string;
  /** Widget interval: "1" "5" "15" "60" "D" "W" "M". */
  interval?: string;
  studies?: string[];
  theme?: TvTheme;
  height?: number;
  /** Hide the widget's own top toolbar for a more compact card. */
  compact?: boolean;
  /** Agents' verdict — drawn as entry/target/stop lines when the fallback chart is used. */
  levels?: Verdict | null;
  /** Live price used for the entry line on the fallback chart. */
  livePrice?: number | null;
  /** Horizon label drawn next to the target / stop lines on the fallback chart. */
  levelsLabel?: string | null;
  /** Use our own OHLC chart (which can draw levels) even when the widget supports the symbol. */
  preferOwnChart?: boolean;
};

function buildHtml(opts: {
  tvSymbol: string;
  interval: string;
  studies: string[];
  theme: TvTheme;
  compact: boolean;
}): string {
  const config = {
    autosize: true,
    symbol: opts.tvSymbol,
    interval: opts.interval,
    timezone: "Etc/UTC",
    theme: opts.theme,
    style: "1",
    locale: "en",
    toolbar_bg: "#FFFFFF",
    enable_publishing: false,
    hide_top_toolbar: opts.compact,
    hide_side_toolbar: opts.compact,
    hide_legend: false,
    allow_symbol_change: false,
    save_image: false,
    withdateranges: !opts.compact,
    details: false,
    studies: opts.studies,
    studies_overrides: STUDIES_OVERRIDES,
    container_id: "tv",
  };

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<style>
  html, body { margin:0; padding:0; height:100%; background:${opts.theme === "dark" ? "#131722" : "#FFFFFF"}; overflow:hidden; }
  #tv { height:100%; width:100%; }
</style>
</head>
<body>
  <div id="tv"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
    try { new TradingView.widget(${JSON.stringify(config)}); }
    catch (e) { document.body.innerHTML = '<p style="font-family:monospace;padding:12px">Chart failed to load.</p>'; }
  </script>
</body>
</html>`;
}

export function TradingViewChart({
  symbol,
  exchange,
  interval = "D",
  studies = DEFAULT_STUDIES,
  theme = "light",
  height = 360,
  compact = false,
  levels,
  livePrice,
  levelsLabel,
  preferOwnChart = false,
}: Props) {
  const [loading, setLoading] = useState(true);
  const supported = useMemo(() => widgetSupports(symbol), [symbol]);
  const tvSymbol = useMemo(() => toTradingViewSymbol(symbol, exchange), [symbol, exchange]);
  const html = useMemo(
    () => buildHtml({ tvSymbol, interval, studies, theme, compact }),
    [tvSymbol, interval, studies, theme, compact],
  );

  if (!supported || preferOwnChart) {
    // NSE / BSE data is not licensed for the free widget — use our own OHLC
    // chart. It's also what we use when the agents' levels must be drawn.
    return (
      <LightweightChart
        symbol={symbol}
        height={height}
        levels={levels}
        livePrice={livePrice}
        levelsLabel={levelsLabel}
      />
    );
  }

  return (
    <View testID="tradingview-chart" style={[styles.card, { height }]}>
      <View style={styles.header}>
        <Text style={styles.headerText}>TRADINGVIEW</Text>
        <Text style={styles.headerSymbol} numberOfLines={1}>
          {tvSymbol}
        </Text>
      </View>

      <View style={styles.body}>
        {Platform.OS === "web" ? (
          // react-native-webview has no web implementation; use a plain iframe.
          React.createElement("iframe", {
            srcDoc: html,
            style: { border: 0, width: "100%", height: "100%", display: "block" },
            sandbox: "allow-scripts allow-same-origin allow-popups",
            onLoad: () => setLoading(false),
            title: `TradingView ${tvSymbol}`,
          })
        ) : (
          <WebView
            key={html}
            originWhitelist={["*"]}
            source={{ html, baseUrl: "https://www.tradingview.com" }}
            javaScriptEnabled
            domStorageEnabled
            scrollEnabled={false}
            nestedScrollEnabled={false}
            allowsInlineMediaPlayback
            setSupportMultipleWindows={false}
            onLoadEnd={() => setLoading(false)}
            onError={() => setLoading(false)}
            style={styles.webview}
          />
        )}
        {loading ? (
          <View style={styles.loading} pointerEvents="none">
            <ActivityIndicator color={colors.onSurface} />
          </View>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: BORDER,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    overflow: "hidden",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: BORDER,
    borderBottomColor: colors.borderStrong,
    backgroundColor: colors.surfaceInverse,
  },
  headerText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurfaceInverse },
  headerSymbol: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceInverse, maxWidth: "60%" },
  body: { flex: 1, backgroundColor: colors.surface },
  webview: { flex: 1, backgroundColor: "transparent" },
  loading: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
  },
});
