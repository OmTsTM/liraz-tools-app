import { AlertCircle, CheckSquare, Loader2, Search, Square } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { PromoNoItem } from "@/api/listings";
import { Badge } from "@/components/ui/badge";
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
import { useSugerirDealPrices, useSugerirDealPricesPreview } from "@/features/campaigns/hooks";
import { useSkusComPromocoes } from "@/features/listings/hooks";
import { cn } from "@/lib/utils";

interface SkuSelectorSemSimulacaoProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profileId: string;
  /** Período da campanha — usado pra filtrar promoções no badge "já em X". */
  periodo: { data_inicio: string; data_fim: string };
  /** Lista atual de item_ids selecionados (vazio = nada). */
  selecionadosAtuais: string[];
  /**
   * `campaignId` precisa ser passado pra sugestão funcionar. Quando ausente
   * (criação de campanha NOVA, antes de existir o id), o modal opera em
   * "modo desconto fixo" (ignora margem alvo e calcula deal = preço × 0,9).
   * Pra criação completa com margem-alvo real, criar primeiro como rascunho.
   */
  campaignId?: string;
  /**
   * Chamado com os items + os deal_prices calculados quando o user confirma.
   * `deal_prices` é mapa item_id → preço pós-desconto, pronto pra POST no ML.
   * `inflar_precos` é mapa item_id → preço novo de listagem, pra aplicar
   *  via `PUT /items/{id}` ANTES de adicionar à campanha (Fase 1, rev8).
   * `fallback_deal_prices` é mapa item_id → deal conservador (≥R$ 79 com
   *  frete) pra retry quando o ML rejeita o agressivo. Só preenchido pros
   *  itens onde a quebra de frete grátis foi aplicada (rev13).
   */
  /**
   * Fluxo 2-passos:
   * - `onInflar(inflarPrecos)`: chama o endpoint que SÓ infla preços (PUT /items).
   *   Retorna resultado pra extensão saber quais inflados e quais falharam.
   *   No fluxo do `--family/local` (sem ML), o parent pode passar uma stub que
   *   resolve com `{inflados: [...], erros: []}` direto.
   * - `onConfirmar(...)`: chama sync-batch SEM `inflar_precos` — só fase 2 da
   *   adição (clamp + POST + retry + descarte).
   */
  onInflar: (inflarPrecos: Record<string, number>) => Promise<{
    inflados: string[];
    ja_no_preco: string[];
    erros: Array<{ item_id: string }>;
  }>;
  onConfirmar: (
    selecionados: string[],
    dealPrices: Record<string, number>,
    fallbackDealPrices: Record<string, number>,
  ) => Promise<void>;
  /** Margem inicial em %. Default 20. */
  margemAlvoInicialPct?: number;
}

/**
 * Modal de seleção de SKUs com cálculo de deal_price por margem-alvo (Leva
 * 5.12 rev4). Fluxo em 2 etapas:
 * 1. User marca SKUs + define margem-alvo
 * 2. Aperta "Calcular sugestão" — chama endpoint backend que faz busca
 *    binária por SKU (preço que atinge margem-alvo). Mostra tabela com
 *    deal_price, desconto% e margem real por linha.
 * 3. Confirma — chama onSave com items + deal_prices calculados.
 *
 * Items sem custo cadastrado aparecem na tabela com erro mas não bloqueiam
 * o save dos outros (mesmo modelo de falha parcial dos endpoints batch).
 */
export function SkuSelectorSemSimulacao({
  open,
  onOpenChange,
  profileId,
  periodo,
  selecionadosAtuais,
  campaignId,
  onInflar,
  onConfirmar,
  margemAlvoInicialPct = 20,
}: SkuSelectorSemSimulacaoProps) {
  // Só busca quando o modal abre — o endpoint varre todos os anúncios da loja
  // (~14s). Sem o gate, a busca disparava no load da tela de detalhe (o Dialog
  // fica montado mesmo fechado), duplicando a varredura em paralelo com o sync.
  const { data, isPending, isError, error } = useSkusComPromocoes(profileId, true, {
    enabled: open,
  });
  const sugestaoMutation = useSugerirDealPrices();
  const sugestaoPreviewMutation = useSugerirDealPricesPreview();

  const [busca, setBusca] = useState("");
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set(selecionadosAtuais));
  const [mostrarSoEmPromo, setMostrarSoEmPromo] = useState(false);
  const [margemAlvoPct, setMargemAlvoPct] = useState<number>(margemAlvoInicialPct);
  const [sugestao, setSugestao] = useState<
    Record<
      string,
      {
        preco_atual: number | null;
        margem_atual_pct: number | null;
        precisa_inflacao: boolean;
        preco_inflado: number | null;
        deal_price: number | null;
        desconto_pct: number | null;
        margem_real_pct: number | null;
        min_aplicado: boolean;
        aviso_degrau: { deal_price: number; margem_pct: number; desconto_pct: number } | null;
        quebra_frete_gratis_aplicada: boolean;
        fallback_deal_price: number | null;
        frete_a_confirmar: boolean;
        erro: string | null;
      }
    >
  >({});

  // Reset ao abrir
  // biome-ignore lint/correctness/useExhaustiveDependencies: só sincroniza quando o modal abre
  useEffect(() => {
    if (open) {
      setSelecionados(new Set(selecionadosAtuais));
      setBusca("");
      setMargemAlvoPct(margemAlvoInicialPct);
      setSugestao({});
      setInflacaoStatus("pendente");
      setInflados(new Set());
      setInflacaoErros(new Set());
    }
  }, [open]);

  // Filtra a lista
  const itemsFiltrados = useMemo(() => {
    if (!data?.results) return [];
    const buscaLower = busca.trim().toLowerCase();
    return data.results.filter((item) => {
      if (mostrarSoEmPromo) {
        const conflitos = promosNoPeriodo(item.promocoes, periodo);
        if (conflitos.length === 0) return false;
      }
      if (!buscaLower) return true;
      return (
        item.item_id.toLowerCase().includes(buscaLower) ||
        (item.sku?.toLowerCase().includes(buscaLower) ?? false) ||
        (item.titulo?.toLowerCase().includes(buscaLower) ?? false)
      );
    });
  }, [data, busca, mostrarSoEmPromo, periodo]);

  const toggle = (itemId: string) => {
    setSelecionados((old) => {
      const next = new Set(old);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
    // Mantém `sugestao` pra preservar cálculos dos SKUs que continuam
    // selecionados. Entradas órfãs (de SKUs desmarcados) ficam, mas não
    // atrapalham — a UI só exibe quando `selecionados.has(item_id)`.
    // Inflação SIM é resetada: se o usuário já apertou "Inflar preços"
    // antes, qualquer mudança de seleção invalida o estado batch
    // (re-clicar "Inflar" depois é idempotente — items já no preço-alvo
    // são pulados automaticamente).
    setInflacaoStatus("pendente");
    setInflados(new Set());
    setInflacaoErros(new Set());
  };

  const marcarVisiveis = () => {
    setSelecionados((old) => {
      const next = new Set(old);
      for (const it of itemsFiltrados) {
        // Pula items sem SKU (mostrados com travessão "—" na coluna SKU).
        // Foi decisão de produto não selecioná-los em lote — sem SKU não
        // dá pra cruzar com custos.xlsx, então sempre cairiam em erro.
        if (!it.sku) continue;
        next.add(it.item_id);
      }
      return next;
    });
    setInflacaoStatus("pendente");
    setInflados(new Set());
    setInflacaoErros(new Set());
  };
  const desmarcarVisiveis = () => {
    setSelecionados((old) => {
      const next = new Set(old);
      for (const it of itemsFiltrados) next.delete(it.item_id);
      return next;
    });
    setInflacaoStatus("pendente");
    setInflados(new Set());
    setInflacaoErros(new Set());
  };

  // Items NOVOS (não estavam na campanha) — só pra esses precisamos sugerir
  const novosSelecionados = useMemo(() => {
    const atuais = new Set(selecionadosAtuais);
    return [...selecionados].filter((id) => !atuais.has(id));
  }, [selecionados, selecionadosAtuais]);

  const handleCalcular = async () => {
    // Otimização: manda só os SKUs que ainda NÃO têm cálculo. Cálculos
    // anteriores são preservados (a margem alvo não mudou — se mudasse,
    // o input já zerou `sugestao` no onChange). Acelera o fluxo de
    // ajustar seleção e re-calcular sem refazer tudo.
    const aCalcular = novosSelecionados.filter((id) => !sugestao[id]);
    if (aCalcular.length === 0) return;
    try {
      // Modo edit: tem campaignId, usa endpoint normal (consulta min/max real).
      // Modo create: sem campaignId, usa preview (assume min%=5%).
      const result = campaignId
        ? await sugestaoMutation.mutateAsync({
            profileId,
            campaignId,
            payload: {
              item_ids: aCalcular,
              margem_alvo: margemAlvoPct / 100,
            },
          })
        : await sugestaoPreviewMutation.mutateAsync({
            profileId,
            payload: {
              item_ids: aCalcular,
              margem_alvo: margemAlvoPct / 100,
            },
          });
      setSugestao((old) => {
        const merged: typeof sugestao = { ...old };
        for (const r of result.results) {
          merged[r.item_id] = {
            preco_atual: r.preco_atual,
            margem_atual_pct: r.margem_atual_pct,
            precisa_inflacao: r.precisa_inflacao,
            preco_inflado: r.preco_inflado,
            deal_price: r.deal_price,
            desconto_pct: r.desconto_pct,
            margem_real_pct: r.margem_real_pct,
            min_aplicado: r.min_aplicado,
            aviso_degrau: r.aviso_degrau,
            quebra_frete_gratis_aplicada: r.quebra_frete_gratis_aplicada,
            fallback_deal_price: r.fallback_deal_price,
            frete_a_confirmar: r.frete_a_confirmar ?? false,
            erro: r.erro,
          };
        }
        return merged;
      });

      // Auto-desmarca SKUs cujo backend retornou "sem custo (fonte=nao_encontrado)".
      // Solução paliativa: enquanto não decidimos o fluxo correto pros itens
      // sem entrada no custos.xlsx, desmarcamos automaticamente pra liberar
      // o "Confirmar" pros demais. O user pode investigar e adicionar custos
      // depois.
      const semCustoIds = result.results
        .filter((r) => r.erro?.toLowerCase().includes("sem custo"))
        .map((r) => r.item_id);
      if (semCustoIds.length > 0) {
        setSelecionados((old) => {
          const next = new Set(old);
          for (const id of semCustoIds) next.delete(id);
          return next;
        });
      }
    } catch {
      // erro de rede — mantém sugestao={}, UI mostra estado de erro
    }
  };

  // Toggle: aplicar inflação Fase 1 nos SKUs que precisam? Default true
  // (segue a recomendação do app baseada na config do perfil).
  const [aplicarInflacao, setAplicarInflacao] = useState(true);

  // ─── Fluxo 2-passos: inflar → confirmar ───────────────────────────
  // Estado da inflação por sessão de uso do modal. Reseta quando recalcula
  // descontos (recalcular invalida os preços inflados anteriores).
  const [inflacaoStatus, setInflacaoStatus] = useState<"pendente" | "rodando" | "concluida">(
    "pendente",
  );
  const [inflados, setInflados] = useState<Set<string>>(new Set());
  const [inflacaoErros, setInflacaoErros] = useState<Set<string>>(new Set());
  const [confirmandoSalvar, setConfirmandoSalvar] = useState(false);

  const handleInflar = async () => {
    const inflarPrecos: Record<string, number> = {};
    for (const [itemId, s] of Object.entries(sugestao)) {
      if (
        aplicarInflacao &&
        s.precisa_inflacao &&
        s.preco_inflado !== null &&
        !s.erro &&
        !s.frete_a_confirmar &&
        selecionados.has(itemId)
      ) {
        inflarPrecos[itemId] = s.preco_inflado;
      }
    }
    if (Object.keys(inflarPrecos).length === 0) {
      // Nada a inflar — habilita confirmar direto
      setInflacaoStatus("concluida");
      return;
    }
    setInflacaoStatus("rodando");
    try {
      const resp = await onInflar(inflarPrecos);
      setInflados(new Set(resp.inflados.concat(resp.ja_no_preco)));
      setInflacaoErros(new Set(resp.erros.map((e) => e.item_id)));
      setInflacaoStatus("concluida");
    } catch {
      // Erro geral — não muda status, user pode tentar de novo
      setInflacaoStatus("pendente");
    }
  };

  const handleConfirmar = async () => {
    const dealPrices: Record<string, number> = {};
    const fallbackDealPrices: Record<string, number> = {};
    const excluidos = new Set<string>(); // frete a confirmar ou inflação falhou
    for (const [itemId, s] of Object.entries(sugestao)) {
      // Frete a confirmar: NÃO sobe na campanha (fica pra ajuste manual).
      if (s.frete_a_confirmar) {
        excluidos.add(itemId);
        continue;
      }
      // Inflação falhou pra esse item: também não tenta adicionar
      // (o preço-base não está em U, ML provavelmente rejeitaria).
      if (inflacaoErros.has(itemId)) {
        excluidos.add(itemId);
        continue;
      }
      if (s.deal_price !== null && !s.erro) {
        dealPrices[itemId] = s.deal_price;
      }
      if (s.quebra_frete_gratis_aplicada && s.fallback_deal_price !== null && !s.erro) {
        fallbackDealPrices[itemId] = s.fallback_deal_price;
      }
    }
    const finais = [...selecionados].filter((id) => !excluidos.has(id));
    setConfirmandoSalvar(true);
    try {
      await onConfirmar(finais, dealPrices, fallbackDealPrices);
      onOpenChange(false);
    } finally {
      setConfirmandoSalvar(false);
    }
  };

  // MLBs com frete a confirmar (cruzam o degrau R$ 79) — não entram na
  // campanha; o usuário baixa a lista e ajusta no painel ML manualmente.
  const mlbsFreteAConfirmar = Object.entries(sugestao)
    .filter(([, s]) => s.frete_a_confirmar && !s.erro)
    .map(([id]) => id);

  // TEMP: download .txt dos MLBs com frete a confirmar.
  const handleDownloadFreteAConfirmar = () => {
    const conteudo = mlbsFreteAConfirmar.join("\n");
    const blob = new Blob([conteudo], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `frete-a-confirmar-${mlbsFreteAConfirmar.length}-itens.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Tem sugestão calculada e válida pra TODOS os items novos selecionados?
  const todosNovosTeemSugestao =
    novosSelecionados.length === 0 ||
    novosSelecionados.every((id) => sugestao[id] && !sugestao[id].erro);

  const algumNovoSemCusto = Object.values(sugestao).some((s) => s.erro?.includes("custo"));
  const skusComInflacao = Object.values(sugestao).filter((s) => s.precisa_inflacao && !s.erro);
  // Separa por direção do ajuste de preço-base — UI mostra texto correto:
  // alguns SKUs podem estar com preço atual ACIMA do U passo3 (resultado de
  // inflação anterior por outro fator T) e precisam DESCER pra atingir U novo.
  const skusVaoSubir = skusComInflacao.filter(
    (s) => s.preco_atual !== null && s.preco_inflado !== null && s.preco_inflado > s.preco_atual,
  );
  const skusVaoDescer = skusComInflacao.filter(
    (s) => s.preco_atual !== null && s.preco_inflado !== null && s.preco_inflado < s.preco_atual,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>Selecionar anúncios</DialogTitle>
          <DialogDescription>
            Marque os anúncios e defina a margem líquida desejada. O app calcula o preço com
            desconto que atinge essa margem por SKU (considerando custo, imposto, comissão ML e
            frete).{" "}
            <Badge variant="outline" className="mx-1 px-1.5 py-0 text-xs">
              em outra promo
            </Badge>{" "}
            sinaliza sobreposição no período — não bloqueia.
          </DialogDescription>
        </DialogHeader>

        {isPending && (
          <div className="flex flex-col items-center justify-center gap-3 py-12 text-sm text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin" />
            Carregando anúncios e promoções do ML…
          </div>
        )}

        {isError && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
            <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
            <div>
              <p className="font-medium">Não foi possível carregar anúncios</p>
              <p className="text-muted-foreground">
                {error instanceof Error ? error.message : "Erro desconhecido"}
              </p>
            </div>
          </div>
        )}

        {data && (
          <>
            <div className="space-y-3">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Buscar por SKU, MLB ou título…"
                  value={busca}
                  onChange={(e) => setBusca(e.target.value)}
                  className="pl-9"
                />
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={marcarVisiveis}
                    disabled={itemsFiltrados.length === 0}
                  >
                    <CheckSquare className="h-3.5 w-3.5" />
                    Marcar {itemsFiltrados.length === data.results.length ? "todos" : "visíveis"}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={desmarcarVisiveis}
                    disabled={itemsFiltrados.length === 0}
                  >
                    <Square className="h-3.5 w-3.5" />
                    Desmarcar {itemsFiltrados.length === data.results.length ? "todos" : "visíveis"}
                  </Button>
                </div>
                <label className="flex cursor-pointer items-center gap-1.5 text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={mostrarSoEmPromo}
                    onChange={(e) => setMostrarSoEmPromo(e.target.checked)}
                  />
                  Só em outra promoção
                </label>
              </div>

              <div className="text-xs text-muted-foreground">
                {data.total} anúncios · {data.items_em_promocao} em promoção ·
                <span className="ml-1 font-medium text-foreground">
                  {selecionados.size} selecionado{selecionados.size !== 1 ? "s" : ""}
                </span>
                {novosSelecionados.length > 0 && (
                  <span className="ml-2">
                    ({novosSelecionados.length} novo{novosSelecionados.length !== 1 ? "s" : ""} a
                    adicionar)
                  </span>
                )}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto rounded-md border">
              {itemsFiltrados.length === 0 ? (
                <div className="py-12 text-center text-sm text-muted-foreground">
                  Nenhum anúncio encontrado.
                </div>
              ) : (
                <ul className="divide-y">
                  {itemsFiltrados.map((item) => {
                    const checked = selecionados.has(item.item_id);
                    const conflitos = promosNoPeriodo(item.promocoes, periodo);
                    const s = sugestao[item.item_id];
                    // Ação opcional: subir o deal pro degrau de R$ 79 (sub-linha).
                    const freteConfirmar = checked && s != null && !s.erro && s.frete_a_confirmar;
                    const avisoDegrau =
                      checked && s && !s.erro && !s.frete_a_confirmar ? s.aviso_degrau : null;
                    const temDeal =
                      checked &&
                      s != null &&
                      !s.erro &&
                      s.deal_price !== null &&
                      !s.frete_a_confirmar;
                    const precisaInflar =
                      checked &&
                      s != null &&
                      !s.erro &&
                      !s.frete_a_confirmar &&
                      s.precisa_inflacao &&
                      s.preco_inflado !== null;
                    const inflouOk = inflados.has(item.item_id);
                    const inflouErro = inflacaoErros.has(item.item_id);
                    const inflandoAgora = inflacaoStatus === "rodando" && precisaInflar;
                    // Direção do ajuste do preço-base: "inflar" = subir o preço
                    // pra atingir U passo3; "desinflar" = baixar quando o preço
                    // atual já está acima de U (resíduo de inflação anterior).
                    // Termo certo evita confusão na UI ("↑infla" pra subir,
                    // "↓desinfla" pra descer).
                    const direcao: "inflar" | "desinflar" | null =
                      precisaInflar && s?.preco_inflado != null && item.preco != null
                        ? s.preco_inflado > item.preco
                          ? "inflar"
                          : "desinflar"
                        : null;
                    return (
                      <li
                        key={item.item_id}
                        className={cn("p-3 hover:bg-muted/40", checked && "bg-primary/5")}
                      >
                        <div className="flex items-start gap-3">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(item.item_id)}
                            className="mt-1.5 cursor-pointer"
                          />
                          {/* Identificação (cresce) */}
                          <button
                            type="button"
                            onClick={() => toggle(item.item_id)}
                            className="min-w-0 flex-1 cursor-pointer text-left"
                          >
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                              <span className="text-sm font-medium">{item.sku ?? "—"}</span>
                              <span className="font-mono text-[11px] text-muted-foreground">
                                {item.item_id}
                              </span>
                              {conflitos.map((p) => (
                                <Badge
                                  key={p.promotion_id}
                                  variant="secondary"
                                  className="px-1.5 py-0 text-[10px] font-normal text-muted-foreground"
                                >
                                  {p.nome ?? p.tipo}
                                  {p.status === "pending" && " (agendada)"}
                                </Badge>
                              ))}
                            </div>
                            {item.titulo && (
                              <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                                {item.titulo}
                              </p>
                            )}
                          </button>

                          {/* Coluna de preço (direita) */}
                          <div className="shrink-0 text-right text-xs tabular-nums">
                            {checked && s?.erro ? (
                              <span className="text-destructive">⚠ {s.erro}</span>
                            ) : freteConfirmar ? (
                              <>
                                {item.preco !== null && (
                                  <div className="text-muted-foreground">
                                    R$ {item.preco.toFixed(2)}
                                  </div>
                                )}
                                <div className="font-medium text-amber-700 dark:text-amber-300">
                                  ⚠ frete a confirmar
                                </div>
                              </>
                            ) : temDeal && s ? (
                              <>
                                {/* Linha 1 (original): só riscada se já inflamos (pós-inflação) */}
                                {item.preco !== null && (
                                  <div
                                    className={cn(
                                      "text-muted-foreground",
                                      (precisaInflar || inflouOk) && "line-through",
                                    )}
                                  >
                                    R$ {item.preco.toFixed(2)}
                                  </div>
                                )}
                                {/* Linha 2 (inflado): aparece se precisa inflar. Riscada após inflar OK. */}
                                {precisaInflar && s.preco_inflado !== null && (
                                  <div
                                    className={cn(
                                      "text-amber-700 dark:text-amber-300",
                                      inflouOk ? "line-through opacity-70" : "font-semibold",
                                    )}
                                  >
                                    R$ {s.preco_inflado.toFixed(2)}
                                    {inflandoAgora && (
                                      <Loader2 className="ml-1 inline h-3 w-3 animate-spin" />
                                    )}
                                  </div>
                                )}
                                {inflouErro && (
                                  <div className="text-destructive">⚠ falha ao ajustar preço</div>
                                )}
                                {/* Linha 3 (deal final): destacada quando inflação NÃO é necessária OU já concluída OK */}
                                {(!precisaInflar || inflouOk) && (
                                  <div className="font-semibold text-foreground">
                                    R$ {s.deal_price?.toFixed(2)}
                                  </div>
                                )}
                                {/* Sub-linha de métricas — só faz sentido quando o deal final está em destaque */}
                                {(!precisaInflar || inflouOk) && (
                                  <div className="text-[10px] text-muted-foreground">
                                    {s.desconto_pct !== null && `−${s.desconto_pct.toFixed(0)}%`}
                                    {s.margem_real_pct !== null &&
                                      ` · margem ${s.margem_real_pct.toFixed(0)}%`}
                                    {s.min_aplicado && (
                                      <span title="Desconto ajustado pro mínimo da campanha">
                                        {" "}
                                        · mín
                                      </span>
                                    )}
                                    {s.quebra_frete_gratis_aplicada && (
                                      <span
                                        className="ml-1 cursor-help"
                                        title={`Quebra de frete grátis (deal < R$ 79). Se o ML rejeitar, tenta R$ ${s.fallback_deal_price?.toFixed(2) ?? "?"} (≥ R$ 79, com frete grátis).`}
                                      >
                                        🚛
                                      </span>
                                    )}
                                  </div>
                                )}
                                {/* Indicador de ajuste do preço-base quando pendente —
                                    "↑infla" se sobe, "↓desinfla" se desce. Fica acima
                                    do deal pra dar contexto. */}
                                {precisaInflar && !inflouOk && !inflouErro && (
                                  <div
                                    className="text-[10px] text-amber-600 dark:text-amber-400"
                                    title={`Antes da campanha, ${
                                      direcao === "desinflar" ? "DESINFLA" : "infla"
                                    } o preço de listagem ${
                                      direcao === "desinflar" ? "PARA BAIXO" : "pra cima"
                                    }: R$ ${s.preco_inflado?.toFixed(2)}${s.margem_atual_pct !== null ? ` (margem atual ${s.margem_atual_pct.toFixed(1)}%)` : ""}`}
                                  >
                                    {direcao === "desinflar" ? "↓desinfla" : "↑infla"}
                                    {" → deal R$ "}
                                    {s.deal_price?.toFixed(2)}
                                  </div>
                                )}
                              </>
                            ) : (
                              <div className="text-muted-foreground">
                                {item.preco !== null ? `R$ ${item.preco.toFixed(2)}` : "—"}
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Aviso de degrau R$ 79 — ação opcional */}
                        {avisoDegrau && (
                          <button
                            type="button"
                            onClick={() => {
                              setSugestao((old) => {
                                const cur = old[item.item_id];
                                if (!cur) return old;
                                return {
                                  ...old,
                                  [item.item_id]: {
                                    ...cur,
                                    deal_price: avisoDegrau.deal_price,
                                    desconto_pct: avisoDegrau.desconto_pct,
                                    margem_real_pct: avisoDegrau.margem_pct,
                                    aviso_degrau: null,
                                  },
                                };
                              });
                            }}
                            className="ml-7 mt-1 rounded border border-amber-500/40 bg-amber-500/5 px-2 py-0.5 text-left text-[11px] text-amber-700 hover:bg-amber-500/15 dark:text-amber-300"
                          >
                            💡 Abaixo do degrau de R$ 79 — paga tarifa fixa R$ 6,75. Subir pra R${" "}
                            {avisoDegrau.deal_price.toFixed(2)}? Margem viraria{" "}
                            {avisoDegrau.margem_pct.toFixed(1)}% (desconto{" "}
                            {avisoDegrau.desconto_pct.toFixed(1)}%)
                          </button>
                        )}

                        {/* Frete a confirmar — cruza o degrau de R$ 79, frete não confiável */}
                        {freteConfirmar && (
                          <div className="ml-7 mt-1 rounded border border-amber-500/40 bg-amber-500/5 px-2 py-1 text-left text-[11px] text-amber-700 dark:text-amber-300">
                            ⚠ Este anúncio cruza o degrau de R$ 79 e o valor do frete não pode ser
                            confirmado pela API do ML. <strong>Não entra na campanha</strong> —
                            ajuste o preço manualmente depois.
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </>
        )}

        {algumNovoSemCusto && (
          <div className="shrink-0 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-700 dark:text-amber-300">
            Alguns SKUs não têm custo cadastrado. Eles não serão adicionados — ajuste o{" "}
            <code>custos.xlsx</code> ou os overrides do perfil e recalcule.
          </div>
        )}

        {skusComInflacao.length > 0 && (
          <div className="shrink-0 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-700 dark:text-amber-300">
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                checked={aplicarInflacao}
                onChange={(e) => setAplicarInflacao(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                <strong>{skusComInflacao.length}</strong>{" "}
                {skusComInflacao.length === 1 ? "SKU precisa" : "SKUs precisam"} ajustar o
                preço-base pra atingir a margem alvo
                {skusVaoSubir.length > 0 && skusVaoDescer.length > 0 ? (
                  <>
                    {" "}
                    ({skusVaoSubir.length} ↑inflar pra cima, {skusVaoDescer.length} ↓desinflar pra
                    baixo)
                  </>
                ) : skusVaoDescer.length > 0 ? (
                  <>
                    {" "}
                    (todos vão ↓desinflar — preço atual está ACIMA do U passo3, resíduo de inflação
                    anterior)
                  </>
                ) : (
                  <> (todos vão ↑inflar pra cima)</>
                )}
                . Ao confirmar, o app vai{" "}
                <strong>atualizar o preço de listagem desses anúncios no ML</strong> (Fase 1) antes
                de adicioná-los à campanha. Desmarque pra adicionar à campanha sem mexer no
                preço-base.
              </span>
            </label>
          </div>
        )}

        {mlbsFreteAConfirmar.length > 0 && (
          <div className="flex shrink-0 items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-700 dark:text-amber-300">
            <span>
              <strong>{mlbsFreteAConfirmar.length}</strong>{" "}
              {mlbsFreteAConfirmar.length === 1 ? "anúncio" : "anúncios"} com frete a confirmar
              (cruza o degrau de R$ 79). <strong>Não entrarão na campanha</strong> — ajuste o preço
              na mão depois.
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="shrink-0 border-amber-500/40"
              onClick={handleDownloadFreteAConfirmar}
            >
              Baixar .txt dos MLBs
            </Button>
          </div>
        )}

        <DialogFooter className="flex shrink-0 flex-col gap-3 border-t pt-4 sm:flex-row sm:items-end sm:justify-between sm:space-x-0">
          <div className="flex items-end gap-2">
            <div className="space-y-1">
              <label htmlFor="margem-alvo" className="text-xs font-medium">
                Margem líquida alvo
              </label>
              <div className="flex items-center gap-1.5">
                <Input
                  id="margem-alvo"
                  type="number"
                  min={0}
                  max={95}
                  step={1}
                  value={margemAlvoPct}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v) && v >= 0 && v <= 95) {
                      setMargemAlvoPct(v);
                      setSugestao({});
                      setInflacaoStatus("pendente");
                      setInflados(new Set());
                      setInflacaoErros(new Set());
                    }
                  }}
                  className="h-9 w-20 text-right tabular-nums"
                />
                <span className="text-sm text-muted-foreground">%</span>
              </div>
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={handleCalcular}
              disabled={
                novosSelecionados.length === 0 ||
                sugestaoMutation.isPending ||
                sugestaoPreviewMutation.isPending
              }
            >
              {sugestaoMutation.isPending || sugestaoPreviewMutation.isPending ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Calculando…
                </>
              ) : (
                "Calcular descontos"
              )}
            </Button>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={handleInflar}
              disabled={
                !todosNovosTeemSugestao ||
                inflacaoStatus !== "pendente" ||
                sugestaoMutation.isPending ||
                sugestaoPreviewMutation.isPending
              }
              title={
                !todosNovosTeemSugestao
                  ? "Aperte 'Calcular descontos' antes de inflar"
                  : inflacaoStatus === "concluida"
                    ? "Preços já inflados — aperte Confirmar"
                    : inflacaoStatus === "rodando"
                      ? "Inflação em andamento…"
                      : undefined
              }
            >
              {inflacaoStatus === "rodando" ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Inflando…
                </>
              ) : inflacaoStatus === "concluida" ? (
                "Preços inflados ✓"
              ) : (
                "Inflar preços"
              )}
            </Button>
            <Button
              type="button"
              onClick={handleConfirmar}
              disabled={
                !todosNovosTeemSugestao ||
                confirmandoSalvar ||
                sugestaoMutation.isPending ||
                sugestaoPreviewMutation.isPending
              }
              title={
                !todosNovosTeemSugestao
                  ? "Aperte 'Calcular descontos' antes de confirmar"
                  : undefined
              }
            >
              {confirmandoSalvar ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Salvando…
                </>
              ) : (
                <>Confirmar {selecionados.size > 0 && `(${selecionados.size})`}</>
              )}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function promosNoPeriodo(
  promocoes: PromoNoItem[],
  periodo: { data_inicio: string; data_fim: string },
): PromoNoItem[] {
  return promocoes.filter((p) => {
    if (!p.start_date || !p.finish_date) return true;
    return p.finish_date >= periodo.data_inicio && p.start_date <= periodo.data_fim;
  });
}
