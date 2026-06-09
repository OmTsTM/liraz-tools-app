import { ArrowLeft, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/components/ui/toaster";
import { useCreateProfile, useDeleteProfile } from "@/features/profiles/hooks";
import { WizardStepImportMCP } from "@/features/profiles/wizard/step-import-mcp";
import { type ConnectionMethod, WizardStepMethod } from "@/features/profiles/wizard/step-method";
import { WizardStepName } from "@/features/profiles/wizard/step-name";
import { WizardStepOAuth } from "@/features/profiles/wizard/step-oauth";

type Step = "name" | "method" | "oauth" | "mcp_import";

export function ProfileNewPage() {
  const navigate = useNavigate();
  const createProfile = useCreateProfile();
  const deleteProfile = useDeleteProfile();

  const [step, setStep] = useState<Step>("name");
  const [profileId, setProfileId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);

  const handleNameSubmit = async (newName: string) => {
    try {
      const profile = await createProfile.mutateAsync({ name: newName });
      setProfileId(profile.id);
      setName(newName);
      setStep("method");
    } catch (err) {
      toast.error("Falha ao criar loja", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleMethodSelect = (method: ConnectionMethod) => {
    setStep(method === "oauth" ? "oauth" : "mcp_import");
  };

  const handleConnected = () => {
    if (!profileId) return;
    toast.success("Loja conectada", {
      description: `${name} pronta pra uso.`,
    });
    navigate(`/profiles/${profileId}`);
  };

  /**
   * Cancela o wizard e apaga o perfil draft criado.
   * Hard delete porque a loja nunca foi conectada — nada a preservar.
   */
  const handleCancelAndDelete = async () => {
    setConfirmCancel(false);
    if (!profileId) {
      navigate("/");
      return;
    }
    try {
      await deleteProfile.mutateAsync(profileId);
      toast.success("Loja descartada", {
        description: `"${name}" foi removida.`,
      });
      navigate("/");
    } catch (err) {
      toast.error("Falha ao descartar loja", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const stepTitle = {
    name: "Adicionar nova loja",
    method: "Como conectar?",
    oauth: "Conectar via Mercado Livre",
    mcp_import: "Importar de MCP existente",
  }[step];

  const stepDescription = {
    name: "Como você quer chamar essa loja no app?",
    method: "Escolha o método de conexão.",
    oauth: "Autorize a aplicação no Mercado Livre.",
    mcp_import: "Reuse credenciais de um projeto MCP existente.",
  }[step];

  // O botão "Cancelar e excluir" aparece quando o perfil já foi criado
  // mas ainda não foi conectado (passos method/oauth/mcp_import)
  const canCancelAndDelete = profileId !== null && step !== "name";

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={() => navigate("/")}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>

        {canCancelAndDelete && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmCancel(true)}
            disabled={deleteProfile.isPending}
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
            Cancelar e excluir
          </Button>
        )}
      </div>

      <div className="flex items-center gap-2 text-sm">
        <StepPill active={step === "name"} done={step !== "name"}>
          1. Nome
        </StepPill>
        <div className="h-px flex-1 bg-border" />
        <StepPill active={step === "method"} done={step === "oauth" || step === "mcp_import"}>
          2. Método
        </StepPill>
        <div className="h-px flex-1 bg-border" />
        <StepPill active={step === "oauth" || step === "mcp_import"} done={false}>
          3. Conexão
        </StepPill>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{stepTitle}</CardTitle>
          <p className="text-sm text-muted-foreground">{stepDescription}</p>
        </CardHeader>
        <CardContent>
          {step === "name" && <WizardStepName onSubmit={handleNameSubmit} />}
          {step === "method" && profileId && (
            <WizardStepMethod onBack={() => setStep("name")} onSelect={handleMethodSelect} />
          )}
          {step === "oauth" && profileId && (
            <WizardStepOAuth
              profileId={profileId}
              onBack={() => setStep("method")}
              onConnected={handleConnected}
            />
          )}
          {step === "mcp_import" && profileId && (
            <WizardStepImportMCP
              profileId={profileId}
              onBack={() => setStep("method")}
              onConnected={handleConnected}
            />
          )}
        </CardContent>
      </Card>

      <AlertDialog open={confirmCancel} onOpenChange={setConfirmCancel}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Descartar "{name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              A loja será apagada permanentemente do app. Como ela ainda não foi conectada ao
              Mercado Livre, nenhum dado importante será perdido — só o nome digitado.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Continuar configurando</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleCancelAndDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Sim, descartar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function StepPill({
  active,
  done,
  children,
}: {
  active: boolean;
  done: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={
        active
          ? "rounded-full bg-foreground px-3 py-1 text-xs font-medium text-background"
          : done
            ? "rounded-full bg-success/10 px-3 py-1 text-xs font-medium text-success"
            : "rounded-full bg-muted px-3 py-1 text-xs font-medium text-muted-foreground"
      }
    >
      {children}
    </span>
  );
}
