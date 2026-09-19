// API client for the TradingAgents backend.
import * as Localization from "expo-localization";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;
const API = `${BASE}/api`;

// Device region, sent with wallet calls so the backend can pick the right
// currency for a wallet that hasn't locked one yet (INR for India, because
// UPI/GPay only exist on INR payment links). The backend decides; this is
// only a hint, and an unknown region is simply omitted.
const REGION = (() => {
  try {
    return Localization.getLocales()[0]?.regionCode || "";
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
};

export type CurrencyOption = {
  code: string;
  symbol: string;
  packs: number[];
  prices: Record<string, number>;
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
  current_step: number;
  total_steps: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

async function j<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = authTokenGetter ? await authTokenGetter() : null;
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail || detail;
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
  trending: () => j<{ results: Quote[] }>(`/trending`),
  markets: (category: string) => j<{ results: Quote[] }>(`/markets/${category}`),
  analyze: (symbol: string, name?: string, language?: string, deviceId?: string) =>
    j<Analysis>(`/analyze`, { method: "POST", body: JSON.stringify({ symbol, name, language, device_id: deviceId }) }),
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
      }),
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