import {
  AlertCircle,
  Archive,
  ArrowLeft,
  Calendar,
  CheckCircle2,
  Loader2,
  LogOut,
  Sparkles,
  Tag,
  TrendingUp,
  Wifi,
} from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { RoleGate } from "@/components/auth/role-gate";
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { ConfigEditorCard } from "@/features/profiles/config-editor-card";
import { useArchiveProfile, useProfile } from "@/features/profiles/hooks";
import { useDisconnectProfile, useTestMLConnection } from "@/features/profiles/oauth-hooks";
import { ProfileStatusBadge } from "@/features/profiles/profile-status-badge";
import { TestConnectionModal } from "@/features/profiles/test-connection-modal";
import { RelatorioDiarioCard } from "@/features/relatorios/relatorio-diario-card";
import type { MLConnectionTestResponse } from "@/types/api";

export function ProfileDashboardPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: profile, isLoading } = useProfile(id);

  const testConnection = useTestMLConnection();
  const disconnect = useDisconnectProfile();
  const archive = useArchiveProfile();

  const [testResult, setTestResult] = useState<MLConnectionTestResponse | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!profile || !id) return null;

  const isConnected = profile.status === "connected";

  const handleTest = async () => {
    try {
      const result = await testConnection.mutateAsync(id);
      setTestResult(result);
    } catch (err) {
      toast.error("Falha no teste de conexão", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleDisconnect = async () => {
    setConfirmDisconnect(false);
    try {
      await disconnect.mutateAsync(id);
      toast.success("Loja desconectada", {
        description: "As credenciais foram removidas do app.",
      });
    } catch (err) {
      toast.error("Falha ao desconectar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleArchive = async () => {
    setConfirmArchive(false);
    try {
      await archive.mutateAsync(id);
      toast.success("Loja arquivada");
      navigate("/");
    } catch (err) {
      toast.error("Falha ao arquivar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={() => navigate("/")}>
          <ArrowLeft className="h-4 w-4" />
          Trocar de loja
        </Button>
        <ProfileStatusBadge status={profile.status} />
      </div>

      <div>
        <h2 className="text-2xl font-semibold tracking-tight">{profile.name}</h2>
        {profile.ml_nickname && (
          <p className="text-sm text-muted-foreground">
            Conectada como <span className="font-mono">@{profile.ml_nickname}</span> (ID{" "}
            {profile.ml_user_id})
          </p>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              {isConnected ? (
                <CheckCircle2 className="h-4 w-4 text-success" />
              ) : (
                <AlertCircle className="h-4 w-4 text-warning" />
              )}
              Conexão ML
            </CardTitle>
            <CardDescription>Status da integração com Mercado Livre</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Status</span>
                <span className="font-medium">{profile.status}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">User ID</span>
                <span className="font-mono">{profile.ml_user_id ?? "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Nickname</span>
                <span className="font-mono">{profile.ml_nickname ?? "—"}</span>
              </div>
            </div>

            {isConnected && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleTest}
                disabled={testConnection.isPending}
                className="w-full"
              >
                {testConnection.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Wifi className="h-4 w-4" />
                )}
                Testar conexão
              </Button>
            )}
          </CardContent>
        </Card>

        <ConfigEditorCard profile={profile} />
        <RelatorioDiarioCard profile={profile} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Operações</CardTitle>
          <CardDescription>
            Relatórios e análises da loja. Novas operações chegam nas próximas levas.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-start gap-2">
            <Button
              variant="default"
              size="sm"
              onClick={() => navigate(`/profiles/${id}/listings`)}
              disabled={!isConnected || !profile.config.custos_xlsx_path}
            >
              <TrendingUp className="h-4 w-4" />
              Gerar relatório de margens
            </Button>
            <RoleGate profileId={id} minimum="operator">
              <Button
                variant="default"
                size="sm"
                onClick={() => navigate(`/profiles/${id}/simulations`)}
                disabled={!isConnected || !profile.config.custos_xlsx_path}
              >
                <Sparkles className="h-4 w-4" />
                Simular reprecificação
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={() => navigate(`/profiles/${id}/campaigns`)}
                disabled={!isConnected}
              >
                <Calendar className="h-4 w-4" />
                Campanhas
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={() => navigate(`/profiles/${id}/reprecificar-tudo`)}
                disabled={!isConnected || !profile.config.custos_xlsx_path}
                title="Pra lojas sem campanha — recalcula o preço-base de N anúncios pra atingir a margem alvo do perfil e aplica direto via PUT no ML"
              >
                <Tag className="h-4 w-4" />
                Reprecificar tudo
              </Button>
            </RoleGate>
          </div>
          {!profile.config.custos_xlsx_path && isConnected && (
            <p className="text-xs text-muted-foreground">
              Configure a planilha de custos (no card "Configuração") pra habilitar os relatórios.
            </p>
          )}
        </CardContent>
      </Card>

      <RoleGate profileId={id} minimum="admin">
        <Card className="border-destructive/40">
          <CardHeader>
            <CardTitle className="text-base">Ações sensíveis</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {isConnected && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmDisconnect(true)}
                disabled={disconnect.isPending}
              >
                <LogOut className="h-4 w-4" />
                Desconectar
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmArchive(true)}
              disabled={archive.isPending}
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            >
              <Archive className="h-4 w-4" />
              Arquivar loja
            </Button>
          </CardContent>
        </Card>
      </RoleGate>

      <TestConnectionModal
        open={testResult !== null}
        onOpenChange={(open) => !open && setTestResult(null)}
        result={testResult}
      />

      <AlertDialog open={confirmDisconnect} onOpenChange={setConfirmDisconnect}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Desconectar do Mercado Livre?</AlertDialogTitle>
            <AlertDialogDescription>
              Vai remover as credenciais salvas. Você precisará reautorizar depois pra voltar a usar
              essa loja. As configurações da loja são mantidas.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={handleDisconnect}>Desconectar</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmArchive} onOpenChange={setConfirmArchive}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Arquivar esta loja?</AlertDialogTitle>
            <AlertDialogDescription>
              A loja some da lista principal, mas o histórico (simulações, rollbacks) fica
              preservado. Você pode ver lojas arquivadas ativando o filtro no seletor.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleArchive}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Arquivar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
