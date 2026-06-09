import { ArrowLeft, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

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
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { useDeleteProfile, useProfile } from "@/features/profiles/hooks";
import { WizardStepImportMCP } from "@/features/profiles/wizard/step-import-mcp";
import { type ConnectionMethod, WizardStepMethod } from "@/features/profiles/wizard/step-method";
import { WizardStepOAuth } from "@/features/profiles/wizard/step-oauth";

type Step = "method" | "oauth" | "mcp_import";

/**
 * Tela acessada quando o usuário clica numa loja em draft pelo seletor.
 * Reusa os steps de método/oauth/import sem precisar criar perfil novo.
 *
 * Também tem botão "Excluir loja" pra descartar perfis em draft que
 * não vão mais ser conectados.
 */
export function ProfileSetupPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: profile, isLoading } = useProfile(id);
  const deleteProfile = useDeleteProfile();
  const [step, setStep] = useState<Step>("method");
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (isLoading) {
    return (
      <div className="mx-auto max-w-xl space-y-4">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!profile || !id) return null;

  const handleConnected = () => {
    toast.success("Loja conectada", {
      description: `${profile.name} pronta pra uso.`,
    });
    navigate(`/profiles/${id}`);
  };

  const handleMethodSelect = (method: ConnectionMethod) => {
    setStep(method === "oauth" ? "oauth" : "mcp_import");
  };

  const handleDelete = async () => {
    setConfirmDelete(false);
    try {
      await deleteProfile.mutateAsync(id);
      toast.success("Loja excluída", {
        description: `"${profile.name}" foi removida.`,
      });
      navigate("/");
    } catch (err) {
      toast.error("Falha ao excluir loja", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const canDelete = profile.status === "draft";

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={() => navigate("/")}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>

        {canDelete && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmDelete(true)}
            disabled={deleteProfile.isPending}
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
            Excluir loja
          </Button>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Conectar {profile.name}</CardTitle>
          <p className="text-sm text-muted-foreground">
            Loja em estado <span className="font-mono">{profile.status}</span> — escolha como
            autorizar.
          </p>
        </CardHeader>
        <CardContent>
          {step === "method" && (
            <WizardStepMethod onBack={() => navigate("/")} onSelect={handleMethodSelect} />
          )}
          {step === "oauth" && (
            <WizardStepOAuth
              profileId={id}
              onBack={() => setStep("method")}
              onConnected={handleConnected}
            />
          )}
          {step === "mcp_import" && (
            <WizardStepImportMCP
              profileId={id}
              onBack={() => setStep("method")}
              onConnected={handleConnected}
            />
          )}
        </CardContent>
      </Card>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir "{profile.name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              A loja será apagada permanentemente. Como ela está em estado draft (nunca foi
              conectada), nenhum dado relevante será perdido.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Excluir permanentemente
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
