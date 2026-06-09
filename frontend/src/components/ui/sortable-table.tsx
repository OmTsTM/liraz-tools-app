import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Direção da ordenação. Ciclo: null → "asc" → "desc" → null (volta a ordem original).
 */
export type SortDirection = "asc" | "desc" | null;

export interface SortState<K extends string = string> {
  key: K | null;
  direction: SortDirection;
}

/**
 * Hook genérico de ordenação + busca pra tabelas. Encapsula:
 *
 * - State da chave/direção atual
 * - Toggle no clique do header (asc → desc → null → asc...)
 * - Aplicação do comparator
 * - Filtro de busca por substring case-insensitive nos campos indicados
 *
 * @param data Lista a ordenar/filtrar
 * @param accessors Mapa de chave → função que extrai o valor a comparar
 *                  (string/number/null). Mesma chave usada em `<SortableTh sortKey>`.
 * @param searchableFields Campos onde a busca varre. Pode ser função (extrair)
 *                        ou só o nome da prop (vai ler como string).
 *
 * Retorna a lista processada, o state atual e o handler de toggle. Estado de
 * busca é responsabilidade do caller (input controlado).
 */
export function useSortableData<T, K extends string = string>(
  data: T[],
  accessors: Record<K, (item: T) => string | number | null | undefined>,
  searchQuery: string,
  searchableFields: (string | ((item: T) => string | null | undefined))[],
): {
  data: T[];
  sortState: SortState<K>;
  toggleSort: (key: K) => void;
} {
  const [sortState, setSortState] = useState<SortState<K>>({ key: null, direction: null });

  const toggleSort = (key: K) => {
    setSortState((old) => {
      if (old.key !== key) return { key, direction: "asc" };
      if (old.direction === "asc") return { key, direction: "desc" };
      return { key: null, direction: null };
    });
  };

  const processed = useMemo(() => {
    let result = data;
    // Busca primeiro (reduz o tamanho antes do sort).
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      result = result.filter((it) => {
        for (const field of searchableFields) {
          const raw =
            typeof field === "function" ? field(it) : (it as Record<string, unknown>)[field];
          if (raw == null) continue;
          if (String(raw).toLowerCase().includes(q)) return true;
        }
        return false;
      });
    }
    // Sort: se key+direction setados, ordena. Senão preserva ordem original.
    if (sortState.key && sortState.direction) {
      const accessor = accessors[sortState.key];
      const dir = sortState.direction === "asc" ? 1 : -1;
      result = [...result].sort((a, b) => {
        const va = accessor(a);
        const vb = accessor(b);
        // null/undefined sempre no fim, independente da direção
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === "number" && typeof vb === "number") {
          return (va - vb) * dir;
        }
        return String(va).localeCompare(String(vb), "pt-BR", { numeric: true }) * dir;
      });
    }
    return result;
  }, [data, sortState, searchQuery, searchableFields, accessors]);

  return { data: processed, sortState, toggleSort };
}

interface SortableThProps<K extends string> {
  sortKey: K;
  state: SortState<K>;
  onSort: (key: K) => void;
  className?: string;
  children: React.ReactNode;
}

/**
 * Header de coluna clicável que mostra a seta de ordenação.
 * Use em <th> ou <TableHead>: passa `sortKey` único, `state` do hook e `onSort`.
 */
export function SortableTh<K extends string>({
  sortKey,
  state,
  onSort,
  className,
  children,
}: SortableThProps<K>) {
  const active = state.key === sortKey && state.direction !== null;
  const Icon = !active ? ArrowUpDown : state.direction === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className={cn(
        "flex w-full items-center gap-1 text-left hover:text-foreground",
        active ? "text-foreground" : "text-muted-foreground",
        className,
      )}
    >
      <span>{children}</span>
      <Icon className={cn("h-3 w-3 shrink-0", active ? "opacity-100" : "opacity-40")} />
    </button>
  );
}
