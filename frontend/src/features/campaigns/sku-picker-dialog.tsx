import { AlertTriangle, CheckSquare, Search, Square, X } from "lucide-react";
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
import { useSkusEligible } from "@/features/campaigns/hooks";
import { cn } from "@/lib/utils";

interface SkuPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profileId: string;
  /** ID da campanha sendo editada — pra ignorar seus próprios SKUs como "ocupados" */
  campaignId: string;
  /** Lista atual de item_ids na campanha */
  selecionadosAtuais: string[];
  /** Chamado com nova lista quando user salva */
  onSave: (skus: string[]) => void | Promise<void>;
  isSaving?: boolean;
}

/**
 * Dialog de seleção de SKUs baseado no CATÁLOGO da loja (Leva 5.9.3).
 *
 * Diferente do `SkuSelectorDialog` que listava items duma simulação, este
 * lista TODOS os anúncios ativos da loja, com flag de "em outra campanha".
 *
 * - Cache do fee_report alimenta título/SKU/preço
 * - Se cache vazio, mostra aviso pra abrir o relatório de margens primeiro
 * - Items já na campanha atual aparecem como pre-selecionados
 * - Items em OUTRA campanha aparecem com badge e checkbox desabilitado
 *   (não dá pra adicionar sem remover da outra primeiro)
 */
export function SkuPickerDialog({
  open,
  onOpenChange,
  profileId,
  campaignId,
  selecionadosAtuais,
  onSave,
  isSaving = false,
}: SkuPickerDialogProps) {
  const { data: items, isPending } = useSkusEligible(open ? profileId : undefined);
  const [filtro, setFiltro] = useState("");
  const [selecionados, setSelecionados] = useState<Set<string>>(() => new Set(selecionadosAtuais));
  const [hasInitialized, setHasInitialized] = useState(false);

  // Inicializa o set quando o dialog abre — usa estado externo
  if (open && !hasInitialized) {
    setSelecionados(new Set(selecionadosAtuais));
    setHasInitialized(true);
  }
  if (!open && hasInitialized) {
    setHasInitialized(false);
    setFiltro("");
  }

  // Items já na campanha atual NÃO contam como "em outra campanha"
  // mesmo se a flag vier true (campanha sendo editada pode estar
  // listando seus próprios SKUs como ocupados). Frontend filtra
  // localmente usando o snapshot de selecionadosAtuais.
  const ocupados = useMemo(() => {
    if (!items) return new Set<string>();
    const current = new Set(selecionadosAtuais);
    return new Set(
      items
        .filter((it) => it.em_outra_campanha && !current.has(it.item_id))
        .map((it) => it.item_id),
    );
  }, [items, selecionadosAtuais]);

  const filtrados = useMemo(() => {
    if (!items) return [];
    if (!filtro.trim()) return items;
    const q = filtro.toLowerCase().trim();
    return items.filter(
      (s) =>
        s.sku?.toLowerCase().includes(q) ||
        s.titulo?.toLowerCase().includes(q) ||
        s.item_id.toLowerCase().includes(q),
    );
  }, [items, filtro]);

  const totalSelecionados = selecionados.size;
  const totalFiltrados = filtrados.length;
  const visiveisSelecionados = filtrados.filter((s) => selecionados.has(s.item_id)).length;
  const todosVisiveisSelecionados = totalFiltrados > 0 && visiveisSelecionados === totalFiltrados;

  const toggleItem = (itemId: string) => {
    if (ocupados.has(itemId)) return; // não dá pra mexer em items ocupados
    setSelecionados((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  const marcarVisiveis = () => {
    setSelecionados((prev) => {
      const next = new Set(prev);
      for (const s of filtrados) {
        if (!ocupados.has(s.item_id)) next.add(s.item_id);
      }
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

  const handleSave = () => {
    onSave(Array.from(selecionados));
  };

  // Cache miss — não conseguimos listar
  const cacheVazio = !isPending && (items?.length ?? 0) === 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-4xl flex-col">
        <DialogHeader>
          <DialogTitle>Selecionar anúncios pra campanha</DialogTitle>
          <DialogDescription>
            Lista todos os anúncios ativos da loja. Items participando de outra campanha aparecem
            desabilitados — remova de lá primeiro pra adicionar aqui.
          </DialogDescription>
        </DialogHeader>

        {/* Indicador de campanha em edição — debug aux */}
        <input type="hidden" value={campaignId} />

        {cacheVazio && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div>
                <p className="font-medium text-amber-700 dark:text-amber-400">
                  Cache do relatório de margens está vazio
                </p>
                <p className="mt-1 text-muted-foreground">
                  Abra a tela "Relatório de margens" pelo menos uma vez pra carregar os dados dos
                  anúncios. Sem isso, não temos como listar o catálogo pra seleção.
                </p>
              </div>
            </div>
          </div>
        )}

        {isPending && (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            Carregando catálogo...
          </div>
        )}

        {!isPending && !cacheVazio && (
          <>
            <div className="space-y-2">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={filtro}
                  onChange={(e) => setFiltro(e.target.value)}
                  placeholder="Buscar por SKU, título ou MLB..."
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
                  </Button>
                </div>
                <span className="text-muted-foreground">
                  <span className="font-medium tabular-nums text-foreground">
                    {totalSelecionados}
                  </span>{" "}
                  selecionado{totalSelecionados !== 1 && "s"}
                </span>
              </div>
            </div>

            <div className="flex-1 overflow-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="w-10 px-2 py-2" />
                    <th className="px-2 py-2">SKU / Título</th>
                    <th className="w-24 px-2 py-2 text-right">Preço</th>
                    <th className="w-32 px-2 py-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {filtrados.length === 0 && (
                    <tr>
                      <td
                        colSpan={4}
                        className="px-4 py-8 text-center text-xs text-muted-foreground"
                      >
                        Nenhum item bate com "{filtro}".
                      </td>
                    </tr>
                  )}
                  {filtrados.map((it) => {
                    const checked = selecionados.has(it.item_id);
                    const ocupado = ocupados.has(it.item_id);
                    return (
                      <tr
                        key={it.item_id}
                        className={cn(
                          "border-b transition-colors",
                          ocupado
                            ? "cursor-not-allowed opacity-50"
                            : "cursor-pointer hover:bg-muted/30",
                          checked && !ocupado && "bg-primary/5",
                        )}
                        onClick={() => !ocupado && toggleItem(it.item_id)}
                      >
                        <td className="px-2 py-2">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={ocupado}
                            onChange={() => toggleItem(it.item_id)}
                            onClick={(e) => e.stopPropagation()}
                            className="h-4 w-4 rounded border-input accent-primary disabled:cursor-not-allowed"
                          />
                        </td>
                        <td className="px-2 py-2">
                          <div className="font-mono text-xs tabular-nums">
                            {it.sku ?? <span className="opacity-50">sem SKU</span>}
                          </div>
                          <div className="truncate text-xs text-muted-foreground">
                            {it.titulo ?? <span className="font-mono">{it.item_id}</span>}
                          </div>
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          {it.preco !== null ? `R$ ${it.preco.toFixed(2)}` : "—"}
                        </td>
                        <td className="px-2 py-2">
                          {ocupado ? (
                            <span
                              className="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400"
                              title={`Em: ${it.nomes_outras_campanhas.join(", ")}`}
                            >
                              Em campanha
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">Disponível</span>
                          )}
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
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSaving}>
            Cancelar
          </Button>
          <Button onClick={handleSave} disabled={isSaving || cacheVazio}>
            {isSaving ? "Salvando..." : "Salvar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
