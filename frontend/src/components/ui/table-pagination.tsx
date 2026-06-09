import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";

import { Button } from "@/components/ui/button";

interface TablePaginationProps {
  /** Página atual (1-indexed). */
  currentPage: number;
  /** Total de itens. */
  totalItems: number;
  /** Tamanho da página. */
  pageSize: number;
  /** Callback ao mudar página. */
  onChange: (page: number) => void;
  /** Label customizado (default: "itens"). */
  itemLabel?: string;
}

/**
 * Paginação numérica com 4 botões (primeira/anterior/próxima/última) e
 * label "X-Y de Z itens". Esconde-se sozinha quando há só uma página.
 *
 * Filosofia: a maioria dos relatórios tem 100-300 itens, então paginação
 * fica útil quando ultrapassa esse limite. Page size é definido pela
 * página que usa o componente.
 */
export function TablePagination({
  currentPage,
  totalItems,
  pageSize,
  onChange,
  itemLabel = "itens",
}: TablePaginationProps) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));

  // Não mostra paginação se cabe tudo em 1 página
  if (totalPages <= 1) return null;

  const start = (currentPage - 1) * pageSize + 1;
  const end = Math.min(currentPage * pageSize, totalItems);

  const goTo = (page: number) => {
    const clamped = Math.max(1, Math.min(totalPages, page));
    onChange(clamped);
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/30 px-4 py-2.5 text-sm">
      <p className="text-muted-foreground">
        Mostrando <span className="font-medium text-foreground tabular-nums">{start}</span>
        {"-"}
        <span className="font-medium text-foreground tabular-nums">{end}</span> de{" "}
        <span className="font-medium text-foreground tabular-nums">{totalItems}</span> {itemLabel}
      </p>

      <div className="flex items-center gap-1">
        <span className="mr-2 text-xs text-muted-foreground tabular-nums">
          Página <span className="font-medium text-foreground">{currentPage}</span> de {totalPages}
        </span>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          onClick={() => goTo(1)}
          disabled={currentPage === 1}
          title="Primeira página"
        >
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          onClick={() => goTo(currentPage - 1)}
          disabled={currentPage === 1}
          title="Página anterior"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          onClick={() => goTo(currentPage + 1)}
          disabled={currentPage === totalPages}
          title="Próxima página"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          onClick={() => goTo(totalPages)}
          disabled={currentPage === totalPages}
          title="Última página"
        >
          <ChevronsRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
