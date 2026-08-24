import { createContext, useContext, useState, type ReactNode } from "react";
import { api, setToken } from "../api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(!!localStorage.getItem("dptv_token"));

  async function login(username: string, password: string) {
    const res = await api.post("/api/auth/login", { username, password });
    setToken(res.data.access_token);
    setIsAuthenticated(true);
  }

  function logout() {
    setToken(null);
    setIsAuthenticated(false);
  }

  return <AuthContext.Provider value={{ isAuthenticated, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
