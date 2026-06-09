import { Check, Loader2, Pencil, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/toaster";
import { useOverrideItemMargin, useRevertItemMargin } from "@/features/simulations/hooks";
import { cn } from "@/lib/utils";
import type { RepricingLineItem } from "@/types/api";

interface EditableMarginCellProps {
  profileId: string;
  simulationId: string;
  item: RepricingLineItem;
  /**
   * Fallback usado quando o item não tem `margem_campanha_pct` (simulações
   * antigas, pré-Leva 5.2.2). Recebe o valor em % (ex: 20 pra 20%).
   */
  margemCampanhaFallback: number;
}

/**
 * Célula da coluna "Margem líquida" da simulação.
 *
 * Mostra a margem efetiva do item: `margem_override_pct` se aplicado,
 * senão `margem_campanha_pct` (default da simulação). Clicar no lápis
 * permite editar o valor — backend recalcula o `deal_price` via busca
 * binária no ML (~2-5s).
 *
 * Quando há override, mostra ícone ✎ e botão de reverter pro valor
 * calculado originalmente pela simulação.
 *
 * Estados:
 * - Read-only: valor + lápis (hover)
 * - Editing: input numérico + check/x
 * - Pending: spinner inline (operação no ML demora)
 *
 * Robustez pra simulações antigas: se o item não tem `margem_campanha_pct`
 * (campo adicionado em versões mais recentes), usa `margemCampanhaFallback`.
 */
export function EditableMarginCell({
  profileId,
  simulationId,
  item,
  margemCampanhaFallback,
}: EditableMarginCellProps) {
  const [editing, setEditing] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const overrideMutation = useOverrideItemMargin();
  const revertMutation = useRevertItemMargin();

  // Usa `!= null` em vez de `!== null` pra pegar undefined também
  // (simulações antigas não têm o campo, vêm como undefined)
  const hasOverride = item.margem_override_pct != null;

  // Cascata de fallbacks: override → campanha_pct do item → fallback global
  const margemEfetiva =
    item.margem_override_pct ?? item.margem_campanha_pct ?? margemCampanhaFallback;

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const handleStartEdit = () => {
    setInputValue(margemEfetiva.toFixed(1));
    setEditing(true);
  };

  const handleCancel = () => {
    setEditing(false);
    setInputValue("");
  };

  const handleSave = async () => {
    // Aceita "25", "25.5", "25,5"
    const normalized = inputValue.trim().replace(",", ".");
    const valuePct = Number(normalized);

    if (!Number.isFinite(valuePct) || valuePct < 1 || valuePct > 80) {
      toast.error("Margem inválida", {
        description: "Informe um número entre 1 e 80 (em %)",
      });
      return;
    }

    try {
      await overrideMutation.mutateAsync({
        profileId,
        simulationId,
        itemId: item.item_id,
        margemAlvo: valuePct / 100, // converte pra decimal
      });
      toast.success("Margem ajustada", {
        description: `${item.sku ?? item.item_id}: ${valuePct.toFixed(1)}% líquido`,
      });
      setEditing(false);
    } catch (err) {
      toast.error("Falha ao recalcular", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleRevert = async () => {
    try {
      await revertMutation.mutateAsync({
        profileId,
        simulationId,
        itemId: item.item_id,
      });
      toast.success("Override revertido", {
        description: `${item.sku ?? item.item_id} voltou pro valor calculado`,
      });
    } catch (err) {
      toast.error("Falha ao reverter", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      handleCancel();
    }
  };

  // === Modo de edição ===
  if (editing) {
    const isPending = overrideMutation.isPending;
    return (
      <div className="flex items-center justify-end gap-1">
        <Input
          ref={inputRef}
          type="text"
          inputMode="decimal"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="20"
          className="h-7 w-16 text-right font-mono text-xs tabular-nums"
          disabled={isPending}
        />
        <span className="text-xs text-muted-foreground">%</span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-success hover:bg-success/10 hover:text-success"
          onClick={handleSave}
          disabled={isPending}
          title="Recalcular (Enter)"
        >
          {isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={handleCancel}
          disabled={isPending}
          title="Cancelar (Esc)"
        >
          <X className="h-3 w-3" />
        </Button>
      </div>
    );
  }

  // === Modo read-only ===
  return (
    <div className="group/margin flex items-center justify-end gap-1">
      <span
        className={cn("font-medium tabular-nums", hasOverride && "text-primary")}
        title={
          hasOverride
            ? `Override aplicado. Original: ${(item.margem_campanha_pct ?? margemCampanhaFallback).toFixed(1)}%`
            : "Margem padrão da simulação"
        }
      >
        {margemEfetiva.toFixed(1)}%{hasOverride && <span className="ml-1 text-xs">✎</span>}
      </span>

      <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover/margin:opacity-100">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-muted-foreground hover:text-foreground"
          onClick={handleStartEdit}
          title="Editar margem"
        >
          <Pencil className="h-3 w-3" />
        </Button>
        {hasOverride && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground hover:text-foreground"
            onClick={handleRevert}
            disabled={revertMutation.isPending}
            title="Reverter pro valor calculado"
          >
            {revertMutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RotateCcw className="h-3 w-3" />
            )}
          </Button>
        )}
      </div>
    </div>
  );
}
