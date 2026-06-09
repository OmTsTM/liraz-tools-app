"""XLSX renderer pra simulação de reprecificação.

Layout idêntico ao do MCP (`tools/reprecificacao.py::_gerar_xlsx_simulacao`):

Linha 1 = cabeçalhos de grupo mesclados (IDENTIFICAÇÃO / ANTES / DEPOIS /
FASE 1 / CAMPANHA / RESULTADO).
Linha 2 = cabeçalhos de coluna.
Linha 3+ = dados (uma linha por SKU simulado).

Colunas:
  A-D: identificação (MLB, SKU, Título, Modalidade)
  E-J: situação ANTES (preço atual + taxas detalhadas)
  K-P: situação DEPOIS (preço novo + taxas detalhadas)
  Q-S: decisão FASE 1 (Custo, Margem atual, Ação)
  T-V: deal_price da campanha
  W-X: resultado projetado

Aba secundária "Exceções" com SKUs que não puderam ser simulados.

Cores idênticas ao MCP pra reconhecimento visual.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


def render_simulation_xlsx(snapshot: dict[str, Any]) -> bytes:
    """Gera XLSX da simulação. Retorna bytes pro download direto."""
    wb = Workbook()
    ws = wb.active
    if ws is None:  # pragma: no cover - openpyxl sempre cria active
        raise RuntimeError("Workbook sem worksheet ativa")
    ws.title = "Simulação"

    # ─── Estilos (idênticos ao MCP) ─────────────────────────────────────
    font_h = Font(name="Arial", size=11, bold=True, color="000000")
    font_grupo = Font(name="Arial", size=12, bold=True, color="FFFFFF")
    font_d = Font(name="Arial", size=10)

    fill_id = PatternFill("solid", start_color="FFE699")
    fill_antes = PatternFill("solid", start_color="F8CBAD")
    fill_depois = PatternFill("solid", start_color="C6E0B4")
    fill_fase1 = PatternFill("solid", start_color="FFD966")
    fill_campanha = PatternFill("solid", start_color="9DC3E6")
    fill_resultado = PatternFill("solid", start_color="00B050")
    fill_subir = PatternFill("solid", start_color="FFA500")
    fill_travado = PatternFill("solid", start_color="D6A4E0")
    fill_mantido = PatternFill("solid", start_color="E2EFDA")
    fill_grupo_id = PatternFill("solid", start_color="595959")
    fill_grupo_antes = PatternFill("solid", start_color="C00000")
    fill_grupo_depois = PatternFill("solid", start_color="385723")
    fill_grupo_fase1 = PatternFill("solid", start_color="BF8F00")
    fill_grupo_campanha = PatternFill("solid", start_color="2E5597")
    fill_grupo_resultado = PatternFill("solid", start_color="006100")
    fill_grupo_info = PatternFill("solid", start_color="404040")
    fill_info = PatternFill("solid", start_color="D9D9D9")

    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Side(border_style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ─── Linha 1: Cabeçalhos de grupo (mesclados) ───────────────────────
    grupos = [
        ("A1:D1", "IDENTIFICAÇÃO", fill_grupo_id),
        ("E1:J1", "ANTES — Situação atual", fill_grupo_antes),
        ("K1:P1", "DEPOIS — Após fase 1 (preço novo)", fill_grupo_depois),
        ("Q1:S1", "FASE 1 — Decisão", fill_grupo_fase1),
        ("T1:V1", "CAMPANHA — Preço na Campanha", fill_grupo_campanha),
        ("W1:X1", "RESULTADO PROJETADO", fill_grupo_resultado),
        ("Y1:Z1", "INFO ML — Campanhas ativas", fill_grupo_info),
    ]
    for ref, label, fill in grupos:
        ws.merge_cells(ref)
        anchor = ref.split(":")[0]
        c = ws[anchor]
        c.value = label
        c.font = font_grupo
        c.alignment = center
        c.fill = fill
        c.border = border

    # ─── Linha 2: Cabeçalhos de coluna ──────────────────────────────────
    headers = [
        # Identificação (A-D)  # noqa: ERA001
        ("MLB", fill_id),
        ("SKU", fill_id),
        ("Título", fill_id),
        ("Modalidade", fill_id),
        # Antes (E-J)  # noqa: ERA001
        ("Preço atual", fill_antes),
        ("Tarifa Clássico", fill_antes),
        ("Tarifa Premium", fill_antes),
        ("% Tarifa", fill_antes),
        ("Tarifa Fixa", fill_antes),
        ("Frete", fill_antes),
        # Depois (K-P)  # noqa: ERA001
        ("Preço novo", fill_depois),
        ("Tarifa Clássico", fill_depois),
        ("Tarifa Premium", fill_depois),
        ("% Tarifa", fill_depois),
        ("Tarifa Fixa", fill_depois),
        ("Frete", fill_depois),
        # Fase 1 (Q-S)
        ("Custo", fill_fase1),
        ("Margem atual %", fill_fase1),
        ("Ação fase 1", fill_fase1),
        # Campanha (T-V)  # noqa: ERA001
        ("Preço na Campanha", fill_campanha),
        ("Desconto %", fill_campanha),
        ("Líq. final projetado", fill_campanha),
        # Resultado (W-X)  # noqa: ERA001
        ("Margem final %", fill_resultado),
        ("Lucro R$", fill_resultado),
        # INFO ML (Y-Z) — Leva 5.8
        ("Em campanha?", fill_info),
        ("Nome(s) da campanha", fill_info),
    ]
    for i, (h, fill) in enumerate(headers, 1):
        c = ws.cell(row=2, column=i, value=h)
        c.font = font_h
        c.alignment = center
        c.fill = fill
        c.border = border

    # ─── Linhas de dados ────────────────────────────────────────────────
    for idx, s in enumerate(snapshot.get("simulacoes", []), start=3):
        ta = s.get("taxas_atual", {})
        tn = s.get("taxas_novo", {})

        nomes_camp = s.get("nomes_campanha") or []
        em_camp = bool(s.get("em_campanha"))

        row = [
            # Identificação
            s["item_id"],
            s.get("sku") or "",
            (s.get("titulo") or "")[:60],
            s.get("modalidade", ""),
            # Antes
            ta.get("preco", 0),
            ta.get("tarifa_classico", 0) or "",
            ta.get("tarifa_premium", 0) or "",
            (ta.get("percentual_tarifa", 0) or 0) / 100,
            ta.get("tarifa_fixa", 0),
            ta.get("frete", 0),
            # Depois
            tn.get("preco", 0),
            tn.get("tarifa_classico", 0) or "",
            tn.get("tarifa_premium", 0) or "",
            (tn.get("percentual_tarifa", 0) or 0) / 100,
            tn.get("tarifa_fixa", 0),
            tn.get("frete", 0),
            # Fase 1
            s["custo"],
            s["margem_atual_pct"] / 100,
            s["fase1_acao"],
            # Campanha — "Preço na Campanha" = U (preço inflado a publicar);
            # cliente paga P após o desconto V. (Fallback p/ snapshots antigos.)
            s.get("preco_inflado", s["deal_price"]),
            s["desconto_pct"] / 100,
            s["liq_final_deal_projetado"],
            # Resultado — margem real no preço final P
            s.get("margem_campanha_pct", 20) / 100,
            s["liq_final_deal_projetado"],
            # INFO ML (Leva 5.8) — col Y, Z
            "Sim" if em_camp else "Não",
            ", ".join(nomes_camp) if nomes_camp else "",
        ]

        for col, val in enumerate(row, 1):
            c = ws.cell(row=idx, column=col, value=val)
            c.font = font_d
            c.border = border

            # Formato monetário (colunas em R$)
            if col in (5, 6, 7, 9, 10, 11, 12, 13, 15, 16, 17, 20, 22, 24):
                c.number_format = "R$ #,##0.00"
            # Formato percentual
            elif col in (8, 14, 18, 21, 23):
                c.number_format = "0.00%"

            # Cor de fundo da "Ação fase 1" (col 19)
            if col == 19:
                if val in ("subir", "preco_aumentado"):
                    c.fill = fill_subir
                elif val in ("baixar", "preco_travado_abaixo_79"):
                    c.fill = fill_travado
                else:
                    c.fill = fill_mantido

    # ─── Aba de exceções ────────────────────────────────────────────────
    excecoes = snapshot.get("excecoes", [])
    if excecoes:
        ws_e = wb.create_sheet("Exceções")
        for i, h in enumerate(
            ["MLB", "SKU", "Título", "Tipo", "Motivo/Detalhe"], 1,
        ):
            c = ws_e.cell(row=1, column=i, value=h)
            c.font = font_h
            c.alignment = center
            c.fill = fill_id
            c.border = border
        for idx, e in enumerate(excecoes, start=2):
            ws_e.cell(row=idx, column=1, value=e.get("item_id"))
            ws_e.cell(row=idx, column=2, value=e.get("sku") or "")
            ws_e.cell(row=idx, column=3, value=(e.get("titulo") or "")[:60])
            ws_e.cell(row=idx, column=4, value=e.get("tipo"))
            ws_e.cell(
                row=idx, column=5,
                value=e.get("motivo") or e.get("detalhe", ""),
            )
        for col_letra, w in {
            "A": 16, "B": 22, "C": 50, "D": 25, "E": 60,
        }.items():
            ws_e.column_dimensions[col_letra].width = w

    # ─── Larguras de coluna na aba principal ────────────────────────────
    larguras = {
        "A": 16, "B": 22, "C": 40, "D": 12,
        "E": 12, "F": 14, "G": 14, "H": 9, "I": 11, "J": 9,
        "K": 12, "L": 14, "M": 14, "N": 9, "O": 11, "P": 9,
        "Q": 11, "R": 13, "S": 16,
        "T": 12, "U": 12, "V": 16,
        "W": 13, "X": 12,
        "Y": 13, "Z": 40,
    }
    for col_letra, w in larguras.items():
        ws.column_dimensions[col_letra].width = w

    ws.row_dimensions[1].height = 26
    ws.row_dimensions[2].height = 32

    # Painéis congelados: 2 linhas de cabeçalho + 4 colunas de identificação
    ws.freeze_panes = "E3"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
