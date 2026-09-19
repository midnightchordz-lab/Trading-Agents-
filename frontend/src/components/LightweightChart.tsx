import React, { useEffect, useMemo, useState } from "react";
import { View, Text, Pressable, StyleSheet, Platform, ActivityIndicator } from "react-native";
import { WebView } from "react-native-webview";
import { colors, fonts, spacing, BORDER } from "@/src/theme";
import { api, OhlcData, Verdict } from "@/src/api";
import { scriptJson } from "@/src/utils/scriptJson";

// Fallback chart for symbols the free TradingView widget is not licensed to
// display (NSE / BSE). Uses TradingView's open-source Lightweight Charts
// (Apache 2.0) fed by our own backend OHLC endpoint. Because the data is ours,
// the agents' entry / target / stop-loss are drawn directly on the candles.
// Indicators (EMA 20/50, Bollinger 20, RSI 14, MACD 12/26/9, Volume) are
// computed in the WebView.

const RANGES = ["1D", "1W", "1M", "1Y"];

type Props = {
  symbol: string;
  height?: number;
  levels?: Verdict | null;
  livePrice?: number | null;
  /** Horizon label shown on the target / stop price lines, e.g. "1-2 WEEKS". */
  levelsLabel?: string | null;
};

function buildHtml(
  data: OhlcData,
  levels: Verdict | null | undefined,
  livePrice: number | null | undefined,
  levelsLabel?: string | null,
): string {
  const payload = scriptJson({
    bars: data.bars,
    intraday: data.range === "1D" || data.range === "1W",
    levelsLabel: levelsLabel || "",
    levels: levels
      ? {
          decision: levels.decision,
          entry: livePrice ?? data.bars[data.bars.length - 1]?.close ?? null,
          target: levels.target_price,
          stop: levels.stop_loss,
        }
      : null,
    colors: {
      up: "#16A34A",
      down: "#E11D48",
      text: "#09090B",
      grid: "#E7E5F4",
      ema20: "#2563EB",
      ema50: "#7C3AED",
      bb: "#F59E0B",
      rsi: "#DB2777",
      macd: "#2563EB",
      signal: "#F59E0B",
      buy: "#16A34A",
      sell: "#E11D48",
      hold: "#F59E0B",
    },
  });

  return `<!DOCTYPE html>
<html><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<style>
  html,body{margin:0;padding:0;background:#fff;overflow:hidden;font-family:monospace}
  #main{width:100%}
  #rsi,#macd{width:100%;border-top:1px solid #E7E5F4}
  .lbl{position:absolute;left:6px;font-size:10px;color:#3F3F55;z-index:2;pointer-events:none}
  .wrap{position:relative}
</style>
<!-- integrity computed against the real file with `openssl dgst -sha384` and
     confirmed stable across refetches. The chart engine is third-party code
     loaded from a CDN, and the iframe sandbox is what stops it reaching our
     origin; this is what stops a swapped CDN file running at all. If the
     version above is ever bumped, the hash MUST be regenerated or the chart
     will refuse to load. -->
<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"
        integrity="sha384-stKllnUqA9AD0gsKCuUtf5XlqAW7PwIgDagoNsTWkjkBmJ/GZ/uHTgEBxdLV2VSK"
        crossorigin="anonymous"></script>
</head><body>
<div class="wrap"><div class="lbl" id="l0"></div><div id="main"></div></div>
<div class="wrap"><div class="lbl">RSI 14</div><div id="rsi"></div></div>
<div class="wrap"><div class="lbl">MACD 12/26/9</div><div id="macd"></div></div>
<script>
(function(){
  var P = ${payload};
  var bars = P.bars;
  if (!bars || bars.length < 2) { document.body.innerHTML = '<p style="padding:12px">No OHLC data.</p>'; return; }
  var H = window.innerHeight, mainH = Math.round(H * 0.62), subH = Math.round(H * 0.19);
  var C = P.colors;

  function ema(vals, n){ var k=2/(n+1), out=[], prev=null; for (var i=0;i<vals.length;i++){ var v=vals[i]; prev = prev===null ? v : v*k + prev*(1-k); out.push(prev);} return out; }
  function sma(vals, n){ var out=[]; for (var i=0;i<vals.length;i++){ if(i<n-1){out.push(null);continue;} var s=0; for(var j=i-n+1;j<=i;j++) s+=vals[j]; out.push(s/n);} return out; }
  function std(vals, n, means){ var out=[]; for (var i=0;i<vals.length;i++){ if(i<n-1||means[i]===null){out.push(null);continue;} var s=0; for(var j=i-n+1;j<=i;j++){ var d=vals[j]-means[i]; s+=d*d;} out.push(Math.sqrt(s/n)); } return out; }
  function rsi(vals, n){ var out=[], g=0, l=0; for (var i=1;i<vals.length;i++){ var ch=vals[i]-vals[i-1]; var up=Math.max(ch,0), dn=Math.max(-ch,0); if(i<=n){ g+=up; l+=dn; if(i===n){ g/=n; l/=n; out[i]= l===0?100:100-100/(1+g/l);} else out[i]=null; } else { g=(g*(n-1)+up)/n; l=(l*(n-1)+dn)/n; out[i]= l===0?100:100-100/(1+g/l);} } out[0]=null; return out; }

  var closes = bars.map(function(b){return b.close;});
  var e20=ema(closes,20), e50=ema(closes,50), bbm=sma(closes,20), bbs=std(closes,20,bbm);
  var e12=ema(closes,12), e26=ema(closes,26), macd=e12.map(function(v,i){return v-e26[i];}), sig=ema(macd,9);
  var r=rsi(closes,14);
  function series(arr, warm){ var out=[]; for (var i=0;i<bars.length;i++){ if(i<warm||arr[i]===null||arr[i]===undefined) continue; out.push({time:bars[i].time, value:arr[i]}); } return out; }

  var common = { layout:{background:{color:'#fff'}, textColor:C.text, fontFamily:'monospace', fontSize:10}, grid:{vertLines:{color:C.grid}, horzLines:{color:C.grid}}, rightPriceScale:{borderColor:C.grid}, timeScale:{borderColor:C.grid, timeVisible:P.intraday, secondsVisible:false}, crosshair:{mode:0}, handleScroll:true, handleScale:true };

  var main = LightweightCharts.createChart(document.getElementById('main'), Object.assign({height:mainH}, common));
  var candles = main.addCandlestickSeries({upColor:C.up, downColor:C.down, borderVisible:false, wickUpColor:C.up, wickDownColor:C.down});
  candles.setData(bars.map(function(b){return {time:b.time, open:b.open, high:b.high, low:b.low, close:b.close};}));
  var vol = main.addHistogramSeries({priceFormat:{type:'volume'}, priceScaleId:'vol'});
  main.priceScale('vol').applyOptions({scaleMargins:{top:0.8,bottom:0}});
  vol.setData(bars.map(function(b,i){return {time:b.time, value:b.volume||0, color:(i>0&&b.close<bars[i-1].close)?'rgba(225,29,72,0.35)':'rgba(22,163,74,0.35)'};}));
  main.addLineSeries({color:C.ema20, lineWidth:1, priceLineVisible:false, lastValueVisible:false}).setData(series(e20,19));
  main.addLineSeries({color:C.ema50, lineWidth:1, priceLineVisible:false, lastValueVisible:false}).setData(series(e50,49));
  var bbU = bbm.map(function(m,i){return m===null?null:m+2*bbs[i];}), bbL = bbm.map(function(m,i){return m===null?null:m-2*bbs[i];});
  var bbOpt = {color:C.bb, lineWidth:1, lineStyle:2, priceLineVisible:false, lastValueVisible:false};
  main.addLineSeries(bbOpt).setData(series(bbU,19)); main.addLineSeries(bbOpt).setData(series(bbL,19));
  main.addLineSeries(Object.assign({}, bbOpt, {lineStyle:0})).setData(series(bbm,19));
  document.getElementById('l0').textContent = 'EMA20 · EMA50 · BB20 · VOL';

  if (P.levels) {
    var L = P.levels, isHold = L.decision === 'HOLD';
    var pfx = P.levelsLabel ? P.levelsLabel + ' ' : '';
    var entryColor = L.decision === 'BUY' ? C.buy : (L.decision === 'SELL' ? C.sell : C.hold);
    if (L.entry != null) candles.createPriceLine({price:L.entry, color:entryColor, lineWidth:2, lineStyle:0, axisLabelVisible:true, title: isHold ? 'HOLD' : L.decision + ' ENTRY'});
    if (L.target != null) candles.createPriceLine({price:L.target, color:C.buy, lineWidth:2, lineStyle:2, axisLabelVisible:true, title:pfx + 'TARGET'});
    if (L.stop != null) candles.createPriceLine({price:L.stop, color:C.sell, lineWidth:2, lineStyle:2, axisLabelVisible:true, title:pfx + 'STOP LOSS'});
  }

  var rsiChart = LightweightCharts.createChart(document.getElementById('rsi'), Object.assign({height:subH}, common));
  var rsiS = rsiChart.addLineSeries({color:C.rsi, lineWidth:1, priceLineVisible:false});
  rsiS.setData(series(r,14));
  [30,70].forEach(function(lv){ rsiS.createPriceLine({price:lv, color:C.grid, lineWidth:1, lineStyle:2, axisLabelVisible:false}); });
  rsiChart.priceScale('right').applyOptions({autoScale:false}); rsiS.applyOptions({autoscaleInfoProvider:function(){return {priceRange:{minValue:0,maxValue:100}};}});

  var macdChart = LightweightCharts.createChart(document.getElementById('macd'), Object.assign({height:subH}, common));
  var hist = macdChart.addHistogramSeries({priceLineVisible:false});
  hist.setData(series(macd.map(function(v,i){return v-sig[i];}),33).map(function(p){return {time:p.time, value:p.value, color:p.value>=0?'rgba(22,163,74,0.5)':'rgba(225,29,72,0.5)'};}));
  macdChart.addLineSeries({color:C.macd, lineWidth:1, priceLineVisible:false}).setData(series(macd,25));
  macdChart.addLineSeries({color:C.signal, lineWidth:1, priceLineVisible:false}).setData(series(sig,33));

  // keep the three panes scrolled/zoomed together
  var charts=[main,rsiChart,macdChart], syncing=false;
  charts.forEach(function(c){ c.timeScale().subscribeVisibleLogicalRangeChange(function(r){ if(syncing||!r) return; syncing=true; charts.forEach(function(o){ if(o!==c) o.timeScale().setVisibleLogicalRange(r); }); syncing=false; }); });
  main.timeScale().fitContent();
  window.addEventListener('resize', function(){ var w=window.innerWidth; charts.forEach(function(c){ c.applyOptions({width:w}); }); });
})();
</script>
</body></html>`;
}

export function LightweightChart({ symbol, height = 380, levels, livePrice, levelsLabel }: Props) {
  const [range, setRange] = useState("1M");
  const [data, setData] = useState<OhlcData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .ohlc(symbol, range)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        if (!cancelled) setError("Chart data unavailable for this ticker.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, range]);

  const html = useMemo(
    () => (data ? buildHtml(data, levels, livePrice, levelsLabel) : ""),
    [data, levels, livePrice, levelsLabel],
  );
  const chartHeight = height - 34 - 36; // header + range chips

  return (
    <View testID="lightweight-chart" style={[styles.card, { height }]}>
      <View style={styles.header}>
        <Text style={styles.headerText}>{levelsLabel ? `CHART · ${levelsLabel} LEVELS` : "CHART · OWN DATA"}</Text>
        <Text style={styles.headerSymbol} numberOfLines={1}>
          {symbol}
        </Text>
      </View>
      <View style={styles.ranges}>
        {RANGES.map((r) => {
          const active = r === range;
          return (
            <Pressable key={r} onPress={() => setRange(r)} style={[styles.rangeBtn, active && styles.rangeBtnActive]}>
              <Text style={[styles.rangeText, active && styles.rangeTextActive]}>{r}</Text>
            </Pressable>
          );
        })}
      </View>
      <View style={[styles.body, { height: chartHeight }]}>
        {error ? (
          <View style={styles.center}>
            <Text style={styles.errorText}>{error}</Text>
          </View>
        ) : html ? (
          Platform.OS === "web" ? (
            React.createElement("iframe", {
              srcDoc: html,
              style: { border: 0, width: "100%", height: "100%", display: "block" },
              // NO allow-same-origin: srcDoc would otherwise inherit the app's
              // own origin, handing third-party chart code (and the CDN script
              // it loads) read access to the session token in localStorage.
              // allow-scripts alone runs the chart in an opaque origin.
              sandbox: "allow-scripts",
              title: `Chart ${symbol}`,
            })
          ) : (
            <WebView
              key={html}
              originWhitelist={["*"]}
              source={{ html }}
              javaScriptEnabled
              scrollEnabled={false}
              nestedScrollEnabled={false}
              setSupportMultipleWindows={false}
              style={styles.webview}
            />
          )
        ) : null}
        {loading ? (
          <View style={[styles.center, styles.overlay]} pointerEvents="none">
            <ActivityIndicator color={colors.onSurface} />
          </View>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: BORDER, borderColor: colors.borderStrong, backgroundColor: colors.surface, overflow: "hidden" },
  header: {
    height: 34,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    borderBottomWidth: BORDER,
    borderBottomColor: colors.borderStrong,
    backgroundColor: colors.surfaceInverse,
  },
  headerText: { fontFamily: fonts.monoBold, fontSize: 11, letterSpacing: 1, color: colors.onSurfaceInverse },
  headerSymbol: { fontFamily: fonts.mono, fontSize: 11, color: colors.onSurfaceInverse, maxWidth: "60%" },
  ranges: { height: 36, flexDirection: "row", borderBottomWidth: BORDER, borderBottomColor: colors.borderStrong },
  rangeBtn: { flex: 1, alignItems: "center", justifyContent: "center", borderEndWidth: 1, borderEndColor: colors.border },
  rangeBtnActive: { backgroundColor: colors.brand },
  rangeText: { fontFamily: fonts.monoBold, fontSize: 11, color: colors.onSurface },
  rangeTextActive: { color: colors.onSurfaceInverse },
  body: { backgroundColor: colors.surface },
  webview: { flex: 1, backgroundColor: "transparent" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.lg },
  overlay: { ...StyleSheet.absoluteFillObject, backgroundColor: colors.surface },
  errorText: { fontFamily: fonts.mono, fontSize: 12, color: colors.onSurfaceTertiary, textAlign: "center" },
});
