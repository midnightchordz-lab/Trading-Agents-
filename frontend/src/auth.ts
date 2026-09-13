import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { storage } from "@/src/utils/storage";
import { api, setAuthTokenGetter, SessionUser } from "@/src/api";

// Session token lives in the secure (Keychain/Keystore) namespace — never in
// plain AsyncStorage. SecureStore keys allow only alphanumerics, ".", "-", "_".
const KEY_SESSION_TOKEN = "auth_session_token";

export type { SessionUser };

export async function getStoredToken(): Promise<string | null> {
  const t = await storage.secureGet<string>(KEY_SESSION_TOKEN, "");
  return t || null;
}

export async function setStoredToken(token: string): Promise<void> {
  await storage.secureSet(KEY_SESSION_TOKEN, token);
}

export async function clearStoredToken(): Promise<void> {
  await storage.secureRemove(KEY_SESSION_TOKEN);
}

// Every api call picks the token up from here.
setAuthTokenGetter(getStoredToken);

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

  const signOut = useCallback(async () => {
    await clearStoredToken();
    setUser(null);
  }, []);

  return { checking, user, refresh: check, signOut };
}

export const AuthContext = createContext<{
  user: SessionUser | null;
  signOut: () => Promise<void>;
}>({ user: null, signOut: async () => {} });

export function useAuth() {
  return useContext(AuthContext);
}
