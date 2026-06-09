"""Repository de migrações executadas (histórico, Leva 5.9.4.C.1)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.infrastructure.db.models import MigracaoExecutadaModel


@dataclass
class MigracaoExecutadaRecord:
    """Entry de histórico de migração."""

    id: UUID
    profile_id: UUID
    campanha_origem_id: UUID | None
    campanha_destino_ml_id: str
    campanha_destino_ml_nome: str | None
    campanha_destino_ml_tipo: str
    item_id: str
    sku: str | None
    operacao: str  # 'add' | 'remove'
    destino_status: str | None  # 'started' | 'pending'
    deal_price: float | None
    margem_pct_prevista: float | None
    sucesso: bool
    dry_run: bool
    erro_detalhe: str | None
    timestamp: datetime


class MigracaoExecutadaRepository:
    """Persistência do histórico de migrações."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, record: MigracaoExecutadaRecord) -> None:
        """Salva uma entrada de histórico."""
        model = MigracaoExecutadaModel(
            id=str(record.id),
            profile_id=str(record.profile_id),
            campanha_origem_id=(
                str(record.campanha_origem_id)
                if record.campanha_origem_id else None
            ),
            campanha_destino_ml_id=record.campanha_destino_ml_id,
            campanha_destino_ml_nome=record.campanha_destino_ml_nome,
            campanha_destino_ml_tipo=record.campanha_destino_ml_tipo,
            item_id=record.item_id,
            sku=record.sku,
            operacao=record.operacao,
            destino_status=record.destino_status,
            deal_price=record.deal_price,
            margem_pct_prevista=record.margem_pct_prevista,
            sucesso=record.sucesso,
            dry_run=record.dry_run,
            erro_detalhe=record.erro_detalhe,
            timestamp=record.timestamp,
        )
        self._session.add(model)
        await self._session.commit()

    async def list_by_profile(
        self,
        profile_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MigracaoExecutadaRecord]:
        """Lista histórico do perfil, mais recentes primeiro."""
        stmt = (
            select(MigracaoExecutadaModel)
            .where(MigracaoExecutadaModel.profile_id == str(profile_id))
            .order_by(MigracaoExecutadaModel.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_to_record(m) for m in result.scalars().all()]

    async def listar_pares_migrados_recentemente(
        self,
        profile_id: UUID,
        *,
        ttl_dias: int = 7,
        incluir_dry_run: bool = False,
    ) -> set[tuple[str, str]]:
        """Pares (item_id, campanha_destino_ml_id) migrados com SUCESSO no TTL.

        Usado pelo detector de oportunidades (Leva 5.9.4.B) pra evitar sugerir
        de novo migrações que ele mesmo já fez. O ML às vezes continua
        retornando o item como `candidate` mesmo após o POST de adição
        (eventual consistency, ou quando o anúncio tem variations e só
        algumas foram aceitas) — sem esse filtro o usuário vê sugestões
        fantasmas dos próprios items que já migrou.

        Filtros aplicados:
          - `operacao == 'add'` (só migrações de adição contam)
          - `sucesso == True` (descartes não bloqueiam re-sugestão)
          - `timestamp >= now() - ttl_dias` (depois desse prazo é razoável
            re-avaliar — usuário pode ter removido manualmente no painel ML)
          - `dry_run == False` por default (simulação não bloqueia execução
            real). Passe `incluir_dry_run=True` se quiser bloquear preview
            de oportunidade que já foi simulada.

        TTL default 7 dias: balanceia "evitar duplicação" com "permitir
        re-sugestão se item foi removido manualmente do ML". Se o user
        REMOVEU manualmente no painel ML, em 7 dias o histórico expira e a
        oportunidade volta a aparecer. Pra forçar re-detecção imediata, há
        rota administrativa de limpeza do histórico (a implementar) ou o
        próprio user pode rodar com `ignorar_historico=True`.
        """
        cutoff = datetime.now(UTC) - timedelta(days=ttl_dias)
        stmt = (
            select(
                MigracaoExecutadaModel.item_id,
                MigracaoExecutadaModel.campanha_destino_ml_id,
            )
            .where(MigracaoExecutadaModel.profile_id == str(profile_id))
            .where(MigracaoExecutadaModel.operacao == "add")
            .where(MigracaoExecutadaModel.sucesso.is_(True))
            .where(MigracaoExecutadaModel.timestamp >= cutoff)
        )
        if not incluir_dry_run:
            stmt = stmt.where(MigracaoExecutadaModel.dry_run.is_(False))
        result = await self._session.execute(stmt)
        return {(row[0], row[1]) for row in result.all()}


def _to_record(model: MigracaoExecutadaModel) -> MigracaoExecutadaRecord:
    return MigracaoExecutadaRecord(
        id=UUID(model.id),
        profile_id=UUID(model.profile_id),
        campanha_origem_id=(
            UUID(model.campanha_origem_id) if model.campanha_origem_id else None
        ),
        campanha_destino_ml_id=model.campanha_destino_ml_id,
        campanha_destino_ml_nome=model.campanha_destino_ml_nome,
        campanha_destino_ml_tipo=model.campanha_destino_ml_tipo,
        item_id=model.item_id,
        sku=model.sku,
        operacao=model.operacao,
        destino_status=model.destino_status,
        deal_price=model.deal_price,
        margem_pct_prevista=model.margem_pct_prevista,
        sucesso=model.sucesso,
        dry_run=model.dry_run,
        erro_detalhe=model.erro_detalhe,
        timestamp=model.timestamp,
    )


def novo_record_id() -> UUID:
    """Helper pra geração de UUID — facilita mock em testes."""
    return uuid4()


def agora_utc() -> datetime:
    """Helper pra timestamp — facilita mock em testes."""
    return datetime.now(UTC)
