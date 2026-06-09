/**
 * Helpers de estilo pra tabelas com freeze panes (colunas/header fixos
 * estilo Excel).
 *
 * Padrão visual:
 * - Header da tabela fica fixo no topo enquanto rola verticalmente
 * - Primeiras N colunas ficam fixas à esquerda enquanto rola horizontalmente
 * - O cruzamento (cantinho superior esquerdo) tem z-index mais alto
 *
 * Por que larguras fixas?
 * `position: sticky; left: X` precisa de `X` conhecido em pixels. Sem
 * largura fixa, a coluna seguinte teria que medir a anterior dinamicamente,
 * o que complica. Sacrificamos flexibilidade da largura por simplicidade
 * de implementação.
 *
 * Z-index layers (do menor pro maior):
 * - 0: células normais (default)
 * - 10: coluna fixa sem ser header
 * - 20: header fixo (não coluna fixa)
 * - 30: header da coluna fixa (intersecção)
 */

// Larguras das 3 primeiras colunas fixas (em px).
// Usadas pra calcular o `left` da próxima coluna.
export const STICKY_COL_WIDTHS = {
  sku: 96,
  mlb: 128,
  titulo: 280,
} as const;

export const STICKY_COL_OFFSETS = {
  sku: 0,
  mlb: STICKY_COL_WIDTHS.sku, // 96
  titulo: STICKY_COL_WIDTHS.sku + STICKY_COL_WIDTHS.mlb, // 224
} as const;

/**
 * Container principal da tabela.
 * - max-h: limita altura pra dar scroll vertical interno
 * - overflow-auto: necessário pro sticky funcionar
 * - relative: estabelece stacking context
 */
export const STICKY_CONTAINER_CLASS = "relative max-h-[70vh] overflow-auto";

/**
 * Estilo de uma célula `<th>` normal do header (não fixa horizontalmente).
 * Sticky no topo + z-index 20.
 */
export const STICKY_HEADER_CLASS = "sticky top-0 z-20 bg-muted/95 backdrop-blur-sm";

/**
 * Estilo de uma célula `<th>` que é header E coluna fixa.
 * Sticky top+left, z-index 30 (acima de todos).
 */
export const STICKY_HEADER_PINNED_CLASS = "sticky top-0 z-30 bg-muted/95 backdrop-blur-sm";

/**
 * Estilo de uma célula `<td>` que é coluna fixa (não header).
 * Sticky left, z-index 10.
 */
export const STICKY_CELL_CLASS = "sticky z-10 bg-background";

/**
 * Gera o `style` inline com `left: Npx` pra uma coluna fixa.
 */
export function stickyLeftStyle(offset: number): React.CSSProperties {
  return { left: `${offset}px` };
}

/**
 * Gera `style` pra uma coluna fixa com largura fixa também.
 */
export function stickyColStyle(offset: number, width: number): React.CSSProperties {
  return {
    left: `${offset}px`,
    minWidth: `${width}px`,
    maxWidth: `${width}px`,
    width: `${width}px`,
  };
}
