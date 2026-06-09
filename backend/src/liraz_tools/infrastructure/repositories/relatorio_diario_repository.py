"""Repository do snapshot diário de KPIs (Fatia 1 de relatórios, jun/2026).

Persiste só os agregados que alimentam comparativos (receita, lucro, pedidos,
unidades). O snapshot completo (SKUs, sugestões) é regenerado on-demand.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.infrastructure.db.models import RelatorioDiarioKpisModel


@dataclass(frozen=True)
class KpisHistorico:
    """Snapshot persistido de um único dia."""

    dia: date
    receita_bruta: float
    lucro_liquido: float
    total_pedidos: int
    total_unidades: int


class RelatorioDiarioRepository:
    """CRUD do snapshot de KPIs por (profile_id, dia)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        profile_id: UUID,
        *,
        dia: date,
        receita_bruta: float,
        lucro_liquido: float,
        total_pedidos: int,
        total_unidades: int,
    ) -> None:
        """Insere ou atualiza o snapshot do dia.

        Idempotente: re-gerar o mesmo dia sobrescreve com novos valores
        (orders atrasadas ou re-cálculos com custos atualizados).
        """
        stmt = select(RelatorioDiarioKpisModel).where(
            RelatorioDiarioKpisModel.profile_id == str(profile_id),
            RelatorioDiarioKpisModel.dia == dia,
        )
        existente = (await self._session.execute(stmt)).scalar_one_or_none()
        agora = datetime.now(UTC)

        if existente is not None:
            existente.receita_bruta = receita_bruta
            existente.lucro_liquido = lucro_liquido
            existente.total_pedidos = total_pedidos
            existente.total_unidades = total_unidades
            existente.gerado_em = agora
        else:
            self._session.add(RelatorioDiarioKpisModel(
                id=str(uuid4()),
                profile_id=str(profile_id),
                dia=dia,
                receita_bruta=receita_bruta,
                lucro_liquido=lucro_liquido,
                total_pedidos=total_pedidos,
                total_unidades=total_unidades,
                gerado_em=agora,
            ))
        await self._session.commit()

    async def buscar(
        self, profile_id: UUID, dia: date,
    ) -> KpisHistorico | None:
        """Retorna o snapshot de um dia específico, ou None se nunca gerado."""
        stmt = select(RelatorioDiarioKpisModel).where(
            RelatorioDiarioKpisModel.profile_id == str(profile_id),
            RelatorioDiarioKpisModel.dia == dia,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return KpisHistorico(
            dia=row.dia,
            receita_bruta=row.receita_bruta,
            lucro_liquido=row.lucro_liquido,
            total_pedidos=row.total_pedidos,
            total_unidades=row.total_unidades,
        )

    async def listar_ultimos_n_dias(
        self, profile_id: UUID, *, ate: date, n: int,
    ) -> list[KpisHistorico]:
        """Retorna até `n` snapshots cobrindo `[ate - n + 1, ate]`, ordenados
        do mais antigo pro mais recente. Dias sem snapshot são pulados — o
        caller decide como tratar (média dos disponíveis, ignorar, etc.)."""
        desde = ate - timedelta(days=n - 1)
        stmt = (
            select(RelatorioDiarioKpisModel)
            .where(
                RelatorioDiarioKpisModel.profile_id == str(profile_id),
                RelatorioDiarioKpisModel.dia >= desde,
                RelatorioDiarioKpisModel.dia <= ate,
            )
            .order_by(RelatorioDiarioKpisModel.dia.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            KpisHistorico(
                dia=r.dia,
                receita_bruta=r.receita_bruta,
                lucro_liquido=r.lucro_liquido,
                total_pedidos=r.total_pedidos,
                total_unidades=r.total_unidades,
            )
            for r in rows
        ]
