import { Check, Loader2, Pencil, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/toaster";
import { useRemoveCostOverride, useSetCostOverride } from "@/features/pricing/hooks";
import { cn } from "@/lib/utils";
import type { ListingFees } from "@/types/api";

const BRL = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});

interface EditableCostCellProps {
  profileId: string;
  listing: ListingFees;
}

/**
 * Célula da coluna "Custo" — read-only por default, vira input ao clicar
 * no lápis. Persiste override via backend e atualiza in-place.
 *
 * Casos:
 * - Linha sem erro + com custo do XLSX: mostra valor + lápis pra editar
 *   (e botão de reverter se já tem override manual)
 * - Linha sem erro + sem custo (não bateu no XLSX): mostra "(sem custo)" +
 *   lápis pra adicionar override
 * - Linha com erro (sem preço, etc): mostra "—" sem ação (override de custo
 *   não ajuda se a linha não tem preço pra calcular margem)
 */
export function EditableCostCell({ profileId, listing }: EditableCostCellProps) {
  const [editing, setEditing] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const setMutation = useSetCostOverride();
  const removeMutation = useRemoveCostOverride();

  const isOverride = listing.custo_fonte === "manual";
  const isFallback =
    listing.custo_fonte === "case_insensitive" ||
    listing.custo_fonte === "prefixo" ||
    listing.custo_fonte === "prefixo_divergente";
  const hasCusto = listing.custo_produto != null;
  const hasErro = Boolean(listing.erro);

  // Não permite editar custo de linhas com erro do ML (sem preço, sem
  // categoria, etc) — o override de custo sozinho não resolveria
  // porque preço também é necessário pro cálculo.
  const canEdit = !hasErro;

  // A chave usada no backend é o SKU se existir, senão o MLB. O backend
  // resolve ambos via mesma cascata.
  const overrideKey = listing.sku ?? listing.item_id;

  // Quando entra em edit mode, foca no input
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const handleStartEdit = () => {
    setInputValue(listing.custo_produto != null ? listing.custo_produto.toFixed(2) : "");
    setEditing(true);
  };

  const handleCancel = () => {
    setEditing(false);
    setInputValue("");
  };

  const handleSave = async () => {
    // Aceita "12,50" ou "12.50"
    const normalized = inputValue.trim().replace(",", ".");
    const value = Number(normalized);

    if (!Number.isFinite(value) || value < 0) {
      toast.error("Valor inválido", {
        description: "Informe um número >= 0 (ex: 12.50)",
      });
      return;
    }

    try {
      await setMutation.mutateAsync({
        profileId,
        key: overrideKey,
        value,
      });
      toast.success("Custo atualizado", {
        description: `${overrideKey}: ${BRL.format(value)}`,
      });
      setEditing(false);
    } catch (err) {
      toast.error("Falha ao salvar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleRevert = async () => {
    try {
      await removeMutation.mutateAsync({
        profileId,
        key: overrideKey,
      });
      toast.success("Override removido", {
        description: `${overrideKey} volta a usar o custo da planilha.`,
      });
    } catch (err) {
      toast.error("Falha ao remover override", {
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
    const isPending = setMutation.isPending;
    return (
      <div className="flex items-center justify-end gap-1">
        <Input
          ref={inputRef}
          type="text"
          inputMode="decimal"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="0,00"
          className="h-7 w-24 text-right font-mono text-xs tabular-nums"
          disabled={isPending}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-success hover:bg-success/10 hover:text-success"
          onClick={handleSave}
          disabled={isPending}
          title="Salvar"
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
  if (hasErro) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  return (
    <div className="group/cost flex items-center justify-end gap-1">
      {hasCusto ? (
        <span
          className={cn(
            "tabular-nums",
            isOverride && "font-medium text-foreground",
            isFallback && "text-warning",
          )}
          title={
            isOverride ? "Override manual" : (listing.custo_fonte_detalhe ?? listing.custo_fonte)
          }
        >
          {BRL.format(listing.custo_produto ?? 0)}
          {isOverride && <span className="ml-1 text-xs">✎</span>}
          {isFallback && <span className="ml-1 text-xs">⚠</span>}
        </span>
      ) : (
        <span className="text-xs italic text-warning">sem custo</span>
      )}

      {canEdit && (
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover/cost:opacity-100">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground hover:text-foreground"
            onClick={handleStartEdit}
            title="Editar custo"
          >
            <Pencil className="h-3 w-3" />
          </Button>
          {isOverride && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6 text-muted-foreground hover:text-foreground"
              onClick={handleRevert}
              disabled={removeMutation.isPending}
              title="Reverter pro custo do XLSX"
            >
              {removeMutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RotateCcw className="h-3 w-3" />
              )}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
