"""Endpoints da feature 'Reprecificar tudo' (operações da loja).

Pra lojas sem campanha, expõe simulação + aplicação direta do P passo3 (margem
alvo Q2 do perfil) no preço-base via PUT /items/{id}.

Rotas:
  POST /api/profiles/{id}/repricing-massa/simular  — calcula P por item
  POST /api/profiles/{id}/repricing-massa/aplicar  — PUT em massa
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from liraz_tools.api.deps import (
    CredsRepo,
    DbSession,
    ProfileRepo,
    require_operator_in_profile,
    require_viewer_in_profile,
)
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.domain.repricing_massa.use_cases import (
    ApplyRepricingMassaUseCase,
    RepricingMassaError,
    RepricingMassaItem,
    RevertRepricingMassaUseCase,
    SimulateRepricingMassaUseCase,
)
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
)
from liraz_tools.infrastructure.repositories.repricing_snapshot_repository import (
    RepricingSnapshotRepository,
)

router = APIRouter(
    prefix="/api/profiles",
    tags=["repricing-massa"],
    # Simular é leitura (cálculo puro) — viewer OK. Aplicar/reverter precisam
    # de operator (validado nos handlers).
    dependencies=[Depends(require_viewer_in_profile)],
)
logger = get_logger(__name__)


def _get_overrides_repo() -> CostOverridesRepository:
    return CostOverridesRepository()


OverridesRepo = Annotated[
    CostOverridesRepository, Depends(_get_overrides_repo)
]


class SimulateRequest(BaseModel):
    item_ids: list[str] = Field(default_factory=list)


class SimulateItemResponse(BaseModel):
    item_id: str
    sku: str | None
    titulo: str | None
    preco_atual: float | None
    preco_novo: float | None
    margem_atual_pct: float | None
    margem_nova_pct: float | None
    custo: float | None
    fonte_custo: str | None
    erro: str | None


class SimulateResponse(BaseModel):
    results: list[SimulateItemResponse]


class ApplyRequest(BaseModel):
    """Mapa `item_id → novo preço` confirmado pelo usuário."""

    precos: dict[str, float] = Field(default_factory=dict)


class ApplyErroResponse(BaseModel):
    item_id: str
    operacao: str
    erro: str


class ApplyResponse(BaseModel):
    ok: bool
    aplicados: list[str]
    ja_no_preco: list[str]
    erros: list[ApplyErroResponse]
    # `session_id` é None se nenhum item foi aplicado de fato. Quando set,
    # frontend pode oferecer botão "Reverter última execução".
    session_id: str | None = None


class SessionResumoResponse(BaseModel):
    session_id: str
    qtd_itens: int
    criado_em_iso: str
    revertido_em_iso: str | None


class ListSessionsResponse(BaseModel):
    sessions: list[SessionResumoResponse]


class RevertResponse(BaseModel):
    ok: bool
    revertidos: list[str]
    ja_no_preco: list[str]
    erros: list[ApplyErroResponse]


def _to_response(item: RepricingMassaItem) -> SimulateItemResponse:
    return SimulateItemResponse(
        item_id=item.item_id,
        sku=item.sku,
        titulo=item.titulo,
        preco_atual=item.preco_atual,
        preco_novo=item.preco_novo,
        margem_atual_pct=item.margem_atual_pct,
        margem_nova_pct=item.margem_nova_pct,
        custo=item.custo,
        fonte_custo=item.fonte_custo,
        erro=item.erro,
    )


@router.post(
    "/{profile_id}/repricing-massa/simular",
    response_model=SimulateResponse,
)
async def simular_repricing_massa(
    profile_id: UUID,
    body: SimulateRequest,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    overrides_repo: OverridesRepo,
) -> SimulateResponse:
    """Pra cada item_id, calcula o P passo3 + margem prevista."""
    use_case = SimulateRepricingMassaUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        overrides_repo=overrides_repo,
    )
    try:
        results = await use_case.execute(
            profile_id=profile_id, item_ids=body.item_ids,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except RepricingMassaError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e

    return SimulateResponse(results=[_to_response(r) for r in results])


@router.post(
    "/{profile_id}/repricing-massa/aplicar",
    response_model=ApplyResponse,
)
async def aplicar_repricing_massa(
    profile_id: UUID,
    body: ApplyRequest,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    session: DbSession,
    _op: Annotated[Any, Depends(require_operator_in_profile)],
) -> ApplyResponse:
    """Aplica PUT /items/{id} em massa pros preços confirmados.

    Persiste snapshot pré-PUT pra permitir undo via `/reverter/{session_id}`.
    """
    snapshot_repo = RepricingSnapshotRepository(session)
    use_case = ApplyRepricingMassaUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        snapshot_repo=snapshot_repo,
    )
    try:
        result, session_id = await use_case.execute(
            profile_id=profile_id, precos=body.precos,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except RepricingMassaError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e

    return ApplyResponse(
        ok=len(result.erros) == 0,
        aplicados=result.aplicados,
        ja_no_preco=result.ja_no_preco,
        erros=[
            ApplyErroResponse(
                item_id=e["item_id"],
                operacao=e["operacao"],
                erro=e["erro"],
            )
            for e in result.erros
        ],
        session_id=str(session_id) if session_id else None,
    )


@router.get(
    "/{profile_id}/repricing-massa/sessoes",
    response_model=ListSessionsResponse,
)
async def listar_sessoes_repricing(
    profile_id: UUID,
    session: DbSession,
    limit: int = 10,
) -> ListSessionsResponse:
    """Últimas N sessões de Reprecificar Tudo desse perfil. Frontend usa pra
    oferecer botão 'Reverter última execução' com data + qtd de itens."""
    repo = RepricingSnapshotRepository(session)
    sessoes = await repo.listar_sessoes(profile_id, limit=limit)
    return ListSessionsResponse(
        sessions=[
            SessionResumoResponse(
                session_id=str(s.session_id),
                qtd_itens=s.qtd_itens,
                criado_em_iso=s.criado_em.isoformat(),
                revertido_em_iso=(
                    s.revertido_em.isoformat() if s.revertido_em else None
                ),
            )
            for s in sessoes
        ],
    )


@router.post(
    "/{profile_id}/repricing-massa/reverter/{session_id}",
    response_model=RevertResponse,
)
async def reverter_sessao_repricing(
    profile_id: UUID,
    session_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    session: DbSession,
    _op: Annotated[Any, Depends(require_operator_in_profile)],
) -> RevertResponse:
    """Reverte uma sessão de Reprecificar Tudo aplicando PUT pros preços
    anteriores. Idempotente — itens já no preço antigo viram `ja_no_preco`."""
    snapshot_repo = RepricingSnapshotRepository(session)
    use_case = RevertRepricingMassaUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        snapshot_repo=snapshot_repo,
    )
    try:
        result = await use_case.execute(profile_id, session_id)
    except ProfileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except RepricingMassaError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e

    return RevertResponse(
        ok=len(result.erros) == 0,
        revertidos=result.aplicados,
        ja_no_preco=result.ja_no_preco,
        erros=[
            ApplyErroResponse(
                item_id=e["item_id"], operacao=e["operacao"], erro=e["erro"],
            )
            for e in result.erros
        ],
    )
