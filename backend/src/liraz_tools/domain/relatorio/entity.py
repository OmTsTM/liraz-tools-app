"""Entidades do relatório diário de vendas (Fatia 1).

`RelatorioDiario` é o snapshot agregado de um dia inteiro de vendas duma loja:
KPIs (receita/lucro/pedidos/etc), curva de vendas por hora, top SKUs, e
sugestões pra ação no dia seguinte.

A entidade vive em memória durante a geração e depois é persistida via
`RelatorioDiarioRepository` pra alimentar comparativos do dia seguinte (vs
ontem) e da média 7d (vs últimos 7 dias).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from uuid import UUID


@dataclass(frozen=True)
class SkuVendido:
    """Agregado por SKU dum único dia.

    Atributos:
        item_id: MLB do anúncio
        sku: SKU interno do vendedor (do `seller_sku` do order_item)
        titulo: título do anúncio (snapshot do dia)
        unidades: total de unidades vendidas no dia (sum de quantity)
        receita: receita bruta (unit_price x quantity)
        taxa_ml: sale_fee somado (taxa que o ML cobrou)
        custo: custo unitário x unidades (do custos.xlsx). None se SKU sem custo.
        frete: frete pago pelo vendedor (estimado pelo nosso modelo de pricing
            quando `unit_price ≥ R$ 79` com free shipping; senão 0).
        lucro: receita - taxa_ml - custo - frete. None quando custo é None.
        margem_pct: lucro / receita. None quando lucro é None.
    """

    item_id: str
    sku: str | None
    titulo: str | None
    unidades: int
    receita: float
    taxa_ml: float
    custo: float | None
    frete: float
    lucro: float | None
    margem_pct: float | None


@dataclass(frozen=True)
class Sugestao:
    """Uma sugestão de ação pro dia seguinte, gerada por heurística.

    `categoria` agrupa visualmente no PDF (top_movers, top_droppers,
    margem_baixa, concentracao_receita, queda_geral, etc).
    """

    categoria: str
    titulo: str
    descricao: str
    # Quando a sugestão referencia SKU(s), lista pra UI render.
    item_ids_envolvidos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KPIs:
    """Os 6 KPIs mostrados no topo do relatório + comparativos.

    `delta_vs_ontem`, `delta_vs_media_7d` e `delta_vs_mesma_dow` ficam None
    quando não há histórico suficiente (primeira execução ou dias faltando).
    """

    receita_bruta: float
    lucro_liquido: float
    margem_media_pct: float | None  # None se receita == 0
    total_pedidos: int
    total_unidades: int
    ticket_medio: float | None  # receita / pedidos; None se 0 pedidos
    taxa_cancelamento_pct: float  # cancelados / total
    # Comparativos: cada um é o delta percentual vs aquele baseline (ex:
    # 0.15 = +15% vs ontem). None = sem baseline disponível.
    delta_receita_vs_ontem: float | None = None
    delta_lucro_vs_ontem: float | None = None
    delta_pedidos_vs_ontem: float | None = None
    delta_receita_vs_media_7d: float | None = None
    delta_lucro_vs_media_7d: float | None = None
    delta_pedidos_vs_media_7d: float | None = None
    # vs MESMO dia da semana passada (= dia - 7). Suaviza o ruído de sazonalidade
    # semanal (domingo vende menos que sexta, etc.). Mais útil que vs ontem
    # quando há padrão semanal forte.
    delta_receita_vs_mesma_dow: float | None = None
    delta_lucro_vs_mesma_dow: float | None = None
    delta_pedidos_vs_mesma_dow: float | None = None


@dataclass(frozen=True)
class LucroPorCategoria:
    """Agregado por `category_id` do ML."""

    category_id: str
    nome_categoria: str | None  # quando disponível via cache
    receita: float
    lucro: float
    margem_pct: float | None
    qtd_skus: int  # skus distintos
    unidades: int


@dataclass(frozen=True)
class AcumuladoMes:
    """Métricas mensais até `dia` (inclusivo), pra projeção e progresso."""

    receita_acumulada: float
    lucro_acumulado: float
    pedidos_acumulados: int
    # Projeção linear pro fim do mês (= média diária * dias totais).
    receita_projetada_mes: float
    lucro_projetado_mes: float
    pedidos_projetados_mes: int
    # Curva diária pra gráfico: lista de (dia, receita_acumulada).
    curva: list[tuple[str, float]]
    dias_decorridos: int  # 1 a 31
    dias_totais_mes: int


@dataclass(frozen=True)
class Cupons:
    """Cupons + Mercado Puntos / cashback aplicados nos pedidos do dia."""

    qtd_pedidos_com_cupom: int
    total_descontado_cupom: float  # do bolso do vendedor? ou do ML?
    qtd_pedidos_com_meli_promo: int  # Mercado Puntos / cashback
    total_meli_promo: float


@dataclass(frozen=True)
class MetricasEnvio:
    """SLA de envio dos pedidos pagos do dia.

    `tempo_medio_dias` = média de (`date_first_visit` - `date_created`) só pros
    pedidos com shipment status `delivered` que já têm `date_first_visit`.
    None quando 0 entregas confirmadas — esperado em pedidos recentes.

    `qtd_pendente` = pedidos que ainda não foram entregues (ou que não temos
    `date_first_visit` ainda). Ajuda contextualizar o `tempo_medio_dias` ("ainda
    tem 30 pendentes; média sobre só 10 entregues").
    """

    qtd_total_pedidos: int  # pagos + com shipment
    qtd_entregue: int  # delivered com date_first_visit
    qtd_pendente: int  # demais (em transit, em criação, etc)
    tempo_medio_dias: float | None  # None se 0 entregues
    tempo_mediano_dias: float | None  # robusto a outliers
    pior_atraso_dias: int | None  # maior valor da amostra


@dataclass(frozen=True)
class RelatorioDiario:
    """Snapshot completo do dia. Imutável após gerado."""

    profile_id: UUID
    profile_name: str
    dia: date_type  # data BRT do dia coberto
    gerado_em_iso: str  # ISO timestamp UTC de quando o snapshot foi calculado

    kpis: KPIs
    skus: list[SkuVendido]  # ordenado por receita desc
    # Vendas agregadas por hora (0-23). Tupla: (unidades, receita).
    vendas_por_hora: list[tuple[int, float]]
    sugestoes: list[Sugestao]
    # Análises adicionais (ago/2026 — Fatia 1 plus):
    lucro_por_categoria: list[LucroPorCategoria]  # ordenado por receita desc
    acumulado_mes: AcumuladoMes
    cupons: Cupons
    metricas_envio: MetricasEnvio

    # Contadores auxiliares pra cobertura/diagnóstico (debug + footer do PDF)
    pedidos_pagos: int
    pedidos_cancelados: int
    skus_sem_custo: int  # quantos SKUs vendidos não tinham custo cadastrado
