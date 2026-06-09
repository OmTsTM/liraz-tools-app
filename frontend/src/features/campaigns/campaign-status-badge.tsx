import { AlertCircle, CheckCircle2, Clock, FileText, Loader2, Play, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import type { CampaignStatus } from "@/types/api";

interface CampaignStatusBadgeProps {
  status: CampaignStatus;
  className?: string;
}

const CONFIG: Record<CampaignStatus, { label: string; icon: React.ReactNode; className: string }> =
  {
    rascunho: {
      label: "Rascunho",
      icon: <FileText className="h-3 w-3" />,
      className: "bg-muted text-muted-foreground",
    },
    agendada: {
      label: "Agendada",
      icon: <Clock className="h-3 w-3" />,
      className: "bg-primary/10 text-primary",
    },
    executando: {
      label: "Executando",
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
      className: "bg-warning/10 text-warning",
    },
    ativa: {
      label: "Ativa",
      icon: <Play className="h-3 w-3" />,
      className: "bg-success/10 text-success",
    },
    finalizada: {
      label: "Finalizada",
      icon: <CheckCircle2 className="h-3 w-3" />,
      className: "bg-success/10 text-success/80",
    },
    cancelada: {
      label: "Cancelada",
      icon: <XCircle className="h-3 w-3" />,
      className: "bg-muted text-muted-foreground",
    },
    falha: {
      label: "Falha",
      icon: <AlertCircle className="h-3 w-3" />,
      className: "bg-destructive/10 text-destructive",
    },
  };

export function CampaignStatusBadge({ status, className }: CampaignStatusBadgeProps) {
  const c = CONFIG[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        c.className,
        className,
      )}
    >
      {c.icon}
      {c.label}
    </span>
  );
}

/**
 * Versão simplificada — só o "dot" colorido pro calendário, sem texto.
 * Usado dentro de células de calendário onde espaço é limitado.
 */
export function CampaignStatusDot({ status }: { status: CampaignStatus }) {
  const colorMap: Record<CampaignStatus, string> = {
    rascunho: "bg-muted-foreground/40",
    agendada: "bg-primary",
    executando: "bg-warning",
    ativa: "bg-success",
    finalizada: "bg-success/60",
    cancelada: "bg-muted-foreground/30",
    falha: "bg-destructive",
  };
  return <span className={cn("inline-block h-2 w-2 rounded-full", colorMap[status])} />;
}
