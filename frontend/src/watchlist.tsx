// Local watchlist store backed by @/src/utils/storage.
// The storage helper only accepts string|number|boolean|null, so we persist a
// JSON string and parse it back into an array of {symbol,name}.

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { storage } from "@/src/utils/storage";

export type WatchItem = { symbol: string; name: string };

const KEY = "watchlist_v1";

type WatchlistCtx = {
  items: WatchItem[];
  ready: boolean;
  isSaved: (symbol: string) => boolean;
  toggle: (item: WatchItem) => void;
  remove: (symbol: string) => void;
};

const Ctx = createContext<WatchlistCtx | null>(null);

async function save(items: WatchItem[]) {
  await storage.setItem(KEY, JSON.stringify(items));
}

export function WatchlistProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (async () => {
      const raw = await storage.getItem(KEY, "");
      try {
        const parsed = raw ? JSON.parse(raw) : [];
        if (Array.isArray(parsed)) setItems(parsed);
      } catch {
        // ignore malformed cache
      }
      setReady(true);
    })();
  }, []);

  const isSaved = useCallback((symbol: string) => items.some((i) => i.symbol === symbol), [items]);

  const toggle = useCallback((item: WatchItem) => {
    setItems((prev) => {
      const exists = prev.some((i) => i.symbol === item.symbol);
      const next = exists
        ? prev.filter((i) => i.symbol !== item.symbol)
        : [{ symbol: item.symbol, name: item.name }, ...prev];
      save(next);
      return next;
    });
  }, []);

  const remove = useCallback((symbol: string) => {
    setItems((prev) => {
      const next = prev.filter((i) => i.symbol !== symbol);
      save(next);
      return next;
    });
  }, []);

  return <Ctx.Provider value={{ items, ready, isSaved, toggle, remove }}>{children}</Ctx.Provider>;
}

export function useWatchlist(): WatchlistCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWatchlist must be used within WatchlistProvider");
  return ctx;
}
