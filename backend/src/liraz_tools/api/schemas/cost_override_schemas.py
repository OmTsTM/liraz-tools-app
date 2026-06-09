"""Schemas dos endpoints de cost overrides."""
from __future__ import annotations

from pydantic import BaseModel, Field

from liraz_tools.api.schemas.pricing_schemas import ListingFeesResponse


class SetCostOverrideRequest(BaseModel):
    """Payload pra criar/atualizar um override de custo.

    A `key` pode ser SKU (string normal) ou MLB (formato MLB12345). A cascata
    de busca em `buscar_custo` resolve ambos automaticamente.
    """

    key: str = Field(min_length=1, max_length=80, description="SKU ou MLB")
    value: float = Field(ge=0, description="Custo em R$ (>= 0)")


class CostOverrideMutationResponse(BaseModel):
    """Resposta após criar/atualizar/remover um override.

    Inclui a linha recalculada (se a linha existia no cache) — o frontend
    substitui no estado local pra refletir lucro/margem novos sem recarregar.
    """

    key: str
    updated_listing: ListingFeesResponse | None = None
    """Linha recalculada com novo custo e lucro/margem. None se cache vazio
    (próxima geração do relatório vai aplicar o override automaticamente)."""
