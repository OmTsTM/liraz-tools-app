/**
 * Wrapper que exige sessão pra renderizar children. Anônimo → redirect /login.
 * Loading → spinner (mesmo padrão do AppLayout).
 *
 * `requireAdmin`: true se a rota é só pra admin do sistema. Não-admin recebe
 * 403 (página simples sem permissão).
 */
import { Loader2, ShieldX } from "lucide-react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "@/contexts/auth-context";

export function ProtectedRoute({
  children,
  requireAdmin = false,
}: {
  children: React.ReactNode;
  requireAdmin?: boolean;
}) {
  const { state, isAdmin } = useAuth();
  const location = useLocation();

  if (state.status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (state.status === "anon") {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  if (requireAdmin && !isAdmin) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <ShieldX className="h-10 w-10 text-muted-foreground" />
        <h2 className="text-lg font-semibold">Acesso restrito</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          Esta área é só pra administradores do sistema. Peça pro admin da empresa pra te dar
          acesso.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
