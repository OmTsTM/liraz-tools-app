"""Endpoints de cost overrides — edição manual de custo por linha.

Rotas:
  POST   /api/profiles/{id}/cost-overrides
  DELETE /api/profiles/{id}/cost-overrides/{key}

Persiste no disco (profiles/<slug>/cost_overrides.json) e atualiza o cache
em memória da geração de relatório (15min) com a linha recalculada.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from liraz_tools.api.deps import ProfileRepo
from liraz_tools.api.schemas.cost_override_schemas import (
    CostOverrideMutationResponse,
    SetCostOverrideRequest,
)
from liraz_tools.api.schemas.pricing_schemas import ListingFeesResponse
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.pricing.use_cases import (
    PricingError,
    RemoveCostOverrideUseCase,
    SetCostOverrideUseCase,
)
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
)
from liraz_tools.infrastructure.repositories.pricing_cache import (
    FeeReportCache,
    get_fee_report_cache,
)

router = APIRouter(prefix="/api/profiles", tags=["cost-overrides"])
logger = get_logger(__name__)


PricingCache = Annotated[FeeReportCache, Depends(get_fee_report_cache)]


def _get_overrides_repo() -> CostOverridesRepository:
    return CostOverridesRepository()


OverridesRepo = Annotated[
    CostOverridesRepository, Depends(_get_overrides_repo)
]


@router.post(
    "/{profile_id}/cost-overrides",
    response_model=CostOverrideMutationResponse,
)
async def set_cost_override(
    profile_id: UUID,
    body: SetCostOverrideRequest,
    profile_repo: ProfileRepo,
    cache: PricingCache,
    overrides_repo: OverridesRepo,
) -> CostOverrideMutationResponse:
    """Cria ou atualiza um override de custo pra um SKU/MLB."""
    use_case = SetCostOverrideUseCase(profile_repo, cache, overrides_repo)
    try:
        updated_listing = await use_case.execute(
            profile_id=profile_id, key=body.key, value=body.value,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PricingError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return CostOverrideMutationResponse(
        key=body.key,
        updated_listing=(
            ListingFeesResponse.from_domain(updated_listing)
            if updated_listing is not None
            else None
        ),
    )


@router.delete(
    "/{profile_id}/cost-overrides/{key}",
    response_model=CostOverrideMutationResponse,
)
async def remove_cost_override(
    profile_id: UUID,
    key: str,
    profile_repo: ProfileRepo,
    cache: PricingCache,
    overrides_repo: OverridesRepo,
) -> CostOverrideMutationResponse:
    """Remove o override e restaura o custo do XLSX (ou null se não bater)."""
    use_case = RemoveCostOverrideUseCase(profile_repo, cache, overrides_repo)
    try:
        updated_listing = await use_case.execute(
            profile_id=profile_id, key=key,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    return CostOverrideMutationResponse(
        key=key,
        updated_listing=(
            ListingFeesResponse.from_domain(updated_listing)
            if updated_listing is not None
            else None
        ),
    )
