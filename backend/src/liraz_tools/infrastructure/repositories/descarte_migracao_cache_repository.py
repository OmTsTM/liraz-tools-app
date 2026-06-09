"""Repository de cache de descartes do detector de migração (Leva 5.9.4 - opt 4)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.infrastructure.db.models import DescarteMigracaoCacheModel


@dataclass(frozen=True)
class DescarteCacheEntry:
    """Uma entrada do cache de descartes."""

    item_id: str
    promotion_id: str
    motivo: str


class DescarteMigracaoCacheRepository:
    """Cache de descartes do detector pra acelerar runs subsequentes."""

    def __init__(self, session: AsyncSession, *, ttl_horas: int = 24) -> None:
        self._session = session
        self._ttl = timedelta(hours=ttl_horas)

    async def listar_validos(
        self, profile_id: UUID,
    ) -> set[tuple[str, str]]:
        """Retorna set de (item_id, promotion_id) descartados ainda no TTL.

        Quem está nesse set deve ser pulado pelo detector.
        """
        agora = datetime.now(UTC)
        stmt = (
            select(
                DescarteMigracaoCacheModel.item_id,
                DescarteMigracaoCacheModel.promotion_id,
            )
            .where(
                DescarteMigracaoCacheModel.profile_id == str(profile_id),
                DescarteMigracaoCacheModel.expira_em > agora,
            )
        )
        result = await self._session.execute(stmt)
        return {(row[0], row[1]) for row in result.all()}

    async def adicionar(
        self,
        profile_id: UUID,
        *,
        item_id: str,
        promotion_id: str,
        motivo: str,
    ) -> None:
        """Salva um descarte. Não dedup — se chamar 2x pro mesmo par, vira
        2 linhas. Limpeza periódica via `purgar_expirados`.
        """
        agora = datetime.now(UTC)
        self._session.add(DescarteMigracaoCacheModel(
            id=str(uuid4()),
            profile_id=str(profile_id),
            item_id=item_id,
            promotion_id=promotion_id,
            motivo=motivo,
            criado_em=agora,
            expira_em=agora + self._ttl,
        ))
        await self._session.commit()

    async def adicionar_lote(
        self,
        profile_id: UUID,
        entries: list[DescarteCacheEntry],
    ) -> None:
        """Salva vários descartes em uma transação só (mais rápido)."""
        if not entries:
            return
        agora = datetime.now(UTC)
        expira = agora + self._ttl
        for e in entries:
            self._session.add(DescarteMigracaoCacheModel(
                id=str(uuid4()),
                profile_id=str(profile_id),
                item_id=e.item_id,
                promotion_id=e.promotion_id,
                motivo=e.motivo,
                criado_em=agora,
                expira_em=expira,
            ))
        await self._session.commit()

    async def purgar_expirados(self) -> int:
        """Remove entries expiradas. Retorna quantas removeu."""
        agora = datetime.now(UTC)
        stmt = delete(DescarteMigracaoCacheModel).where(
            DescarteMigracaoCacheModel.expira_em <= agora,
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return cast("CursorResult[Any]", result).rowcount or 0

    async def invalidar_perfil(self, profile_id: UUID) -> int:
        """Remove TODOS os descartes do perfil (uso: quando configuração
        muda — faixa de margem, etc — invalida cache pra forçar
        re-avaliação)."""
        stmt = delete(DescarteMigracaoCacheModel).where(
            DescarteMigracaoCacheModel.profile_id == str(profile_id),
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return cast("CursorResult[Any]", result).rowcount or 0
