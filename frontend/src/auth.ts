import { useCallback, useEffect, useState } from "react";
import { storage } from "@/src/utils/storage";
import { api } from "@/src/api";

const KEY_SESSION_TOKEN = "auth:session_token";

export type SessionUser = { id: string; phone?: string | null; email?: string | null };

export async function getStoredToken(): Promise<string | null> {
  const t = await storage.getItem<string>(KEY_SESSION_TOKEN, "");
  return t || null;
}

export async function setStoredToken(token: string): Promise<void> {
  await storage.setItem(KEY_SESSION_TOKEN, token);
}

export async function clearStoredToken(): Promise<void> {
  await storage.setItem(KEY_SESSION_TOKEN, "");
}

/** Resolves once on mount: is there a still-valid session? */
export function useAuthGate() {
  const [checking, setChecking] = useState(true);
  const [user, setUser] = useState<SessionUser | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    const token = await getStoredToken();
    if (!token) {
      setUser(null);
      setChecking(false);
      return;
    }
    try {
      const me = await api.authMe(token);
      setUser(me);
    } catch {
      await clearStoredToken();
      setUser(null);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  return { checking, user, refresh: check };
}
