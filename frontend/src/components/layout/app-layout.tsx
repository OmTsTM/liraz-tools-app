import { Link, Outlet, useLocation } from "react-router-dom";

import { ThemeToggle } from "@/components/ui/theme-toggle";

export function AppLayout() {
  const location = useLocation();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="container flex h-14 items-center justify-between">
          {/* Logo é link pra home (seleção de lojas). Padrão de toda webapp:
              clique no logo = volta pro início. */}
          <Link
            to="/"
            className="flex items-center gap-2 rounded-md outline-none transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label="Voltar pra seleção de lojas"
          >
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-foreground">
              <span className="text-xs font-bold text-background">L</span>
            </div>
            <h1 className="text-sm font-semibold">LiraZ Tools</h1>
          </Link>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground">v0.1.0</span>
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/*
        key={location.pathname} força React a remontar a árvore quando muda
        de rota, disparando o `animate-fade-in` em cima do main. Resultado:
        cada rota nova entra com fade suave.
      */}
      <main key={location.pathname} className="container animate-fade-in py-8">
        <Outlet />
      </main>
    </div>
  );
}
