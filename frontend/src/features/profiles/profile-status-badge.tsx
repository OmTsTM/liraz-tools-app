import { Badge } from "@/components/ui/badge";
import type { ProfileStatus } from "@/types/api";

const STATUS_CONFIG: Record<
  ProfileStatus,
  { label: string; variant: "default" | "secondary" | "success" | "warning" | "outline" }
> = {
  draft: { label: "Não conectada", variant: "outline" },
  connected: { label: "Conectada", variant: "success" },
  disconnected: { label: "Desconectada", variant: "warning" },
  archived: { label: "Arquivada", variant: "secondary" },
};

export function ProfileStatusBadge({ status }: { status: ProfileStatus }) {
  const config = STATUS_CONFIG[status];
  return <Badge variant={config.variant}>{config.label}</Badge>;
}
