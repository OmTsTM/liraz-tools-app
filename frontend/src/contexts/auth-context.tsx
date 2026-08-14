/**
 * Contexto global de autenticação.
 *
 * - Carrega `/api/auth/me` no boot. Se 401, marca `user=null` (não logado).
 * - Expõe `login(email, password)` e `logout()` que invalidam caches do
 *   TanStack Query depois (forçando refetch com a nova sessão).
 * - Registra um handler global de 401 no `apiRequest` — qualquer endpoint
 *   que devolver 401 dispara o auto-logout (volta pra `/login`).
 */
import { useQueryClient } from "@tanstack/react-query";
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";

import { type AuthUser, authApi } from "@/api/auth";
import { ApiError, setUnauthorizedHandler } from "@/api/client";

type AuthState =
  | { status: "loading"; user: null }
  | { status: "anon"; user: null }
  | { status: "auth"; user: AuthUser };

interface AuthContextValue {
  state: AuthState;
  user: AuthUser | null;
  isAdmin: boolean;
  login(email: string, password: string): Promise<AuthUser>;
  logout(): Promise<void>;
  refresh(): Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading", user: null });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // Evita loops de redirect caso `me()` em si dê 401 (esperado quando anon)
  const handlingUnauthRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const me = await authApi.me();
      setState({ status: "auth", user: me });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setState({ status: "anon", user: null });
      } else {
        // Erro de rede / 500: trata como anon pra não travar a UI no boot
        setState({ status: "anon", user: null });
      }
    }
  }, []);

  // Boot: tenta carregar user atual
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Handler global de 401: limpa estado + manda pra /login. Não dispara durante
  // o /me inicial (que SABE que pode dar 401).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (handlingUnauthRef.current) return;
      handlingUnauthRef.current = true;
      setState({ status: "anon", user: null });
      queryClient.clear();
      navigate("/login", { replace: true });
      // Solta o lock no próximo tick — permite logins subsequentes funcionarem
      setTimeout(() => {
        handlingUnauthRef.current = false;
      }, 100);
    });
    return () => {
      setUnauthorizedHandler(null);
    };
  }, [navigate, queryClient]);

  const login = useCallback(
    async (email: string, password: string) => {
      const u = await authApi.login(email, password);
      setState({ status: "auth", user: u });
      // Limpa cache do TanStack Query — dados anteriores (de outra sessão ou
      // de tentativas anônimas em cache) podem virar inválidos. Próxima
      // navegação refetcha tudo.
      queryClient.clear();
      return u;
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignora erro de logout — limpamos local mesmo
    }
    setState({ status: "anon", user: null });
    queryClient.clear();
    navigate("/login", { replace: true });
  }, [navigate, queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      user: state.user,
      isAdmin: state.status === "auth" && state.user.is_admin,
      login,
      logout,
      refresh,
    }),
    [state, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth precisa estar dentro de <AuthProvider>");
  return ctx;
}
