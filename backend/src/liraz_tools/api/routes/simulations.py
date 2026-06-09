"""Endpoints de simulação de reprecificação.

Rotas:
  POST   /api/profiles/{id}/simulations              — disparar nova
  GET    /api/profiles/{id}/simulations              — listar
  GET    /api/profiles/{id}/simulations/{sim_id}     — detalhe + progresso
  POST   /api/profiles/{id}/simulations/{sim_id}/resume — retomar interrupted
  GET    /api/profiles/{id}/simulations/{sim_id}/xlsx   — download
  DELETE /api/profiles/{id}/simulations/{sim_id}     — apagar snapshot
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from liraz_tools.api.deps import CredsRepo, ProfileRepo
from liraz_tools.api.schemas.simulation_schemas import (
    OverrideMarginRequest,
    SimulationDetailResponse,
    SimulationSummaryResponse,
    StartSimulationRequest,
    StartSimulationResponse,
)
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.pricing.repricing_use_cases import (
    DeleteRepricingSimulationUseCase,
    ExportRepricingXLSXUseCase,
    GetRepricingSimulationUseCase,
    InvalidMarginError,
    ItemNotFoundError,
    ListRepricingSimulationsUseCase,
    NoCustosConfiguredError,
    NotConnectedError,
    OverrideItemMarginUseCase,
    RepricingError,
    ResumeSimulationUseCase,
    RevertItemMarginUseCase,
    StartRepricingSimulationUseCase,
)
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)

router = APIRouter(prefix="/api/profiles", tags=["simulations"])
logger = get_logger(__name__)


# ─── Dependency injection ───────────────────────────────────────────────────


def _get_snapshots_repo() -> SnapshotsRepository:
    return SnapshotsRepository()


def _get_overrides_repo() -> CostOverridesRepository:
    return CostOverridesRepository()


SnapshotsRepo = Annotated[
    SnapshotsRepository, Depends(_get_snapshots_repo)
]
OverridesRepo = Annotated[
    CostOverridesRepository, Depends(_get_overrides_repo)
]


# ─── Endpoints ──────────────────────────────────────────────────────────────


@router.post(
    "/{profile_id}/simulations",
    response_model=StartSimulationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_simulation(
    profile_id: UUID,
    body: StartSimulationRequest,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    snapshots_repo: SnapshotsRepo,
    overrides_repo: OverridesRepo,
) -> StartSimulationResponse:
    """Dispara simulação em background. Retorna 202 com simulation_id."""
    use_case = StartRepricingSimulationUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        snapshots_repo=snapshots_repo,
        overrides_repo=overrides_repo,
    )
    try:
        simulation_id = await use_case.execute(
            profile_id=profile_id, concorrencia=body.concorrencia,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    except (NoCustosConfiguredError, NotConnectedError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e
    except RepricingError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e),
        ) from e

    return StartSimulationResponse(simulation_id=simulation_id)


@router.get(
    "/{profile_id}/simulations",
    response_model=list[SimulationSummaryResponse],
)
async def list_simulations(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    snapshots_repo: SnapshotsRepo,
) -> list[SimulationSummaryResponse]:
    """Lista todas simulações do perfil (mais recentes primeiro)."""
    use_case = ListRepricingSimulationsUseCase(profile_repo, snapshots_repo)
    try:
        summaries = await use_case.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e

    return [
        SimulationSummaryResponse(**s.model_dump()) for s in summaries
    ]


@router.get(
    "/{profile_id}/simulations/{simulation_id}",
    response_model=SimulationDetailResponse,
)
async def get_simulation(
    profile_id: UUID,
    simulation_id: str,
    profile_repo: ProfileRepo,
    snapshots_repo: SnapshotsRepo,
) -> SimulationDetailResponse:
    """Detalhe completo. Frontend faz polling aqui pra ver progresso."""
    use_case = GetRepricingSimulationUseCase(profile_repo, snapshots_repo)
    try:
        sim = await use_case.execute(profile_id, simulation_id)
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e

    if sim is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"simulação {simulation_id} não encontrada",
        )

    return SimulationDetailResponse(**sim.model_dump())


@router.post(
    "/{profile_id}/simulations/{simulation_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_simulation(
    profile_id: UUID,
    simulation_id: str,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    snapshots_repo: SnapshotsRepo,
    overrides_repo: OverridesRepo,
) -> dict[str, bool]:
    """Retoma simulação `interrupted` ou `failed`.

    Reaproveita itens já processados no snapshot — só simula os pendentes.
    """
    use_case = ResumeSimulationUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        snapshots_repo=snapshots_repo,
        overrides_repo=overrides_repo,
    )
    try:
        ok = await use_case.execute(profile_id, simulation_id)
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    except (NotConnectedError, RepricingError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"simulação {simulation_id} não encontrada",
        )

    return {"resumed": True}


@router.get("/{profile_id}/simulations/{simulation_id}/xlsx")
async def download_simulation_xlsx(
    profile_id: UUID,
    simulation_id: str,
    profile_repo: ProfileRepo,
    snapshots_repo: SnapshotsRepo,
) -> Response:
    """Baixa o XLSX do snapshot (mesmo layout do MCP)."""
    use_case = ExportRepricingXLSXUseCase(profile_repo, snapshots_repo)
    try:
        result = await use_case.execute(profile_id, simulation_id)
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"simulação {simulation_id} não encontrada",
        )

    xlsx_bytes, filename = result
    return Response(
        content=xlsx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.delete(
    "/{profile_id}/simulations/{simulation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_simulation(
    profile_id: UUID,
    simulation_id: str,
    profile_repo: ProfileRepo,
    snapshots_repo: SnapshotsRepo,
) -> None:
    """Apaga snapshot do disco + cancela task se estiver em execução."""
    use_case = DeleteRepricingSimulationUseCase(profile_repo, snapshots_repo)
    try:
        await use_case.execute(profile_id, simulation_id)
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e


@router.post(
    "/{profile_id}/simulations/{simulation_id}/items/{item_id}/override-margin",
)
async def override_item_margin(
    profile_id: UUID,
    simulation_id: str,
    item_id: str,
    body: OverrideMarginRequest,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    snapshots_repo: SnapshotsRepo,
) -> dict[str, Any]:
    """Aplica margem custom num item. Backend faz busca binária no ML.

    Operação assíncrona típica leva 2-5s (15-20 chamadas ao ML pra
    convergir). Frontend deve mostrar spinner.

    Retorna o item atualizado (formato `RepricingLineItem`).
    """
    use_case = OverrideItemMarginUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        snapshots_repo=snapshots_repo,
    )
    try:
        item = await use_case.execute(
            profile_id=profile_id,
            simulation_id=simulation_id,
            item_id=item_id,
            margem_alvo=body.margem_alvo,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except ItemNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except (InvalidMarginError, NotConnectedError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e
    except RepricingError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e),
        ) from e
    return item


@router.delete(
    "/{profile_id}/simulations/{simulation_id}/items/{item_id}/override-margin",
)
async def revert_item_margin(
    profile_id: UUID,
    simulation_id: str,
    item_id: str,
    profile_repo: ProfileRepo,
    snapshots_repo: SnapshotsRepo,
) -> dict[str, Any]:
    """Reverte override de margem dum item pro calculado originalmente."""
    use_case = RevertItemMarginUseCase(profile_repo, snapshots_repo)
    try:
        item = await use_case.execute(profile_id, simulation_id, item_id)
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except ItemNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except RepricingError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e
    return item
