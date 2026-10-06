// Web build: React Native Firebase's native SDKs don't exist here, and the web
// app isn't registered with Firebase, so telemetry is a no-op. Same exports as
// firebase.ts — Metro picks this file for web automatically.

export function setTelemetryUser(_userId: string | null, _consented: boolean): void {}

export function trackScreen(_pathname: string): void {}

type EventName = "analysis_started" | "compare_started" | "portfolio_optimized" | "wallet_topup";

export function trackEvent(_name: EventName, _params?: Record<string, string | number>): void {}
