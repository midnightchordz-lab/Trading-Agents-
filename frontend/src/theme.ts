// Brutalist trading terminal design tokens (from design_guidelines.json).
// Sharp 0-radius corners, 2pt strong borders, no shadows, light surface.

export const colors = {
  surface: "#FFFFFF",
  onSurface: "#09090B",
  surfaceSecondary: "#F5F3FF",
  surfaceTertiary: "#E4E4E7",
  onSurfaceTertiary: "#3F3F55",
  surfaceInverse: "#12121A",
  onSurfaceInverse: "#FFFFFF",
  brand: "#4F46E5",
  brandDeep: "#7C3AED",
  brandBlue: "#2563EB",
  success: "#16A34A",
  onSuccess: "#FFFFFF",
  warning: "#F59E0B",
  onWarning: "#000000",
  error: "#E11D48",
  onError: "#FFFFFF",
  info: "#2563EB",
  onInfo: "#FFFFFF",
  border: "#E7E5F4",
  borderStrong: "#12121A",
  divider: "#E7E5F4",
} as const;

// Vibrant accent palette used for chips, cards, headers and icons.
export const accents = {
  blue: "#2563EB",
  indigo: "#4F46E5",
  violet: "#7C3AED",
  pink: "#DB2777",
  amber: "#F59E0B",
  teal: "#0D9488",
  lime: "#65A30D",
  orange: "#EA580C",
  cyan: "#0891B2",
} as const;

export const ACCENT_CYCLE = [
  accents.blue,
  accents.violet,
  accents.pink,
  accents.amber,
  accents.teal,
  accents.orange,
  accents.cyan,
  accents.lime,
] as const;

export function accentAt(i: number): string {
  return ACCENT_CYCLE[((i % ACCENT_CYCLE.length) + ACCENT_CYCLE.length) % ACCENT_CYCLE.length];
}

export const HEADER_GRADIENT = ["#4F46E5", "#7C3AED", "#DB2777"] as const;
export const CTA_GRADIENT = ["#2563EB", "#7C3AED"] as const;

export const PHASE_COLORS: Record<string, string> = {
  analysis: accents.blue,
  debate: accents.pink,
  trade: accents.teal,
  risk: accents.amber,
  decision: colors.success,
};

export const CATEGORY_COLORS: Record<string, string> = {
  trending: accents.amber,
  stocks: accents.blue,
  crypto: accents.violet,
  commodities: accents.teal,
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

export const fonts = {
  display: "SpaceGrotesk-Bold",
  displayMed: "SpaceGrotesk-Medium",
  displayReg: "SpaceGrotesk-Regular",
  mono: "JetBrainsMono-Regular",
  monoMed: "JetBrainsMono-Medium",
  monoBold: "JetBrainsMono-Bold",
} as const;

export const RADIUS = 0;
export const BORDER = 2;

export type Decision = "BUY" | "SELL" | "HOLD";
export type Sentiment = "bullish" | "bearish" | "neutral" | null | undefined;

export function verdictColors(decision?: string): { bg: string; fg: string } {
  switch ((decision || "").toUpperCase()) {
    case "BUY":
      return { bg: colors.success, fg: colors.onSuccess };
    case "SELL":
      return { bg: colors.error, fg: colors.onError };
    case "HOLD":
      return { bg: colors.warning, fg: colors.onWarning };
    default:
      return { bg: colors.surfaceInverse, fg: colors.onSurfaceInverse };
  }
}

export function sentimentColor(sentiment?: Sentiment): string {
  switch (sentiment) {
    case "bullish":
      return colors.success;
    case "bearish":
      return colors.error;
    case "neutral":
      return colors.onSurfaceTertiary;
    default:
      return colors.onSurfaceTertiary;
  }
}

export function changeColor(v?: number | null): string {
  if (v == null) return colors.onSurfaceTertiary;
  if (v > 0) return colors.success;
  if (v < 0) return colors.error;
  return colors.onSurfaceTertiary;
}
