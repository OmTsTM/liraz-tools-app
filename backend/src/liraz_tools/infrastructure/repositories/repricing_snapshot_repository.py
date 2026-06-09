"""Repository do snapshot persistente de Reprecificar Tudo (jun/2026).

A cada execução do botão "Reprecificar Tudo", N linhas são gravadas (1 por
PUT bem-sucedido) sob uma `session_id` única. Permite reverter a sessão
inteira via `marcar_revertido` + PUTs pros `preco_anterior`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.infrastructure.db.models import RepricingSnapshotModel


@dataclass(frozen=True)
class SnapshotItem:
    """Uma linha individual."""

    item_id: str
    preco_anterior: float
    preco_novo: float


@dataclass(frozen=True)
class SessaoResumo:
    """Resumo duma sessão pra listagem na UI."""

    session_id: UUID
    qtd_itens: int
    criado_em: datetime
    revertido_em: datetime | None


class RepricingSnapshotRepository:
    """CRUD do snapshot."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def nova_sessao() -> UUID:
        """Gera um UUID novo pra agrupar os PUTs duma execução."""
        return uuid4()

    async def gravar_em_lote(
        self,
        profile_id: UUID,
        session_id: UUID,
        items: list[SnapshotItem],
    ) -> None:
        """Insere N linhas em uma transação."""
        if not items:
            return
        agora = datetime.now(UTC)
        for it in items:
            self._session.add(RepricingSnapshotModel(
                id=str(uuid4()),
                profile_id=str(profile_id),
                session_id=str(session_id),
                item_id=it.item_id,
                preco_anterior=it.preco_anterior,
                preco_novo=it.preco_novo,
                created_at=agora,
                revertido_em=None,
            ))
        await self._session.commit()

    async def listar_sessoes(
        self, profile_id: UUID, *, limit: int = 10,
    ) -> list[SessaoResumo]:
        """Últimas N sessões do perfil, do mais recente pro mais antigo.

        Agrupa por session_id e devolve qtd, timestamps.
        """
        stmt = (
            select(
                RepricingSnapshotModel.session_id,
                func.count(RepricingSnapshotModel.id).label("qtd"),
                func.min(RepricingSnapshotModel.created_at).label("criado"),
                func.max(RepricingSnapshotModel.revertido_em).label("revertido"),
            )
            .where(RepricingSnapshotModel.profile_id == str(profile_id))
            .group_by(RepricingSnapshotModel.session_id)
            .order_by(func.min(RepricingSnapshotModel.created_at).desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            SessaoResumo(
                session_id=UUID(r[0]),
                qtd_itens=int(r[1]),
                criado_em=r[2],
                revertido_em=r[3],
            )
            for r in rows
        ]

    async def buscar_itens_da_sessao(
        self, profile_id: UUID, session_id: UUID,
    ) -> list[SnapshotItem]:
        """Devolve as N linhas duma sessão pra preparar o undo."""
        stmt = (
            select(RepricingSnapshotModel)
            .where(
                RepricingSnapshotModel.profile_id == str(profile_id),
                RepricingSnapshotModel.session_id == str(session_id),
            )
            .order_by(RepricingSnapshotModel.created_at.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            SnapshotItem(
                item_id=r.item_id,
                preco_anterior=r.preco_anterior,
                preco_novo=r.preco_novo,
            )
            for r in rows
        ]

    async def marcar_revertido(
        self, profile_id: UUID, session_id: UUID,
    ) -> None:
        """Set `revertido_em=now` em todas as linhas da sessão. Idempotente."""
        agora = datetime.now(UTC)
        stmt = (
            update(RepricingSnapshotModel)
            .where(
                RepricingSnapshotModel.profile_id == str(profile_id),
                RepricingSnapshotModel.session_id == str(session_id),
            )
            .values(revertido_em=agora)
        )
        await self._session.execute(stmt)
        await self._session.commit()
