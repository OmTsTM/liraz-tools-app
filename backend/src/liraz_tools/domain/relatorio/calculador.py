"""KPIs + agregação por SKU + heurísticas de sugestão (Fatia 1).

Recebe a lista de `_ItemLinha` do coletor + custos.xlsx + histórico
persistido pra comparativos. Devolve um `RelatorioDiario` imutável.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import UTC, datetime
from datetime import date as date_type
from typing import Any
from uuid import UUID

from liraz_tools.domain.relatorio.coletor import _ItemLinha
from liraz_tools.domain.relatorio.custos_logisticos import CustoLogisticoPorVenda
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
from liraz_tools.infrastructure.repositories.relatorio_diario_repository import (
    KpisHistorico,
)


def _delta_pct(atual: float, base: float) -> float | None:
    """`(atual - base) / base`. None se base == 0 (evita divisão por 0)."""
    if base == 0:
        return None
    return (atual - base) / base


def _agregar_por_sku(
    linhas: list[_ItemLinha],
    custos_por_sku: dict[str, float],
    custos_logisticos_por_item: dict[str, CustoLogisticoPorVenda],
) -> tuple[list[SkuVendido], int]:
    """Agrupa linhas por item_id, soma métricas, calcula lucro REAL.

    `custos_por_sku` mapa SKU → custo unitário (do `custos.xlsx`). Itens
    sem SKU ou com SKU não cadastrado ficam com `custo=None`.

    `custos_logisticos_por_item` mapa MLB → custo logístico unitário (frete
    + tarifa fixa que o vendedor paga). Cobre o gap do `sale_fee` que é só
    a comissão. SKUs sem entrada caem em zero (lucro inflado conservador).

    Retorna (lista ordenada por receita desc, qtd de SKUs sem custo).
    """
    # Considera só linhas de pedidos pagos pra agregação financeira
    pagas = [lin for lin in linhas if lin.status == "paid"]

    agrupado: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "unidades": 0,
        "receita": 0.0,
        "taxa_ml": 0.0,
        "titulo": None,
        "sku": None,
    })
    for lin in pagas:
        g = agrupado[lin.item_id]
        g["unidades"] += lin.quantity
        g["receita"] += lin.unit_price * lin.quantity
        g["taxa_ml"] += lin.sale_fee
        if g["titulo"] is None:
            g["titulo"] = lin.titulo
        if g["sku"] is None and lin.seller_sku:
            g["sku"] = lin.seller_sku

    skus: list[SkuVendido] = []
    sem_custo = 0
    for item_id, g in agrupado.items():
        sku = g["sku"]
        custo_unit = custos_por_sku.get(sku) if sku else None
        cl = custos_logisticos_por_item.get(item_id)
        custo_logistico_total = (
            round(cl.por_unidade * g["unidades"], 2) if cl is not None else 0.0
        )
        custo_total: float | None
        lucro: float | None
        margem_pct: float | None
        if custo_unit is None or custo_unit <= 0:
            custo_total = None
            lucro = None
            margem_pct = None
            sem_custo += 1
        else:
            custo_total = round(custo_unit * g["unidades"], 2)
            lucro = round(
                g["receita"] - g["taxa_ml"] - custo_total - custo_logistico_total,
                2,
            )
            margem_pct = (lucro / g["receita"]) if g["receita"] > 0 else None

        skus.append(SkuVendido(
            item_id=item_id,
            sku=sku,
            titulo=g["titulo"],
            unidades=g["unidades"],
            receita=round(g["receita"], 2),
            taxa_ml=round(g["taxa_ml"], 2),
            custo=custo_total,
            frete=custo_logistico_total,
            lucro=lucro,
            margem_pct=margem_pct,
        ))

    skus.sort(key=lambda s: s.receita, reverse=True)
    return skus, sem_custo


def _calcular_vendas_por_hora(linhas: list[_ItemLinha]) -> list[tuple[int, float]]:
    """Retorna lista de 24 tuplas (unidades, receita) indexadas por hora."""
    por_hora: dict[int, tuple[int, float]] = dict.fromkeys(range(24), (0, 0.0))
    for lin in linhas:
        if lin.status != "paid":
            continue
        u, r = por_hora[lin.hora_local]
        por_hora[lin.hora_local] = (u + lin.quantity, r + lin.unit_price * lin.quantity)
    return [por_hora[h] for h in range(24)]


def _calcular_kpis(
    skus: list[SkuVendido],
    pedidos_pagos: int,
    pedidos_cancelados: int,
    ontem: KpisHistorico | None,
    media_7d: KpisHistorico | None,  # média sintética como KpisHistorico
    mesma_dow: KpisHistorico | None = None,  # mesmo dia da semana passada
) -> KPIs:
    """Compõe os 6 KPIs principais + deltas comparativos."""
    receita = sum(s.receita for s in skus)
    # Lucro só dos SKUs com custo; itens sem custo são excluídos do total
    # de lucro (evita inflar artificialmente). Margem média idem.
    lucro = sum(s.lucro for s in skus if s.lucro is not None)
    total_pedidos = pedidos_pagos + pedidos_cancelados
    total_unidades = sum(s.unidades for s in skus)
    ticket_medio = (receita / pedidos_pagos) if pedidos_pagos > 0 else None
    margem_media = (lucro / receita) if receita > 0 else None
    taxa_cancel = (
        pedidos_cancelados / total_pedidos if total_pedidos > 0 else 0.0
    )

    return KPIs(
        receita_bruta=round(receita, 2),
        lucro_liquido=round(lucro, 2),
        margem_media_pct=margem_media,
        total_pedidos=total_pedidos,
        total_unidades=total_unidades,
        ticket_medio=round(ticket_medio, 2) if ticket_medio else None,
        taxa_cancelamento_pct=taxa_cancel,
        delta_receita_vs_ontem=(
            _delta_pct(receita, ontem.receita_bruta) if ontem else None
        ),
        delta_lucro_vs_ontem=(
            _delta_pct(lucro, ontem.lucro_liquido) if ontem else None
        ),
        delta_pedidos_vs_ontem=(
            _delta_pct(pedidos_pagos, ontem.total_pedidos) if ontem else None
        ),
        delta_receita_vs_media_7d=(
            _delta_pct(receita, media_7d.receita_bruta) if media_7d else None
        ),
        delta_lucro_vs_media_7d=(
            _delta_pct(lucro, media_7d.lucro_liquido) if media_7d else None
        ),
        delta_pedidos_vs_media_7d=(
            _delta_pct(pedidos_pagos, media_7d.total_pedidos)
            if media_7d else None
        ),
        delta_receita_vs_mesma_dow=(
            _delta_pct(receita, mesma_dow.receita_bruta) if mesma_dow else None
        ),
        delta_lucro_vs_mesma_dow=(
            _delta_pct(lucro, mesma_dow.lucro_liquido) if mesma_dow else None
        ),
        delta_pedidos_vs_mesma_dow=(
            _delta_pct(pedidos_pagos, mesma_dow.total_pedidos)
            if mesma_dow else None
        ),
    )


def _media_7d(historico: list[KpisHistorico]) -> KpisHistorico | None:
    """Calcula uma média sintética dos N dias passados. None se a janela
    está vazia. Não exige 7 dias completos — pondera pelos que tem.
    """
    if not historico:
        return None
    n = len(historico)
    return KpisHistorico(
        dia=historico[-1].dia,
        receita_bruta=sum(h.receita_bruta for h in historico) / n,
        lucro_liquido=sum(h.lucro_liquido for h in historico) / n,
        total_pedidos=int(sum(h.total_pedidos for h in historico) / n),
        total_unidades=int(sum(h.total_unidades for h in historico) / n),
    )


def _gerar_sugestoes(
    skus: list[SkuVendido],
    kpis: KPIs,
    historico_7d: list[KpisHistorico],
) -> list[Sugestao]:
    """Heurísticas determinísticas. Cada categoria emite no máx 1 sugestão.

    Regras:
    1. Concentração de receita: top 3 SKUs respondem por > 50% → diversificar
    2. Margem baixa: SKUs com margem < 15% e receita > R$ 100 → revisar preço
    3. SKUs sem custo: > 3 SKUs vendidos sem custo → cadastrar custos
    4. Queda geral: receita do dia < 70% da média 7d → investigar
    5. Top performer: SKU campeão (top 1) com receita ≥ 30% do total → reposição
    6. Cancelamento alto: > 10% → revisar políticas de venda
    """
    sugs: list[Sugestao] = []

    if not skus:
        sugs.append(Sugestao(
            categoria="sem_dados",
            titulo="Sem vendas no dia",
            descricao=(
                "Nenhum pedido pago registrado. Verifique se os anúncios "
                "principais estão ativos e se o status da conta no ML está OK."
            ),
        ))
        return sugs

    receita_total = kpis.receita_bruta

    # 1. Concentração — top 3
    if len(skus) >= 3 and receita_total > 0:
        top3 = sum(s.receita for s in skus[:3])
        if top3 / receita_total > 0.5:
            pct = top3 / receita_total * 100
            sugs.append(Sugestao(
                categoria="concentracao",
                titulo=f"Top 3 SKUs concentram {pct:.0f}% da receita",
                descricao=(
                    "Risco de dependência. Considere ampliar destaque pros "
                    "SKUs de cauda longa ou diversificar mix de oferta nas "
                    "próximas campanhas."
                ),
                item_ids_envolvidos=[s.item_id for s in skus[:3]],
            ))

    # 2. Margem baixa
    margem_baixa = [
        s for s in skus
        if s.margem_pct is not None and s.margem_pct < 0.15 and s.receita > 100
    ]
    if margem_baixa:
        nomes = ", ".join(
            (s.sku or s.item_id) for s in margem_baixa[:5]
        )
        sugs.append(Sugestao(
            categoria="margem_baixa",
            titulo=(
                f"{len(margem_baixa)} SKU"
                f"{'s' if len(margem_baixa) != 1 else ''} com margem < 15%"
            ),
            descricao=(
                f"Revisar preço-base ou custo de: {nomes}"
                f"{' e outros' if len(margem_baixa) > 5 else ''}. "
                "Margem abaixo de 15% é o piso operacional do app."
            ),
            item_ids_envolvidos=[s.item_id for s in margem_baixa],
        ))

    # 3. Sem custo cadastrado
    sem_custo = [s for s in skus if s.custo is None]
    if len(sem_custo) > 3:
        sugs.append(Sugestao(
            categoria="sem_custo",
            titulo=(
                f"{len(sem_custo)} SKUs vendidos sem custo no XLSX"
            ),
            descricao=(
                "Cadastrar o custo desses itens no `custos.xlsx` pra que "
                "lucro e margem do dia fiquem corretos. Sem custo, eles "
                "não contam no lucro liníquido total."
            ),
            item_ids_envolvidos=[s.item_id for s in sem_custo[:20]],
        ))

    # 4. Queda geral
    if historico_7d:
        media_receita = sum(h.receita_bruta for h in historico_7d) / len(historico_7d)
        if media_receita > 0 and receita_total < media_receita * 0.7:
            queda_pct = (1 - receita_total / media_receita) * 100
            sugs.append(Sugestao(
                categoria="queda_geral",
                titulo=f"Receita -{queda_pct:.0f}% vs média de 7 dias",
                descricao=(
                    "Queda significativa. Investigar: anúncios pausados, "
                    "BuyBox perdida, mudança de fee em alguma categoria, "
                    "ou sazonalidade conhecida. Compare com o dia equivalente "
                    "da semana passada antes de tomar ação."
                ),
            ))

    # 5. Top performer
    if skus and receita_total > 0:
        top1 = skus[0]
        if top1.receita / receita_total >= 0.3:
            pct = top1.receita / receita_total * 100
            sugs.append(Sugestao(
                categoria="top_performer",
                titulo=(
                    f"{top1.sku or top1.item_id} respondeu por {pct:.0f}% da "
                    "receita do dia"
                ),
                descricao=(
                    f"{top1.unidades} unidades vendidas. Confirmar estoque "
                    "pra próximos dias e considerar destacar nas próximas "
                    "campanhas atrativas (Inverno, Esporte, 06.06)."
                ),
                item_ids_envolvidos=[top1.item_id],
            ))

    # 6. Cancelamento alto
    if kpis.taxa_cancelamento_pct > 0.10 and kpis.total_pedidos >= 10:
        pct = kpis.taxa_cancelamento_pct * 100
        sugs.append(Sugestao(
            categoria="cancelamento",
            titulo=f"Taxa de cancelamento de {pct:.1f}%",
            descricao=(
                "Acima do esperado. Verificar: estoque desatualizado, "
                "atraso de envio, ou problema de pagamento recorrente."
            ),
        ))

    return sugs


def _agregar_por_categoria(
    linhas: list[_ItemLinha],
    skus: list[SkuVendido],
    nomes_categorias: dict[str, str],
) -> list[LucroPorCategoria]:
    """Agrupa SKUs por category_id e soma receita/lucro/unidades."""
    # Mapa item_id → category_id pra linkar SkuVendido ao category_id
    cat_by_item: dict[str, str] = {}
    for lin in linhas:
        if lin.status == "paid" and lin.category_id and lin.item_id not in cat_by_item:
            cat_by_item[lin.item_id] = lin.category_id

    grupos: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "receita": 0.0, "lucro": 0.0, "unidades": 0, "skus": 0,
    })
    for s in skus:
        cat = cat_by_item.get(s.item_id)
        if not cat:
            continue
        g = grupos[cat]
        g["receita"] += s.receita
        g["unidades"] += s.unidades
        g["skus"] += 1
        if s.lucro is not None:
            g["lucro"] += s.lucro

    out: list[LucroPorCategoria] = []
    for cat_id, g in grupos.items():
        margem = (g["lucro"] / g["receita"]) if g["receita"] > 0 else None
        out.append(LucroPorCategoria(
            category_id=cat_id,
            nome_categoria=nomes_categorias.get(cat_id),
            receita=round(g["receita"], 2),
            lucro=round(g["lucro"], 2),
            margem_pct=margem,
            qtd_skus=g["skus"],
            unidades=g["unidades"],
        ))
    out.sort(key=lambda c: c.receita, reverse=True)
    return out


def _calcular_cupons(linhas: list[_ItemLinha]) -> Cupons:
    """Soma cupons e Mercado Puntos / cashback dos pedidos pagos."""
    orders_com_cupom: set[int] = set()
    orders_com_meli: set[int] = set()
    total_cupom = 0.0
    total_meli = 0.0
    for lin in linhas:
        if lin.status != "paid":
            continue
        if lin.coupon_amount > 0:
            orders_com_cupom.add(lin.order_id)
            total_cupom += lin.coupon_amount
        if lin.meli_promo_amount > 0:
            orders_com_meli.add(lin.order_id)
            total_meli += lin.meli_promo_amount
    return Cupons(
        qtd_pedidos_com_cupom=len(orders_com_cupom),
        total_descontado_cupom=round(total_cupom, 2),
        qtd_pedidos_com_meli_promo=len(orders_com_meli),
        total_meli_promo=round(total_meli, 2),
    )


def _calcular_acumulado_mes(
    dia: date_type,
    receita_dia: float,
    lucro_dia: float,
    pedidos_dia: int,
    historico_mes: list[KpisHistorico],
) -> AcumuladoMes:
    """Soma KPIs dia 1..(dia-1) do histórico + os do dia atual.

    `historico_mes` = snapshots persistidos do dia 1 até dia-1. Junto com os
    valores do dia atual, monta a curva acumulada. Projeção linear simples.
    """
    dias_totais = calendar.monthrange(dia.year, dia.month)[1]
    # Curva: lista (dia_str, receita_acumulada). Inclui dia atual no fim.
    curva: list[tuple[str, float]] = []
    acumulado_rec = 0.0
    acumulado_luc = 0.0
    acumulado_ped = 0
    for h in historico_mes:
        # Inclui só dias do mês corrente
        if h.dia.month != dia.month or h.dia.year != dia.year:
            continue
        acumulado_rec += h.receita_bruta
        acumulado_luc += h.lucro_liquido
        acumulado_ped += h.total_pedidos
        curva.append((h.dia.isoformat(), round(acumulado_rec, 2)))
    # Dia atual:
    acumulado_rec += receita_dia
    acumulado_luc += lucro_dia
    acumulado_ped += pedidos_dia
    curva.append((dia.isoformat(), round(acumulado_rec, 2)))

    dias_decorridos = dia.day
    # Projeção: média diária * dias totais. Usa só dias com dado pra média.
    n = max(1, dias_decorridos)
    media_diaria_rec = acumulado_rec / n
    media_diaria_luc = acumulado_luc / n
    media_diaria_ped = acumulado_ped / n
    return AcumuladoMes(
        receita_acumulada=round(acumulado_rec, 2),
        lucro_acumulado=round(acumulado_luc, 2),
        pedidos_acumulados=acumulado_ped,
        receita_projetada_mes=round(media_diaria_rec * dias_totais, 2),
        lucro_projetado_mes=round(media_diaria_luc * dias_totais, 2),
        pedidos_projetados_mes=int(media_diaria_ped * dias_totais),
        curva=curva,
        dias_decorridos=dias_decorridos,
        dias_totais_mes=dias_totais,
    )


def montar_relatorio(
    *,
    profile_id: UUID,
    profile_name: str,
    dia: date_type,
    linhas: list[_ItemLinha],
    custos_por_sku: dict[str, float],
    custos_logisticos_por_item: dict[str, CustoLogisticoPorVenda],
    ontem: KpisHistorico | None,
    historico_7d: list[KpisHistorico],
    mesma_dow: KpisHistorico | None,
    historico_mes: list[KpisHistorico],
    metricas_envio: MetricasEnvio,
    nomes_categorias: dict[str, str],
) -> RelatorioDiario:
    """Pipeline completo: linhas → KPIs + SKUs + sugestões → RelatorioDiario."""
    skus, qtd_sem_custo = _agregar_por_sku(
        linhas, custos_por_sku, custos_logisticos_por_item,
    )

    # Conta pedidos distintos por status
    orders_pagos = {lin.order_id for lin in linhas if lin.status == "paid"}
    orders_cancelados = {lin.order_id for lin in linhas if lin.status == "cancelled"}

    vendas_por_hora = _calcular_vendas_por_hora(linhas)
    media_7d_hist = _media_7d(historico_7d)
    kpis = _calcular_kpis(
        skus,
        pedidos_pagos=len(orders_pagos),
        pedidos_cancelados=len(orders_cancelados),
        ontem=ontem,
        media_7d=media_7d_hist,
        mesma_dow=mesma_dow,
    )
    sugestoes = _gerar_sugestoes(skus, kpis, historico_7d)
    lucro_por_categoria = _agregar_por_categoria(linhas, skus, nomes_categorias)
    cupons = _calcular_cupons(linhas)
    acumulado_mes = _calcular_acumulado_mes(
        dia, kpis.receita_bruta, kpis.lucro_liquido, kpis.total_pedidos,
        historico_mes,
    )

    return RelatorioDiario(
        profile_id=profile_id,
        profile_name=profile_name,
        dia=dia,
        gerado_em_iso=datetime.now(UTC).isoformat(),
        kpis=kpis,
        skus=skus,
        vendas_por_hora=vendas_por_hora,
        sugestoes=sugestoes,
        lucro_por_categoria=lucro_por_categoria,
        acumulado_mes=acumulado_mes,
        cupons=cupons,
        metricas_envio=metricas_envio,
        pedidos_pagos=len(orders_pagos),
        pedidos_cancelados=len(orders_cancelados),
        skus_sem_custo=qtd_sem_custo,
    )
