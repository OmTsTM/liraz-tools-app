import { Check, FileText, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useSimulations } from "@/features/simulations/hooks";
import { cn } from "@/lib/utils";
import type { SimulationSummary } from "@/types/api";

interface SimulationPickerProps {
  profileId: string;
  /** ID da simulação selecionada (null se nenhuma). */
  value: string | null;
  onChange: (simulationId: string | null) => void;
  disabled?: boolean;
}

/**
 * Substitui o `<select>` HTML por um dialog com cards visuais de cada
 * simulação. Mostra ID, data de criação, totais de simulados/exceções
 * e margens configuradas pra cada simulação.
 *
 * Só lista simulações com `estado=completed` (outras não fazem sentido
 * como base de campanha).
 */
export function SimulationPicker({ profileId, value, onChange, disabled }: SimulationPickerProps) {
  const [open, setOpen] = useState(false);
  const { data: simulations, isPending } = useSimulations(profileId);

  const completedSims = simulations?.filter((s) => s.estado === "completed") ?? [];
  const selected = completedSims.find((s) => s.simulation_id === value) ?? null;

  const handleSelect = (id: string) => {
    onChange(id);
    setOpen(false);
  };

  const handleClear = () => {
    onChange(null);
  };

  return (
    <>
      {/* Display do valor selecionado */}
      {selected ? (
        <div className="space-y-2">
          <SimulationCard simulation={selected} selected highlighted={false} />
          <div className="flex gap-2">
            <Dialog open={open} onOpenChange={setOpen}>
              <DialogTrigger asChild>
                <Button type="button" variant="outline" size="sm" disabled={disabled}>
                  Trocar simulação
                </Button>
              </DialogTrigger>
              <SimulationDialogContent
                simulations={completedSims}
                isPending={isPending}
                currentValue={value}
                onSelect={handleSelect}
              />
            </Dialog>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleClear}
              disabled={disabled}
              className="text-muted-foreground hover:text-destructive"
            >
              <X className="h-4 w-4" />
              Remover seleção
            </Button>
          </div>
        </div>
      ) : (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button
              type="button"
              variant="outline"
              className="w-full justify-start text-muted-foreground"
              disabled={disabled}
            >
              <FileText className="mr-2 h-4 w-4" />
              Escolher simulação...
            </Button>
          </DialogTrigger>
          <SimulationDialogContent
            simulations={completedSims}
            isPending={isPending}
            currentValue={value}
            onSelect={handleSelect}
          />
        </Dialog>
      )}
    </>
  );
}

function SimulationDialogContent({
  simulations,
  isPending,
  currentValue,
  onSelect,
}: {
  simulations: SimulationSummary[];
  isPending: boolean;
  currentValue: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <DialogContent className="max-h-[80vh] max-w-3xl overflow-hidden">
      <DialogHeader>
        <DialogTitle>Escolher simulação de reprecificação</DialogTitle>
        <DialogDescription>
          A simulação define os preços novos e o deal_price (preço na campanha) aplicados aos
          anúncios. Só simulações <strong>concluídas</strong> aparecem aqui.
        </DialogDescription>
      </DialogHeader>

      <div className="-mx-6 max-h-[60vh] space-y-2 overflow-y-auto px-6 py-1">
        {isPending && (
          <>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </>
        )}
        {!isPending && simulations.length === 0 && (
          <div className="rounded-md border border-dashed bg-muted/20 p-8 text-center text-sm text-muted-foreground">
            Nenhuma simulação concluída ainda. Crie uma na tela de simulações primeiro.
          </div>
        )}
        {!isPending &&
          simulations.map((s) => (
            <SimulationCard
              key={s.simulation_id}
              simulation={s}
              highlighted={s.simulation_id === currentValue}
              onClick={() => onSelect(s.simulation_id)}
            />
          ))}
      </div>
    </DialogContent>
  );
}

function SimulationCard({
  simulation,
  selected = false,
  highlighted,
  onClick,
}: {
  simulation: SimulationSummary;
  /** Modo "exibição do valor escolhido" (não interativo). */
  selected?: boolean;
  /** Destacar visualmente como "atualmente em uso". */
  highlighted: boolean;
  onClick?: () => void;
}) {
  const dt = new Date(simulation.criado_em);
  const Wrapper = onClick ? "button" : "div";

  return (
    <Wrapper
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "w-full rounded-md border p-3 text-left transition-colors",
        onClick && "hover:bg-accent",
        highlighted && "border-primary bg-primary/5",
        selected && "border-primary/40 bg-primary/5",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="font-mono text-xs">{simulation.simulation_id}</p>
            {highlighted && (
              <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium text-primary">
                <Check className="h-3 w-3" />
                Selecionada
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Criada em {dt.toLocaleString("pt-BR")}
          </p>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <div className="text-right">
            <p className="font-semibold tabular-nums text-success">{simulation.total_simulados}</p>
            <p className="text-[10px] text-muted-foreground">simulados</p>
          </div>
          <div className="text-right">
            <p
              className={cn(
                "font-semibold tabular-nums",
                simulation.total_excecoes > 0 ? "text-warning" : "text-muted-foreground",
              )}
            >
              {simulation.total_excecoes}
            </p>
            <p className="text-[10px] text-muted-foreground">exceções</p>
          </div>
          <div className="text-right">
            <p className="font-semibold tabular-nums">{simulation.total_ativos_analisados}</p>
            <p className="text-[10px] text-muted-foreground">total</p>
          </div>
        </div>
      </div>
    </Wrapper>
  );
}
