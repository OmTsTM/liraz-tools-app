"""Schemas da API pra endpoints de pricing."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from liraz_tools.domain.pricing.entity import FeeReport, ListingFees


class ListingFeesResponse(BaseModel):
    """Mesmo shape do ListingFees do domínio — exportado pra OpenAPI."""
    item_id: str
    sku: str | None
    title: str | None
    status: str | None
    modalidade: str
    modalidade_id: str | None
    preco: float
    comissao_valor: float
    comissao_percentual: float
    tarifa_fixa: float
    tarifa_fixa_fonte: str
    frete_vendedor: float
    frete_fonte: str
    free_shipping: bool
    valor_liquido: float
    custo_produto: float | None
    custo_fonte: str
    custo_fonte_detalhe: str | None
    lucro_bruto: float | None
    imposto_valor: float | None
    lucro_liquido: float | None
    margem_liquida_percentual: float | None
    erro: str | None

    @classmethod
    def from_domain(cls, listing: ListingFees) -> ListingFeesResponse:
        return cls(**listing.model_dump())


class FeeReportResponse(BaseModel):
    """Relatório completo pra exibir na UI."""
    profile_id: str
    profile_slug: str
    generated_at: datetime
    cep_destino: str
    aliquota_imposto: float
    custos_xlsx_path: str

    listings: list[ListingFeesResponse]
    total_anuncios: int
    skus_no_custos_xlsx: int

    matches: dict[str, int]
    anuncios_sem_custo: list[dict[str, Any]]
    anuncios_com_fallback: list[dict[str, Any]]

    @classmethod
    def from_domain(cls, report: FeeReport) -> FeeReportResponse:
        return cls(
            profile_id=report.profile_id,
            profile_slug=report.profile_slug,
            generated_at=report.generated_at,
            cep_destino=report.cep_destino,
            aliquota_imposto=report.aliquota_imposto,
            custos_xlsx_path=report.custos_xlsx_path,
            listings=[ListingFeesResponse.from_domain(it) for it in report.listings],
            total_anuncios=report.total_anuncios,
            skus_no_custos_xlsx=report.skus_no_custos_xlsx,
            matches=report.matches,
            anuncios_sem_custo=report.anuncios_sem_custo,
            anuncios_com_fallback=report.anuncios_com_fallback,
        )


class UploadCustosXLSXResponse(BaseModel):
    """Resposta ao upload — devolve o path absoluto onde foi salvo."""
    saved_path: str
    size_bytes: int
    skus_carregados: int
