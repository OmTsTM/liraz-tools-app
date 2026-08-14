/**
 * Root layout — envolve toda a árvore de rotas no AuthProvider.
 *
 * `AuthProvider` depende de `useNavigate` (do react-router), então precisa
 * ser renderizado DENTRO do `RouterProvider`. Esse layout é o root da árvore
 * de rotas e expõe um `<Outlet />` pras rotas filhas.
 */
import { Outlet } from "react-router-dom";

import { AuthProvider } from "@/contexts/auth-context";

export function RootLayout() {
  return (
    <AuthProvider>
      <Outlet />
    </AuthProvider>
  );
}
