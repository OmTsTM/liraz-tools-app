import { ArrowLeft, Key, Package } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type ConnectionMethod = "oauth" | "mcp_import";

interface WizardStepMethodProps {
  onBack: () => void;
  onSelect: (method: ConnectionMethod) => void;
}

interface MethodCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  recommended?: boolean;
  onClick: () => void;
}

function MethodCard({ icon, title, description, recommended, onClick }: MethodCardProps) {
  return (
    <Card
      onClick={onClick}
      className={cn(
        "group cursor-pointer p-6 transition-all hover:border-foreground/40 hover:shadow-md",
      )}
    >
      <CardContent className="flex h-full flex-col gap-4 p-0">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-muted-foreground group-hover:bg-foreground/10 group-hover:text-foreground transition-colors">
          {icon}
        </div>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold">{title}</h3>
            {recommended && (
              <span className="rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
                Recomendado
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export function WizardStepMethod({ onBack, onSelect }: WizardStepMethodProps) {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2">
        <MethodCard
          icon={<Key className="h-5 w-5" />}
          title="Conectar via Mercado Livre"
          description="Autorize uma aplicação ML no navegador. Padrão pra novas lojas."
          recommended
          onClick={() => onSelect("oauth")}
        />
        <MethodCard
          icon={<Package className="h-5 w-5" />}
          title="Importar de MCP existente"
          description="Reusa credenciais de um projeto MCP já autorizado. Sem nova autorização no ML."
          onClick={() => onSelect("mcp_import")}
        />
      </div>

      <div className="flex justify-between border-t pt-4">
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>
      </div>
    </div>
  );
}
