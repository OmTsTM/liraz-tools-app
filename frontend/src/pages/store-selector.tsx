import { Archive, Plus, ServerCrash, Store } from "lucide-react";
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
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/auth-context";
import { useActivateProfile, useDeleteProfile, useProfiles } from "@/features/profiles/hooks";
import { ProfileCard } from "@/features/profiles/profile-card";
import type { Profile } from "@/types/api";

export function StoreSelectorPage() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const { data, isLoading, isError, error, refetch } = useProfiles();
  // Lista total (incluindo arquivadas) só pra saber se mostra o link.
  // Non-admins não enxergam arquivadas via API (backend filtra), então o
  // count fica zerado pra eles — botão some.
  const { data: dataWithArchived } = useProfiles(true);
  const activate = useActivateProfile();
  const deleteProfile = useDeleteProfile();

  const [profileToDelete, setProfileToDelete] = useState<Profile | null>(null);

  const archivedCount = dataWithArchived?.items.filter((p) => p.status === "archived").length ?? 0;

  const handleOpen = (profile: Profile) => {
    if (profile.status !== "connected") {
      navigate(`/profiles/${profile.id}/setup`);
      return;
    }
    // Navega ANTES de ativar (fire-and-forget). Motivo: se há outras
    // requests HTTP em andamento (ex: relatório de margens carregando
    // em background), o navegador pode estar com as 6 conexões HTTP/1.1
    // ocupadas. Esperar /activate aqui trava a navegação.
    //
    // O endpoint /activate é leve (uma writes simples no SQLite), e mesmo
    // se demorar, o `useActiveProfile()` na próxima tela já busca a info
    // que ele atualiza. Erros são silenciosos (já estamos navegando).
    activate.mutate(profile.id, {
      onError: (err) => {
        toast.error("Falha ao ativar loja", {
          description: err instanceof Error ? err.message : "Erro desconhecido",
        });
      },
    });
    navigate(`/profiles/${profile.id}`);
  };

  const handleConfirmDelete = async () => {
    if (!profileToDelete) return;
    const name = profileToDelete.name;
    setProfileToDelete(null);
    try {
      await deleteProfile.mutateAsync(profileToDelete.id);
      toast.success("Loja excluída", { description: `"${name}" foi removida.` });
    } catch (err) {
      toast.error("Falha ao excluir loja", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight">Bem-vindo</h2>
        <p className="text-muted-foreground">Selecione a loja com a qual deseja trabalhar hoje.</p>
      </div>

      {isLoading && <LoadingSkeleton />}

      {isError && (
        <ErrorState
          message={error instanceof Error ? error.message : "Erro desconhecido"}
          onRetry={() => refetch()}
        />
      )}

      {data && data.items.length === 0 && (
        <EmptyState
          isAdmin={isAdmin}
          onCreate={() => navigate("/profiles/new")}
          archivedCount={archivedCount}
          onViewArchived={() => navigate("/profiles/archived")}
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="space-y-3">
            {data.items.map((profile) => (
              <ProfileCard
                key={profile.id}
                profile={profile}
                onOpen={handleOpen}
                onDelete={(p) => setProfileToDelete(p)}
              />
            ))}
          </div>

          {/* Admin global gerencia o cadastro de lojas. Operator/Viewer não
              criam nem arquivam — só usam as lojas liberadas. */}
          {isAdmin && (
            <div className="flex flex-wrap items-center gap-3 border-t pt-6">
              <Button variant="outline" onClick={() => navigate("/profiles/new")}>
                <Plus className="h-4 w-4" />
                Adicionar nova loja
              </Button>

              {archivedCount > 0 && (
                <Button
                  variant="ghost"
                  onClick={() => navigate("/profiles/archived")}
                  className="text-muted-foreground"
                >
                  <Archive className="h-4 w-4" />
                  Lojas arquivadas ({archivedCount})
                </Button>
              )}
            </div>
          )}
        </>
      )}

      <AlertDialog
        open={profileToDelete !== null}
        onOpenChange={(open) => !open && setProfileToDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir "{profileToDelete?.name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              Esta loja nunca foi conectada ao Mercado Livre, então pode ser apagada sem perdas. A
              ação é permanente.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
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

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((i) => (
        <Card key={i} className="flex items-center gap-4 p-4">
          <Skeleton className="h-12 w-12 rounded-lg" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-5 w-1/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        </Card>
      ))}
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="flex flex-col items-center gap-4 p-12 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
        <ServerCrash className="h-6 w-6 text-destructive" />
      </div>
      <div className="space-y-1">
        <h3 className="text-base font-semibold">Não foi possível carregar</h3>
        <p className="text-sm text-muted-foreground">{message}</p>
        <p className="text-xs text-muted-foreground">
          Verifique se o backend está rodando em localhost:8000.
        </p>
      </div>
      <Button variant="outline" onClick={onRetry}>
        Tentar de novo
      </Button>
    </Card>
  );
}

function EmptyState({
  isAdmin,
  onCreate,
  archivedCount,
  onViewArchived,
}: {
  isAdmin: boolean;
  onCreate: () => void;
  archivedCount: number;
  onViewArchived: () => void;
}) {
  const hasArchived = archivedCount > 0;

  return (
    <Card className="flex flex-col items-center gap-4 p-12 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
        <Store className="h-6 w-6 text-muted-foreground" />
      </div>
      <div className="space-y-1">
        <h3 className="text-base font-semibold">
          {hasArchived ? "Nenhuma loja ativa" : "Nenhuma loja conectada"}
        </h3>
        <p className="max-w-md text-sm text-muted-foreground">
          {!isAdmin
            ? "Você ainda não tem acesso a nenhuma loja. Peça ao admin da empresa pra liberar."
            : hasArchived
              ? `Você tem ${archivedCount} loja(s) arquivada(s). Desarquive uma delas ou adicione uma nova loja.`
              : "Comece adicionando uma loja do Mercado Livre. Você pode conectar via OAuth normal ou importar credenciais de um projeto MCP existente."}
        </p>
      </div>
      {isAdmin && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button onClick={onCreate}>
            <Plus className="h-4 w-4" />
            {hasArchived ? "Adicionar nova loja" : "Adicionar primeira loja"}
          </Button>
          {hasArchived && (
            <Button variant="outline" onClick={onViewArchived}>
              <Archive className="h-4 w-4" />
              Ver lojas arquivadas ({archivedCount})
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}
