import { CheckCircle2, ExternalLink } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { MLConnectionTestResponse } from "@/types/api";

interface TestConnectionModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  result: MLConnectionTestResponse | null;
}

export function TestConnectionModal({ open, onOpenChange, result }: TestConnectionModalProps) {
  if (!result) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-success" />
            Conexão validada
          </DialogTitle>
          <DialogDescription>
            Estes são os dados retornados pelo endpoint <code className="font-mono">/users/me</code>{" "}
            do Mercado Livre.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 rounded-lg border bg-muted/30 p-4 text-sm">
          <Row label="ID" value={String(result.id ?? "—")} mono />
          <Row label="Nickname" value={result.nickname ?? "—"} mono />
          <Row label="E-mail" value={result.email ?? "—"} />
          <Row label="Site" value={result.site_id ?? "—"} />
          <Row label="País" value={result.country_id ?? "—"} />
          <Row label="Tipo" value={result.user_type ?? "—"} />
          <Row
            label="Cadastrado em"
            value={
              result.registration_date
                ? new Date(result.registration_date).toLocaleDateString("pt-BR")
                : "—"
            }
          />
        </div>

        {result.id && (
          <a
            href={`https://www.mercadolivre.com.br/perfil/${result.nickname}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground hover:underline"
          >
            Ver perfil no Mercado Livre <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? "font-mono" : "font-medium"}>{value}</span>
    </div>
  );
}
