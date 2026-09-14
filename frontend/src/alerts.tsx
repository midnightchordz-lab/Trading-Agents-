// Local price-alert store backed by @/src/utils/storage.
// Alerts are evaluated in-app whenever a fresh quote is available (the analysis
// screen poll and the Alerts tab pull-to-refresh). When a level is crossed the
// alert is marked triggered and the user is pinged with a haptic + in-app
// dialog. No push notifications / background work — everything is in-app so it
// works in Expo Go today.

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Alert as RNAlert } from "react-native";
import * as Haptics from "expo-haptics";
import { storage } from "@/src/utils/storage";

export type PriceAlert = {
  id: string;
  symbol: string;
  name: string;
  label: string; // "TARGET" | "STOP LOSS" | "PRICE"
  price: number;
  direction: "above" | "below";
  currency?: string;
  createdAt: string;
  triggered: boolean;
  triggeredAt?: string | null;
};

const KEY = "price_alerts_v1";
const HISTORY_KEY = "alert_history_v1";
const HISTORY_LIMIT = 100;

/** Permanent log entry written the moment an alert fires, so the user can
 *  look back at which calls actually played out. */
export type FiredAlert = {
  id: string;
  symbol: string;
  name: string;
  label: string;
  price: number;
  direction: "above" | "below";
  currency?: string;
  createdAt: string;
  firedAt: string;
  priceAtFire: number;
};

type AlertsCtx = {
  items: PriceAlert[];
  history: FiredAlert[];
  ready: boolean;
  add: (a: Omit<PriceAlert, "id" | "createdAt" | "triggered" | "triggeredAt">) => void;
  remove: (id: string) => void;
  clearTriggered: () => void;
  clearHistory: () => void;
  findActive: (symbol: string, price: number, direction: "above" | "below") => PriceAlert | undefined;
  evaluate: (symbol: string, price?: number | null) => void;
};

const Ctx = createContext<AlertsCtx | null>(null);

function uid(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

async function save(items: PriceAlert[]) {
  await storage.setItem(KEY, JSON.stringify(items));
}

async function saveHistory(items: FiredAlert[]) {
  await storage.setItem(HISTORY_KEY, JSON.stringify(items));
}

export function AlertsProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<PriceAlert[]>([]);
  const [history, setHistory] = useState<FiredAlert[]>([]);
  const [ready, setReady] = useState(false);
  const itemsRef = useRef<PriceAlert[]>([]);
  itemsRef.current = items;
  const historyRef = useRef<FiredAlert[]>([]);
  historyRef.current = history;

  useEffect(() => {
    (async () => {
      const raw = await storage.getItem(KEY, "");
      try {
        const parsed = raw ? JSON.parse(raw) : [];
        if (Array.isArray(parsed)) setItems(parsed);
      } catch {
        // ignore malformed cache
      }
      const rawHist = await storage.getItem(HISTORY_KEY, "");
      try {
        const parsed = rawHist ? JSON.parse(rawHist) : [];
        if (Array.isArray(parsed)) setHistory(parsed);
      } catch {
        // ignore malformed cache
      }
      setReady(true);
    })();
  }, []);

  const add = useCallback((a: Omit<PriceAlert, "id" | "createdAt" | "triggered" | "triggeredAt">) => {
    setItems((prev) => {
      // Avoid duplicate active alerts on the same symbol/price/direction.
      const dupe = prev.some(
        (i) => !i.triggered && i.symbol === a.symbol && Math.abs(i.price - a.price) < 1e-6 && i.direction === a.direction,
      );
      if (dupe) return prev;
      const next = [
        { ...a, id: uid(), createdAt: new Date().toISOString(), triggered: false, triggeredAt: null },
        ...prev,
      ];
      save(next);
      return next;
    });
  }, []);

  const remove = useCallback((id: string) => {
    setItems((prev) => {
      const next = prev.filter((i) => i.id !== id);
      save(next);
      return next;
    });
  }, []);

  const clearTriggered = useCallback(() => {
    setItems((prev) => {
      const next = prev.filter((i) => !i.triggered);
      save(next);
      return next;
    });
  }, []);

  const clearHistory = useCallback(() => {
    setHistory(() => {
      saveHistory([]);
      return [];
    });
  }, []);

  const findActive = useCallback(
    (symbol: string, price: number, direction: "above" | "below") =>
      itemsRef.current.find(
        (i) => !i.triggered && i.symbol === symbol && Math.abs(i.price - price) < 1e-6 && i.direction === direction,
      ),
    [],
  );

  const evaluate = useCallback((symbol: string, price?: number | null) => {
    if (price == null || Number.isNaN(price)) return;
    const fired: PriceAlert[] = [];
    const current = itemsRef.current;
    const next = current.map((a) => {
      if (a.triggered || a.symbol !== symbol) return a;
      const hit = a.direction === "above" ? price >= a.price : price <= a.price;
      if (hit) {
        const t = { ...a, triggered: true, triggeredAt: new Date().toISOString() };
        fired.push(t);
        return t;
      }
      return a;
    });
    if (fired.length === 0) return;
    setItems(next);
    save(next);

    // Permanent log — kept even after the user clears fired alerts.
    const firedAt = new Date().toISOString();
    const logged: FiredAlert[] = fired.map((a) => ({
      id: a.id,
      symbol: a.symbol,
      name: a.name,
      label: a.label,
      price: a.price,
      direction: a.direction,
      currency: a.currency,
      createdAt: a.createdAt,
      firedAt,
      priceAtFire: price,
    }));
    const nextHistory = [...logged, ...historyRef.current].slice(0, HISTORY_LIMIT);
    setHistory(nextHistory);
    saveHistory(nextHistory);

    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    const a = fired[0];
    const cur = a.currency && a.currency !== "USD" ? ` ${a.currency}` : "";
    const px = a.direction === "above" ? "rose to" : "fell to";
    const more = fired.length > 1 ? ` (+${fired.length - 1} more)` : "";
    RNAlert.alert(
      "🔔 Price Alert",
      `${a.symbol} ${px} your ${a.label} of $${a.price.toLocaleString("en-US")}${cur}.${more}`,
    );
  }, []);

  return (
    <Ctx.Provider value={{ items, history, ready, add, remove, clearTriggered, clearHistory, findActive, evaluate }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAlerts(): AlertsCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAlerts must be used within AlertsProvider");
  return ctx;
}
