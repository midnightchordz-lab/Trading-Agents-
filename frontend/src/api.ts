// API client for the TradingAgents backend.
import * as Linking from "expo-linking";
import { detectRegion } from "@/src/region";
import { File as FsFile } from "expo-file-system";
import { Platform } from "react-native";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;
const API = `${BASE}/api`;

// Device region, sent with wallet calls so the backend can pick the right
// currency for a wallet that hasn't locked one yet (INR for India, because
// UPI/GPay only exist on INR payment links). The backend decides; this is
// only a hint, and an unknown region is simply omitted. See region.ts for why
// this is the time zone first and the language setting second.
const REGION = detectRegion();

// Deep link back into THIS app, sent with a top-up so the payment page can
// render a "Return to the app" button. `createURL` knows which runtime we are
// in — `frontend://` in an installed build, `exp://…` in Expo Go — which is
// why the server is told rather than left to guess a scheme. Not needed on
// web, where checkout is a script-opened popup that can close itself.
const RETURN_URL = (() => {
  if (Platform.OS === "web") return "";
  try {
    return Linking.createURL("/");
  } catch {
    return "";
  }
})();

export type SearchResult = {
  symbol: string;
  name: string;
  exchange?: string;
  type?: string;
};

export type Quote = {
  symbol: string;
  name: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePercent: number | null;
  currency?: string;
  exchange?: string;
  dayHigh?: number;
  dayLow?: number;
  fiftyTwoWeekHigh?: number;
  fiftyTwoWeekLow?: number;
  sparkline: number[];
};

export type AgentMessageT = {
  id: string;
  agent: string;
  tag: string;
  phase: "analysis" | "debate" | "trade" | "risk" | "decision";
  content: string;
  sentiment: "bullish" | "bearish" | "neutral" | null;
  ts: string;
};

export type Verdict = {
  decision: "BUY" | "SELL" | "HOLD";
  confidence: number;
  target_price: number | null;
  stop_loss: number | null;
  time_horizon: string;
  summary: string;
  key_risks: string[];
};

export type Debate = {
  bull: string;
  bear: string;
  fundamentals: string;
  agreements: string[];
  disagreements: string[];
  recommendation: string;
};

export type ChartData = {
  symbol: string;
  range: string;
  points: number[];
  last: number | null;
  change: number | null;
  changePercent: number | null;
  currency?: string;
};

export type NewsItem = {
  title: string;
  publisher?: string | null;
  link: string;
  published?: number | null;
  thumbnail?: string | null;
  sentiment?: "BULLISH" | "BEARISH" | "NEUTRAL" | null;
};

export type OhlcBar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type OhlcData = {
  symbol: string;
  range: string;
  interval: string;
  currency?: string;
  bars: OhlcBar[];
};

export type PortfolioHoldingInput = { symbol: string; quantity: number; avg_price: number };

export type ImportCandidate = { symbol: string; name?: string | null; type?: string | null; exchange?: string | null };
export type ImportRow = {
  row: number;
  input: string;
  quantity: number | null;
  avg_price: number | null;
  error: string | null;
  /** "ok" — unambiguous; "check" — plausible but needs confirmation;
   *  "not_found" — no tradeable listing; "invalid" — parse-time error. */
  status: "ok" | "check" | "not_found" | "invalid";
  symbol: string | null;
  candidates: ImportCandidate[];
};
export type ImportResult = {
  rows: ImportRow[];
  counts: { ok: number; check: number; not_found: number; invalid: number };
};

export type PortfolioAction = {
  symbol: string;
  current_weight: number;
  suggested_weight: number;
  delta: number;
  action: "ADD" | "HOLD" | "TRIM" | "SELL";
};

export type PortfolioOptimizeResult = {
  objective: string;
  used_agent_views_for: string[];
  missing_agent_view_for: string[];
  dropped_symbols: string[];
  total_value: number;
  current_weights: Record<string, number>;
  suggested_weights: Record<string, number>;
  actions: PortfolioAction[];
  suggested_shares: Record<string, number>;
  leftover_cash: number;
  expected_return: number;
  volatility: number;
  sharpe: number;
  current_prices: Record<string, number>;
};

export type GroundingCheck = {
  id: string;
  ok: boolean;
  severity: "info" | "warn" | "fail";
  message: string;
};

export type Grounding = {
  status: "grounded" | "warning" | "failed" | "unverified";
  checks: GroundingCheck[];
  evidence: {
    price: number;
    dayLow?: number | null;
    dayHigh?: number | null;
    fiftyTwoWeekLow?: number | null;
    fiftyTwoWeekHigh?: number | null;
    currency?: string | null;
    asOf: string;
  } | null;
};

export type TimeframeCall = {
  horizon: "short_term" | "medium_term" | "long_term";
  label: string;
  decision: "BUY" | "SELL" | "HOLD";
  confidence: number;
  target_price: number | null;
  stop_loss: number | null;
  thesis: string | null;
};

export type Timeframes = {
  short_term: TimeframeCall;
  medium_term: TimeframeCall;
  long_term: TimeframeCall;
};

export type SessionUser = {
  id: string;
  phone?: string | null;
  email?: string | null;
  /** The identity this account signed in WITH (phone number or email), decided
   *  by the backend. Shown as-is: an email on the account may have come from a
   *  payment receipt rather than a sign-in, so the app must not choose. */
  identity?: string | null;
  identity_type?: "phone" | "email";
  /** False until the account has affirmatively agreed to the CURRENT privacy
   *  notice. The app shows the consent screen once while this is false; the
   *  backend independently 403s /analyze with `consent_required` until then. */
  consent_given?: boolean;
  consent_version_required?: string;
};

// Every request carries the stored session token. auth.ts registers the
// getter on import so api.ts stays free of a circular dependency.
let authTokenGetter: (() => Promise<string | null>) | null = null;

export function setAuthTokenGetter(fn: () => Promise<string | null>) {
  authTokenGetter = fn;
}

export type WalletBalance = {
  device_id: string;
  balance: number;
  currency: string;
  symbol: string;
  prices: Record<string, number>;
  packs: number[];
  free_credits_remaining: number;
  enforcement_enabled: boolean;
  is_admin?: boolean;
  payments_live: boolean;
  /** iOS sells wallet credit through Apple In-App Purchase (App Store
   *  guideline 3.1.1 forbids Razorpay for digital content consumed in-app).
   *  False means the iOS purchase path isn't configured yet. */
  iap_enabled?: boolean;
  /** Launch promotion — everything is free until the configured date, so no
   *  purchase UI may be shown (an external payment path for digital content
   *  is what Apple's 3.1.1 prohibits, whether or not it charges today). */
  launch_free_active?: boolean;
  /** Powers the on-screen countdown; remaining is null outside the window. */
  launch_free_daily_remaining?: number | null;
  launch_free_daily_cap?: number;
  /** False until the account's first top-up locks a currency. The app asks
   *  once — and only once — which currency to use, then never again. */
  currency_locked?: boolean;
  /** Everything the app needs to render either choice, so it holds no
   *  currency knowledge of its own (no hardcoded amounts or symbols). */
  currency_options?: CurrencyOption[];
  /** True while no money has reached the wallet, so INR / USD can still be
   *  chosen (or a lock left by an abandoned checkout undone). */
  currency_changeable?: boolean;
};

export type CurrencyOption = {
  code: string;
  symbol: string;
  packs: number[];
  prices: Record<string, number>;
};

/** One scanner: a factual filter, refreshed in the background. `as_of` is
 *  when its list was computed; `delayed` means 15-minute delayed data. */
export type ScanMeta = {
  id: string;
  group: "in" | "us" | "crypto";
  market: string;
  kind: "live" | "daily";
  title: string;
  description: string;
  count: number;
  as_of: string | null;
  source: "upstox" | "yahoo" | null;
  delayed: boolean;
  note?: string | null;
  /** Daily lists: the trading day (YYYY-MM-DD) whose close they describe. */
  session_date?: string | null;
  /** True while a daily list is recomputed from today's live price. */
  provisional?: boolean;
};
export type ScanItem = {
  symbol: string;
  analyze_symbol: string;
  label: string;
  price: number | null;
  change_pct: number | null;
};

export type TopupOrder = { order_id: string; amount: number; currency: string; checkout_url: string };

export type IapPack = { product_id: string; amount: number };

export type IapConfig = {
  enabled: boolean;
  ios_api_key: string;
  packs: IapPack[];
  currency: string;
  symbol: string;
};

export type PaymentStatus = { order_id: string; status: string; balance: number };

/** What the AI read off a chart screenshot (unverified until checked against data). */
export type ChartReading = {
  is_chart: boolean;
  symbol: string | null;
  company: string | null;
  exchange: string | null;
  timeframe: string | null;
  trend: "up" | "down" | "sideways" | null;
  patterns: string[];
  support: number[];
  resistance: number[];
  indicators: Record<string, number>;
  last_price: number | null;
  observations: string | null;
};
export type ChartCheckRow = { item: string; chart: string; data: string; agrees: boolean; note: string | null };
export type ChartReadResult = {
  is_chart: boolean;
  reading_id: string | null;
  reading: ChartReading;
  candidates: { symbol: string; name?: string | null; exchange?: string | null }[];
  message?: string;
};

export type HealthBucket = "in_equity" | "us_equity" | "other_equity" | "debt" | "gold" | "crypto" | "cash";
export type RiskReturn = { annual_return: number; volatility: number; sharpe: number | null; max_drawdown: number };
export type FrontierPoint = { volatility: number; return: number };
export type HealthReport = {
  base_currency: "INR" | "USD";
  invested_value: number;
  cash: number;
  holdings: { symbol: string; name?: string | null; weight: number; value: number; bucket: HealthBucket; sector: string }[];
  skipped: { symbol: string; reason: string }[];
  days_analysed: number;
  concentration: { holdings: number; effective_holdings: number; top: { symbol: string; weight: number }[] };
  avg_correlation: number | null;
  clusters: { symbols: string[]; weight: number; avg_correlation: number }[];
  sectors: { sector: string; weight: number }[];
  asset_mix: { bucket: HealthBucket; label: string; weight: number }[];
  drift: { bucket: HealthBucket; label: string; actual: number; target: number; drift: number; flag: boolean }[];
  targets_set: boolean;
  benchmark: { symbol: string; name: string };
  market: { beta: number; r_squared: number; correlation: number; days: number } | null;
  risk_free_rate: number;
  risk_return: RiskReturn | null;
  benchmark_risk_return: RiskReturn | null;
  frontier: {
    you: FrontierPoint;
    curve: FrontierPoint[];
    lowest_risk: FrontierPoint;
    best_return_per_risk: FrontierPoint | null;
    same_risk_best_return: FrontierPoint | null;
    same_return_lowest_risk: FrontierPoint | null;
    return_gap_at_same_risk: number | null;
    risk_gap_at_same_return: number | null;
  } | null;
  liquidity: { symbol: string; avg_daily_value: number; position_share_of_day: number; thinly_traded: boolean; flag: boolean }[];
  diversification: {
    score: number;
    label: string;
    parts: { breadth: number; independence: number; sectors: number; asset_classes: number };
  };
  observations: string[];
};

export type ReviewRow = { label: string; n: number; pnl: number; win: number | null; extra?: string };
export type ReviewTable = { id: string; title: string; label: string; note: string | null; rows: ReviewRow[] };
export type TradeReview = {
  period: { from: string | null; to: string | null };
  sources: { broker: string; format: string; kind: "fills" | "contracts"; rows: number }[];
  money: {
    gross: number;
    charges: number;
    net: number;
    charges_known: boolean;
    count: number;
    win_rate: number | null;
    avg_win: number | null;
    avg_loss: number | null;
    gross_profit: number;
    gross_loss: number;
    profit_factor: number | null;
    counted_in: "positions" | "contracts";
  };
  positions: number;
  contracts: number;
  underlyings: number;
  active_days: number | null;
  green_days: number | null;
  observations: string[];
  tables: ReviewTable[];
  tax: { turnover: number; net_pnl_after_charges: number; charges: number; charges_exact: boolean; note: string };
  what_if: { id: string; title: string; detail: string; actual: number; hypothetical: number; difference: number }[];
  what_if_note: string;
  share: {
    period: { from: string | null; to: string | null };
    win_rate: number | null;
    profit_factor: number | null;
    count: number;
    counted_in: "positions" | "contracts";
    active_days: number | null;
    green_days: number | null;
    lines: string[];
  };
  open_positions: { contract: string; status: string }[];
  notes: string[];
  risk_line: string;
  billing?: { free_run: boolean; charged: number | null; symbol: string; free_left: number };
};

export type TradeReviewPricing = {
  charged: boolean;
  free_runs: number;
  free_left: number;
  price: number;
  currency: string;
  symbol: string;
  balance: number;
};

export type Analysis = {
  id: string;
  symbol: string;
  name: string;
  status: "running" | "completed" | "error";
  messages: AgentMessageT[];
  quote: Quote | null;
  verdict: Verdict | null;
  debate: Debate | null;
  timeframes?: Timeframes | null;
  grounding?: Grounding | null;
  /** Set when the analysis was started from a chart screenshot. */
  chart_reading?: ChartReading | null;
  chart_check?: ChartCheckRow[] | null;
  current_step: number;
  total_steps: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

function friendlyStatus(status: number): string {
  if (status === 401) return "Please sign in again.";
  if (status === 429) return "Too many attempts — wait a minute and try again.";
  if (status === 413) return "That file is too large.";
  if (status >= 500) return "Our server had a problem — try again in a moment.";
  return "Something went wrong — try again.";
}

async function j<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = authTokenGetter ? await authTokenGetter() : null;
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      // Do not set Content-Type for multipart/form-data: the browser must set
      // the boundary itself, and a manually set header without a boundary
      // produces an unparseable stream.
      ...(opts?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts?.headers || {}),
    },
  });
  if (!res.ok) {
    // Our API always answers errors as JSON {detail: "..."}. Anything else —
    // a hosting proxy's HTML error page, a timeout, a validation array — must
    // not reach the user as "HTTP 502" or "[object Object]".
    let detail = friendlyStatus(res.status);
    try {
      const body = await res.json();
      if (typeof body?.detail === "string" && body.detail) detail = body.detail;
    } catch {}
    const error: Error & { status?: number } = new Error(detail);
    // The status, without touching the message: callers match machine-readable
    // prefixes like "contact_required:email,phone" on it, so nothing may be
    // appended — but a caller that shows the error to a user needs to be able
    // to say WHICH failure it was ("Not authenticated" and "Not Found" look
    // the same on screen and mean very different things).
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export const api = {
  search: (q: string) => j<{ results: SearchResult[] }>(`/search?q=${encodeURIComponent(q)}`),
  quote: (symbol: string) => j<Quote>(`/quote/${encodeURIComponent(symbol)}`),
  chart: (symbol: string, range: string) =>
    j<ChartData>(`/chart/${encodeURIComponent(symbol)}?range=${encodeURIComponent(range)}`),
  news: (symbol: string) => j<{ results: NewsItem[] }>(`/news/${encodeURIComponent(symbol)}`),
  ohlc: (symbol: string, range: string) =>
    j<OhlcData>(`/ohlc/${encodeURIComponent(symbol)}?range=${encodeURIComponent(range)}`),
  portfolioOptimize: (body: {
    holdings: PortfolioHoldingInput[];
    objective: "hrp" | "max_sharpe" | "min_volatility";
    use_agent_views: boolean;
    cash: number;
  }) =>
    j<PortfolioOptimizeResult>(`/portfolio/optimize`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Upload a .xlsx or .csv file; returns parsed rows with resolved tickers.
   *  On web, `asset.file` is the native File object; on native it is a URI. */
  portfolioImport: (asset: { uri: string; name: string; mimeType?: string | null; file: Blob | null }) => {
    const form = new FormData();
    if (asset.file) {
      // Web: the picker hands over a real browser File.
      form.append("file", asset.file, asset.name);
    } else {
      // Native: an expo-file-system File over the picked (cached) copy. Expo
      // replaces the global fetch, and its FormData encoder only reads strings,
      // Blobs or objects with bytes() — the old React Native
      // { uri, name, type } literal threw "Unsupported FormDataPart
      // implementation" on Android. The cached copy keeps the extension
      // (<id>.xlsx), which is what the backend uses to pick the parser.
      form.append("file", new FsFile(asset.uri) as unknown as Blob, asset.name);
    }
    return j<ImportResult>(`/portfolio/import`, { method: "POST", body: form });
  },
  scans: () => j<{ india_market_open: boolean; scans: ScanMeta[] }>(`/scans`),
  scan: (id: string) => j<ScanMeta & { items: ScanItem[] }>(`/scans/${encodeURIComponent(id)}`),
  trending: () => j<{ results: Quote[] }>(`/trending`),
  markets: (category: string) => j<{ results: Quote[] }>(`/markets/${category}`),
  analyze: (symbol: string, name?: string, language?: string, deviceId?: string, chartReadingId?: string) =>
    j<Analysis>(`/analyze`, {
      method: "POST",
      body: JSON.stringify({
        symbol,
        name,
        language,
        device_id: deviceId,
        ...(chartReadingId ? { chart_reading_id: chartReadingId } : {}),
      }),
    }),
  // Same upload path as portfolioImport (web File, or an expo-file-system File
  // on native). The image is read once on the server and never stored.
  portfolioHealth: (body: {
    holdings: { symbol: string; quantity: number }[];
    cash: number;
    targets?: Partial<Record<HealthBucket, number>> | null;
  }) => j<HealthReport>(`/portfolio/health`, { method: "POST", body: JSON.stringify(body) }),
  tradeReviewPricing: () => j<TradeReviewPricing>(`/trade-review/pricing`),
  /** Up to three broker statement PDFs; parsed in memory, nothing stored. */
  tradeReview: (assets: { uri: string; name: string; file: Blob | null }[]) => {
    const form = new FormData();
    for (const asset of assets) {
      // Same web/native split as portfolioImport.
      if (asset.file) form.append("files", asset.file, asset.name);
      else form.append("files", new FsFile(asset.uri) as unknown as Blob, asset.name);
    }
    return j<TradeReview>(`/trade-review`, { method: "POST", body: form });
  },
  chartRead: (asset: { uri: string; name: string; file: Blob | null }) => {
    const form = new FormData();
    if (asset.file) {
      form.append("file", asset.file, asset.name);
    } else {
      form.append("file", new FsFile(asset.uri) as unknown as Blob, asset.name);
    }
    return j<ChartReadResult>(`/chart/read`, { method: "POST", body: form });
  },
  getWalletBalance: (deviceId: string) =>
    j<WalletBalance>(
      `/wallet/balance?device_id=${encodeURIComponent(deviceId)}${REGION ? `&region=${encodeURIComponent(REGION)}` : ""}`
    ),
  createTopupOrder: (
    deviceId: string,
    amount: number,
    contact?: { email?: string; phone?: string },
    // Only honoured on the account's very first top-up; the backend ignores
    // it once a currency is locked.
    currency?: string
  ) =>
    j<TopupOrder>(`/pay/order`, {
      method: "POST",
      body: JSON.stringify({
        device_id: deviceId,
        amount,
        ...(contact || {}),
        ...(currency ? { currency } : {}),
        ...(REGION ? { region: REGION } : {}),
        ...(RETURN_URL ? { return_url: RETURN_URL } : {}),
      }),
    }),
  setWalletCurrency: (deviceId: string, currency: string) =>
    j<{ currency: string; changed: boolean }>(`/wallet/currency`, {
      method: "POST",
      body: JSON.stringify({ device_id: deviceId, currency }),
    }),
  getPaymentStatus: (orderId: string) => j<PaymentStatus>(`/pay/status/${orderId}`),
  getIapConfig: (currency?: string) =>
    j<IapConfig>(`/pay/iap/config${currency ? `?currency=${encodeURIComponent(currency)}` : ""}`),
  getAnalysis: (id: string) => j<Analysis>(`/analysis/${id}`),
  history: () => j<{ results: Analysis[] }>(`/history`),
  remove: (id: string) => j<{ ok: boolean }>(`/analysis/${id}`, { method: "DELETE" }),
  requestOtp: (identifier: string, deviceId?: string) =>
    j<{ identifier: string; identifier_type: string; sent: boolean; debug_otp?: string }>(`/auth/otp/request`, {
      method: "POST",
      body: JSON.stringify({ identifier, device_id: deviceId }),
    }),
  verifyOtp: (identifier: string, otp: string, deviceId?: string) =>
    j<{ token: string; user: SessionUser }>(`/auth/otp/verify`, {
      method: "POST",
      body: JSON.stringify({ identifier, otp, device_id: deviceId }),
    }),
  googleSession: (sessionId: string, deviceId?: string) =>
    j<{ token: string; user: SessionUser }>(`/auth/session`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, device_id: deviceId }),
    }),
  // The raw Apple identity token. It is never trusted here — the backend
  // verifies its signature against Apple's published keys and checks the
  // audience before it becomes a session.
  //
  // `authorizationCode` is sent alongside it and is NOT for signing in: it is
  // the only thing that can be exchanged for a refresh token, and a refresh
  // token is the only thing Apple's revoke endpoint accepts — which Apple
  // requires us to call when the user deletes their account. It is single-use
  // and expires in minutes, so it has to travel with the sign-in.
  appleSignIn: (identityToken: string, deviceId?: string, authorizationCode?: string | null) =>
    j<{ token: string; user: SessionUser }>(`/auth/apple`, {
      method: "POST",
      body: JSON.stringify({
        token: identityToken,
        device_id: deviceId,
        ...(authorizationCode ? { authorization_code: authorizationCode } : {}),
      }),
    }),
  authMe: (token: string) =>
    j<SessionUser>(`/auth/me`, { headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } }),
  deleteAccount: () => j<{ deleted: boolean }>(`/account`, { method: "DELETE" }),
  // Only ever sends true: the backend rejects `agreed: false` outright rather
  // than storing a "no", and withdrawal is deleteAccount.
  recordConsent: () =>
    j<{ consent_given: boolean; consent_version: string }>(`/consent`, {
      method: "POST",
      body: JSON.stringify({ agreed: true }),
    }),
};