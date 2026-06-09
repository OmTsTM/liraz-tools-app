"""Renderer do PDF do relatório diário (Fatia 1).

matplotlib gera os gráficos como PNG em buffer; reportlab compõe o documento.
Tudo em memória — devolve `bytes` pra serializar como response do FastAPI.

Layout (~2 páginas A4):
- Página 1: header + grid de KPIs + gráfico de vendas por hora + top 10 SKUs
- Página 2: tabela top 20 SKUs detalhada + sugestões pra amanhã + nota de rodapé

Acentuação: usa Helvetica (built-in) com encoding default. Funciona pro PT-BR
"básico" (ã, ç, ó passam); se algum caractere quebrar visualmente, dá pra
trocar pra TTF Unicode depois.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # Sem display, evita backend conflicts no Windows.

import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from liraz_tools.domain.relatorio.entity import (
    AcumuladoMes,
    Cupons,
    KPIs,
    LucroPorCategoria,
    MetricasEnvio,
    RelatorioDiario,
    SkuVendido,
    Sugestao,
)

# ──────────────────────────────────────────────────────────────────────────
# Fontes — tenta TTF Unicode (Windows tem Arial em system32) com fallback
# pra Helvetica built-in. Suporte a acentos PT-BR é o critério.
# ──────────────────────────────────────────────────────────────────────────


def _registrar_fonte_unicode() -> str:
    """Tenta registrar Arial TTF do Windows. Retorna nome da fonte a usar."""
    candidatos = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    candidatos_bold = [
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for normal, bold in zip(candidatos, candidatos_bold, strict=False):
        if normal.exists():
            try:
                pdfmetrics.registerFont(TTFont("AppFont", str(normal)))
                if bold.exists():
                    pdfmetrics.registerFont(TTFont("AppFont-Bold", str(bold)))
                else:
                    pdfmetrics.registerFont(TTFont("AppFont-Bold", str(normal)))
                return "AppFont"
            except Exception:
                continue
    return "Helvetica"


FONTE = _registrar_fonte_unicode()
FONTE_BOLD = "AppFont-Bold" if FONTE == "AppFont" else "Helvetica-Bold"


# ──────────────────────────────────────────────────────────────────────────
# Identidade visual ML
# ──────────────────────────────────────────────────────────────────────────
# Amarelo institucional do Mercado Livre (PMS Yellow C aproximado) + azul
# do logotipo. Usados na capa e em headers decorativos das páginas internas.
# Pra futuros marketplaces (Shopee, Amazon), cada um vai ter seu próprio
# `_marketplace_theme`. Por enquanto só ML.
ML_AMARELO = colors.HexColor("#FFE600")
ML_AZUL_LOGO = colors.HexColor("#2D3277")
ML_AZUL_LINK = colors.HexColor("#3483FA")

# Logo oficial do Mercado Livre. Carregada uma vez no boot do módulo.
_LOGO_ML_PATH = Path(__file__).parent / "assets" / "logo_ml.png"
_LOGO_ML = ImageReader(str(_LOGO_ML_PATH)) if _LOGO_ML_PATH.exists() else None
# Proporção da logo (largura / altura). Detectada via PIL/reportlab pra
# manter aspect ratio em qualquer dimensão de rendering.
_LOGO_ML_RATIO: float = 1.0
if _LOGO_ML is not None:
    _w, _h = _LOGO_ML.getSize()
    if _h > 0:
        _LOGO_ML_RATIO = _w / _h

_MESES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def _data_extensa(d: Any) -> str:
    """Formata data como '1 de junho de 2026' (BR)."""
    return f"{d.day} de {_MESES_PT[d.month - 1]} de {d.year}"


# ──────────────────────────────────────────────────────────────────────────
# Helpers de formatação
# ──────────────────────────────────────────────────────────────────────────


def _fmt_brl(v: float) -> str:
    """Formata R$ no estilo BR: 1.234,56."""
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v: float | None, casas: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.{casas}f}%"


def _fmt_delta(v: float | None) -> tuple[str, colors.Color]:
    """Formata delta percentual + cor (verde positivo, vermelho negativo)."""
    if v is None:
        return "—", colors.gray
    sinal = "+" if v >= 0 else ""
    cor = colors.HexColor("#16a34a") if v >= 0 else colors.HexColor("#dc2626")
    return f"{sinal}{v * 100:.1f}%", cor


def _fmt_int(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def _label_sku_com_titulo(sku_vendido: SkuVendido, max_chars: int = 48) -> str:
    """Compõe label legível pra eixos de gráfico:
    - Tem SKU e título: "SKU — Título truncado"
    - Tem só título:    "Título truncado"
    - Sem nem título:   o item_id (MLB) como fallback
    """
    sku = sku_vendido.sku
    titulo = sku_vendido.titulo
    if sku and titulo:
        head = f"{sku} — {titulo}"
    elif titulo:
        head = titulo
    elif sku:
        head = sku
    else:
        return sku_vendido.item_id[:max_chars]
    return head[:max_chars] + ("…" if len(head) > max_chars else "")


# ──────────────────────────────────────────────────────────────────────────
# Gráficos (matplotlib → PNG bytes)
# ──────────────────────────────────────────────────────────────────────────


def _grafico_vendas_por_hora(
    vendas_por_hora: list[tuple[int, float]],
) -> bytes:
    """Barras: receita por hora 0-23."""
    fig, ax = plt.subplots(figsize=(10, 3.4), dpi=130)
    horas = list(range(24))
    receitas = [r for (_, r) in vendas_por_hora]
    ax.bar(horas, receitas, color="#3b82f6", width=0.7)
    ax.set_xticks(horas[::2])
    ax.set_xticklabels([f"{h:02d}h" for h in horas[::2]], fontsize=9)
    ax.set_ylabel("Receita (R$)", fontsize=9)
    ax.set_title("Vendas por hora (BRT)", fontsize=11, loc="left", pad=8)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="y", labelsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _grafico_curva_acumulada(acum: AcumuladoMes) -> bytes:
    """Linha: receita acumulada do mês + projeção até o fim do mês."""
    fig, ax = plt.subplots(figsize=(10, 3.8), dpi=130)
    if acum.curva:
        dias_iso = [d for (d, _) in acum.curva]
        valores = [v for (_, v) in acum.curva]
        xs = list(range(1, len(dias_iso) + 1))
        ax.plot(xs, valores, color="#3b82f6", linewidth=2, marker="o", markersize=3)
        # Linha de projeção do dia atual até o fim do mês
        if acum.dias_decorridos < acum.dias_totais_mes and valores:
            proj_xs = [acum.dias_decorridos, acum.dias_totais_mes]
            proj_ys = [valores[-1], acum.receita_projetada_mes]
            ax.plot(
                proj_xs, proj_ys, color="#94a3b8",
                linestyle="--", linewidth=1.5,
            )
            ax.scatter(
                [acum.dias_totais_mes], [acum.receita_projetada_mes],
                color="#94a3b8", s=30, zorder=5,
            )
            ax.annotate(
                f"projeção: {_fmt_brl(acum.receita_projetada_mes)}",
                xy=(acum.dias_totais_mes, acum.receita_projetada_mes),
                xytext=(-90, 8),
                textcoords="offset points",
                fontsize=7, color="#64748b",
            )
    ax.set_xlim(0.5, acum.dias_totais_mes + 0.5)
    ax.set_xticks(list(range(1, acum.dias_totais_mes + 1, max(1, acum.dias_totais_mes // 10))))
    ax.set_xlabel("Dia do mês", fontsize=9)
    ax.set_ylabel("Receita acumulada (R$)", fontsize=9)
    ax.set_title(
        f"Acumulado do mês — dia {acum.dias_decorridos} de {acum.dias_totais_mes}",
        fontsize=11, loc="left", pad=8,
    )
    ax.grid(linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _grafico_lucro_por_categoria(
    categorias: list[LucroPorCategoria], n: int = 8,
) -> bytes:
    """Barras horizontais: lucro por categoria (top N)."""
    top = categorias[:n]
    fig, ax = plt.subplots(
        figsize=(10, max(3.5, 0.55 * len(top))), dpi=130,
    )
    if top:
        nomes = [
            (c.nome_categoria or c.category_id)[:34] for c in reversed(top)
        ]
        lucros = [c.lucro for c in reversed(top)]
        receitas = [c.receita for c in reversed(top)]
        ax.barh(nomes, receitas, color="#cbd5e1", label="Receita")
        ax.barh(nomes, lucros, color="#10b981", label="Lucro")
        ax.legend(fontsize=8, loc="lower right")
        # Anota o valor da receita no fim de cada barra
        for i, r in enumerate(receitas):
            ax.text(
                r, i, f" {_fmt_brl(r)}", va="center", fontsize=7,
                color="#475569",
            )
    ax.set_xlabel("R$", fontsize=9)
    ax.set_title(
        f"Receita vs lucro por categoria (top {len(top)})",
        fontsize=11, loc="left", pad=8,
    )
    ax.tick_params(labelsize=8)
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _grafico_top_skus_por_lucro(skus: list[SkuVendido], n: int = 10) -> bytes:
    """Barras horizontais: top N SKUs por LUCRO (não receita)."""
    skus_com_lucro = [s for s in skus if s.lucro is not None and s.lucro > 0]
    skus_com_lucro.sort(key=lambda s: s.lucro or 0, reverse=True)
    top = skus_com_lucro[:n]
    fig, ax = plt.subplots(
        figsize=(10, max(3.5, 0.55 * len(top))), dpi=130,
    )
    if top:
        nomes = [_label_sku_com_titulo(s) for s in reversed(top)]
        valores = [s.lucro or 0 for s in reversed(top)]
        bars = ax.barh(nomes, valores, color="#0ea5e9")
        for bar, v in zip(bars, valores, strict=False):
            ax.text(
                v, bar.get_y() + bar.get_height() / 2,
                f" {_fmt_brl(v)}", va="center", fontsize=8,
            )
    ax.set_xlabel("Lucro (R$)", fontsize=9)
    ax.set_title(
        f"Top {len(top)} SKUs por lucro líquido",
        fontsize=11, loc="left", pad=8,
    )
    ax.tick_params(labelsize=8)
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _grafico_top_skus(skus: list[SkuVendido], n: int = 10) -> bytes:
    """Barras horizontais: top N SKUs por receita."""
    top = skus[:n]
    fig, ax = plt.subplots(
        figsize=(10, max(3.5, 0.55 * len(top))), dpi=130,
    )
    nomes = [_label_sku_com_titulo(s) for s in reversed(top)]
    valores = [s.receita for s in reversed(top)]
    bars = ax.barh(nomes, valores, color="#10b981")
    ax.set_xlabel("Receita (R$)", fontsize=9)
    ax.set_title(
        f"Top {len(top)} SKUs por receita",
        fontsize=11, loc="left", pad=8,
    )
    ax.tick_params(axis="y", labelsize=8)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for bar, v in zip(bars, valores, strict=False):
        ax.text(
            v, bar.get_y() + bar.get_height() / 2,
            f" {_fmt_brl(v)}", va="center", fontsize=8,
        )
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# Blocos do PDF (cada bloco devolve uma lista de "flowables" do reportlab)
# ──────────────────────────────────────────────────────────────────────────


def _estilos() -> dict[str, ParagraphStyle]:
    sheet = getSampleStyleSheet()
    base = sheet["Normal"].clone("Base")
    base.fontName = FONTE
    base.fontSize = 10
    base.leading = 12

    titulo = base.clone("Titulo")
    titulo.fontName = FONTE_BOLD
    titulo.fontSize = 18
    titulo.leading = 22
    titulo.textColor = colors.HexColor("#0f172a")

    h2 = base.clone("H2")
    h2.fontName = FONTE_BOLD
    h2.fontSize = 12
    h2.leading = 16
    h2.spaceBefore = 8
    h2.textColor = colors.HexColor("#1e293b")

    kpi_label = base.clone("KPILabel")
    kpi_label.fontSize = 8
    kpi_label.textColor = colors.HexColor("#64748b")

    kpi_value = base.clone("KPIValue")
    kpi_value.fontName = FONTE_BOLD
    kpi_value.fontSize = 16
    kpi_value.leading = 18
    kpi_value.textColor = colors.HexColor("#0f172a")

    kpi_delta = base.clone("KPIDelta")
    kpi_delta.fontSize = 8

    footer = base.clone("Footer")
    footer.fontSize = 7
    footer.textColor = colors.HexColor("#94a3b8")

    return {
        "base": base, "titulo": titulo, "h2": h2,
        "kpi_label": kpi_label, "kpi_value": kpi_value, "kpi_delta": kpi_delta,
        "footer": footer,
    }


def _bloco_header(rel: RelatorioDiario, est: dict[str, Any]) -> list[Any]:
    data_fmt = rel.dia.strftime("%d/%m/%Y")
    titulo = Paragraph(
        f"{rel.profile_name} — Relatório de {data_fmt}",
        est["titulo"],
    )
    return [titulo, Spacer(1, 6)]


def _kpi_cell(
    label: str,
    value: str,
    deltas: list[tuple[str, str, colors.Color]],
    est: dict[str, Any],
) -> list[Any]:
    """Compõe o conteúdo dum 'card' de KPI: label, valor grande, deltas coloridos."""
    inner: list[Any] = [
        Paragraph(label, est["kpi_label"]),
        Paragraph(value, est["kpi_value"]),
    ]
    for prefixo, texto, cor in deltas:
        st = est["kpi_delta"].clone(f"d{prefixo}")
        st.textColor = cor
        inner.append(Paragraph(f"<b>{prefixo}</b> {texto}", st))
    return inner


def _bloco_kpis(kpis: KPIs, est: dict[str, Any]) -> Table:
    d_ont_r, c_ont_r = _fmt_delta(kpis.delta_receita_vs_ontem)
    d_7d_r, c_7d_r = _fmt_delta(kpis.delta_receita_vs_media_7d)
    d_dow_r, c_dow_r = _fmt_delta(kpis.delta_receita_vs_mesma_dow)
    d_ont_l, c_ont_l = _fmt_delta(kpis.delta_lucro_vs_ontem)
    d_7d_l, c_7d_l = _fmt_delta(kpis.delta_lucro_vs_media_7d)
    d_dow_l, c_dow_l = _fmt_delta(kpis.delta_lucro_vs_mesma_dow)
    d_ont_p, c_ont_p = _fmt_delta(kpis.delta_pedidos_vs_ontem)
    d_7d_p, c_7d_p = _fmt_delta(kpis.delta_pedidos_vs_media_7d)
    d_dow_p, c_dow_p = _fmt_delta(kpis.delta_pedidos_vs_mesma_dow)

    receita_cell = _kpi_cell(
        "RECEITA BRUTA", _fmt_brl(kpis.receita_bruta),
        [
            ("vs ontem:", d_ont_r, c_ont_r),
            ("vs 7d:", d_7d_r, c_7d_r),
            ("vs DOW:", d_dow_r, c_dow_r),
        ],
        est,
    )
    lucro_cell = _kpi_cell(
        "LUCRO LÍQUIDO*", _fmt_brl(kpis.lucro_liquido),
        [
            ("vs ontem:", d_ont_l, c_ont_l),
            ("vs 7d:", d_7d_l, c_7d_l),
            ("vs DOW:", d_dow_l, c_dow_l),
        ],
        est,
    )
    pedidos_cell = _kpi_cell(
        "PEDIDOS / UNIDADES",
        f"{_fmt_int(kpis.total_pedidos)} / {_fmt_int(kpis.total_unidades)}",
        [
            ("vs ontem:", d_ont_p, c_ont_p),
            ("vs 7d:", d_7d_p, c_7d_p),
            ("vs DOW:", d_dow_p, c_dow_p),
        ],
        est,
    )
    ticket_cell = _kpi_cell(
        "TICKET MÉDIO",
        _fmt_brl(kpis.ticket_medio) if kpis.ticket_medio else "—",
        [], est,
    )
    margem_cell = _kpi_cell(
        "MARGEM MÉDIA", _fmt_pct(kpis.margem_media_pct), [], est,
    )
    cancel_cell = _kpi_cell(
        "CANCELAMENTO", _fmt_pct(kpis.taxa_cancelamento_pct), [], est,
    )

    data = [
        [receita_cell, lucro_cell, pedidos_cell],
        [ticket_cell, margem_cell, cancel_cell],
    ]
    t = Table(
        data,
        colWidths=[5.7 * cm, 5.7 * cm, 5.7 * cm],
        rowHeights=[2.6 * cm, 1.6 * cm],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _bloco_tabela_skus(
    skus: list[SkuVendido], n: int = 20, _est: dict[str, Any] | None = None,
) -> Table:
    """Top N SKUs em tabela: SKU | Título | Unidades | Receita | Lucro | Margem."""
    header = ["SKU", "Título", "Un.", "Receita", "Lucro", "Margem"]
    rows = [header]
    for s in skus[:n]:
        sku_lbl = (s.sku or s.item_id)[:18]
        titulo = (s.titulo or "—")[:38]
        lucro_str = _fmt_brl(s.lucro) if s.lucro is not None else "—"
        margem_str = _fmt_pct(s.margem_pct) if s.margem_pct is not None else "—"
        rows.append([
            sku_lbl, titulo, str(s.unidades),
            _fmt_brl(s.receita), lucro_str, margem_str,
        ])
    t = Table(
        rows,
        colWidths=[2.8 * cm, 6.5 * cm, 1.0 * cm, 2.6 * cm, 2.4 * cm, 1.7 * cm],
    )
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), FONTE_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), FONTE),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor("#f8fafc"),
        ]),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
    ]))
    return t


def _bloco_skus_sangrando(skus: list[SkuVendido]) -> Table | None:
    """Tabela de SKUs com margem < 15% e que tiveram receita > R$ 50 no dia.

    Filtro de receita evita ruído de SKU com 1 venda sub-piso. Devolve None
    se não há sangrando (= não renderiza a seção).
    """
    sangrando = [
        s for s in skus
        if s.margem_pct is not None and s.margem_pct < 0.15 and s.receita > 50
    ]
    if not sangrando:
        return None
    sangrando.sort(key=lambda s: s.margem_pct or 0)  # piores primeiro
    rows = [["SKU", "Título", "Un.", "Receita", "Lucro", "Margem"]]
    for s in sangrando[:15]:
        rows.append([
            (s.sku or s.item_id)[:18],
            (s.titulo or "—")[:36],
            str(s.unidades),
            _fmt_brl(s.receita),
            _fmt_brl(s.lucro) if s.lucro is not None else "—",
            _fmt_pct(s.margem_pct),
        ])
    t = Table(
        rows,
        colWidths=[2.8 * cm, 6.5 * cm, 1.0 * cm, 2.6 * cm, 2.4 * cm, 1.7 * cm],
    )
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), FONTE_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), FONTE),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#991b1b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.HexColor("#fef2f2"), colors.HexColor("#fee2e2"),
        ]),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#fca5a5")),
    ]))
    return t


def _bloco_cupons_e_acumulado(
    cupons: Cupons,
    acum: AcumuladoMes,
    envio: MetricasEnvio,
    est: dict[str, Any],
) -> Table:
    """Grid 3x2 com cards: cupons, cashback, acumulado, projeção, SLA, pendentes."""
    cupons_cell = [
        Paragraph("CUPONS APLICADOS", est["kpi_label"]),
        Paragraph(_fmt_brl(cupons.total_descontado_cupom), est["kpi_value"]),
        Paragraph(
            f"em {cupons.qtd_pedidos_com_cupom} pedido"
            f"{'s' if cupons.qtd_pedidos_com_cupom != 1 else ''}",
            est["kpi_delta"],
        ),
    ]
    meli_cell = [
        Paragraph("MERCADO PUNTOS / CASHBACK", est["kpi_label"]),
        Paragraph(_fmt_brl(cupons.total_meli_promo), est["kpi_value"]),
        Paragraph(
            f"em {cupons.qtd_pedidos_com_meli_promo} pedido"
            f"{'s' if cupons.qtd_pedidos_com_meli_promo != 1 else ''}",
            est["kpi_delta"],
        ),
    ]
    progresso = (
        acum.dias_decorridos / acum.dias_totais_mes * 100
        if acum.dias_totais_mes > 0 else 0
    )
    acum_cell = [
        Paragraph("ACUMULADO DO MÊS", est["kpi_label"]),
        Paragraph(_fmt_brl(acum.receita_acumulada), est["kpi_value"]),
        Paragraph(
            f"em {acum.pedidos_acumulados} pedidos "
            f"({progresso:.0f}% do mês)",
            est["kpi_delta"],
        ),
    ]
    proj_cell = [
        Paragraph("PROJEÇÃO FIM DO MÊS", est["kpi_label"]),
        Paragraph(_fmt_brl(acum.receita_projetada_mes), est["kpi_value"]),
        Paragraph(
            f"lucro projetado: {_fmt_brl(acum.lucro_projetado_mes)}",
            est["kpi_delta"],
        ),
    ]
    sla_valor = (
        f"{envio.tempo_medio_dias:.1f} dias"
        if envio.tempo_medio_dias is not None
        else "—"
    )
    sla_sub = (
        f"mediana {envio.tempo_mediano_dias:.1f}d · "
        f"{envio.qtd_entregue} entregue"
        f"{'s' if envio.qtd_entregue != 1 else ''} / "
        f"{envio.qtd_total_pedidos}"
        if envio.tempo_medio_dias is not None
        else f"{envio.qtd_total_pedidos} pedidos com shipment"
    )
    sla_cell = [
        Paragraph("SLA ENVIO MÉDIO", est["kpi_label"]),
        Paragraph(sla_valor, est["kpi_value"]),
        Paragraph(sla_sub, est["kpi_delta"]),
    ]
    pior_atraso_str = (
        f"{envio.pior_atraso_dias} dias"
        if envio.pior_atraso_dias is not None
        else "—"
    )
    pendentes_cell = [
        Paragraph("ENVIOS PENDENTES", est["kpi_label"]),
        Paragraph(str(envio.qtd_pendente), est["kpi_value"]),
        Paragraph(
            f"pior atraso entregue: {pior_atraso_str}",
            est["kpi_delta"],
        ),
    ]
    data = [
        [cupons_cell, meli_cell, sla_cell],
        [acum_cell, proj_cell, pendentes_cell],
    ]
    t = Table(
        data,
        colWidths=[5.7 * cm, 5.7 * cm, 5.7 * cm],
        rowHeights=[1.9 * cm, 1.9 * cm],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _bloco_sugestoes(
    sugestoes: list[Sugestao], est: dict[str, Any],
) -> list[Any]:
    bullets: list[Any] = [Paragraph("Sugestões para amanhã", est["h2"])]
    if not sugestoes:
        p = Paragraph(
            "Sem alertas hoje — operação dentro do esperado.",
            est["base"],
        )
        bullets.append(p)
        return bullets
    for s in sugestoes:
        bold = est["base"].clone(f"s{s.categoria}")
        bold.fontName = FONTE_BOLD
        bullets.append(Spacer(1, 4))
        bullets.append(Paragraph(f"• {s.titulo}", bold))
        bullets.append(Paragraph(s.descricao, est["base"]))
    return bullets


def _bloco_footer(rel: RelatorioDiario, est: dict[str, Any]) -> Paragraph:
    nota = (
        f"Gerado em {rel.gerado_em_iso[:19].replace('T', ' ')} UTC. "
        "* Lucro líquido = receita - comissão ML (sale_fee) - custo "
        "(custos.xlsx) - frete pago pelo vendedor (R$ 17-25 pra SKUs >= R$ 79 "
        "com frete grátis) - tarifa fixa ML (R$ 4-8,55 pra SKUs < R$ 79). "
        f"SKUs sem custo cadastrado: {rel.skus_sem_custo} "
        "(ficam fora do lucro líquido total)."
    )
    return Paragraph(nota, est["footer"])


# ──────────────────────────────────────────────────────────────────────────
# Pipeline principal
# ──────────────────────────────────────────────────────────────────────────


def _desenhar_capa(canvas: Any, _doc: Any, rel: RelatorioDiario) -> None:
    """Callback do template "capa" — pinta a capa do relatório.

    Não usa flowables: desenha tudo direto no canvas pra ter controle total
    sobre layout (full-bleed amarelo + posicionamento absoluto). Usa a logo
    oficial do ML em PNG (`assets/logo_ml.png`).
    """
    largura, altura = A4
    # Fundo amarelo cheio
    canvas.setFillColor(ML_AMARELO)
    canvas.rect(0, 0, largura, altura, stroke=0, fill=1)

    # Logo oficial no canto superior esquerdo (altura fixa = 2cm, largura
    # proporcional ao aspect ratio detectado no boot). drawImage usa o canto
    # INFERIOR-esquerdo como (x, y).
    if _LOGO_ML is not None:
        altura_logo = 2.0 * cm
        largura_logo = altura_logo * _LOGO_ML_RATIO
        canvas.drawImage(
            _LOGO_ML,
            2.5 * cm, altura - 2.5 * cm - altura_logo,
            width=largura_logo, height=altura_logo,
            mask="auto",
            preserveAspectRatio=True,
        )

    # Linha fina divisória abaixo da logo
    canvas.setStrokeColor(ML_AZUL_LOGO)
    canvas.setLineWidth(2)
    canvas.line(
        2.5 * cm, altura - 4.8 * cm,
        largura - 2.5 * cm, altura - 4.8 * cm,
    )

    # Bloco central — nome da loja + título
    canvas.setFillColor(ML_AZUL_LOGO)
    canvas.setFont(FONTE_BOLD, 42)
    nome = rel.profile_name
    largura_texto = canvas.stringWidth(nome, FONTE_BOLD, 42)
    canvas.drawString((largura - largura_texto) / 2, altura / 2 + 1.5 * cm, nome)

    canvas.setFont(FONTE, 18)
    subtitulo = "Relatório de vendas"
    largura_sub = canvas.stringWidth(subtitulo, FONTE, 18)
    canvas.drawString(
        (largura - largura_sub) / 2,
        altura / 2 + 0.5 * cm,
        subtitulo,
    )

    canvas.setFont(FONTE_BOLD, 20)
    data_str = _data_extensa(rel.dia)
    largura_data = canvas.stringWidth(data_str, FONTE_BOLD, 20)
    canvas.drawString(
        (largura - largura_data) / 2,
        altura / 2 - 0.6 * cm,
        data_str,
    )

    # Faixa de rodapé com info de geração
    canvas.setFillColor(ML_AZUL_LOGO)
    canvas.rect(0, 0, largura, 2.5 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont(FONTE, 9)
    canvas.drawString(
        2.5 * cm, 1 * cm,
        f"Gerado em {rel.gerado_em_iso[:19].replace('T', ' ')} UTC",
    )
    canvas.setFont(FONTE_BOLD, 9)
    rodape_dir = "LiraZ Tools"
    largura_rodape = canvas.stringWidth(rodape_dir, FONTE_BOLD, 9)
    canvas.drawString(largura - 2.5 * cm - largura_rodape, 1 * cm, rodape_dir)


def _desenhar_header_pagina(canvas: Any, doc: Any, rel: RelatorioDiario) -> None:
    """Callback do template "content" — header decorativo fino nas páginas
    de conteúdo. Faixa amarela curta no topo + logo ML pequena à esquerda
    + nome da loja e data à direita. Identifica visualmente que o conteúdo
    vem do marketplace ML.
    """
    largura, altura = A4
    # Faixa amarela superior fina (full-bleed)
    canvas.setFillColor(ML_AMARELO)
    canvas.rect(0, altura - 0.9 * cm, largura, 0.9 * cm, stroke=0, fill=1)

    # Logo pequena à esquerda na faixa amarela
    if _LOGO_ML is not None:
        altura_logo = 0.7 * cm
        largura_logo = altura_logo * _LOGO_ML_RATIO
        canvas.drawImage(
            _LOGO_ML,
            1.5 * cm, altura - 0.8 * cm,
            width=largura_logo, height=altura_logo,
            mask="auto",
            preserveAspectRatio=True,
        )

    # Texto identificador à direita
    canvas.setFillColor(ML_AZUL_LOGO)
    canvas.setFont(FONTE_BOLD, 9)
    direita = f"{rel.profile_name} · {rel.dia.strftime('%d/%m/%Y')}"
    largura_dir = canvas.stringWidth(direita, FONTE_BOLD, 9)
    canvas.drawString(largura - 1.5 * cm - largura_dir, altura - 0.55 * cm, direita)

    # Footer com número da página
    canvas.setFont(FONTE, 8)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    canvas.drawCentredString(
        largura / 2, 0.8 * cm,
        f"Página {doc.page} · {rel.profile_name} · {rel.dia.isoformat()}",
    )


def renderizar_pdf(rel: RelatorioDiario) -> bytes:
    """Compõe o PDF do relatório diário e devolve como bytes.

    Layout:
    - Página 1: capa cheia amarela com identidade ML, nome da loja e data
    - Páginas 2+: header decorativo fino + conteúdo (KPIs, gráficos, tabelas)

    Callbacks de canvas (`onFirstPage`, `onLaterPages`) desenham capa e
    headers antes dos flowables, garantindo que o background não interfira
    com o layout natural.
    """
    buf = io.BytesIO()
    largura_pg, altura_pg = A4
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        title=f"Relatorio {rel.profile_name} {rel.dia.isoformat()}",
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=2.0 * cm, bottomMargin=1.8 * cm,
    )
    # Frame da capa: ocupa página inteira, mas o conteúdo de flowables é
    # vazio — usamos só pra a página existir e o `onPage` pintar a capa.
    frame_capa = Frame(
        0, 0, largura_pg, altura_pg, id="capa",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    # Frame das páginas de conteúdo: respeita as margens do doc.
    frame_content = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="content",
    )
    doc.addPageTemplates([
        PageTemplate(
            id="capa", frames=[frame_capa],
            onPage=lambda c, d: _desenhar_capa(c, d, rel),
        ),
        PageTemplate(
            id="content", frames=[frame_content],
            onPage=lambda c, d: _desenhar_header_pagina(c, d, rel),
        ),
    ])
    est = _estilos()
    story: list[Any] = []

    # ─── Página 1: CAPA ─────────────────────────────────────────────────
    # Pinta a capa via callback do template "capa" e transiciona pro
    # template de conteúdo na próxima página. Um Paragraph "invisível"
    # (texto vazio) é necessário pra reportlab renderizar a página da capa —
    # PageBreak sozinho ou Spacer mínimo são descartados.
    story.append(Paragraph(" ", est["base"]))
    story.append(NextPageTemplate("content"))
    story.append(PageBreak())

    # ─── Página 2: KPIs + vendas por hora + top por receita ────────────
    story.append(_bloco_kpis(rel.kpis, est))
    story.append(Spacer(1, 10))

    png_hora = _grafico_vendas_por_hora(rel.vendas_por_hora)
    img1 = Image(io.BytesIO(png_hora), width=18 * cm, height=6.2 * cm)
    story.append(img1)
    story.append(Spacer(1, 6))

    if rel.skus:
        png_top = _grafico_top_skus(rel.skus, n=10)
        n = min(10, len(rel.skus))
        # Mesma proporção do figsize matplotlib (10:0.55*n com altura mínima 3.5).
        altura_cm = max(6.5, 1.0 * n)
        img2 = Image(io.BytesIO(png_top), width=18 * cm, height=altura_cm * cm)
        story.append(img2)

    # ─── Página 2: acumulado do mês + cupons + top por LUCRO ──────────
    story.append(PageBreak())
    story.append(Paragraph("Cupons, cashback, SLA e progresso do mês", est["h2"]))
    story.append(
        _bloco_cupons_e_acumulado(
            rel.cupons, rel.acumulado_mes, rel.metricas_envio, est,
        ),
    )
    story.append(Spacer(1, 10))

    png_curva = _grafico_curva_acumulada(rel.acumulado_mes)
    story.append(Image(io.BytesIO(png_curva), width=18 * cm, height=6.8 * cm))
    story.append(Spacer(1, 12))

    # Top por lucro — só se houver SKUs com lucro positivo calculado
    if rel.skus and any(s.lucro is not None and s.lucro > 0 for s in rel.skus):
        png_lucro = _grafico_top_skus_por_lucro(rel.skus, n=10)
        skus_lucro_count = sum(
            1 for s in rel.skus if s.lucro is not None and s.lucro > 0
        )
        n_lucro = min(10, skus_lucro_count)
        altura_lucro = max(6.5, 1.0 * n_lucro)
        story.append(Image(io.BytesIO(png_lucro), width=18 * cm, height=altura_lucro * cm))

    # ─── Página 3: categoria + sangrando + tabela completa ────────────
    story.append(PageBreak())
    story.append(Paragraph("Lucro por categoria ML", est["h2"]))
    if rel.lucro_por_categoria:
        png_cat = _grafico_lucro_por_categoria(rel.lucro_por_categoria, n=8)
        n_cat = min(8, len(rel.lucro_por_categoria))
        altura_cat = max(5.5, 1.0 * n_cat)
        story.append(Image(io.BytesIO(png_cat), width=18 * cm, height=altura_cat * cm))
    else:
        story.append(Paragraph("Sem dados de categoria.", est["base"]))
    story.append(Spacer(1, 10))

    # SKUs sangrando (margem < 15%)
    sangrando = _bloco_skus_sangrando(rel.skus)
    if sangrando is not None:
        story.append(Paragraph(
            "⚠ SKUs sangrando (margem < 15%)",
            est["h2"],
        ))
        story.append(sangrando)
        story.append(Spacer(1, 10))

    story.append(Paragraph("Detalhe — top 20 SKUs por receita", est["h2"]))
    if rel.skus:
        story.append(_bloco_tabela_skus(rel.skus, n=20))
    else:
        story.append(Paragraph("Nenhuma venda registrada.", est["base"]))
    story.append(Spacer(1, 12))

    story.extend(_bloco_sugestoes(rel.sugestoes, est))
    story.append(Spacer(1, 12))
    story.append(_bloco_footer(rel, est))

    doc.build(story)
    return buf.getvalue()
