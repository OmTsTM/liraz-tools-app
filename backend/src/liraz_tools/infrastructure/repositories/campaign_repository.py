"""Repository SQLAlchemy pra campanhas."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.domain.campaigns.entity import Campaign, CampaignStatus
from liraz_tools.infrastructure.db.models import CampaignModel
from liraz_tools.infrastructure.repositories.campaigns_list_cache import (
    get_campaigns_list_cache,
)


class CampaignNotFoundError(Exception):
    """Campanha não existe pra esse perfil."""


class CampaignRepository:
    """CRUD de campanhas com SQLAlchemy async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, profile_id: UUID, campaign_id: UUID) -> Campaign:
        """Busca por id. Garante que pertence ao perfil informado.

        Levanta CampaignNotFoundError se não existe ou pertence a outro perfil.
        """
        stmt = select(CampaignModel).where(
            CampaignModel.id == str(campaign_id),
            CampaignModel.profile_id == str(profile_id),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise CampaignNotFoundError(
                f"campanha {campaign_id} não encontrada no perfil {profile_id}"
            )
        return _to_entity(row)

    async def list_by_profile(
        self,
        profile_id: UUID,
        statuses: list[CampaignStatus] | None = None,
        include_archived: bool = False,
    ) -> list[Campaign]:
        """Lista campanhas do perfil, ordenadas por data_inicio (mais recente primeiro).

        Filtro opcional por status — útil pra UI mostrar só "agendadas e ativas".

        `include_archived` controla campanhas auto-arquivadas (Leva 5.7.1):
        false (default) esconde da listagem; true mostra incluindo as antigas.
        """
        stmt = select(CampaignModel).where(
            CampaignModel.profile_id == str(profile_id)
        )
        if statuses:
            stmt = stmt.where(CampaignModel.status.in_(statuses))
        if not include_archived:
            stmt = stmt.where(CampaignModel.archived_at.is_(None))
        stmt = stmt.order_by(CampaignModel.data_inicio.desc())

        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def list_all_by_status(
        self,
        status: CampaignStatus,
    ) -> list[Campaign]:
        """Lista campanhas em um status, ATRAVÉS de todos os perfis.

        Usado pelo scheduler que precisa varrer "todas as agendadas" e
        "todas as ativas" pra decidir o que fazer.
        """
        stmt = (
            select(CampaignModel)
            .where(CampaignModel.status == status)
            .order_by(CampaignModel.data_inicio.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def list_finalizadas_para_arquivar(self, cutoff: date) -> list[Campaign]:
        """Campanhas em status `finalizada` cujo data_fim <= cutoff e ainda
        não foram arquivadas. Usado pelo scheduler (Leva 5.7.1) pra
        soft-delete automático após 15 dias.
        """
        stmt = (
            select(CampaignModel)
            .where(CampaignModel.status == "finalizada")
            .where(CampaignModel.data_fim <= cutoff)
            .where(CampaignModel.archived_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def find_by_ml_id(
        self, profile_id: UUID, ml_campaign_id: str,
    ) -> Campaign | None:
        """Procura campanha local com determinado `ml_campaign_id`.

        Usado pela importação ML (Leva 5.9.2) pra detectar idempotência —
        se já importou antes, devolve a existente em vez de criar duplicata.
        """
        stmt = (
            select(CampaignModel)
            .where(CampaignModel.profile_id == str(profile_id))
            .where(CampaignModel.ml_campaign_id == ml_campaign_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_nomes_do_perfil(
        self,
        profile_id: UUID,
        exclude_campaign_id: UUID | None = None,
    ) -> set[str]:
        """Retorna o set de nomes de campanhas do perfil — em QUALQUER status.

        Usado pra detectar duplicatas na criação/edição. Inclui inclusive
        finalizadas/canceladas (decisão do user — quer ver TODOS os nomes).

        `exclude_campaign_id` permite ignorar a própria campanha no caso de
        edit (renomear pro mesmo nome não conta como conflito).
        """
        stmt = select(CampaignModel.nome).where(
            CampaignModel.profile_id == str(profile_id)
        )
        if exclude_campaign_id is not None:
            stmt = stmt.where(CampaignModel.id != str(exclude_campaign_id))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def create(self, campaign: Campaign) -> Campaign:
        """Cria nova campanha. Retorna o entity (com timestamps refrescados)."""
        now = datetime.now(UTC)
        model = CampaignModel(
            id=str(campaign.id),
            profile_id=str(campaign.profile_id),
            nome=campaign.nome,
            simulacao_id=campaign.simulacao_id,
            data_inicio=campaign.data_inicio,
            data_fim=campaign.data_fim,
            hora_disparo=campaign.hora_disparo,
            hora_fim=campaign.hora_fim,
            skus_selecionados_json=(
                json.dumps(campaign.skus_selecionados)
                if campaign.skus_selecionados is not None
                else None
            ),
            status=campaign.status,
            created_at=now,
            updated_at=now,
            ml_campaign_id=campaign.ml_campaign_id,
            aplicacao_id=campaign.aplicacao_id,
            rollback_id=campaign.rollback_id,
            erro=campaign.erro,
            archived_at=campaign.archived_at,
            origem=campaign.origem,
            margem_min_migracao=campaign.margem_min_migracao,
            margem_max_migracao=campaign.margem_max_migracao,
            renovada=campaign.renovada,
        )
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        get_campaigns_list_cache().bump(campaign.profile_id)
        return _to_entity(model)

    async def update(self, campaign: Campaign) -> Campaign:
        """Atualiza campanha existente. Refresca `updated_at`."""
        stmt = select(CampaignModel).where(
            CampaignModel.id == str(campaign.id),
            CampaignModel.profile_id == str(campaign.profile_id),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise CampaignNotFoundError(
                f"campanha {campaign.id} não encontrada"
            )

        model.nome = campaign.nome
        model.simulacao_id = campaign.simulacao_id
        model.data_inicio = campaign.data_inicio
        model.data_fim = campaign.data_fim
        model.hora_disparo = campaign.hora_disparo
        model.hora_fim = campaign.hora_fim
        model.skus_selecionados_json = (
            json.dumps(campaign.skus_selecionados)
            if campaign.skus_selecionados is not None
            else None
        )
        model.status = campaign.status
        model.updated_at = datetime.now(UTC)
        model.ml_campaign_id = campaign.ml_campaign_id
        model.aplicacao_id = campaign.aplicacao_id
        model.rollback_id = campaign.rollback_id
        model.erro = campaign.erro
        model.archived_at = campaign.archived_at
        model.origem = campaign.origem
        model.margem_min_migracao = campaign.margem_min_migracao
        model.margem_max_migracao = campaign.margem_max_migracao
        model.renovada = campaign.renovada

        await self._session.commit()
        await self._session.refresh(model)
        get_campaigns_list_cache().bump(campaign.profile_id)
        return _to_entity(model)

    async def delete(self, profile_id: UUID, campaign_id: UUID) -> bool:
        """Apaga uma campanha. Retorna True se existia."""
        stmt = select(CampaignModel).where(
            CampaignModel.id == str(campaign_id),
            CampaignModel.profile_id == str(profile_id),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self._session.delete(model)
        await self._session.commit()
        get_campaigns_list_cache().bump(profile_id)
        return True


def _to_entity(model: CampaignModel) -> Campaign:
    """Converte ORM model → entity Pydantic.

    UUIDs vêm como str do SQLite, precisa converter pra UUID.
    """
    return Campaign(
        id=UUID(str(model.id)) if not isinstance(model.id, UUID) else model.id,
        profile_id=(
            UUID(str(model.profile_id))
            if not isinstance(model.profile_id, UUID)
            else model.profile_id
        ),
        nome=model.nome,
        simulacao_id=model.simulacao_id,
        data_inicio=model.data_inicio,
        data_fim=model.data_fim,
        hora_disparo=model.hora_disparo,
        hora_fim=model.hora_fim,
        skus_selecionados=(
            json.loads(model.skus_selecionados_json)
            if model.skus_selecionados_json
            else None
        ),
        status=model.status,  # type: ignore[arg-type]
        created_at=model.created_at,
        updated_at=model.updated_at,
        ml_campaign_id=model.ml_campaign_id,
        aplicacao_id=model.aplicacao_id,
        rollback_id=model.rollback_id,
        erro=model.erro,
        archived_at=model.archived_at,
        origem=model.origem,  # type: ignore[arg-type]
        margem_min_migracao=model.margem_min_migracao,
        margem_max_migracao=model.margem_max_migracao,
        renovada=getattr(model, "renovada", False) or False,
    )
