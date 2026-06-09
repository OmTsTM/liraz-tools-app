import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, ExternalLink, Loader2 } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toaster";
import { useProfile } from "@/features/profiles/hooks";
import { useGetAuthorizationUrl, useSaveCredentials } from "@/features/profiles/oauth-hooks";

const schema = z.object({
  client_id: z.string().min(1, "App ID obrigatório").trim(),
  client_secret: z.string().min(1, "Secret obrigatório").trim(),
  redirect_uri: z.string().url("URL inválida").trim(),
});

type FormValues = z.infer<typeof schema>;

interface WizardStepOAuthProps {
  profileId: string;
  onBack: () => void;
  onConnected: () => void;
}

export function WizardStepOAuth({ profileId, onBack, onConnected }: WizardStepOAuthProps) {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      client_id: "",
      client_secret: "",
      redirect_uri: "http://localhost:8000/api/auth/callback",
    },
  });

  const saveCredentials = useSaveCredentials();
  const getAuthUrl = useGetAuthorizationUrl();

  // Poll do perfil pra detectar quando conecta
  const { data: profile, refetch } = useProfile(profileId);

  useEffect(() => {
    if (profile?.status === "connected") {
      onConnected();
    }
  }, [profile?.status, onConnected]);

  // Polling enquanto não conecta — só roda depois que o usuário abre o navegador
  useEffect(() => {
    if (!getAuthUrl.isSuccess) return;
    const interval = setInterval(() => {
      refetch();
    }, 2000);
    return () => clearInterval(interval);
  }, [getAuthUrl.isSuccess, refetch]);

  const onSubmit = async (data: FormValues) => {
    try {
      await saveCredentials.mutateAsync({ profileId, data });
      const result = await getAuthUrl.mutateAsync(profileId);
      window.open(result.url, "_blank", "noopener,noreferrer");
      toast.success("Aba aberta no navegador", {
        description: "Autorize a aplicação no Mercado Livre.",
      });
    } catch (err) {
      toast.error("Falha ao iniciar autorização", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const isWaiting = getAuthUrl.isSuccess && profile?.status !== "connected";

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
      <div className="rounded-lg border bg-muted/30 p-4 text-sm">
        <p className="font-medium">Como obter essas credenciais</p>
        <ol className="ml-5 mt-2 list-decimal space-y-1 text-muted-foreground">
          <li>
            Acesse{" "}
            <a
              href="https://developers.mercadolivre.com.br/devcenter"
              target="_blank"
              rel="noreferrer"
              className="font-medium text-foreground underline"
            >
              developers.mercadolivre.com.br/devcenter
            </a>
          </li>
          <li>Crie uma nova aplicação (ou use uma existente)</li>
          <li>
            Configure o Redirect URI como{" "}
            <code className="rounded bg-background px-1 font-mono text-xs">
              http://localhost:8000/api/auth/callback
            </code>
          </li>
          <li>Copie o App ID e Secret Key abaixo</li>
        </ol>
      </div>

      <div className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="client_id">App ID (Client ID)</Label>
          <Input
            id="client_id"
            placeholder="1234567890123456"
            autoComplete="off"
            {...register("client_id")}
          />
          {errors.client_id && (
            <p className="text-xs text-destructive">{errors.client_id.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="client_secret">Secret Key (Client Secret)</Label>
          <Input
            id="client_secret"
            type="password"
            placeholder="•••••••••••••••••••••••••"
            autoComplete="off"
            {...register("client_secret")}
          />
          {errors.client_secret && (
            <p className="text-xs text-destructive">{errors.client_secret.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="redirect_uri">Redirect URI</Label>
          <Input id="redirect_uri" autoComplete="off" {...register("redirect_uri")} />
          {errors.redirect_uri && (
            <p className="text-xs text-destructive">{errors.redirect_uri.message}</p>
          )}
          <p className="text-xs text-muted-foreground">
            Precisa bater exatamente com o que está cadastrado na sua aplicação ML.
          </p>
        </div>
      </div>

      {isWaiting && (
        <div className="flex items-center gap-3 rounded-lg border border-warning/30 bg-warning/5 p-4 text-sm">
          <Loader2 className="h-4 w-4 animate-spin text-warning" />
          <div className="flex-1">
            <p className="font-medium">Aguardando autorização...</p>
            <p className="text-muted-foreground">
              Complete o fluxo na aba que abrimos. Esta página detecta automaticamente quando você
              terminar.
            </p>
          </div>
        </div>
      )}

      <div className="flex justify-between border-t pt-4">
        <Button type="button" variant="ghost" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>
        <Button
          type="submit"
          disabled={saveCredentials.isPending || getAuthUrl.isPending || isWaiting}
        >
          {saveCredentials.isPending || getAuthUrl.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ExternalLink className="h-4 w-4" />
          )}
          Abrir Mercado Livre
        </Button>
      </div>
    </form>
  );
}
