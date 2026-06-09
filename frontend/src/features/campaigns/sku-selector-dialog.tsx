import { CheckSquare, Search, Square, X } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useSimulation } from "@/features/simulations/hooks";
import { cn } from "@/lib/utils";
import type { RepricingLineItem } from "@/types/api";

interface SkuSelectorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profileId: string;
  simulacaoId: string;
  /**
   * Lista atual de item_ids selecionados. `null` = todos (default — sem
   * filtro de seleção). Vazio `[]` = nenhum.
   */
  selecionadosAtuais: string[] | null;
  /** Chamado com a nova lista quando o user clica "Salvar". */
  onSave: (selecionados: string[] | null) => void;
}

/**
 * Modal pra escolher quais SKUs entram na campanha.
 *
 * Comportamento:
 * - Só lista items "aplicáveis" (fase1_acao != "mantido") — os outros já
 *   seriam pulados pelo backend de qualquer jeito; não faz sentido oferecer.
 * - Default ao abrir: se `selecionadosAtuais === null`, todos checados.
 *   Senão, só os do array.
 * - Busca por SKU ou título.
 * - "Marcar visíveis" / "Desmarcar visíveis" respeitam o filtro de busca.
 * - "Marcar todos" / "Desmarcar todos" ignoram o filtro.
 * - Salvar: se TODOS estão marcados, passa `null` ao onSave (mantém o sinal
 *   semântico "sem filtro"). Senão, passa o array.
 */
export function SkuSelectorDialog({
  open,
  onOpenChange,
  profileId,
  simulacaoId,
  selecionadosAtuais,
  onSave,
}: SkuSelectorDialogProps) {
  const { data: simulation, isPending } = useSimulation(
    open ? profileId : undefined,
    open ? simulacaoId : undefined,
  );

  // Items aplicáveis = não-mantidos (os outros não vão pro ML).
  const aplicaveis = useMemo<RepricingLineItem[]>(() => {
    if (!simulation) return [];
    return simulation.simulacoes.filter((s) => s.fase1_acao !== "mantido");
  }, [simulation]);

  // Set de selecionados local — começa cheio (todos) se selecionadosAtuais=null
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [filtro, setFiltro] = useState("");

  // Quando o modal abre OU a simulação carrega, inicializa o set baseado no
  // estado externo. Não usa useEffect com deps pra evitar resets indesejados —
  // usa um state-flag que reseta junto com a abertura do modal.
  const [hasInitialized, setHasInitialized] = useState(false);
  if (open && !hasInitialized && aplicaveis.length > 0) {
    const inicial =
      selecionadosAtuais === null
        ? new Set(aplicaveis.map((s) => s.item_id))
        : new Set(selecionadosAtuais);
    setSelecionados(inicial);
    setHasInitialized(true);
  }
  if (!open && hasInitialized) {
    setHasInitialized(false);
    setFiltro("");
  }

  // Filtra a lista pelo input de busca (SKU ou título)
  const filtrados = useMemo(() => {
    if (!filtro.trim()) return aplicaveis;
    const q = filtro.toLowerCase().trim();
    return aplicaveis.filter(
      (s) =>
        (s.sku?.toLowerCase().includes(q) ?? false) ||
        (s.titulo?.toLowerCase().includes(q) ?? false) ||
        s.item_id.toLowerCase().includes(q),
    );
  }, [aplicaveis, filtro]);

  const totalAplicaveis = aplicaveis.length;
  const totalSelecionados = selecionados.size;
  const totalVisiveis = filtrados.length;
  const visiveisSelecionados = filtrados.filter((s) => selecionados.has(s.item_id)).length;

  const todosVisiveisSelecionados = totalVisiveis > 0 && visiveisSelecionados === totalVisiveis;

  const toggleItem = (item_id: string) => {
    setSelecionados((prev) => {
      const next = new Set(prev);
      if (next.has(item_id)) next.delete(item_id);
      else next.add(item_id);
      return next;
    });
  };

  const marcarVisiveis = () => {
    setSelecionados((prev) => {
      const next = new Set(prev);
      for (const s of filtrados) next.add(s.item_id);
      return next;
    });
  };

  const desmarcarVisiveis = () => {
    setSelecionados((prev) => {
      const next = new Set(prev);
      for (const s of filtrados) next.delete(s.item_id);
      return next;
    });
  };

  const marcarTodos = () => {
    setSelecionados(new Set(aplicaveis.map((s) => s.item_id)));
  };

  const desmarcarTodos = () => {
    setSelecionados(new Set());
  };

  const handleSave = () => {
    // Se TODOS estão marcados, persiste como null (semântica "sem filtro").
    // Isso evita um lock futuro caso a simulação ganhe items novos.
    if (selecionados.size === aplicaveis.length) {
      onSave(null);
    } else {
      onSave(Array.from(selecionados));
    }
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col">
        <DialogHeader>
          <DialogTitle>Selecionar anúncios da campanha</DialogTitle>
          <DialogDescription>
            Escolha quais anúncios da simulação vão entrar nesta campanha. Itens cuja simulação não
            mudou de preço (mantido) já são automaticamente excluídos e não aparecem aqui.
          </DialogDescription>
        </DialogHeader>

        {isPending && (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            Carregando simulação...
          </div>
        )}

        {!isPending && totalAplicaveis === 0 && (
          <div className="rounded-md border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
            Nenhum anúncio elegível nesta simulação. Todos os itens foram marcados como "mantido"
            (não há mudança de preço pra aplicar).
          </div>
        )}

        {!isPending && totalAplicaveis > 0 && (
          <>
            {/* Toolbar: busca + ações */}
            <div className="space-y-2">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={filtro}
                  onChange={(e) => setFiltro(e.target.value)}
                  placeholder="Buscar por SKU, título ou item_id..."
                  className="pl-9"
                />
                {filtro && (
                  <button
                    type="button"
                    onClick={() => setFiltro("")}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={todosVisiveisSelecionados ? desmarcarVisiveis : marcarVisiveis}
                    className="h-7 text-xs"
                  >
                    {todosVisiveisSelecionados ? (
                      <Square className="h-3 w-3" />
                    ) : (
                      <CheckSquare className="h-3 w-3" />
                    )}
                    {todosVisiveisSelecionados ? "Desmarcar visíveis" : "Marcar visíveis"}
                    {filtro && <span className="ml-1 opacity-60">({totalVisiveis})</span>}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={marcarTodos}
                    className="h-7 text-xs"
                  >
                    Marcar todos
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={desmarcarTodos}
                    className="h-7 text-xs"
                  >
                    Desmarcar todos
                  </Button>
                </div>
                <span className="text-muted-foreground">
                  <span className="tabular-nums font-medium text-foreground">
                    {totalSelecionados}
                  </span>{" "}
                  de <span className="tabular-nums">{totalAplicaveis}</span> selecionado
                  {totalSelecionados !== 1 && "s"}
                </span>
              </div>
            </div>

            {/* Lista de items — scroll interno */}
            <div className="flex-1 overflow-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="w-10 px-2 py-2" />
                    <th className="px-2 py-2">SKU / Título</th>
                    <th className="w-24 px-2 py-2 text-right">Preço atual</th>
                    <th className="w-24 px-2 py-2 text-right">Preço novo</th>
                    <th className="w-24 px-2 py-2 text-right">Deal</th>
                  </tr>
                </thead>
                <tbody>
                  {filtrados.length === 0 && (
                    <tr>
                      <td
                        colSpan={5}
                        className="px-4 py-8 text-center text-xs text-muted-foreground"
                      >
                        Nenhum item bate com a busca "{filtro}".
                      </td>
                    </tr>
                  )}
                  {filtrados.map((s) => {
                    const checked = selecionados.has(s.item_id);
                    return (
                      <tr
                        key={s.item_id}
                        className={cn(
                          "cursor-pointer border-b transition-colors hover:bg-muted/30",
                          checked && "bg-primary/5",
                        )}
                        onClick={() => toggleItem(s.item_id)}
                      >
                        <td className="px-2 py-2">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleItem(s.item_id)}
                            onClick={(e) => e.stopPropagation()}
                            className="h-4 w-4 rounded border-input accent-primary"
                            aria-label={`Selecionar ${s.sku || s.item_id}`}
                          />
                        </td>
                        <td className="px-2 py-2">
                          <div className="font-medium tabular-nums">
                            {s.sku ?? <span className="opacity-50">sem SKU</span>}
                          </div>
                          <div className="truncate text-xs text-muted-foreground">
                            {s.titulo ?? <span className="font-mono">{s.item_id}</span>}
                          </div>
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          R$ {s.liq_final_atual.toFixed(2)}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          R$ {s.preco_novo.toFixed(2)}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          R$ {s.deal_price.toFixed(2)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            onClick={handleSave}
            disabled={isPending || (totalSelecionados === 0 && totalAplicaveis > 0)}
            title={
              totalSelecionados === 0 && totalAplicaveis > 0
                ? "Selecione pelo menos um anúncio"
                : undefined
            }
          >
            Salvar seleção
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
