import { ChevronRight, Store, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ProfileStatusBadge } from "@/features/profiles/profile-status-badge";
import { cn } from "@/lib/utils";
import type { Profile } from "@/types/api";

interface ProfileCardProps {
  profile: Profile;
  onOpen: (profile: Profile) => void;
  onDelete?: (profile: Profile) => void;
}

export function ProfileCard({ profile, onOpen, onDelete }: ProfileCardProps) {
  const isReady = profile.status === "connected";
  const canDelete = profile.status === "draft" && onDelete !== undefined;

  // Card todo clicável. Botão "Abrir/Configurar" segue funcionando como
  // call-to-action visual. Apertar Enter ou Space no card também abre.
  const handleCardClick = () => {
    onOpen(profile);
  };

  const handleCardKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(profile);
    }
  };

  return (
    // biome-ignore lint/a11y/useSemanticElements: usar <button> quebraria o layout flex do Card
    <Card
      role="button"
      tabIndex={0}
      onClick={handleCardClick}
      onKeyDown={handleCardKey}
      className={cn(
        "group flex cursor-pointer items-center gap-4 p-4 transition-all",
        "hover:border-foreground/20 hover:shadow-md",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        !isReady && "opacity-80",
      )}
    >
      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-muted">
        <Store className="h-6 w-6 text-muted-foreground" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-base font-semibold">{profile.name}</h3>
          <ProfileStatusBadge status={profile.status} />
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {profile.ml_nickname ? `@${profile.ml_nickname}` : "Pendente de conexão"}
        </p>
      </div>

      <div className="flex items-center gap-2 opacity-0 transition-opacity group-hover:opacity-100">
        {canDelete && (
          <Button
            variant="ghost"
            size="icon"
            onClick={(e) => {
              e.stopPropagation();
              onDelete?.(profile);
            }}
            className="h-9 w-9 text-muted-foreground hover:text-destructive"
            title="Excluir loja não configurada"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        )}
        <Button
          variant={isReady ? "default" : "outline"}
          size="sm"
          onClick={(e) => {
            // Não propaga pro card pra evitar duplo handler
            e.stopPropagation();
            onOpen(profile);
          }}
        >
          {isReady ? "Abrir" : "Configurar"}
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </Card>
  );
}
