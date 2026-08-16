// Brutalist trading terminal design tokens (from design_guidelines.json).
// Sharp 0-radius corners, 2pt strong borders, no shadows, light surface.

export const colors = {
  surface: "#FFFFFF",
  onSurface: "#09090B",
  surfaceSecondary: "#F4F4F5",
  surfaceTertiary: "#E4E4E7",
  onSurfaceTertiary: "#27272A",
  surfaceInverse: "#09090B",
  onSurfaceInverse: "#FFFFFF",
  brand: "#09090B",
  success: "#16A34A",
  onSuccess: "#FFFFFF",
  warning: "#F59E0B",
  onWarning: "#000000",
  error: "#DC2626",
  onError: "#FFFFFF",
  info: "#2563EB",
  onInfo: "#FFFFFF",
  border: "#E4E4E7",
  borderStrong: "#09090B",
  divider: "#E4E4E7",
} as const;

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
