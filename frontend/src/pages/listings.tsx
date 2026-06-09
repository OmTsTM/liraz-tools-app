import {
  type ColumnDef,
  type PaginationState,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";

// Augmentation: estendemos ColumnMeta do TanStack pra ter um campo `align`
// tipado em vez de usar `any` nos acessos a column.columnDef.meta
declare module "@tanstack/react-table" {
  interface ColumnMeta<TData, TValue> {
    align?: "left" | "right" | "center";
  }
}
import {
  AlertCircle,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ArrowUpDown,
  Download,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { pricingApi } from "@/api/pricing";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ColumnToggle, type ColumnDef as ToggleColumnDef } from "@/components/ui/column-toggle";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TablePagination } from "@/components/ui/table-pagination";
import { toast } from "@/components/ui/toaster";
import { EditableCostCell } from "@/features/pricing/editable-cost-cell";
import { useFeeReport, useRefreshFeeReport } from "@/features/pricing/hooks";
import { useProfile } from "@/features/profiles/hooks";
import {
  STICKY_CELL_CLASS,
  STICKY_COL_WIDTHS,
  STICKY_CONTAINER_CLASS,
  STICKY_HEADER_CLASS,
  STICKY_HEADER_PINNED_CLASS,
  stickyColStyle,
} from "@/lib/sticky-table";
import { cn } from "@/lib/utils";
import type { ListingFees } from "@/types/api";

const PAGE_SIZE_LISTINGS = 300;

// IDs das colunas que ficam fixas à esquerda. Devem bater com `accessorKey`
// ou `id` das columnDefs do TanStack Table (em buildColumns).
const STICKY_COLUMN_IDS: readonly string[] = ["sku", "item_id", "title"];

const STICKY_WIDTH_BY_ID: Record<string, number> = {
  sku: STICKY_COL_WIDTHS.sku,
  item_id: STICKY_COL_WIDTHS.mlb,
  title: STICKY_COL_WIDTHS.titulo,
};

// Definição das colunas da tabela de listings (em sincronia com buildColumns).
// Usado pelo ColumnToggle. IDs devem bater com `accessorKey` ou `id` das
// columnDefs do TanStack — caso contrário a visibilidade não funciona.
const LISTINGS_TABLE_COLUMNS: ToggleColumnDef[] = [
  { key: "sku", label: "SKU" },
  { key: "item_id", label: "MLB" },
  { key: "title", label: "Título" },
  { key: "preco", label: "Preço" },
  { key: "comissao_valor", label: "Comissão" },
  { key: "tarifa_fixa", label: "Tarifa Fixa" },
  { key: "frete_vendedor", label: "Frete" },
  { key: "custo_produto", label: "Custo" },
  { key: "lucro_liquido", label: "Lucro líquido" },
  { key: "margem_liquida_percentual", label: "Margem" },
];

// ─── Formatadores ──────────────────────────────────────────────────────────

const BRL = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});

const PCT = new Intl.NumberFormat("pt-BR", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function fmtBRL(v: number | null | undefined): string {
  if (v == null) return "—";
  return BRL.format(v);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return PCT.format(v);
}

// ─── Estilo do highlight de margem ─────────────────────────────────────────

/** Classe Tailwind aplicada na linha de acordo com a margem líquida. */
function marginRowClass(margin: number | null): string {
  if (margin == null) return "";
  if (margin < 0) return "bg-destructive/5 hover:bg-destructive/10";
  if (margin < 0.15) return "bg-warning/5 hover:bg-warning/10";
  if (margin >= 0.25) return "bg-success/5 hover:bg-success/10";
  return "";
}

// ─── Filtros disponíveis ───────────────────────────────────────────────────

type FilterKey =
  | "all"
  | "margem_baixa"
  | "correspondencia_aproximada"
  | "sem_custo"
  | "sem_sku"
  | "erro";

const FILTER_LABELS: Record<FilterKey, string> = {
  all: "Todos",
  margem_baixa: "Margem < 20%",
  correspondencia_aproximada: "Divergência de SKU/Preço",
  sem_custo: "Sem custo",
  sem_sku: "Sem SKU",
  erro: "Com erro",
};

function applyFilter(listings: ListingFees[], filter: FilterKey): ListingFees[] {
  switch (filter) {
    case "all":
      return listings;
    case "margem_baixa":
      return listings.filter(
        (l) => l.margem_liquida_percentual != null && l.margem_liquida_percentual < 0.2,
      );
    case "correspondencia_aproximada":
      // case_insensitive, prefixo ou prefixo_divergente — tudo que não foi
      // match exato e nem override manual, mas tem custo resolvido por heurística
      return listings.filter(
        (l) =>
          l.custo_fonte === "case_insensitive" ||
          l.custo_fonte === "prefixo" ||
          l.custo_fonte === "prefixo_divergente",
      );
    case "sem_custo":
      return listings.filter((l) => l.custo_produto == null && !l.erro);
    case "sem_sku":
      return listings.filter((l) => !l.sku && !l.erro);
    case "erro":
      return listings.filter((l) => Boolean(l.erro));
  }
}

// ─── Página ────────────────────────────────────────────────────────────────

export function ListingsPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: profile } = useProfile(id);
  const { data: report, isPending, isError, error, isFetching } = useFeeReport(id);
  const refresh = useRefreshFeeReport();

  const [globalFilter, setGlobalFilter] = useState("");
  const [sorting, setSorting] = useState<SortingState>([
    { id: "margem_liquida_percentual", desc: false },
  ]);
  const [filter, setFilter] = useState<FilterKey>("all");

  // Visibilidade de colunas (persistido em localStorage). Default: todas
  // visíveis. TanStack Table aceita `columnVisibility` no state diretamente.
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>(() => {
    try {
      const raw = localStorage.getItem("listings_table_columns_v1");
      if (raw) return JSON.parse(raw);
    } catch {
      // ignora
    }
    return {};
  });

  // Persiste mudanças no localStorage
  useEffect(() => {
    try {
      localStorage.setItem("listings_table_columns_v1", JSON.stringify(columnVisibility));
    } catch {
      // ignora
    }
  }, [columnVisibility]);

  // Paginação — TanStack tem suporte nativo via getPaginationRowModel
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: PAGE_SIZE_LISTINGS,
  });

  // Reseta pra primeira página quando filtros mudam.
  // biome-ignore lint/correctness/useExhaustiveDependencies: dispara só quando filtros mudam
  useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [filter, globalFilter]);

  const columns = useMemo(() => buildColumns(id ?? ""), [id]);

  const filteredData = useMemo(
    () => (report ? applyFilter(report.listings, filter) : []),
    [report, filter],
  );

  const table = useReactTable<ListingFees>({
    data: filteredData,
    columns,
    state: { sorting, globalFilter, columnVisibility, pagination },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
    globalFilterFn: (row, _colId, query: string) => {
      const q = query.toLowerCase();
      const l = row.original;
      return (
        (l.sku ?? "").toLowerCase().includes(q) ||
        l.item_id.toLowerCase().includes(q) ||
        (l.title ?? "").toLowerCase().includes(q)
      );
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  const handleRefresh = async () => {
    if (!id) return;
    try {
      await refresh.mutateAsync(id);
      toast.success("Cache invalidado", {
        description: "Recarregando dados do Mercado Livre...",
      });
    } catch (err) {
      toast.error("Falha ao recarregar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleDownloadXLSX = () => {
    if (!id) return;
    window.open(pricingApi.getXlsxDownloadUrl(id), "_blank");
  };

  // ─── Estados de carregamento e erro ─────────────────────────────────────

  if (isPending) {
    return (
      <div className="space-y-6">
        <Header profileName={profile?.name} onBack={() => navigate(`/profiles/${id}`)} />
        <LoadingState />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6">
        <Header profileName={profile?.name} onBack={() => navigate(`/profiles/${id}`)} />
        <ErrorState
          message={error instanceof Error ? error.message : "Erro desconhecido"}
          onBack={() => navigate(`/profiles/${id}`)}
        />
      </div>
    );
  }

  if (!report) return null;

  return (
    <div className="space-y-6">
      <Header
        profileName={profile?.name}
        onBack={() => navigate(`/profiles/${id}`)}
        generatedAt={report.generated_at}
      />

      {/* Métricas resumidas */}
      <div className="grid gap-3 sm:grid-cols-4">
        <SummaryCard label="Anúncios" value={report.total_anuncios.toString()} />
        <SummaryCard
          label="Sem custo"
          value={report.anuncios_sem_custo.length.toString()}
          tone={report.anuncios_sem_custo.length > 0 ? "warning" : "default"}
        />
        <SummaryCard
          label="Divergência de SKU/Preço"
          value={report.anuncios_com_fallback.length.toString()}
          tone={report.anuncios_com_fallback.length > 0 ? "warning" : "default"}
        />
        <SummaryCard label="SKUs no custos.xlsx" value={report.skus_no_custos_xlsx.toString()} />
      </div>

      {/* Barra de ações */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative max-w-md flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Buscar por SKU, MLB ou título..."
            value={globalFilter}
            onChange={(e) => setGlobalFilter(e.target.value)}
            className="pl-9"
          />
        </div>

        <div className="flex flex-wrap gap-1 rounded-md border bg-muted/30 p-1">
          {(Object.keys(FILTER_LABELS) as FilterKey[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              className={cn(
                "rounded px-3 py-1 text-xs font-medium transition-colors",
                filter === key
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {FILTER_LABELS[key]}
            </button>
          ))}
        </div>

        <div className="ml-auto flex gap-2">
          <ColumnToggle
            columns={LISTINGS_TABLE_COLUMNS}
            visible={
              new Set(
                LISTINGS_TABLE_COLUMNS.filter((c) => columnVisibility[c.key] !== false).map(
                  (c) => c.key,
                ),
              )
            }
            onChange={(next) => {
              // Converte Set -> Record que TanStack espera
              const allCols = LISTINGS_TABLE_COLUMNS.map((c) => c.key);
              const newVisibility: Record<string, boolean> = {};
              for (const col of allCols) {
                newVisibility[col] = next.has(col);
              }
              setColumnVisibility(newVisibility);
            }}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={refresh.isPending || isFetching}
          >
            {refresh.isPending || isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            Recarregar
          </Button>
          <Button variant="outline" size="sm" onClick={handleDownloadXLSX}>
            <Download className="h-4 w-4" />
            XLSX
          </Button>
        </div>
      </div>

      {/* Tabela */}
      <Card className="relative overflow-hidden">
        {/*
          Overlay durante refresh: bloqueia interações com dados velhos
          enquanto o backend recalcula. Usa pointer-events-none + opacity
          baixa pra deixar visualmente claro que está "stale".
        */}
        {isFetching && !refresh.isPending && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60 backdrop-blur-sm">
            <div className="flex items-center gap-2 rounded-lg border bg-background px-4 py-2 shadow-md">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              <span className="text-sm font-medium">Atualizando dados...</span>
            </div>
          </div>
        )}
        {refresh.isPending && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60 backdrop-blur-sm">
            <div className="flex max-w-md flex-col items-center gap-2 rounded-lg border bg-background px-6 py-4 text-center shadow-md">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <p className="text-sm font-medium">Invalidando cache...</p>
              <p className="text-xs text-muted-foreground">
                A próxima geração do relatório vai consultar o ML novamente (pode levar 30-60s).
              </p>
            </div>
          </div>
        )}

        <div
          className={cn(
            STICKY_CONTAINER_CLASS,
            "transition-opacity",
            (isFetching || refresh.isPending) && "pointer-events-none opacity-50",
          )}
        >
          <table className="w-full text-sm">
            <thead className="border-b">
              {table.getHeaderGroups().map((group) => {
                // Mapeia offset acumulado das colunas sticky visíveis
                const visibleStickyIds = group.headers
                  .filter((h) => STICKY_COLUMN_IDS.includes(h.column.id))
                  .map((h) => h.column.id);
                const offsetMap = new Map<string, number>();
                let acc = 0;
                for (const cid of visibleStickyIds) {
                  offsetMap.set(cid, acc);
                  acc += STICKY_WIDTH_BY_ID[cid] ?? 0;
                }

                return (
                  <tr key={group.id}>
                    {group.headers.map((header) => {
                      const sortDir = header.column.getIsSorted();
                      const canSort = header.column.getCanSort();
                      const isSticky = STICKY_COLUMN_IDS.includes(header.column.id);
                      const offset = offsetMap.get(header.column.id) ?? 0;
                      const width = STICKY_WIDTH_BY_ID[header.column.id];

                      return (
                        <th
                          key={header.id}
                          className={cn(
                            "px-3 py-2.5 text-left font-medium",
                            isSticky ? STICKY_HEADER_PINNED_CLASS : STICKY_HEADER_CLASS,
                            header.column.columnDef.meta?.align === "right" && "text-right",
                            canSort && "cursor-pointer select-none",
                          )}
                          style={isSticky && width ? stickyColStyle(offset, width) : undefined}
                          onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                        >
                          <span className="inline-flex items-center gap-1">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {canSort && <SortIcon dir={sortDir === false ? null : sortDir} />}
                          </span>
                        </th>
                      );
                    })}
                  </tr>
                );
              })}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => {
                // Calcula offsets sticky por linha (mesmo cálculo do header)
                const visibleStickyIds = row
                  .getVisibleCells()
                  .filter((c) => STICKY_COLUMN_IDS.includes(c.column.id))
                  .map((c) => c.column.id);
                const offsetMap = new Map<string, number>();
                let acc = 0;
                for (const cid of visibleStickyIds) {
                  offsetMap.set(cid, acc);
                  acc += STICKY_WIDTH_BY_ID[cid] ?? 0;
                }

                return (
                  <tr
                    key={row.id}
                    className={cn(
                      "border-b transition-colors",
                      marginRowClass(row.original.margem_liquida_percentual),
                    )}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const isSticky = STICKY_COLUMN_IDS.includes(cell.column.id);
                      const offset = offsetMap.get(cell.column.id) ?? 0;
                      const width = STICKY_WIDTH_BY_ID[cell.column.id];

                      return (
                        <td
                          key={cell.id}
                          className={cn(
                            "px-3 py-2",
                            isSticky && STICKY_CELL_CLASS,
                            // Mantém o background da row colorida quando sticky:
                            // se não setar bg explícito aqui, sticky usa transparente
                            // e cor de margem da row "vaza" — desejado.
                            cell.column.columnDef.meta?.align === "right" && "text-right",
                          )}
                          style={isSticky && width ? stickyColStyle(offset, width) : undefined}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {table.getRowModel().rows.length === 0 && (
            <div className="p-8 text-center text-sm text-muted-foreground">
              Nenhum anúncio bate com os filtros.
            </div>
          )}
        </div>

        <TablePagination
          currentPage={table.getState().pagination.pageIndex + 1}
          totalItems={table.getFilteredRowModel().rows.length}
          pageSize={table.getState().pagination.pageSize}
          onChange={(page) => table.setPageIndex(page - 1)}
          itemLabel="anúncios"
        />
      </Card>

      {/* Rodapé com info */}
      <div className="text-xs text-muted-foreground">
        Total filtrado: {table.getFilteredRowModel().rows.length} de {report.listings.length}{" "}
        anúncios · Frete simulado com CEP <span className="font-mono">{report.cep_destino}</span> ·
        Imposto {fmtPct(report.aliquota_imposto)} · Última atualização{" "}
        {new Date(report.generated_at).toLocaleString("pt-BR")}
      </div>
    </div>
  );
}

// ─── Componentes auxiliares ────────────────────────────────────────────────

function Header({
  profileName,
  onBack,
  generatedAt,
}: {
  profileName?: string;
  onBack: () => void;
  generatedAt?: string;
}) {
  return (
    <div className="space-y-3">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="h-4 w-4" />
        Voltar pro dashboard
      </Button>
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">
          Relatório de margens
          {profileName && (
            <span className="ml-2 text-base font-normal text-muted-foreground">
              · {profileName}
            </span>
          )}
        </h2>
        {generatedAt && (
          <p className="text-sm text-muted-foreground">
            Gerado em {new Date(generatedAt).toLocaleString("pt-BR")} · Cache válido por 15 minutos
          </p>
        )}
      </div>
    </div>
  );
}

function SortIcon({ dir }: { dir: "asc" | "desc" | null }) {
  if (dir === null) return <ArrowUpDown className="h-3 w-3 opacity-40" />;
  return dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />;
}

function SummaryCard({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "warning";
}) {
  return (
    <Card>
      <CardContent className="space-y-1 p-4">
        <p
          className={cn(
            "text-xs font-medium",
            tone === "warning" ? "text-warning" : "text-muted-foreground",
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
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i}>
            <CardContent className="space-y-2 p-4">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-7 w-16" />
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent className="space-y-3 p-6">
          <div className="flex items-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            <div className="flex-1">
              <p className="text-sm font-medium">Calculando taxas e margens...</p>
              <p className="text-xs text-muted-foreground">
                Consulta a API do Mercado Livre pra cada anúncio. Pode levar 30-60 segundos na
                primeira vez. As próximas chamadas usam cache.
              </p>
            </div>
          </div>
          <div className="space-y-2">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ErrorState({
  message,
  onBack,
}: {
  message: string;
  onBack: () => void;
}) {
  const isCustosNotConfigured = message.toLowerCase().includes("custos");

  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-4 p-12 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
          <AlertCircle className="h-6 w-6 text-destructive" />
        </div>
        <div className="space-y-1">
          <h3 className="text-base font-semibold">
            {isCustosNotConfigured
              ? "Planilha de custos não configurada"
              : "Não foi possível gerar o relatório"}
          </h3>
          <p className="max-w-md text-sm text-muted-foreground">{message}</p>
        </div>
        <Button variant="outline" onClick={onBack}>
          Voltar pro dashboard
        </Button>
      </CardContent>
    </Card>
  );
}

// ─── Definição das colunas ─────────────────────────────────────────────────

function buildColumns(profileId: string): ColumnDef<ListingFees>[] {
  return [
    {
      accessorKey: "sku",
      header: "SKU",
      cell: ({ row }) => (
        <span className="font-mono text-xs">
          {row.original.sku ?? <span className="italic text-muted-foreground">sem SKU</span>}
        </span>
      ),
    },
    {
      accessorKey: "item_id",
      header: "MLB",
      cell: ({ row }) => (
        <a
          href={`https://produto.mercadolivre.com.br/${row.original.item_id.replace("MLB", "MLB-")}`}
          target="_blank"
          rel="noreferrer"
          className="font-mono text-xs hover:underline"
          title="Abrir no Mercado Livre"
        >
          {row.original.item_id}
        </a>
      ),
    },
    {
      accessorKey: "title",
      header: "Título",
      cell: ({ row }) => {
        if (row.original.erro) {
          return (
            <span className="text-destructive" title={row.original.erro}>
              ⚠ {row.original.erro.slice(0, 50)}
            </span>
          );
        }
        return (
          <span className="block max-w-xs truncate" title={row.original.title ?? ""}>
            {row.original.title ?? "—"}
          </span>
        );
      },
    },
    {
      accessorKey: "preco",
      header: "Preço",
      meta: { align: "right" },
      cell: ({ getValue }) => <span className="tabular-nums">{fmtBRL(getValue() as number)}</span>,
    },
    {
      accessorKey: "comissao_valor",
      header: "Comissão",
      meta: { align: "right" },
      cell: ({ row }) => (
        <div className="tabular-nums">
          <span>{fmtBRL(row.original.comissao_valor)}</span>
          <span className="ml-1 text-xs text-muted-foreground">
            ({row.original.comissao_percentual.toFixed(1)}%)
          </span>
        </div>
      ),
    },
    {
      accessorKey: "tarifa_fixa",
      header: "Tarifa fixa",
      meta: { align: "right" },
      cell: ({ row }) => (
        <span className="tabular-nums" title={`Fonte: ${row.original.tarifa_fixa_fonte}`}>
          {fmtBRL(row.original.tarifa_fixa)}
        </span>
      ),
    },
    {
      accessorKey: "frete_vendedor",
      header: "Frete",
      meta: { align: "right" },
      cell: ({ row }) => (
        <span className="tabular-nums" title={`Fonte: ${row.original.frete_fonte}`}>
          {row.original.free_shipping ? (
            fmtBRL(row.original.frete_vendedor)
          ) : (
            <span className="text-xs text-muted-foreground">N/A</span>
          )}
        </span>
      ),
    },
    {
      accessorKey: "custo_produto",
      header: "Custo",
      meta: { align: "right" },
      cell: ({ row }) => <EditableCostCell profileId={profileId} listing={row.original} />,
    },
    {
      accessorKey: "lucro_liquido",
      header: "Lucro líquido",
      meta: { align: "right" },
      cell: ({ getValue }) => (
        <span className="font-medium tabular-nums">{fmtBRL(getValue() as number | null)}</span>
      ),
    },
    {
      accessorKey: "margem_liquida_percentual",
      header: "Margem",
      meta: { align: "right" },
      cell: ({ getValue }) => {
        const margin = getValue() as number | null;
        return (
          <span
            className={cn(
              "font-semibold tabular-nums",
              margin == null && "text-muted-foreground",
              margin != null && margin < 0 && "text-destructive",
              margin != null && margin >= 0 && margin < 0.15 && "text-warning",
              margin != null && margin >= 0.25 && "text-success",
            )}
          >
            {fmtPct(margin)}
          </span>
        );
      },
      sortingFn: (a, b) => {
        const av = a.original.margem_liquida_percentual ?? Number.POSITIVE_INFINITY;
        const bv = b.original.margem_liquida_percentual ?? Number.POSITIVE_INFINITY;
        return av - bv;
      },
    },
  ];
}
