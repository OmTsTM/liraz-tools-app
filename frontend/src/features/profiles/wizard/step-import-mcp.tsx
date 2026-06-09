import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Loader2, Upload } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toaster";
import { useImportFromMCP } from "@/features/profiles/oauth-hooks";

const schema = z.object({
  tokens_db_path: z.string().min(1, "Caminho obrigatório").trim(),
  client_id: z.string().min(1, "Client ID obrigatório").trim(),
  client_secret: z.string().min(1, "Client Secret obrigatório").trim(),
  redirect_uri: z.string().min(1, "Redirect URI obrigatório").trim(),
});

type FormValues = z.infer<typeof schema>;

interface WizardStepImportMCPProps {
  profileId: string;
  onBack: () => void;
  onConnected: () => void;
}

export function WizardStepImportMCP({ profileId, onBack, onConnected }: WizardStepImportMCPProps) {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      tokens_db_path: "C:\\Users\\Matteus\\projetos\\mcp-mercadolivre\\data\\tokens.db",
      client_id: "",
      client_secret: "",
      redirect_uri: "",
    },
  });

  const importMutation = useImportFromMCP();

  const onSubmit = async (data: FormValues) => {
    try {
      await importMutation.mutateAsync({ profileId, data });
      toast.success("Loja importada com sucesso", {
        description: "Credenciais validadas contra o Mercado Livre.",
      });
      onConnected();
    } catch (err) {
      toast.error("Falha ao importar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
      <div className="rounded-lg border bg-muted/30 p-4 text-sm">
        <p className="font-medium">Importação de projeto MCP</p>
        <p className="mt-1 text-muted-foreground">
          Lê o <code className="font-mono text-xs">tokens.db</code> de um projeto MCP existente e
          reusa o refresh token. Nenhuma autorização nova é necessária no Mercado Livre.
        </p>
      </div>

      <div className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="tokens_db_path">Caminho do tokens.db</Label>
          <Input
            id="tokens_db_path"
            placeholder="C:\Users\...\mcp-mercadolivre\data\tokens.db"
            autoComplete="off"
            {...register("tokens_db_path")}
          />
          {errors.tokens_db_path && (
            <p className="text-xs text-destructive">{errors.tokens_db_path.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="client_id">CLIENT_ID do MCP</Label>
          <Input
            id="client_id"
            placeholder="App ID da aplicação ML usada pelo MCP"
            autoComplete="off"
            {...register("client_id")}
          />
          {errors.client_id && (
            <p className="text-xs text-destructive">{errors.client_id.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="client_secret">CLIENT_SECRET do MCP</Label>
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
          <Label htmlFor="redirect_uri">Redirect URI do MCP</Label>
          <Input
            id="redirect_uri"
            placeholder="https://mcp.seudominio.com/mcp/callback"
            autoComplete="off"
            {...register("redirect_uri")}
          />
          {errors.redirect_uri && (
            <p className="text-xs text-destructive">{errors.redirect_uri.message}</p>
          )}
          <p className="text-xs text-muted-foreground">
            Precisa ser exatamente o que está cadastrado na aplicação ML do MCP.
          </p>
        </div>
      </div>

      <div className="flex justify-between border-t pt-4">
        <Button type="button" variant="ghost" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>
        <Button type="submit" disabled={importMutation.isPending}>
          {importMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Upload className="h-4 w-4" />
          )}
          Importar
        </Button>
      </div>
    </form>
  );
}
