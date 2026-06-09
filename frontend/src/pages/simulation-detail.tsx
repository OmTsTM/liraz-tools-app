import {
  AlertCircle,
  ArrowLeft,
  ArrowUpRight,
  Download,
  Loader2,
  Pause,
  Play,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { simulationsApi } from "@/api/simulations";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { type ColumnDef, ColumnToggle, useColumnVisibility } from "@/components/ui/column-toggle";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TablePagination } from "@/components/ui/table-pagination";
import { toast } from "@/components/ui/toaster";
import { useProfile } from "@/features/profiles/hooks";
import { EditableMarginCell } from "@/features/simulations/editable-margin-cell";
import { useResumeSimulation, useSimulation } from "@/features/simulations/hooks";
import {
  STICKY_CELL_CLASS,
  type STICKY_COL_OFFSETS,
  STICKY_COL_WIDTHS,
  STICKY_CONTAINER_CLASS,
  STICKY_HEADER_CLASS,
  STICKY_HEADER_PINNED_CLASS,
  stickyColStyle,
} from "@/lib/sticky-table";
import { cn } from "@/lib/utils";
import type { RepricingException, RepricingLineItem, SimulationState } from "@/types/api";

const PAGE_SIZE_SIMULATIONS = 300;

const BRL = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});
const PCT = new Intl.NumberFormat("pt-BR", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const fmtBRL = (v: number | null | undefined) => (v == null ? "—" : BRL.format(v));
const fmtPctFromNumber = (v: number | null | undefined) => (v == null ? "—" : PCT.format(v / 100));

// ─── Definição das colunas da tabela de simulações ──────────────────────────
const SIM_COLUMNS: ColumnDef[] = [
  { key: "sku", label: "SKU" },
  { key: "mlb", label: "MLB" },
  { key: "titulo", label: "Título" },
  { key: "custo", label: "Custo" },
  { key: "margem_atual", label: "Margem atual" },
  { key: "preco_atual", label: "Preço atual" },
  { key: "preco_novo", label: "Preço novo" },
  { key: "acao", label: "Ação Fase 1" },
  { key: "comissao", label: "Comissão" },
  { key: "tarifa_fixa", label: "Tarifa Fixa" },
  { key: "frete", label: "Frete" },
  { key: "margem_liquida", label: "Margem líquida" },
  { key: "preco_campanha", label: "Preço na Campanha" },
  { key: "desconto", label: "Desconto" },
  { key: "liq_projetado", label: "Líq. projetado" },
  // Leva 5.8 — info de promoções ativas no ML
  { key: "em_campanha", label: "Em campanha?" },
  { key: "nome_campanha", label: "Nome da campanha" },
];

export function SimulationDetailPage() {
  const { id, sim_id } = useParams<{ id: string; sim_id: string }>();
  const navigate = useNavigate();

  const { data: profile } = useProfile(id);
  const { data: simulation, isPending, isError } = useSimulation(id, sim_id);
  const resume = useResumeSimulation();

  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"simulados" | "excecoes">("simulados");
  const [currentPage, setCurrentPage] = useState(1);
  const [visibleColumns, setVisibleColumns] = useColumnVisibility(
    SIM_COLUMNS,
    "simulation_table_columns_v1",
  );

  // Reseta pra página 1 quando filtros mudam (busca ou troca de tab).
  // O effect intencionalmente NÃO depende de setCurrentPage (estável).
  // biome-ignore lint/correctness/useExhaustiveDependencies: dispara só quando filtros mudam
  useEffect(() => {
    setCurrentPage(1);
  }, [search, tab]);

  const filteredSims = useMemo(() => {
    if (!simulation) return [];
    if (!search) return simulation.simulacoes;
    const q = search.toLowerCase();
    return simulation.simulacoes.filter(
      (s) =>
        (s.sku ?? "").toLowerCase().includes(q) ||
        s.item_id.toLowerCase().includes(q) ||
        (s.titulo ?? "").toLowerCase().includes(q),
    );
  }, [simulation, search]);

  const filteredExceptions = useMemo(() => {
    if (!simulation) return [];
    if (!search) return simulation.excecoes;
    const q = search.toLowerCase();
    return simulation.excecoes.filter(
      (e) =>
        (e.sku ?? "").toLowerCase().includes(q) ||
        e.item_id.toLowerCase().includes(q) ||
        (e.titulo ?? "").toLowerCase().includes(q) ||
        e.tipo.toLowerCase().includes(q),
    );
  }, [simulation, search]);

  const handleDownloadXLSX = () => {
    if (!id || !sim_id) return;
    window.open(simulationsApi.getXlsxDownloadUrl(id, sim_id), "_blank");
  };

  const handleResume = async () => {
    if (!id || !sim_id) return;
    try {
      await resume.mutateAsync({ profileId: id, simulationId: sim_id });
      toast.success("Simulação retomada");
    } catch (err) {
      toast.error("Falha ao retomar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  if (isPending) return <LoadingState />;

  if (isError || !simulation) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${id}/simulations`)}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>
        <Card>
          <CardContent className="p-8 text-center text-sm text-muted-foreground">
            Simulação não encontrada.
          </CardContent>
        </Card>
      </div>
    );
  }

  const progress =
    simulation.total_ativos_analisados > 0
      ? simulation.total_processados / simulation.total_ativos_analisados
      : 0;

  const isTerminal =
    simulation.estado === "completed" ||
    simulation.estado === "failed" ||
    simulation.estado === "interrupted";

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${id}/simulations`)}>
        <ArrowLeft className="h-4 w-4" />
        Voltar pra lista
      </Button>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-2xl font-semibold tracking-tight">Simulação</h2>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{simulation.simulation_id}</p>
          <p className="text-sm text-muted-foreground">
            {profile?.name} · {new Date(simulation.criado_em).toLocaleString("pt-BR")}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <StateBadge state={simulation.estado} />
          {(simulation.estado === "interrupted" || simulation.estado === "failed") && (
            <Button variant="outline" size="sm" onClick={handleResume} disabled={resume.isPending}>
              {resume.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              Retomar
            </Button>
          )}
          {isTerminal && (
            <Button variant="outline" size="sm" onClick={handleDownloadXLSX}>
              <Download className="h-4 w-4" />
              Baixar XLSX
            </Button>
          )}
        </div>
      </div>

      {/* Erro fatal se houver */}
      {simulation.erro_fatal && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex gap-3 p-4">
            <AlertCircle className="h-5 w-5 shrink-0 text-destructive" />
            <div>
              <p className="font-medium text-destructive">Erro fatal</p>
              <p className="mt-0.5 text-sm">{simulation.erro_fatal}</p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Métricas */}
      <div className="grid gap-3 sm:grid-cols-4">
        <SummaryCard
          label="Total de anúncios"
          value={simulation.total_ativos_analisados.toString()}
        />
        <SummaryCard
          label="Simulados"
          value={simulation.total_simulados.toString()}
          tone="success"
        />
        <SummaryCard
          label="Exceções"
          value={simulation.total_excecoes.toString()}
          tone={simulation.total_excecoes > 0 ? "warning" : "default"}
        />
        <SummaryCard
          label={simulation.estado === "running" ? "Progresso" : "Processados"}
          value={
            simulation.estado === "running"
              ? `${Math.round(progress * 100)}%`
              : simulation.total_processados.toString()
          }
        />
      </div>

      {/* Barra de progresso quando running */}
      {simulation.estado === "running" && (
        <Card>
          <CardContent className="space-y-2 p-4">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">Calculando taxas e simulando preços...</span>
              <span className="text-muted-foreground">
                {simulation.total_processados}/{simulation.total_ativos_analisados}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress * 100}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Polling automático a cada 2 segundos. Pode fechar a aba e voltar depois — a simulação
              continua rodando no backend.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Tabs + busca */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 rounded-md border bg-muted/30 p-1">
          <button
            type="button"
            onClick={() => setTab("simulados")}
            className={cn(
              "rounded px-3 py-1.5 text-xs font-medium transition-colors",
              tab === "simulados"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            Simulados ({simulation.total_simulados})
          </button>
          <button
            type="button"
            onClick={() => setTab("excecoes")}
            className={cn(
              "rounded px-3 py-1.5 text-xs font-medium transition-colors",
              tab === "excecoes"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            Exceções ({simulation.total_excecoes})
          </button>
        </div>

        <Input
          placeholder="Buscar por SKU, MLB ou título..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-md flex-1"
        />

        {tab === "simulados" && (
          <ColumnToggle
            columns={SIM_COLUMNS}
            visible={visibleColumns}
            onChange={setVisibleColumns}
            storageKey="simulation_table_columns_v1"
          />
        )}
      </div>

      {/* Tabela */}
      {tab === "simulados" && id && sim_id && (
        <SimulacoesTable
          items={filteredSims}
          total={simulation.total_simulados}
          visible={visibleColumns}
          profileId={id}
          simulationId={sim_id}
          margemCampanhaPadrao={simulation.margem_alvo_campanha * 100}
          currentPage={currentPage}
          onPageChange={setCurrentPage}
          pageSize={PAGE_SIZE_SIMULATIONS}
        />
      )}
      {tab === "excecoes" && (
        <ExceptionsTable items={filteredExceptions} total={simulation.total_excecoes} />
      )}

      {/* Config usada */}
      <div className="text-xs text-muted-foreground">
        CEP <span className="font-mono">{simulation.cep_destino}</span> · Imposto{" "}
        {fmtPctFromNumber(simulation.aliquota_imposto * 100)} · Margem alvo (Q2){" "}
        {fmtPctFromNumber(simulation.margem_alvo_campanha * 100)} · Paralelismo{" "}
        {simulation.concorrencia_usada}
        {simulation.retomado_de_checkpoint && " · Retomada de checkpoint"}
      </div>
    </div>
  );
}

// ─── Tabela de simulados ───────────────────────────────────────────────────

function SimulacoesTable({
  items,
  total,
  visible,
  profileId,
  simulationId,
  margemCampanhaPadrao,
  currentPage,
  onPageChange,
  pageSize,
}: {
  items: RepricingLineItem[];
  total: number;
  visible: Set<string>;
  profileId: string;
  simulationId: string;
  /** Margem alvo da campanha da simulação (em %, ex: 20 pra 20%). */
  margemCampanhaPadrao: number;
  currentPage: number;
  onPageChange: (page: number) => void;
  pageSize: number;
}) {
  if (total === 0) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-sm text-muted-foreground">
          Nenhum anúncio simulado ainda.
        </CardContent>
      </Card>
    );
  }

  const show = (key: string) => visible.has(key);

  // Aplica paginação localmente — items já foram filtrados pelo search
  const totalFiltered = items.length;
  const startIdx = (currentPage - 1) * pageSize;
  const pagedItems = items.slice(startIdx, startIdx + pageSize);

  // Sticky columns: SKU (left:0), MLB (left:96), Título (left:224)
  // Quando uma delas está escondida, as seguintes deslizam pra esquerda
  // pra manter a sequência (calculado em runtime, mais simples).
  const stickyChain: { key: keyof typeof STICKY_COL_OFFSETS; width: number }[] = [];
  if (show("sku")) stickyChain.push({ key: "sku", width: STICKY_COL_WIDTHS.sku });
  if (show("mlb")) stickyChain.push({ key: "mlb", width: STICKY_COL_WIDTHS.mlb });
  if (show("titulo")) stickyChain.push({ key: "titulo", width: STICKY_COL_WIDTHS.titulo });

  // Mapa key → offset acumulado
  const offsetMap = new Map<string, number>();
  let acc = 0;
  for (const c of stickyChain) {
    offsetMap.set(c.key, acc);
    acc += c.width;
  }
  const offsetFor = (key: string) => offsetMap.get(key) ?? 0;

  return (
    <Card className="overflow-hidden">
      <div className={STICKY_CONTAINER_CLASS}>
        <table className="w-full text-sm">
          <thead className="border-b">
            <tr>
              {show("sku") && (
                <th
                  className={cn(STICKY_HEADER_PINNED_CLASS, "px-3 py-2.5 text-left font-medium")}
                  style={stickyColStyle(offsetFor("sku"), STICKY_COL_WIDTHS.sku)}
                >
                  SKU
                </th>
              )}
              {show("mlb") && (
                <th
                  className={cn(STICKY_HEADER_PINNED_CLASS, "px-3 py-2.5 text-left font-medium")}
                  style={stickyColStyle(offsetFor("mlb"), STICKY_COL_WIDTHS.mlb)}
                >
                  MLB
                </th>
              )}
              {show("titulo") && (
                <th
                  className={cn(STICKY_HEADER_PINNED_CLASS, "px-3 py-2.5 text-left font-medium")}
                  style={stickyColStyle(offsetFor("titulo"), STICKY_COL_WIDTHS.titulo)}
                >
                  Título
                </th>
              )}
              {show("custo") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}>
                  Custo
                </th>
              )}
              {show("margem_atual") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}>
                  Margem atual
                </th>
              )}
              {show("preco_atual") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}>
                  Preço atual
                </th>
              )}
              {show("preco_novo") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}>
                  Preço novo
                </th>
              )}
              {show("acao") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-left font-medium")}>
                  Ação
                </th>
              )}
              {show("comissao") && (
                <th
                  className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}
                  title="Comissão sobre o Preço novo"
                >
                  Comissão
                </th>
              )}
              {show("tarifa_fixa") && (
                <th
                  className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}
                  title="Tarifa fixa do ML no Preço novo"
                >
                  Tarifa Fixa
                </th>
              )}
              {show("frete") && (
                <th
                  className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}
                  title="Frete vendedor estimado no Preço novo"
                >
                  Frete
                </th>
              )}
              {show("margem_liquida") && (
                <th
                  className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}
                  title="Margem líquida na campanha. Clique no lápis pra customizar."
                >
                  Margem líquida
                </th>
              )}
              {show("preco_campanha") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}>
                  Preço na Campanha
                </th>
              )}
              {show("desconto") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}>
                  Desconto
                </th>
              )}
              {show("liq_projetado") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-right font-medium")}>
                  Líq. projetado
                </th>
              )}
              {show("em_campanha") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-center font-medium")}>
                  Em campanha?
                </th>
              )}
              {show("nome_campanha") && (
                <th className={cn(STICKY_HEADER_CLASS, "px-3 py-2.5 text-left font-medium")}>
                  Nome da campanha
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {pagedItems.map((s) => {
              const precoAtual = s.taxas_atual.preco;
              const precoChange = s.preco_novo - precoAtual;
              const comissaoNovo = s.taxas_novo.tarifa_classico || s.taxas_novo.tarifa_premium || 0;

              return (
                <tr key={s.item_id} className="border-b transition-colors hover:bg-muted/30">
                  {show("sku") && (
                    <td
                      className={cn(STICKY_CELL_CLASS, "px-3 py-2 font-mono text-xs")}
                      style={stickyColStyle(offsetFor("sku"), STICKY_COL_WIDTHS.sku)}
                    >
                      {s.sku ?? <span className="italic text-muted-foreground">sem SKU</span>}
                    </td>
                  )}
                  {show("mlb") && (
                    <td
                      className={cn(STICKY_CELL_CLASS, "px-3 py-2")}
                      style={stickyColStyle(offsetFor("mlb"), STICKY_COL_WIDTHS.mlb)}
                    >
                      <a
                        href={`https://produto.mercadolivre.com.br/${s.item_id.replace("MLB", "MLB-")}`}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-xs hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {s.item_id}
                        <ArrowUpRight className="ml-1 inline h-3 w-3" />
                      </a>
                    </td>
                  )}
                  {show("titulo") && (
                    <td
                      className={cn(STICKY_CELL_CLASS, "truncate px-3 py-2")}
                      style={stickyColStyle(offsetFor("titulo"), STICKY_COL_WIDTHS.titulo)}
                      title={s.titulo ?? ""}
                    >
                      {s.titulo ?? "—"}
                    </td>
                  )}
                  {show("custo") && (
                    <td className="px-3 py-2 text-right tabular-nums">{fmtBRL(s.custo)}</td>
                  )}
                  {show("margem_atual") && (
                    <td
                      className={cn(
                        "px-3 py-2 text-right font-medium tabular-nums",
                        s.margem_atual_pct < 0 && "text-destructive",
                        s.margem_atual_pct >= 0 && s.margem_atual_pct < 15 && "text-warning",
                        s.margem_atual_pct >= 30 && "text-success",
                      )}
                    >
                      {fmtPctFromNumber(s.margem_atual_pct)}
                    </td>
                  )}
                  {show("preco_atual") && (
                    <td className="px-3 py-2 text-right tabular-nums">{fmtBRL(precoAtual)}</td>
                  )}
                  {show("preco_novo") && (
                    <td className="px-3 py-2 text-right tabular-nums">
                      <div className="flex items-center justify-end gap-1">
                        <span className="font-medium">{fmtBRL(s.preco_novo)}</span>
                        {precoChange > 0 && <TrendingUp className="h-3 w-3 text-success" />}
                        {precoChange < 0 && <TrendingDown className="h-3 w-3 text-destructive" />}
                      </div>
                    </td>
                  )}
                  {show("acao") && (
                    <td className="px-3 py-2">
                      <Fase1Badge action={s.fase1_acao} title={s.fase1_motivo} />
                    </td>
                  )}
                  {show("comissao") && (
                    <td className="px-3 py-2 text-right text-xs tabular-nums">
                      <div className="flex items-center justify-end gap-1">
                        <span>{fmtBRL(comissaoNovo)}</span>
                        {s.taxas_novo.percentual_tarifa > 0 && (
                          <span className="text-muted-foreground">
                            ({s.taxas_novo.percentual_tarifa.toFixed(1)}%)
                          </span>
                        )}
                      </div>
                    </td>
                  )}
                  {show("tarifa_fixa") && (
                    <td className="px-3 py-2 text-right text-xs tabular-nums">
                      {s.taxas_novo.tarifa_fixa > 0 ? (
                        fmtBRL(s.taxas_novo.tarifa_fixa)
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  )}
                  {show("frete") && (
                    <td className="px-3 py-2 text-right text-xs tabular-nums">
                      {s.taxas_novo.frete > 0 ? (
                        fmtBRL(s.taxas_novo.frete)
                      ) : (
                        <span className="text-muted-foreground">N/A</span>
                      )}
                    </td>
                  )}
                  {show("margem_liquida") && (
                    <td className="px-3 py-2">
                      <EditableMarginCell
                        profileId={profileId}
                        simulationId={simulationId}
                        item={s}
                        margemCampanhaFallback={margemCampanhaPadrao}
                      />
                    </td>
                  )}
                  {show("preco_campanha") && (
                    <td className="px-3 py-2 text-right tabular-nums">{fmtBRL(s.deal_price)}</td>
                  )}
                  {show("desconto") && (
                    <td className="px-3 py-2 text-right font-medium tabular-nums text-primary">
                      {fmtPctFromNumber(s.desconto_pct)}
                    </td>
                  )}
                  {show("liq_projetado") && (
                    <td className="px-3 py-2 text-right tabular-nums">
                      {fmtBRL(s.liq_final_deal_projetado)}
                    </td>
                  )}
                  {show("em_campanha") && (
                    <td className="px-3 py-2 text-center">
                      {s.em_campanha ? (
                        <span
                          className="inline-flex items-center rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
                          title={
                            s.nomes_campanha && s.nomes_campanha.length > 0
                              ? `Em: ${s.nomes_campanha.join(", ")}`
                              : "Em alguma campanha"
                          }
                        >
                          Sim
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">Não</span>
                      )}
                    </td>
                  )}
                  {show("nome_campanha") && (
                    <td
                      className="max-w-[240px] truncate px-3 py-2 text-xs"
                      title={(s.nomes_campanha ?? []).join(", ")}
                    >
                      {s.nomes_campanha && s.nomes_campanha.length > 0 ? (
                        s.nomes_campanha.join(", ")
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
        {pagedItems.length === 0 && (
          <div className="p-8 text-center text-sm text-muted-foreground">
            Nenhum resultado pra essa busca.
          </div>
        )}
      </div>
      <TablePagination
        currentPage={currentPage}
        totalItems={totalFiltered}
        pageSize={pageSize}
        onChange={onPageChange}
        itemLabel="anúncios"
      />
    </Card>
  );
}

// ─── Tabela de exceções ────────────────────────────────────────────────────

function ExceptionsTable({
  items,
  total,
}: {
  items: RepricingException[];
  total: number;
}) {
  if (total === 0) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-sm text-success">
          🎉 Nenhuma exceção — todos os anúncios foram simulados com sucesso.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/50">
            <tr>
              <th className="px-3 py-2.5 text-left font-medium">SKU</th>
              <th className="px-3 py-2.5 text-left font-medium">MLB</th>
              <th className="px-3 py-2.5 text-left font-medium">Título</th>
              <th className="px-3 py-2.5 text-left font-medium">Tipo</th>
              <th className="px-3 py-2.5 text-left font-medium">Motivo/Detalhe</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.item_id} className="border-b transition-colors hover:bg-muted/30">
                <td className="px-3 py-2 font-mono text-xs">
                  {e.sku ?? <span className="italic text-muted-foreground">sem SKU</span>}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{e.item_id}</td>
                <td className="max-w-xs truncate px-3 py-2" title={e.titulo ?? ""}>
                  {e.titulo ?? "—"}
                </td>
                <td className="px-3 py-2">
                  <span className="rounded bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning">
                    {e.tipo}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs text-muted-foreground">
                  {e.motivo || e.detalhe || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {items.length === 0 && (
        <div className="p-8 text-center text-sm text-muted-foreground">
          Nenhum resultado pra essa busca.
        </div>
      )}
    </Card>
  );
}

// ─── Helpers de UI ─────────────────────────────────────────────────────────

function Fase1Badge({
  action,
  title,
}: {
  action: RepricingLineItem["fase1_acao"];
  title: string;
}) {
  const config: Record<string, { label: string; className: string }> = {
    mantido: { label: "Mantido", className: "bg-success/10 text-success" },
    subir: { label: "Subir", className: "bg-primary/10 text-primary" },
    baixar: { label: "Baixar", className: "bg-warning/10 text-warning" },
    // legados (snapshots antigos do modelo 50%)
    preco_aumentado: { label: "Aumentado", className: "bg-primary/10 text-primary" },
    preco_travado_abaixo_79: {
      label: "Travado R$78,90",
      className: "bg-warning/10 text-warning",
    },
  };
  const c = config[action] ?? {
    label: action,
    className: "bg-muted text-muted-foreground",
  };
  return (
    <span
      className={cn("inline-block rounded px-2 py-0.5 text-xs font-medium", c.className)}
      title={title}
    >
      {c.label}
    </span>
  );
}

function StateBadge({ state }: { state: SimulationState }) {
  const config: Record<
    SimulationState,
    { label: string; icon: React.ReactNode; className: string }
  > = {
    running: {
      label: "Executando",
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
      className: "bg-primary/10 text-primary",
    },
    completed: {
      label: "Concluída",
      icon: null,
      className: "bg-success/10 text-success",
    },
    failed: {
      label: "Falhou",
      icon: <AlertCircle className="h-3 w-3" />,
      className: "bg-destructive/10 text-destructive",
    },
    interrupted: {
      label: "Interrompida",
      icon: <Pause className="h-3 w-3" />,
      className: "bg-warning/10 text-warning",
    },
  };
  const c = config[state];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        c.className,
      )}
    >
      {c.icon}
      {c.label}
    </span>
  );
}

function SummaryCard({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "success" | "warning";
}) {
  return (
    <Card>
      <CardContent className="space-y-1 p-4">
        <p
          className={cn(
            "text-xs font-medium",
            tone === "warning" && "text-warning",
            tone === "success" && "text-success",
            tone === "default" && "text-muted-foreground",
          )}
        >
          {label}
        </p>
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
      </CardContent>
    </Card>
  );
}

function LoadingState() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-32" />
      <Skeleton className="h-20 w-full" />
      <div className="grid gap-3 sm:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-96 w-full" />
    </div>
  );
}
