"""Entidades do contexto Pricing.

ListingFees representa as taxas/custos/margem de UM anúncio.
FeeReport é o conjunto agregado pra exibir na UI (lista + diagnóstico).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

MatchType = Literal[
    "manual",
    "exato",
    "case_insensitive",
    "prefixo",
    "prefixo_divergente",
    "nao_encontrado",
]


class ListingFees(BaseModel):
    """Taxas e custos calculados de um único anúncio.

    Espelha os campos do calculator (`calcular_taxas_anuncio`), com
    adições do join de custos e cálculo de lucro líquido pra UI.
    """
    model_config = ConfigDict(extra="ignore")

    # Identificação
    item_id: str
    sku: str | None
    title: str | None
    status: str | None

    # Modalidade
    modalidade: str  # "Clássico" / "Premium" / ...
    modalidade_id: str | None

    # Preços e taxas
    preco: float
    comissao_valor: float
    comissao_percentual: float
    tarifa_fixa: float
    tarifa_fixa_fonte: str  # "api" | "regra_drop_off"
    frete_vendedor: float
    frete_fonte: str  # "shipping_options" | "cache" | "indisponivel" | "n/a"
    free_shipping: bool

    # Calculados (Valor Final = preco - comissao - tarifa_fixa - frete)
    valor_liquido: float

    # Custo do produto (após join)
    custo_produto: float | None
    custo_fonte: MatchType  # tipo do match (ou 'nao_encontrado')
    custo_fonte_detalhe: str | None  # ex: "prefixo:5209"

    # Lucro com imposto aplicado (calculado no use case)
    lucro_bruto: float | None
    imposto_valor: float | None
    lucro_liquido: float | None
    margem_liquida_percentual: float | None  # 0.0 - 1.0 (ou negativo)

    # Erro (se houve falha no cálculo)
    erro: str | None = None


class FeeReport(BaseModel):
    """Relatório agregado de um perfil.

    `listings` tem uma entrada por anúncio ativo (mesmo se deu erro —
    a entrada terá apenas item_id + erro preenchidos).
    `diagnostico` tem contagem de matches por tipo + lista de SKUs
    com problemas pra alerta na UI.
    """
    profile_id: str
    profile_slug: str
    generated_at: datetime
    cep_destino: str
    aliquota_imposto: float
    custos_xlsx_path: str

    listings: list[ListingFees]
    total_anuncios: int
    skus_no_custos_xlsx: int

    matches: dict[str, int]  # contagem por tipo de match
    anuncios_sem_custo: list[dict[str, Any]]
    anuncios_com_fallback: list[dict[str, Any]]

    @classmethod
    def now_utc(cls) -> datetime:
        return datetime.now(UTC)
