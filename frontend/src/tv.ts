// TradingView helpers: maps the Yahoo Finance symbols the backend uses
// to TradingView symbols, and holds the default widget configuration.
// No backend or agent-pipeline changes are needed for this.

export type TvTheme = "light" | "dark";

// Default indicators for the embedded Advanced Chart (Volume is on by default).
export const DEFAULT_STUDIES = [
  "RSI@tv-basicstudies",
  "MACD@tv-basicstudies",
  "MAExp@tv-basicstudies",
  "BB@tv-basicstudies",
];

// Per-study parameter overrides. The free widget applies one override per
// study type (so a single EMA length — 20 here); a second EMA can be added
// from the chart's own Indicators menu.
export const STUDIES_OVERRIDES: Record<string, number | string | boolean> = {
  "moving average exponential.length": 20,
  "relative strength index.length": 14,
  "bollinger bands.length": 20,
  "volume.volume ma.visible": false,
};

const YAHOO_COMMODITIES: Record<string, string> = {
  // Use TradingView's freely-available TVC index symbols where possible so the
  // widget renders without the "subscription required" popup that COMEX/NYMEX
  // continuous futures trigger on the free widget.
  "GC=F": "TVC:GOLD",
  "SI=F": "TVC:SILVER",
  "CL=F": "TVC:USOIL",
  "BZ=F": "TVC:UKOIL",
  "NG=F": "NYMEX:NG1!",
  "HG=F": "COMEX:HG1!",
  "PL=F": "TVC:PLATINUM",
  "ZW=F": "CBOT:ZW1!",
  "ZC=F": "CBOT:ZC1!",
  "ZS=F": "CBOT:ZS1!",
};

const YAHOO_INDICES: Record<string, string> = {
  "^GSPC": "SP:SPX",
  "^DJI": "DJ:DJI",
  "^IXIC": "NASDAQ:IXIC",
  "^NSEI": "NSE:NIFTY",
  "^BSESN": "BSE:SENSEX",
  "^FTSE": "FTSE:UKX",
  "^N225": "TVC:NI225",
  "^HSI": "HSI:HSI",
};

// Yahoo suffix → TradingView exchange prefix.
const SUFFIX_EXCHANGE: Record<string, string> = {
  NS: "NSE",
  BO: "BSE",
  HK: "HKEX",
  L: "LSE",
  T: "TSE",
  TO: "TSX",
  V: "TSXV",
  AX: "ASX",
  DE: "XETR",
  PA: "EURONEXT",
  AS: "EURONEXT",
  MI: "MIL",
  SW: "SIX",
  SI: "SGX",
  KS: "KRX",
  SS: "SSE",
  SZ: "SZSE",
  TW: "TWSE",
  SA: "BMFBOVESPA",
  MX: "BMV",
  JO: "JSE",
};

const CRYPTO_QUOTE_MAP: Record<string, string> = {
  USD: "USDT",
  USDT: "USDT",
  BTC: "BTC",
  ETH: "ETH",
};

/**
 * Convert a Yahoo Finance ticker to a TradingView symbol.
 *  RELIANCE.NS -> NSE:RELIANCE     BTC-USD -> BINANCE:BTCUSDT
 *  GC=F        -> COMEX:GC1!       ^NSEI   -> NSE:NIFTY
 *  AAPL        -> AAPL (TradingView resolves bare US tickers)
 *  0700.HK     -> HKEX:700
 */
export function toTradingViewSymbol(yahoo: string, exchangeHint?: string): string {
  const s = (yahoo || "").trim().toUpperCase();
  if (!s) return "AAPL";

  if (YAHOO_COMMODITIES[s]) return YAHOO_COMMODITIES[s];
  if (YAHOO_INDICES[s]) return YAHOO_INDICES[s];

  // Crypto: BTC-USD, ETH-USD, SOL-USDT ...
  const crypto = s.match(/^([A-Z0-9]{2,10})-(USD|USDT|BTC|ETH)$/);
  if (crypto) {
    const base = crypto[1];
    const quote = CRYPTO_QUOTE_MAP[crypto[2]] ?? crypto[2];
    return `BINANCE:${base}${quote}`;
  }

  // Forex: EURUSD=X
  const fx = s.match(/^([A-Z]{6})=X$/);
  if (fx) return `FX:${fx[1]}`;

  // Exchange-suffixed equities: RELIANCE.NS, 0700.HK, BP.L
  const suffixed = s.match(/^(.+)\.([A-Z]{1,3})$/);
  if (suffixed) {
    const [, base, suffix] = suffixed;
    const exchange = SUFFIX_EXCHANGE[suffix];
    if (exchange) {
      const ticker = suffix === "HK" ? String(parseInt(base, 10)) : base.replace(/-/g, "_");
      return `${exchange}:${ticker}`;
    }
  }

  // Bare US tickers — use the exchange hint from the backend search result if present.
  const hint = (exchangeHint || "").toUpperCase();
  if (/NASDAQ|NMS|NGM|NCM/.test(hint)) return `NASDAQ:${s}`;
  if (/NYSE|NYQ/.test(hint)) return `NYSE:${s}`;
  if (/AMEX|ASE|PCX|ARCA/.test(hint)) return `AMEX:${s}`;
  return s;
}

/** Map the app's chart ranges to a sensible default widget interval. */
export function rangeToInterval(range?: string): string {
  switch ((range || "").toUpperCase()) {
    case "1D":
      return "5";
    case "1W":
      return "60";
    case "1Y":
      return "W";
    case "1M":
    default:
      return "D";
  }
}
