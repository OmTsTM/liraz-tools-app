import { ArchiveRestore, ArrowLeft, Inbox, Store } from "lucide-react";
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
import { useProfiles, useUnarchiveProfile } from "@/features/profiles/hooks";
import type { Profile } from "@/types/api";

export function ArchivedStoresPage() {
  const navigate = useNavigate();
  const { data, isLoading } = useProfiles(true);
  const unarchive = useUnarchiveProfile();

  const [profileToUnarchive, setProfileToUnarchive] = useState<Profile | null>(null);

  // Filtra só arquivadas
  const archived = data?.items.filter((p) => p.status === "archived") ?? [];

  const handleConfirm = async () => {
    if (!profileToUnarchive) return;
    const name = profileToUnarchive.name;
    setProfileToUnarchive(null);
    try {
      const result = await unarchive.mutateAsync(profileToUnarchive.id);
      const isConnected = result.status === "connected";
      toast.success("Loja desarquivada", {
        description: isConnected
          ? `"${name}" está conectada e pronta pra uso.`
          : `"${name}" foi restaurada como rascunho — você precisará reconfigurar.`,
      });
    } catch (err) {
      toast.error("Falha ao desarquivar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate("/")}>
        <ArrowLeft className="h-4 w-4" />
        Voltar
      </Button>

      <div className="space-y-1">
        <h2 className="text-2xl font-semibold tracking-tight">Lojas arquivadas</h2>
        <p className="text-sm text-muted-foreground">
          Lojas arquivadas ficam ocultas da lista principal. Você pode desarquivar a qualquer
          momento — se as credenciais ainda estiverem salvas, ela volta conectada automaticamente.
        </p>
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[1, 2].map((i) => (
            <Card key={i} className="flex items-center gap-4 p-4">
              <Skeleton className="h-12 w-12 rounded-lg" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-5 w-1/3" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            </Card>
          ))}
        </div>
      )}

      {!isLoading && archived.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-12 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
            <Inbox className="h-6 w-6 text-muted-foreground" />
          </div>
          <div className="space-y-1">
            <h3 className="text-base font-semibold">Nenhuma loja arquivada</h3>
            <p className="text-sm text-muted-foreground">
              Quando você arquivar lojas, elas aparecem aqui.
            </p>
          </div>
          <Button variant="outline" onClick={() => navigate("/")}>
            Voltar pro seletor
          </Button>
        </Card>
      )}

      {archived.length > 0 && (
        <div className="space-y-3">
          {archived.map((profile) => (
            <ArchivedCard
              key={profile.id}
              profile={profile}
              onUnarchive={() => setProfileToUnarchive(profile)}
              isLoading={unarchive.isPending && unarchive.variables === profile.id}
            />
          ))}
        </div>
      )}

      <AlertDialog
        open={profileToUnarchive !== null}
        onOpenChange={(open) => !open && setProfileToUnarchive(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Desarquivar "{profileToUnarchive?.name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              {profileToUnarchive?.ml_user_id
                ? "A loja vai voltar conectada — credenciais ainda estão salvas."
                : "A loja vai voltar como rascunho — você precisará reconfigurar a conexão."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirm}>Desarquivar</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

interface ArchivedCardProps {
  profile: Profile;
  onUnarchive: () => void;
  isLoading: boolean;
}

function ArchivedCard({ profile, onUnarchive, isLoading }: ArchivedCardProps) {
  const archivedDate = profile.archived_at
    ? new Date(profile.archived_at).toLocaleDateString("pt-BR")
    : null;

  return (
    <Card className="group flex items-center gap-4 p-4 opacity-75 transition-all hover:opacity-100">
      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-muted">
        <Store className="h-6 w-6 text-muted-foreground" />
      </div>

      <div className="min-w-0 flex-1">
        <h3 className="truncate text-base font-semibold">{profile.name}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {profile.ml_nickname ? `@${profile.ml_nickname}` : "Nunca foi conectada"}
          {archivedDate && (
            <>
              {" · "}
              <span>Arquivada em {archivedDate}</span>
            </>
          )}
        </p>
      </div>

      <Button
        variant="outline"
        size="sm"
        onClick={onUnarchive}
        disabled={isLoading}
        className="opacity-0 transition-opacity group-hover:opacity-100"
      >
        <ArchiveRestore className="h-4 w-4" />
        Desarquivar
      </Button>
    </Card>
  );
}
