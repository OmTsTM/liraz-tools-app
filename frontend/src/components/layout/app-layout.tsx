import { LogOut, Shield, User } from "lucide-react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { useAuth } from "@/contexts/auth-context";

export function AppLayout() {
  const location = useLocation();
  const { user, isAdmin, logout } = useAuth();

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
            {user && (
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="ghost" size="sm" className="gap-2">
                    <User className="h-4 w-4" />
                    <span className="text-xs">{user.nome || user.email}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="end" className="w-56 p-2">
                  <div className="border-b px-2 pb-2">
                    <div className="text-sm font-medium">{user.nome || "—"}</div>
                    <div className="text-xs text-muted-foreground">{user.email}</div>
                    {isAdmin && (
                      <div className="mt-1 inline-flex items-center gap-1 rounded bg-foreground/10 px-1.5 py-0.5 text-[10px] font-medium">
                        <Shield className="h-3 w-3" />
                        Admin do sistema
                      </div>
                    )}
                  </div>
                  <div className="space-y-1 pt-2">
                    {isAdmin && (
                      <Button asChild variant="ghost" size="sm" className="w-full justify-start">
                        <Link to="/admin/users">
                          <Shield className="mr-2 h-4 w-4" />
                          Gerenciar usuários
                        </Link>
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full justify-start text-destructive hover:text-destructive"
                      onClick={() => void logout()}
                    >
                      <LogOut className="mr-2 h-4 w-4" />
                      Sair
                    </Button>
                  </div>
                </PopoverContent>
              </Popover>
            )}
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
