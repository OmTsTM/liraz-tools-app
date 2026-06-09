import { AlertTriangle, ArrowUpRight, Loader2, Pencil, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { SortableTh, useSortableData } from "@/components/ui/sortable-table";
import { toast } from "@/components/ui/toaster";
import {
  useCampaignSkusInfo,
  useInflarPrecos,
  useMargensCampanha,
  useSyncSkusBatch,
  useUpdateCampaignSkus,
} from "@/features/campaigns/hooks";
import { SkuSelectorSemSimulacao } from "@/features/campaigns/sku-selector-sem-simulacao";
import { cn } from "@/lib/utils";
import type { Campaign } from "@/types/api";

interface CampaignSkusPanelProps {
  profileId: string;
  campaign: Campaign;
}

/**
 * Painel "Anúncios incluídos" no detail page.
 *
 * Comportamento de edição (modelo passo3 — unificado mai/2026):
 * - Campanha local editável (rascunho/agendada) e campanha origem='ml'
 *   usam o MESMO seletor `SkuSelectorSemSimulacao` (passo3, calcula
 *   deal_price por item). O que muda é o handler de save:
 *   - Local: PATCH /skus (só persiste a lista; os preços recalculam na
 *     hora do play via `StartCampaignPasso3UseCase` no backend).
 *   - ML: POST /skus/sync-batch (aplica no ML na hora — diff add+remove
 *     com inflação/deal/fallback).
 * - Campanha local finalizada/cancelada → read-only, sem botão de edit.
 */
export function CampaignSkusPanel({ profileId, campaign }: CampaignSkusPanelProps) {
  const { data: items, isPending } = useCampaignSkusInfo(profileId, campaign.id);

  // Carrega deal_price/original_price do ML pra cada SKU automaticamente
  // quando a campanha está no ML. Pesado (~20s pra 200 SKUs na 1ª vez),
  // mas cacheado por 5 min — F5/voltar pra tela dentro disso é instantâneo.
  // Cache compartilhado com `MargensEditorCard` abaixo.
  const margensQuery = useMargensCampanha(profileId, campaign.id, Boolean(campaign.ml_campaign_id));
  const margensMap = useMemo(() => {
    const m = new Map<string, { deal: number | null; base: number | null }>();
    for (const r of margensQuery.data ?? []) {
      m.set(r.item_id, { deal: r.deal_price, base: r.preco_base });
    }
    return m;
  }, [margensQuery.data]);
  const mostrarPrecoVenda = Boolean(campaign.ml_campaign_id);
  const updateMutation = useUpdateCampaignSkus();
  const syncBatchMutation = useSyncSkusBatch();
  const inflarPrecosMutation = useInflarPrecos();
  const [showPicker, setShowPicker] = useState(false);

  const skusSelected = campaign.skus_selecionados ?? [];
  // Local editable (rascunho/agendada) E ML → ambos usam o mesmo seletor
  // passo3. `canEditMl` só diferencia o handler de save (sync-batch que
  // aplica no ML agora vs PATCH /skus que persiste pro play).
  // Sync-batch aplica no ML sempre que a campanha JÁ está lá (independente
  // de ter sido criada no app ou importada do painel). `origem` só
  // distingue "veio do painel ML" vs "criada aqui" — pra decidir se mexer
  // no ML, o que importa é a presença real (= `ml_campaign_id` setado).
  const canEditMl = Boolean(campaign.ml_campaign_id);
  const canEdit = canEditMl || campaign.is_editable;

  const itemsSemInfo = (items ?? []).filter((it) => it.titulo === null).length;
  const totalItems = items?.length ?? skusSelected.length;

  // Busca + ordenação local da tabela.
  // O preço atual usado pro sort é o `dealAtivo` (quando há) — mais relevante
  // do que o `it.preco` cru, porque é o que o cliente paga na campanha.
  const [busca, setBusca] = useState("");
  const itemsSorted = useSortableData(
    items ?? [],
    {
      sku: (it) => it.sku ?? "",
      item_id: (it) => it.item_id,
      titulo: (it) => it.titulo ?? "",
      preco: (it) => {
        const m = margensMap.get(it.item_id);
        return m?.deal ?? it.preco ?? null;
      },
    },
    busca,
    ["sku", "item_id", "titulo"],
  );

  // ── Inflar preços (Fase 1, isolada) — só ML ───────────────────────
  // Pra campanha LOCAL, a inflação não acontece aqui — os preços rolam no
  // play via `StartCampaignPasso3UseCase`. Então o handler local resolve
  // imediatamente como sucesso (libera o botão Confirmar).
  const handleInflarLocal = async (
    _inflarPrecos: Record<string, number>,
  ): Promise<{
    inflados: string[];
    ja_no_preco: string[];
    erros: Array<{ item_id: string }>;
  }> => {
    return { inflados: [], ja_no_preco: [], erros: [] };
  };

  const handleInflarMl = async (
    inflarPrecos: Record<string, number>,
  ): Promise<{
    inflados: string[];
    ja_no_preco: string[];
    erros: Array<{ item_id: string }>;
  }> => {
    if (Object.keys(inflarPrecos).length === 0) {
      return { inflados: [], ja_no_preco: [], erros: [] };
    }
    try {
      const result = await inflarPrecosMutation.mutateAsync({
        profileId,
        campaignId: campaign.id,
        inflar_precos: inflarPrecos,
      });
      const partes: string[] = [];
      if (result.inflados.length > 0) {
        partes.push(`${result.inflados.length} inflado${result.inflados.length !== 1 ? "s" : ""}`);
      }
      if (result.ja_no_preco.length > 0) {
        partes.push(`${result.ja_no_preco.length} já no preço`);
      }
      if (result.erros.length > 0) {
        partes.push(`${result.erros.length} falhou`);
      }
      const descricao = partes.join(" · ");
      if (result.erros.length === 0) {
        toast.success("Preços inflados", { description: descricao });
      } else {
        toast.error("Inflação com avisos", {
          description: `${descricao}. Itens com falha ficarão fora da campanha.`,
        });
      }
      return {
        inflados: result.inflados,
        ja_no_preco: result.ja_no_preco,
        erros: result.erros.map((e) => ({ item_id: e.item_id })),
      };
    } catch (err) {
      toast.error("Falha ao inflar preços", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
      throw err;
    }
  };

  // ── Confirmar pra campanha LOCAL (PATCH /skus) ───────────────────
  // Local só persiste a lista de SKUs — os preços (deal/inflar/fallback)
  // são RECALCULADOS no play via `StartCampaignPasso3UseCase` no backend.
  // Por isso a signature recebe os preços mas IGNORA.
  const handleConfirmarLocal = async (
    novosSkus: string[],
    _dealPrices: Record<string, number>,
    _fallbackDealPrices: Record<string, number>,
  ) => {
    try {
      await updateMutation.mutateAsync({
        profileId,
        campaignId: campaign.id,
        skus: novosSkus.length > 0 ? novosSkus : null,
      });
      setShowPicker(false);
      toast.success("SKUs atualizados", {
        description: `${novosSkus.length} anúncio${novosSkus.length !== 1 ? "s" : ""} na campanha`,
      });
    } catch (err) {
      toast.error("Falha ao atualizar SKUs", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  // ── Confirmar pra campanha ML — sync-batch SEM inflar_precos ────
  // A inflação já rolou na fase 1 (handleInflarMl). Aqui é só fase 2:
  // clamp + POST de adição + retry de fallback + descarte.
  const handleConfirmarMl = async (
    novosSelecionados: string[],
    dealPrices: Record<string, number>,
    fallbackDealPrices: Record<string, number>,
  ) => {
    const atuais = new Set(skusSelected);
    const novos = new Set(novosSelecionados);
    const adicionar = novosSelecionados.filter((id) => !atuais.has(id));
    const remover = skusSelected.filter((id) => !novos.has(id));

    if (adicionar.length === 0 && remover.length === 0) {
      setShowPicker(false);
      return;
    }

    try {
      const result = await syncBatchMutation.mutateAsync({
        profileId,
        campaignId: campaign.id,
        payload: {
          adicionar,
          remover,
          deal_prices: dealPrices,
          inflar_precos: {},
          fallback_deal_prices: fallbackDealPrices,
        },
      });
      setShowPicker(false);

      const partes: string[] = [];
      if (result.inflados.length > 0) {
        partes.push(
          `${result.inflados.length} preço${result.inflados.length !== 1 ? "s" : ""} inflado${result.inflados.length !== 1 ? "s" : ""}`,
        );
      }
      if (result.adicionados.length > 0) {
        partes.push(
          `${result.adicionados.length} adicionado${result.adicionados.length !== 1 ? "s" : ""}`,
        );
      }
      if (result.fallbacks_usados?.length > 0) {
        partes.push(`${result.fallbacks_usados.length} via fallback frete grátis`);
      }
      if (result.clamped && result.clamped.length > 0) {
        partes.push(
          `${result.clamped.length} entrou${result.clamped.length !== 1 ? "" : ""} com deal ajustado ao piso de credibilidade do ML (margem ≥ 16,5%)`,
        );
      }
      if (result.pulados_por_margem && result.pulados_por_margem.length > 0) {
        partes.push(
          `${result.pulados_por_margem.length} pulado${result.pulados_por_margem.length !== 1 ? "s" : ""} — clamp violaria piso de margem (16,5%)`,
        );
      }
      if (result.pulados_ausente && result.pulados_ausente.length > 0) {
        partes.push(
          `${result.pulados_ausente.length} pulado${result.pulados_ausente.length !== 1 ? "s" : ""} — ML não aceita como candidate (condição/categoria)`,
        );
      }
      if (result.reprecificados_20pct && result.reprecificados_20pct.length > 0) {
        partes.push(
          `${result.reprecificados_20pct.length} negado${result.reprecificados_20pct.length !== 1 ? "s" : ""} pelo ML → desinflado${result.reprecificados_20pct.length !== 1 ? "s" : ""} pra 20% de margem (fora da campanha)`,
        );
      }
      if (result.revertidos && result.revertidos.length > 0) {
        partes.push(
          `${result.revertidos.length} revertido${result.revertidos.length !== 1 ? "s" : ""} ao preço original (fora da campanha)`,
        );
      }
      if (result.pendentes_lock && result.pendentes_lock.length > 0) {
        partes.push(
          `${result.pendentes_lock.length} pendente${result.pendentes_lock.length !== 1 ? "s" : ""} — ML travou (423), preço-base mantido inflado; ML deve adicionar nas próximas horas`,
        );
      }
      if (result.removidos.length > 0) {
        partes.push(
          `${result.removidos.length} removido${result.removidos.length !== 1 ? "s" : ""}`,
        );
      }
      if (result.erros.length > 0) {
        partes.push(`${result.erros.length} falhou`);
      }
      const descricao = partes.join(" · ");

      if (result.erros.length === 0) {
        toast.success("SKUs sincronizados no ML", { description: descricao });
      } else {
        toast.error("Sincronização com avisos", {
          description: `${descricao}. Erros: ${result.erros
            .slice(0, 2)
            .map((e) => `${e.item_id} (${e.operacao})`)
            .join(", ")}`,
        });
      }
    } catch (err) {
      toast.error("Falha ao sincronizar SKUs", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const isSaving =
    updateMutation.isPending || syncBatchMutation.isPending || inflarPrecosMutation.isPending;

  // ── Caso 1: campanha vazia (rascunho cru) ─────────────────────────
  if (totalItems === 0 && !campaign.simulacao_id) {
    return (
      <Card className="border-dashed">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Anúncios incluídos</CardTitle>
            {canEdit && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowPicker(true)}
                disabled={isSaving}
              >
                <Pencil className="h-4 w-4" />
                Adicionar SKUs
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Nenhum anúncio adicionado ainda.{" "}
          {canEdit
            ? 'Clique em "Adicionar SKUs" pra trazer anúncios pra dentro da campanha.'
            : "Aguarde os SKUs serem importados."}
        </CardContent>
        {canEdit && (
          <SkuSelectorSemSimulacao
            open={showPicker}
            onOpenChange={setShowPicker}
            profileId={profileId}
            campaignId={campaign.id}
            periodo={{ data_inicio: campaign.data_inicio, data_fim: campaign.data_fim }}
            selecionadosAtuais={skusSelected}
            onInflar={canEditMl ? handleInflarMl : handleInflarLocal}
            onConfirmar={canEditMl ? handleConfirmarMl : handleConfirmarLocal}
          />
        )}
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">
            Anúncios incluídos{" "}
            <span className="ml-1 text-sm font-normal text-muted-foreground tabular-nums">
              ({totalItems})
            </span>
          </CardTitle>
          <div className="flex items-center gap-2">
            {/* Indicador discreto enquanto busca deal_price/preço-base do ML
                pra cada SKU. Carrega automaticamente quando há
                ml_campaign_id; cache de 5 min, primeira vez ~20s. */}
            {mostrarPrecoVenda && margensQuery.isLoading && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                Buscando preços de venda…
              </span>
            )}
            {canEdit && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowPicker(true)}
                disabled={isSaving}
              >
                <Pencil className="h-4 w-4" />
                Adicionar/editar SKUs
              </Button>
            )}
          </div>
        </div>
        {items && items.length > 0 && (
          <div className="relative mt-2">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Buscar por SKU, MLB ou título…"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              className="h-8 pl-8 text-xs"
            />
          </div>
        )}
        {itemsSemInfo > 0 && (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
            <AlertTriangle className="h-3 w-3 text-amber-500" />
            {itemsSemInfo} {itemsSemInfo === 1 ? "item sem" : "items sem"} dados — podem ter sido
            removidos do ML ou a loja está desconectada.
          </p>
        )}
      </CardHeader>
      <CardContent className="p-0">
        {isPending ? (
          <div className="space-y-2 p-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : (
          <div className="max-h-[400px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="px-3 py-2">
                    <SortableTh
                      sortKey="sku"
                      state={itemsSorted.sortState}
                      onSort={itemsSorted.toggleSort}
                    >
                      SKU
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2">
                    <SortableTh
                      sortKey="item_id"
                      state={itemsSorted.sortState}
                      onSort={itemsSorted.toggleSort}
                    >
                      MLB
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2">
                    <SortableTh
                      sortKey="titulo"
                      state={itemsSorted.sortState}
                      onSort={itemsSorted.toggleSort}
                    >
                      Título
                    </SortableTh>
                  </th>
                  <th className="px-3 py-2 text-right">
                    <SortableTh
                      sortKey="preco"
                      state={itemsSorted.sortState}
                      onSort={itemsSorted.toggleSort}
                      className="justify-end"
                    >
                      {mostrarPrecoVenda ? "Preço de venda" : "Preço atual"}
                    </SortableTh>
                  </th>
                </tr>
              </thead>
              <tbody>
                {itemsSorted.data.map((it) => {
                  const margem = margensMap.get(it.item_id);
                  const dealAtivo = margem?.deal ?? null;
                  // base do ML (`original_price` da promoção) tem prioridade
                  // sobre `it.preco` (do cache do app) quando disponível.
                  const baseAtivo = margem?.base ?? it.preco ?? null;
                  const temDesconto =
                    mostrarPrecoVenda &&
                    dealAtivo !== null &&
                    baseAtivo !== null &&
                    Math.abs(baseAtivo - dealAtivo) > 0.01;
                  return (
                    <tr key={it.item_id} className="border-b transition-colors hover:bg-muted/30">
                      <td className="px-3 py-2 font-mono text-xs">
                        {it.sku ?? <span className="italic text-muted-foreground">—</span>}
                      </td>
                      <td className="px-3 py-2">
                        <a
                          href={`https://produto.mercadolivre.com.br/${it.item_id.replace("MLB", "MLB-")}`}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono text-xs hover:underline"
                        >
                          {it.item_id}
                          <ArrowUpRight className="ml-1 inline h-3 w-3" />
                        </a>
                      </td>
                      <td
                        className={cn(
                          "max-w-[300px] truncate px-3 py-2",
                          it.titulo === null && "italic text-muted-foreground",
                        )}
                      >
                        {it.titulo ?? "(sem dados no cache)"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {temDesconto && baseAtivo !== null && dealAtivo !== null ? (
                          <>
                            <div className="text-xs text-muted-foreground line-through">
                              R$ {baseAtivo.toFixed(2).replace(".", ",")}
                            </div>
                            <div className="font-semibold">
                              R$ {dealAtivo.toFixed(2).replace(".", ",")}
                            </div>
                          </>
                        ) : it.preco !== null ? (
                          `R$ ${it.preco.toFixed(2).replace(".", ",")}`
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

      {canEdit && (
        <SkuSelectorSemSimulacao
          open={showPicker}
          onOpenChange={setShowPicker}
          profileId={profileId}
          campaignId={campaign.id}
          periodo={{ data_inicio: campaign.data_inicio, data_fim: campaign.data_fim }}
          selecionadosAtuais={skusSelected}
          onInflar={canEditMl ? handleInflarMl : handleInflarLocal}
          onConfirmar={canEditMl ? handleConfirmarMl : handleConfirmarLocal}
        />
      )}
    </Card>
  );
}
