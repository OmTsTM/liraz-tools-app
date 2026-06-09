import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, LogIn } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/auth-context";

const loginSchema = z.object({
  email: z.string().min(1, "Informe o e-mail").email("E-mail inválido"),
  password: z.string().min(1, "Informe a senha"),
});

type LoginForm = z.infer<typeof loginSchema>;

export function LoginPage() {
  const { login, state } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // Se o user já está logado, redireciona pra home — não faz sentido ver login
  useEffect(() => {
    if (state.status === "auth") {
      const target = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(target, { replace: true });
    }
  }, [state.status, navigate, location.state]);

  const form = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await login(values.email, values.password);
      // O efeito acima redireciona quando state vira "auth"
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        form.setError("password", { message: "E-mail ou senha incorretos" });
      } else if (e instanceof ApiError && e.status === 403) {
        toast.error("Conta desativada", {
          description: "Peça pra um admin reativar sua conta.",
        });
      } else {
        toast.error("Erro de login", {
          description: e instanceof Error ? e.message : "Tente de novo",
        });
      }
    }
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="mb-2 flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-foreground">
              <span className="text-sm font-bold text-background">L</span>
            </div>
            <span className="text-base font-semibold">LiraZ Tools</span>
          </div>
          <CardTitle className="text-xl">Entrar</CardTitle>
          <CardDescription>
            Use o e-mail e senha cadastrados pelo administrador da empresa.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="email">E-mail</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                autoFocus
                disabled={form.formState.isSubmitting}
                {...form.register("email")}
              />
              {form.formState.errors.email && (
                <p className="text-xs text-destructive">{form.formState.errors.email.message}</p>
              )}
            </div>
            <div className="space-y-1">
              <Label htmlFor="password">Senha</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                disabled={form.formState.isSubmitting}
                {...form.register("password")}
              />
              {form.formState.errors.password && (
                <p className="text-xs text-destructive">{form.formState.errors.password.message}</p>
              )}
            </div>
            <Button type="submit" className="w-full" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <LogIn className="mr-2 h-4 w-4" />
              )}
              Entrar
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
