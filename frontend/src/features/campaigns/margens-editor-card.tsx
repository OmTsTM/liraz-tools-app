import {
  AlertTriangle,
  CheckSquare,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
  Square,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import type { EditarMargemItem } from "@/api/campaigns";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SortableTh, useSortableData } from "@/components/ui/sortable-table";
import { toast } from "@/components/ui/toaster";
import { useEditarMargemSkus, useMargensCampanha } from "@/features/campaigns/hooks";
import { cn } from "@/lib/utils";

interface MargensEditorCardProps {
  profileId: string;
  campaignId: string;
  /** Quando true, carrega a query imediatamente. Quando false, mostra botão pra
   *  ativar — a query é pesada (~20s pra 200 SKUs). */
  autoLoad?: boolean;
}

/**
 * Editor de margens dentro de uma campanha ML.
 *
 * Mostra cada SKU com preço-base (U), preço de venda (deal), desconto% e
 * margem líquida real. Permite selecionar 1+ SKUs e editar a margem alvo —
 * o app calcula o novo deal_price e aplica via POST /seller-promotions/items.
 * Quando o deal calculado ultrapassa `ml_max` do ML, o item é pulado.
 */
export function MargensEditorCard({
  profileId,
  campaignId,
  autoLoad = false,
}: MargensEditorCardProps) {
  const [enabled, setEnabled] = useState(autoLoad);
  const query = useMargensCampanha(profileId, campaignId, enabled);
  const editarMutation = useEditarMargemSkus();

  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [showEditor, setShowEditor] = useState(false);
  const [margemAlvoPct, setMargemAlvoPct] = useState(20);
  // Painel de detalhes do último lote: guarda itens que não aplicaram
  // (erro/pulado/sem_mudança) pra o user ver SKU + motivo. Some quando o user
  // dispensa OU quando inicia outra edição.
  const [ultimoLoteDetalhes, setUltimoLoteDetalhes] = useState<EditarMargemItem[] | null>(null);

  const allItems = query.data ?? [];

  // Busca + ordenação local. "Marcar todos" e seleção continuam baseados na
  // lista FILTRADA visível, não na crua — mais intuitivo (você só seleciona
  // o que está vendo).
  const [busca, setBusca] = useState("");
  const sorted = useSortableData(
    allItems,
    {
      sku: (it) => it.sku ?? "",
      item_id: (it) => it.item_id,
      preco_base: (it) => it.preco_base,
      deal_price: (it) => it.deal_price,
      desconto_pct: (it) => it.desconto_pct,
      margem_pct: (it) => it.margem_pct,
    },
    busca,
    ["sku", "item_id", "titulo"],
  );
  const items = sorted.data;

  const toggleAll = () => {
    if (selecionados.size === items.length) {
      setSelecionados(new Set());
    } else {
      setSelecionados(new Set(items.map((it) => it.item_id)));
    }
  };

  const toggle = (itemId: string) => {
    setSelecionados((old) => {
      const next = new Set(old);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  // Preview client-side: pra cada selecionado, calcula deal_novo pelo passo3
  // pra mostrar antes do user confirmar. Usa os componentes vindos do backend.
  const preview = useMemo(() => {
    if (selecionados.size === 0) return [];
    const alvo = margemAlvoPct / 100;
    const DEGRAU = 79;
    return items
      .filter((it) => selecionados.has(it.item_id))
      .map((it) => {
        if (
          it.custo === null ||
          it.comissao_pct === null ||
          it.tarifa_fixa === null ||
          it.frete === null ||
          it.aliquota === null
        ) {
          return { item: it, deal_novo: null, status: "sem_componentes" as const };
        }
        const denom = 1 - it.comissao_pct - it.aliquota - alvo;
        if (denom <= 0) {
          return { item: it, deal_novo: null, status: "margem_inviavel" as const };
        }
        const dpBelow = (it.custo + it.tarifa_fixa) / denom;
        const dpAbove = (it.custo + it.frete) / denom;
        let dealNovo: number;
        if (dpBelow < DEGRAU) {
          dealNovo = Math.round(dpBelow * 100) / 100;
        } else if (dpAbove >= DEGRAU) {
          dealNovo = Math.round(dpAbove * 100) / 100;
        } else {
          dealNovo = DEGRAU;
        }
        // Clamp ml_max → pula
        if (it.ml_max !== null && dealNovo > it.ml_max) {
          return {
            item: it,
            deal_novo: dealNovo,
            status: "pulado_ml_max" as const,
          };
        }
        // Clamp ml_min → ajusta pra cima
        if (it.ml_min !== null && dealNovo < it.ml_min) {
          dealNovo = it.ml_min;
        }
        return { item: it, deal_novo: dealNovo, status: "ok" as const };
      });
  }, [items, selecionados, margemAlvoPct]);

  const pulados = preview.filter((p) => p.status !== "ok").length;
  const aplicaveis = preview.filter((p) => p.status === "ok").length;

  const handleAplicar = async () => {
    const itemIds = preview.filter((p) => p.status === "ok").map((p) => p.item.item_id);
    if (itemIds.length === 0) return;
    try {
      const resp = await editarMutation.mutateAsync({
        profileId,
        campaignId,
        payload: { item_ids: itemIds, margem_alvo: margemAlvoPct / 100 },
      });
      const aplicados = resp.results.filter((r) => r.status === "aplicado").length;
      const puladosResp = resp.results.filter((r) => r.status.startsWith("pulado")).length;
      const erros = resp.results.filter((r) => r.status === "erro").length;
      const partes: string[] = [];
      if (aplicados > 0) partes.push(`${aplicados} aplicado${aplicados !== 1 ? "s" : ""}`);
      if (puladosResp > 0) partes.push(`${puladosResp} pulado${puladosResp !== 1 ? "s" : ""}`);
      if (erros > 0) partes.push(`${erros} erro${erros !== 1 ? "s" : ""}`);
      const descricao = partes.join(" · ");
      if (erros === 0) {
        toast.success("Margens editadas", { description: descricao });
      } else {
        toast.error("Edição com avisos", { description: descricao });
      }
      // Painel inline com detalhes do que NÃO aplicou (motivo por SKU). Toast
      // some rápido; o painel fica até o user dispensar.
      const naoAplicados = resp.results.filter((r) => r.status !== "aplicado");
      setUltimoLoteDetalhes(naoAplicados.length > 0 ? naoAplicados : null);
      setShowEditor(false);
      setSelecionados(new Set());
    } catch (err) {
      toast.error("Falha ao editar margens", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">
            Margens dentro da campanha
            {items.length > 0 && (
              <span className="ml-1 text-sm font-normal text-muted-foreground tabular-nums">
                ({items.length})
              </span>
            )}
          </CardTitle>
          <div className="flex items-center gap-2">
            {enabled && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => query.refetch()}
                disabled={query.isFetching}
              >
                <RefreshCw className={cn("h-4 w-4", query.isFetching && "animate-spin")} />
                Atualizar
              </Button>
            )}
            {selecionados.size > 0 && (
              <Button variant="default" size="sm" onClick={() => setShowEditor(true)}>
                <Pencil className="h-4 w-4" />
                Editar margem ({selecionados.size})
              </Button>
            )}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Mostra o preço de venda real (deal) e a margem líquida que sobra após taxas. Selecione 1+
          SKUs e clique no lápis pra ajustar a margem em lote.
        </p>
        {enabled && allItems.length > 0 && (
          <div className="relative mt-2">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              placeholder="Buscar por SKU, MLB ou título…"
              className="h-8 pl-8 text-xs"
            />
          </div>
        )}
      </CardHeader>
      {ultimoLoteDetalhes && ultimoLoteDetalhes.length > 0 && (
        <div className="mx-4 mb-2 rounded-md border border-warning/40 bg-warning/5 p-3 text-xs">
          <div className="mb-2 flex items-start justify-between gap-2">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <div>
                <p className="font-medium">
                  {ultimoLoteDetalhes.length} item
                  {ultimoLoteDetalhes.length !== 1 ? "s" : ""} não aplicado
                  {ultimoLoteDetalhes.length !== 1 ? "s" : ""} no último lote
                </p>
                <p className="text-muted-foreground">Cada linha tem o motivo retornado pelo ML.</p>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setUltimoLoteDetalhes(null)}
              className="-mr-1 -mt-1 h-6 w-6 p-0"
              title="Dispensar"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
          <ul className="space-y-1">
            {ultimoLoteDetalhes.map((r) => (
              <li key={r.item_id} className="flex items-start gap-2">
                <span className="shrink-0 font-mono">{r.sku ?? r.item_id}</span>
                <Badge variant="outline" className="text-[10px]">
                  {r.status}
                </Badge>
                <span className="flex-1 text-muted-foreground">
                  {r.motivo ?? "sem motivo retornado"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <CardContent className="p-0">
        {!enabled ? (
          <div className="px-4 py-8 text-center">
            <Button onClick={() => setEnabled(true)}>Carregar margens</Button>
            <p className="mt-2 text-xs text-muted-foreground">
              Pesado (~20s pra 200 SKUs) — só carrega quando você pede.
            </p>
          </div>
        ) : query.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Calculando margens (1–3 chamadas ML por item)…
          </div>
        ) : query.isError ? (
          <div className="m-4 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
            <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
            <div>
              <p className="font-medium">Não foi possível carregar margens</p>
              <p className="text-muted-foreground">
                {query.error instanceof Error ? query.error.message : "Erro desconhecido"}
              </p>
            </div>
          </div>
        ) : items.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            Nenhum SKU pra mostrar.
          </div>
        ) : (
          <div className="max-h-[600px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="w-8 px-3 py-2">
                    <button type="button" onClick={toggleAll} className="flex items-center">
                      {selecionados.size === items.length && items.length > 0 ? (
                        <CheckSquare className="h-4 w-4" />
                      ) : (
                        <Square className="h-4 w-4" />
                      )}
                    </button>
                  </th>
                  <th className="px-3 py-2">
                    <SortableTh sortKey="sku" state={sorted.sortState} onSort={sorted.toggleSort}>
                      SKU
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2">
                    <SortableTh
                      sortKey="item_id"
                      state={sorted.sortState}
                      onSort={sorted.toggleSort}
                    >
                      MLB
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2 text-right">
                    <SortableTh
                      sortKey="preco_base"
                      state={sorted.sortState}
                      onSort={sorted.toggleSort}
                      className="justify-end"
                    >
                      Preço-base (U)
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2 text-right">
                    <SortableTh
                      sortKey="deal_price"
                      state={sorted.sortState}
                      onSort={sorted.toggleSort}
                      className="justify-end"
                    >
                      Preço venda
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2 text-right">
                    <SortableTh
                      sortKey="desconto_pct"
                      state={sorted.sortState}
                      onSort={sorted.toggleSort}
                      className="justify-end"
                    >
                      Desconto
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2 text-right">
                    <SortableTh
                      sortKey="margem_pct"
                      state={sorted.sortState}
                      onSort={sorted.toggleSort}
                      className="justify-end"
                    >
                      Margem
                    </SortableTh>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const selected = selecionados.has(it.item_id);
                  const margemRuim = it.margem_pct !== null && it.margem_pct < 18;
                  const margemBoa =
                    it.margem_pct !== null && it.margem_pct >= 18 && it.margem_pct <= 22;
                  const margemAlta = it.margem_pct !== null && it.margem_pct > 22;
                  return (
                    <tr
                      key={it.item_id}
                      className={cn(
                        "border-b transition-colors hover:bg-muted/30",
                        selected && "bg-primary/5",
                      )}
                    >
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggle(it.item_id)}
                          disabled={it.erro !== null}
                          className="cursor-pointer disabled:cursor-not-allowed"
                        />
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {it.sku ?? <span className="italic text-muted-foreground">—</span>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{it.item_id}</td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {it.preco_base !== null ? `R$ ${it.preco_base.toFixed(2)}` : "—"}
                      </td>
                      <td className="px-3 py-2 text-right font-medium tabular-nums">
                        {it.deal_price !== null ? `R$ ${it.deal_price.toFixed(2)}` : "—"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-xs text-muted-foreground">
                        {it.desconto_pct !== null ? `−${it.desconto_pct.toFixed(1)}%` : "—"}
                      </td>
                      <td
                        className={cn(
                          "px-3 py-2 text-right font-semibold tabular-nums",
                          margemRuim && "text-destructive",
                          margemBoa && "text-success",
                          margemAlta && "text-amber-600 dark:text-amber-400",
                        )}
                      >
                        {it.erro !== null ? (
                          <span className="cursor-help text-xs text-amber-600" title={it.erro}>
                            ⚠
                          </span>
                        ) : it.margem_pct !== null ? (
                          `${it.margem_pct.toFixed(2)}%`
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>

      {/* ─── Dialog do editor de margem em lote ──────────────────────── */}
      <Dialog open={showEditor} onOpenChange={setShowEditor}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Editar margem em lote</DialogTitle>
            <DialogDescription>
              {selecionados.size} SKU{selecionados.size !== 1 ? "s" : ""} selecionado
              {selecionados.size !== 1 ? "s" : ""}. Itens cujo `deal_price` ultrapassaria o `ml_max`
              do ML serão pulados (não inflamos preço-base automaticamente).
            </DialogDescription>
          </DialogHeader>

          <div className="flex items-end gap-3 py-2">
            <div className="space-y-1">
              <label htmlFor="margem-alvo-editor" className="text-xs font-medium">
                Margem líquida alvo
              </label>
              <div className="flex items-center gap-1.5">
                <Input
                  id="margem-alvo-editor"
                  type="number"
                  min={1}
                  max={80}
                  step={1}
                  value={margemAlvoPct}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v) && v >= 1 && v <= 80) {
                      setMargemAlvoPct(v);
                    }
                  }}
                  className="h-9 w-20 text-right tabular-nums"
                />
                <span className="text-sm text-muted-foreground">%</span>
              </div>
            </div>
            <div className="ml-auto flex items-center gap-3 text-xs">
              <Badge variant="outline" className="border-success/40 text-success">
                {aplicaveis} aplicável{aplicaveis !== 1 ? "is" : ""}
              </Badge>
              {pulados > 0 && (
                <Badge
                  variant="outline"
                  className="border-amber-500/40 text-amber-700 dark:text-amber-300"
                >
                  {pulados} pulado{pulados !== 1 ? "s" : ""}
                </Badge>
              )}
            </div>
          </div>

          <div className="max-h-[400px] overflow-auto rounded-md border">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-3 py-2">SKU</th>
                  <th className="px-3 py-2 text-right">Deal atual</th>
                  <th className="px-3 py-2 text-right">Deal novo</th>
                  <th className="px-3 py-2 text-right">ml_max</th>
                  <th className="px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {preview.map((p) => (
                  <tr key={p.item.item_id} className="border-b">
                    <td className="px-3 py-1.5 font-mono">{p.item.sku ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {p.item.deal_price !== null ? `R$ ${p.item.deal_price.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums font-medium">
                      {p.deal_novo !== null ? `R$ ${p.deal_novo.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
                      {p.item.ml_max !== null ? `R$ ${p.item.ml_max.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-1.5">
                      {p.status === "ok" ? (
                        <span className="text-success">✓ aplicar</span>
                      ) : p.status === "pulado_ml_max" ? (
                        <span className="text-amber-600 dark:text-amber-400">
                          ⚠ pulado (deal {">"} ml_max)
                        </span>
                      ) : p.status === "margem_inviavel" ? (
                        <span className="text-destructive">⚠ margem inviável</span>
                      ) : (
                        <span className="text-muted-foreground">⚠ sem dados</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setShowEditor(false)}
              disabled={editarMutation.isPending}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              onClick={handleAplicar}
              disabled={aplicaveis === 0 || editarMutation.isPending}
            >
              {editarMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Aplicando…
                </>
              ) : (
                `Aplicar em ${aplicaveis} SKU${aplicaveis !== 1 ? "s" : ""}`
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
