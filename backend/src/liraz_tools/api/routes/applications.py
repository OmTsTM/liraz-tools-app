"""Endpoints de aplicação de campanha (Leva 5.4).

Rotas:
  POST /api/profiles/{id}/campaigns/{cid}/start
       Dispara aplicação manual. Body: {imediato: bool=true}.
       Retorna Application com application_id pra polling.

  GET  /api/profiles/{id}/campaigns/{cid}/application
       Aplicação MAIS RECENTE da campanha (pra polling do frontend).

  GET  /api/profiles/{id}/applications/{app_id}
       Detalhe de aplicação específica.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from liraz_tools.api.deps import CredsRepo, DbSession, ProfileRepo
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.applications.entity import Application
from liraz_tools.domain.applications.start_passo3_use_case import (
    StartCampaignPasso3UseCase,
)
from liraz_tools.domain.applications.use_cases import (
    CampaignNotReadyError,
    JanelaInsuficienteError,
    RevertCampaignUseCase,
    RevertNotAvailableError,
    StartCampaignUseCase,
)
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.infrastructure.background.job_runner import (
    JobRunner,
    get_job_runner,
)
from liraz_tools.infrastructure.repositories.applications_repository import (
    ApplicationsRepository,
    RollbacksRepository,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignNotFoundError,
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)

router = APIRouter(prefix="/api/profiles", tags=["applications"])
logger = get_logger(__name__)


# ─── Schemas ────────────────────────────────────────────────────────────


class StartCampaignRequest(BaseModel):
    """Body do POST /start."""

    imediato: bool = True
    """Se True (default), muda data_inicio + hora_disparo pra agora antes
    de disparar (botão Play). Se False, usa horários da campanha como
    estão (futuro: chamada pelo scheduler — Leva 5.5)."""


# ─── DI ─────────────────────────────────────────────────────────────────


def _get_apps_repo() -> ApplicationsRepository:
    return ApplicationsRepository()


def _get_rollbacks_repo() -> RollbacksRepository:
    return RollbacksRepository()


def _get_snaps_repo() -> SnapshotsRepository:
    return SnapshotsRepository()


def _get_job_runner_dep() -> JobRunner:
    return get_job_runner()


def _get_campaign_repo(session: DbSession) -> CampaignRepository:
    return CampaignRepository(session)


AppsRepoDep = Annotated[ApplicationsRepository, Depends(_get_apps_repo)]
RollbacksRepoDep = Annotated[RollbacksRepository, Depends(_get_rollbacks_repo)]
SnapsRepoDep = Annotated[SnapshotsRepository, Depends(_get_snaps_repo)]
JobRunnerDep = Annotated[JobRunner, Depends(_get_job_runner_dep)]
CampaignRepoDep = Annotated[CampaignRepository, Depends(_get_campaign_repo)]


# ─── Endpoints ──────────────────────────────────────────────────────────


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/start",
    response_model=Application,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    body: StartCampaignRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    snapshots_repo: SnapsRepoDep,
    applications_repo: AppsRepoDep,
    rollbacks_repo: RollbacksRepoDep,
    creds_repo: CredsRepo,
    job_runner: JobRunnerDep,
) -> Application:
    """Dispara a aplicação manual da campanha — modo 'Iniciar Agora'.

    Retorna 202 + Application com application_id pra polling. O trabalho
    em si roda em background; o cliente faz GET /application periodicamente
    pra ver progresso.

    Ramifica entre dois fluxos:
    - Campanha tem `simulacao_id` → fluxo clássico (snapshot da simulação).
    - Campanha sem `simulacao_id` (rascunho passo3) → computa sugestão na
      hora via `StartCampaignPasso3UseCase`.
    """
    # Carrega a campanha pra decidir o fluxo. Erros de not found são
    # capturados pelo handler abaixo.
    try:
        campaign = await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    try:
        if campaign.simulacao_id is not None:
            classic_uc = StartCampaignUseCase(
                profile_repo=profile_repo,
                campaign_repo=campaign_repo,
                snapshots_repo=snapshots_repo,
                applications_repo=applications_repo,
                rollbacks_repo=rollbacks_repo,
                creds_repo=creds_repo,
                job_runner=job_runner,
            )
            return await classic_uc.execute(
                profile_id=profile_id,
                campaign_id=campaign_id,
                imediato=body.imediato,
            )

        # Sem simulação: fluxo passo3 (deal_price computado na hora).
        passo3_uc = StartCampaignPasso3UseCase(
            profile_repo=profile_repo,
            campaign_repo=campaign_repo,
            applications_repo=applications_repo,
            creds_repo=creds_repo,
            job_runner=job_runner,
        )
        return await passo3_uc.execute(
            profile_id=profile_id,
            campaign_id=campaign_id,
            imediato=body.imediato,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except CampaignNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (CampaignNotReadyError, JanelaInsuficienteError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/{profile_id}/campaigns/{campaign_id}/application",
    response_model=Application | None,
)
async def get_latest_application(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    applications_repo: AppsRepoDep,
) -> Application | None:
    """Aplicação MAIS RECENTE de uma campanha (usada pra polling).

    Retorna None se a campanha nunca foi disparada.
    """
    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    apps = applications_repo.list_by_campaign(profile.slug, str(campaign_id))
    return apps[0] if apps else None


@router.get(
    "/{profile_id}/applications/{application_id}",
    response_model=Application,
)
async def get_application(
    profile_id: UUID,
    application_id: str,
    profile_repo: ProfileRepo,
    applications_repo: AppsRepoDep,
) -> Application:
    """Detalhe de uma aplicação específica pelo seu id."""
    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    app = applications_repo.get(profile.slug, application_id)
    if app is None:
        raise HTTPException(
            status_code=404,
            detail=f"aplicação '{application_id}' não encontrada",
        )
    return app


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/revert",
    response_model=Application,
    status_code=status.HTTP_202_ACCEPTED,
)
async def revert_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    applications_repo: AppsRepoDep,
    rollbacks_repo: RollbacksRepoDep,
    creds_repo: CredsRepo,
    job_runner: JobRunnerDep,
) -> Application:
    """Reverte preços aplicados pela campanha usando o rollback gerado.

    Pré-requisitos:
    - Campanha em status `ativa`, `finalizada` ou `falha`
    - Campanha tem `rollback_id` preenchido

    Cria nova Application com phase=reverting pra auditoria. Trabalho
    roda em background — polling igual ao start.
    """
    use_case = RevertCampaignUseCase(
        profile_repo=profile_repo,
        campaign_repo=campaign_repo,
        applications_repo=applications_repo,
        rollbacks_repo=rollbacks_repo,
        creds_repo=creds_repo,
        job_runner=job_runner,
    )
    try:
        return await use_case.execute(
            profile_id=profile_id,
            campaign_id=campaign_id,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except CampaignNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RevertNotAvailableError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
