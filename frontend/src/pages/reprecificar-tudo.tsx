import {
  AlertCircle,
  ArrowLeft,
  CheckSquare,
  History,
  Loader2,
  Search,
  Square,
  Undo2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { useSkusComPromocoes } from "@/features/listings/hooks";
import { useProfile } from "@/features/profiles/hooks";
import {
  useAplicarRepricingMassa,
  useRepricingSessoes,
  useReverterSessaoRepricing,
  useSimularRepricingMassa,
} from "@/features/repricing-massa/hooks";
import { cn } from "@/lib/utils";

import type { RepricingMassaSimulateItem } from "@/api/repricing-massa";

/**
 * Página "Reprecificar tudo" — pra lojas sem campanha.
 *
 * Fluxo:
 * 1. Carrega lista de SKUs (mesma fonte do seletor de campanha)
 * 2. User filtra + seleciona não-problemáticos (com SKU, com preço)
 * 3. Clica "Simular" → backend calcula P passo3 (margem alvo Q2 do perfil)
 *    por item e retorna preço atual, preço novo, margem prevista, erro
 * 4. User revisa tabela. Itens com erro ficam visíveis mas não aplicam.
 * 5. Clica "Aplicar reprecificação" → backend faz PUT /items em massa.
 *
 * A margem alvo NÃO é input — usa Q2/R2 do perfil (escada Filosofia B
 * passo3 evita frete grátis quando possível, com piso R2).
 */
export function ReprecificarTudoPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const profileId = id ?? "";

  const { data: profile } = useProfile(profileId);
  const { data, isPending, isError, error } = useSkusComPromocoes(profileId, true, {
    enabled: !!profileId,
  });

  const simularMutation = useSimularRepricingMassa();
  const aplicarMutation = useAplicarRepricingMassa();
  const sessoesQuery = useRepricingSessoes(profileId);
  const reverterMutation = useReverterSessaoRepricing();
  const [confirmandoReverter, setConfirmandoReverter] = useState<string | null>(null);

  const [busca, setBusca] = useState("");
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [simulacao, setSimulacao] = useState<Map<string, RepricingMassaSimulateItem>>(new Map());
  const [aplicados, setAplicados] = useState<Set<string>>(new Set());

  const itemsFiltrados = useMemo(() => {
    if (!data?.results) return [];
    const buscaLower = busca.trim().toLowerCase();
    if (!buscaLower) return data.results;
    return data.results.filter(
      (it) =>
        it.item_id.toLowerCase().includes(buscaLower) ||
        (it.sku?.toLowerCase().includes(buscaLower) ?? false) ||
        (it.titulo?.toLowerCase().includes(buscaLower) ?? false),
    );
  }, [data, busca]);

  // Items "não-problemáticos" pra seleção em lote — têm SKU E preço > 0.
  // Sem SKU = não dá pra cruzar com custos.xlsx; sem preço = item sem dados.
  const naoProblematicos = useMemo(
    () => itemsFiltrados.filter((it) => it.sku && it.preco && it.preco > 0),
    [itemsFiltrados],
  );

  const toggle = (itemId: string) => {
    setSelecionados((old) => {
      const next = new Set(old);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
    // Simulação fica stale ao mudar seleção
    setSimulacao(new Map());
    setAplicados(new Set());
  };

  const marcarNaoProblematicos = () => {
    setSelecionados((old) => {
      const next = new Set(old);
      for (const it of naoProblematicos) next.add(it.item_id);
      return next;
    });
    setSimulacao(new Map());
    setAplicados(new Set());
  };

  const desmarcarTodos = () => {
    setSelecionados(new Set());
    setSimulacao(new Map());
    setAplicados(new Set());
  };

  const handleSimular = async () => {
    if (selecionados.size === 0) return;
    try {
      const resp = await simularMutation.mutateAsync({
        profileId,
        itemIds: [...selecionados],
      });
      const mapa = new Map<string, RepricingMassaSimulateItem>();
      for (const r of resp.results) mapa.set(r.item_id, r);
      setSimulacao(mapa);
      setAplicados(new Set());
    } catch (err) {
      toast.error("Falha ao simular", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  // Itens aptos pra aplicar = simulados, sem erro, preço novo definido E
  // diferente do atual. Sem mudança = sem PUT (ainda assim aceitamos por
  // idempotência no backend, mas filtra na UI pra deixar claro).
  const aptosParaAplicar = useMemo(() => {
    const out: RepricingMassaSimulateItem[] = [];
    for (const item of simulacao.values()) {
      if (item.erro) continue;
      if (item.preco_novo === null || item.preco_atual === null) continue;
      out.push(item);
    }
    return out;
  }, [simulacao]);

  const handleAplicar = async () => {
    if (aptosParaAplicar.length === 0) return;
    const precos: Record<string, number> = {};
    for (const it of aptosParaAplicar) {
      if (it.preco_novo !== null) precos[it.item_id] = it.preco_novo;
    }
    try {
      const resp = await aplicarMutation.mutateAsync({ profileId, precos });
      setAplicados(new Set([...resp.aplicados, ...resp.ja_no_preco]));

      const partes: string[] = [];
      if (resp.aplicados.length > 0) {
        partes.push(
          `${resp.aplicados.length} reprecificado${resp.aplicados.length !== 1 ? "s" : ""}`,
        );
      }
      if (resp.ja_no_preco.length > 0) {
        partes.push(`${resp.ja_no_preco.length} já no preço`);
      }
      if (resp.erros.length > 0) {
        partes.push(`${resp.erros.length} falhou`);
      }
      const descricao = partes.join(" · ");
      if (resp.erros.length === 0) {
        toast.success("Reprecificação aplicada", { description: descricao });
      } else {
        toast.error("Reprecificação com avisos", {
          description: `${descricao}. Falhas: ${resp.erros
            .slice(0, 2)
            .map((e) => `${e.item_id} (${e.erro})`)
            .join(", ")}`,
        });
      }
    } catch (err) {
      toast.error("Falha ao aplicar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const margemAlvoPct = profile
    ? Math.round((profile.config.margem_alvo_campanha ?? 0.2) * 100)
    : 20;
  const margemMinimaPct = profile ? Math.round((profile.config.margem_minima ?? 0.15) * 100) : 15;

  return (
    <div className="container mx-auto max-w-6xl space-y-4 py-6">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${profileId}`)}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>
      </div>

      <div>
        <h1 className="text-2xl font-semibold">Reprecificar tudo</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Calcula o preço ideal de cada anúncio (modelo passo3, margem alvo{" "}
          <strong>{margemAlvoPct}%</strong> · piso <strong>{margemMinimaPct}%</strong>) e aplica
          direto no preço-base via PUT no ML — sem campanha. Use pra lojas que não podem participar
          de campanhas.
        </p>
      </div>

      {/* Última execução: mostra a sessão mais recente com botão de reverter.
          Persistida no DB, então sobrevive a restart do backend. */}
      {(() => {
        const ultima = sessoesQuery.data?.sessions[0];
        if (!ultima) return null;
        const dataLocal = new Date(ultima.criado_em_iso).toLocaleString("pt-BR", {
          dateStyle: "short",
          timeStyle: "short",
        });
        const jaRevertida = ultima.revertido_em_iso !== null;
        return (
          <Card className={cn(!jaRevertida && "border-warning/40 bg-warning/5")}>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="flex items-start gap-2 text-sm">
                <History className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <div>
                  <p className="font-medium">
                    Última execução: <span className="tabular-nums">{ultima.qtd_itens}</span> SKU
                    {ultima.qtd_itens !== 1 ? "s" : ""} reprecificados em {dataLocal}
                  </p>
                  {jaRevertida ? (
                    <p className="text-xs text-muted-foreground">
                      Já revertida em{" "}
                      {new Date(ultima.revertido_em_iso ?? "").toLocaleString("pt-BR", {
                        dateStyle: "short",
                        timeStyle: "short",
                      })}
                      .
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      Preços anteriores ficam guardados — dá pra reverter pra deixar tudo como
                      antes.
                    </p>
                  )}
                </div>
              </div>
              {!jaRevertida && (
                <div className="flex items-center gap-2">
                  {confirmandoReverter === ultima.session_id ? (
                    <>
                      <span className="text-xs font-medium text-warning">
                        Confirmar? Vai aplicar PUT no ML.
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setConfirmandoReverter(null)}
                        disabled={reverterMutation.isPending}
                      >
                        Cancelar
                      </Button>
                      <Button
                        variant="default"
                        size="sm"
                        onClick={async () => {
                          try {
                            const r = await reverterMutation.mutateAsync({
                              profileId,
                              sessionId: ultima.session_id,
                            });
                            const partes: string[] = [];
                            if (r.revertidos.length > 0) {
                              partes.push(`${r.revertidos.length} revertidos`);
                            }
                            if (r.ja_no_preco.length > 0) {
                              partes.push(`${r.ja_no_preco.length} já no preço antigo`);
                            }
                            if (r.erros.length > 0) {
                              partes.push(`${r.erros.length} erros`);
                            }
                            toast.success("Reversão aplicada", {
                              description: partes.join(" · "),
                            });
                            setConfirmandoReverter(null);
                          } catch (err) {
                            toast.error("Falha ao reverter", {
                              description: err instanceof Error ? err.message : "Erro desconhecido",
                            });
                          }
                        }}
                        disabled={reverterMutation.isPending}
                      >
                        {reverterMutation.isPending ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <Undo2 className="mr-2 h-4 w-4" />
                        )}
                        Sim, reverter
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setConfirmandoReverter(ultima.session_id)}
                    >
                      <Undo2 className="mr-2 h-4 w-4" />
                      Reverter esta execução
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        );
      })()}

      {isPending && (
        <Card>
          <CardContent className="space-y-2 py-6">
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </CardContent>
        </Card>
      )}

      {isError && (
        <Card className="border-destructive/40">
          <CardContent className="flex items-start gap-2 py-4 text-sm">
            <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
            <div>
              <p className="font-medium">Não foi possível carregar anúncios</p>
              <p className="text-muted-foreground">
                {error instanceof Error ? error.message : "Erro desconhecido"}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              1. Selecionar anúncios{" "}
              <span className="ml-1 text-sm font-normal text-muted-foreground tabular-nums">
                ({selecionados.size} de {data.total})
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Buscar por SKU, MLB ou título…"
                value={busca}
                onChange={(e) => setBusca(e.target.value)}
                className="pl-9"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={marcarNaoProblematicos}
                disabled={naoProblematicos.length === 0}
                title="Marca anúncios com SKU + preço (exclui itens sem SKU ou sem dados, que não podem ser reprecificados)"
              >
                <CheckSquare className="h-3.5 w-3.5" />
                Marcar todos sem problemas ({naoProblematicos.length})
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={desmarcarTodos}
                disabled={selecionados.size === 0}
              >
                <Square className="h-3.5 w-3.5" />
                Desmarcar todos
              </Button>
            </div>

            <div className="max-h-[400px] overflow-y-auto rounded-md border">
              {itemsFiltrados.length === 0 ? (
                <div className="py-12 text-center text-sm text-muted-foreground">
                  Nenhum anúncio encontrado.
                </div>
              ) : (
                <ul className="divide-y">
                  {itemsFiltrados.map((item) => {
                    const checked = selecionados.has(item.item_id);
                    const semSku = !item.sku;
                    const semPreco = !item.preco || item.preco <= 0;
                    const problematico = semSku || semPreco;
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
                            disabled={problematico}
                            className="mt-1.5 cursor-pointer disabled:cursor-not-allowed"
                          />
                          <button
                            type="button"
                            onClick={() => !problematico && toggle(item.item_id)}
                            disabled={problematico}
                            className="min-w-0 flex-1 cursor-pointer text-left disabled:cursor-not-allowed"
                          >
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                              <span className="text-sm font-medium">
                                {item.sku ?? (
                                  <span className="italic text-muted-foreground">sem SKU</span>
                                )}
                              </span>
                              <span className="font-mono text-[11px] text-muted-foreground">
                                {item.item_id}
                              </span>
                              {problematico && (
                                <Badge
                                  variant="outline"
                                  className="border-amber-500/40 px-1.5 py-0 text-[10px] font-normal text-amber-700 dark:text-amber-300"
                                >
                                  {semSku ? "sem SKU" : "sem preço"}
                                </Badge>
                              )}
                            </div>
                            {item.titulo && (
                              <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                                {item.titulo}
                              </p>
                            )}
                          </button>
                          <div className="shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                            {item.preco !== null ? `R$ ${item.preco.toFixed(2)}` : "—"}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="flex justify-end">
              <Button
                type="button"
                onClick={handleSimular}
                disabled={selecionados.size === 0 || simularMutation.isPending}
              >
                {simularMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Calculando…
                  </>
                ) : (
                  `Simular reprecificação (${selecionados.size})`
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {simulacao.size > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              2. Revisar simulação
              <span className="ml-1 text-sm font-normal text-muted-foreground tabular-nums">
                ({aptosParaAplicar.length} aptos · {simulacao.size - aptosParaAplicar.length} com
                erro)
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="max-h-[500px] overflow-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="px-3 py-2">SKU</th>
                    <th className="px-3 py-2">MLB</th>
                    <th className="px-3 py-2 text-right">Atual</th>
                    <th className="px-3 py-2 text-right">Novo</th>
                    <th className="px-3 py-2 text-right">Δ</th>
                    <th className="px-3 py-2 text-right">Margem</th>
                    <th className="px-3 py-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...simulacao.values()].map((it) => {
                    const aplicado = aplicados.has(it.item_id);
                    const delta =
                      it.preco_novo !== null && it.preco_atual !== null
                        ? it.preco_novo - it.preco_atual
                        : null;
                    const deltaPct =
                      delta !== null && it.preco_atual ? (delta / it.preco_atual) * 100 : null;
                    return (
                      <tr
                        key={it.item_id}
                        className={cn(
                          "border-b transition-colors",
                          it.erro
                            ? "bg-destructive/5"
                            : aplicado
                              ? "bg-success/5"
                              : "hover:bg-muted/30",
                        )}
                      >
                        <td className="px-3 py-2 font-mono text-xs">
                          {it.sku ?? <span className="italic text-muted-foreground">—</span>}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">{it.item_id}</td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {it.preco_atual !== null ? `R$ ${it.preco_atual.toFixed(2)}` : "—"}
                        </td>
                        <td className="px-3 py-2 text-right font-semibold tabular-nums">
                          {it.preco_novo !== null ? `R$ ${it.preco_novo.toFixed(2)}` : "—"}
                        </td>
                        <td
                          className={cn(
                            "px-3 py-2 text-right text-xs tabular-nums",
                            delta !== null && delta > 0 && "text-success",
                            delta !== null && delta < 0 && "text-destructive",
                          )}
                        >
                          {delta !== null
                            ? `${delta >= 0 ? "+" : ""}R$ ${delta.toFixed(2)}${deltaPct !== null ? ` (${deltaPct >= 0 ? "+" : ""}${deltaPct.toFixed(1)}%)` : ""}`
                            : "—"}
                        </td>
                        <td className="px-3 py-2 text-right text-xs tabular-nums">
                          {it.margem_nova_pct !== null ? `${it.margem_nova_pct.toFixed(1)}%` : "—"}
                          {it.margem_atual_pct !== null && (
                            <span className="ml-1 text-muted-foreground">
                              (era {it.margem_atual_pct.toFixed(1)}%)
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-xs">
                          {aplicado ? (
                            <Badge
                              variant="outline"
                              className="border-success/40 px-1.5 py-0 text-success"
                            >
                              ✓ aplicado
                            </Badge>
                          ) : it.erro ? (
                            <span className="text-destructive" title={it.erro}>
                              ⚠ {it.erro.length > 50 ? `${it.erro.slice(0, 50)}…` : it.erro}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">pronto</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">
                Itens com erro NÃO serão reprecificados. Revise antes de aplicar — o PUT muda o
                preço-base no ML imediatamente.
              </p>
              <Button
                type="button"
                onClick={handleAplicar}
                disabled={
                  aptosParaAplicar.length === 0 || aplicarMutation.isPending || aplicados.size > 0
                }
                title={
                  aplicados.size > 0
                    ? "Reprecificação já foi aplicada — simule de novo pra repetir"
                    : undefined
                }
              >
                {aplicarMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Aplicando…
                  </>
                ) : (
                  `Aplicar reprecificação (${aptosParaAplicar.length})`
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
