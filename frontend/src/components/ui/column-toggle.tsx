import { Check, ChevronDown, Eye } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export interface ColumnDef {
  /** Identificador único da coluna (usado como key do Set). */
  key: string;
  /** Label visível no dropdown. */
  label: string;
}

interface ColumnToggleProps {
  /** Definição de todas as colunas disponíveis. */
  columns: ColumnDef[];
  /** Set com as keys atualmente visíveis. */
  visible: Set<string>;
  /** Callback para alternar a visibilidade de uma coluna. */
  onChange: (next: Set<string>) => void;
  /** Chave do localStorage pra persistir a configuração. Se omitido, não persiste. */
  storageKey?: string;
}

/**
 * Dropdown com checkboxes pra ligar/desligar colunas duma tabela.
 *
 * Por default todas marcadas. Inclui ações "Marcar todas" / "Desmarcar
 * todas" / "Restaurar padrão" no rodapé.
 *
 * Use `storageKey` pra persistir a preferência entre sessões via
 * localStorage. Sem ele, o estado é apenas em memória.
 */
export function ColumnToggle({ columns, visible, onChange, storageKey }: ColumnToggleProps) {
  // Persiste mudanças no localStorage
  useEffect(() => {
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(Array.from(visible)));
    } catch {
      // ignora erros de quota/SSR/etc
    }
  }, [visible, storageKey]);

  const toggle = (key: string) => {
    const next = new Set(visible);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    onChange(next);
  };

  const showAll = () => onChange(new Set(columns.map((c) => c.key)));
  const hideAll = () => onChange(new Set());

  const hiddenCount = columns.length - visible.size;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <Eye className="h-4 w-4" />
          Colunas
          {hiddenCount > 0 && (
            <span className="ml-0.5 rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-primary">
              {hiddenCount}
            </span>
          )}
          <ChevronDown className="h-3 w-3 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-96 w-56 overflow-y-auto">
        <DropdownMenuLabel className="text-xs">Mostrar colunas</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {columns.map((col) => {
          const isVisible = visible.has(col.key);
          return (
            <DropdownMenuItem
              key={col.key}
              onSelect={(e) => {
                e.preventDefault(); // mantém aberto pra marcar várias
                toggle(col.key);
              }}
              className="gap-2 text-sm"
            >
              <div
                className={cn(
                  "flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border",
                  isVisible ? "border-primary bg-primary text-primary-foreground" : "border-input",
                )}
              >
                {isVisible && <Check className="h-3 w-3" />}
              </div>
              <span className={cn(!isVisible && "text-muted-foreground")}>{col.label}</span>
            </DropdownMenuItem>
          );
        })}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={(e) => {
            e.preventDefault();
            showAll();
          }}
          className="text-xs"
        >
          Marcar todas
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={(e) => {
            e.preventDefault();
            hideAll();
          }}
          className="text-xs"
        >
          Desmarcar todas
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Hook auxiliar pra gerenciar state de colunas com persistência opcional.
 * Inicializa do localStorage se presente, senão usa todas as colunas como
 * visíveis por default.
 */
export function useColumnVisibility(
  columns: ColumnDef[],
  storageKey?: string,
): [Set<string>, (next: Set<string>) => void] {
  const allKeys = columns.map((c) => c.key);

  // Lazy init: lê do localStorage só na primeira render
  const [visible, setVisible] = useState<Set<string>>(() => {
    if (!storageKey) return new Set(allKeys);
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          // Filtra keys que não existem mais (caso schema mude entre versões)
          return new Set(parsed.filter((k: string) => allKeys.includes(k)));
        }
      }
    } catch {
      // ignora — fallback pro default
    }
    return new Set(allKeys);
  });

  return [visible, setVisible];
}
