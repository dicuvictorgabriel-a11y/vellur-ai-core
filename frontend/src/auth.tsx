import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "./api/client";
import type { Me } from "./api/types";

interface AuthContextValue {
  me: Me | null;
  loading: boolean;
  error: string;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setMe(await api.me());
    } catch (reason) {
      setMe({ logged_in: false });
      setError(reason instanceof Error ? reason.message : "Cannot reach the controller");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (password: string) => {
    setError("");
    try {
      const result = await api.login(password);
      const nextMe = await api.me();
      setMe({ ...nextMe, must_change: result.must_change ?? nextMe.must_change });
    } catch (reason) {
      const message = reason instanceof ApiError ? reason.message : "Login failed";
      setError(message);
      throw new Error(message);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setMe({ logged_in: false });
    }
  }, []);

  const changePassword = useCallback(async (current: string, next: string) => {
    await api.changePassword(current, next);
    setMe(await api.me());
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ me, loading, error, login, logout, changePassword, refresh }),
    [me, loading, error, login, logout, changePassword, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
