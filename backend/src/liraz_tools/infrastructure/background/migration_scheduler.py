"""Scheduler de migrações automáticas (Leva 5.9.4.C.3).

Task assíncrona separada do `CampaignScheduler` principal. Roda em
intervalo mais longo (default 6h) por perfil que tem
`migracao_automatica_ativa=True`. Cada perfil tem timestamp próprio
da última execução pra respeitar `migracao_intervalo_horas` individual.

Por execução, pra cada campanha origem='ml' do perfil, faz:
1. Detector + Executor de migrações (5.9.4.B + C.1)
2. Verificador + Corretor de cobertura (5.9.4.C.2.A)

Tudo respeitando `profile.config.migracao_dry_run` (default true =
freio extra). Se dry_run=true: salva histórico marcado, não chama
endpoints reais do ML.

Liga via env var `ENABLE_MIGRATION_SCHEDULER=true`. Default desligado.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.migration.ciclos_5_11 import (
    ResultadoCiclo511,
    ciclo_a_renovar_guarda_chuvas,
    ciclo_b_bootstrap_guarda_chuva,
    ciclo_c_onboarding_total,
)
from liraz_tools.domain.migration.cobertura import (
    CorrigirCoberturaUseCase,
    VerificarCoberturaUseCase,
)
from liraz_tools.domain.migration.executor import ExecutarMigracoesUseCase
from liraz_tools.domain.migration.use_cases import (
    DetectarOportunidadesMigracaoUseCase,
)
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.descarte_migracao_cache_repository import (
    DescarteMigracaoCacheRepository,
)
from liraz_tools.infrastructure.repositories.migracao_executada_repository import (
    MigracaoExecutadaRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

logger = get_logger(__name__)


# Frequência com que verificamos "alguém precisa rodar agora?". Cada perfil
# tem seu próprio intervalo (migracao_intervalo_horas), mas precisamos
# checar com frequência menor pra capturar mudanças de config.
SCHEDULER_CHECK_INTERVAL_S = 60 * 10  # 10 min

# Lock global em memória pra evitar 2 execuções simultâneas pro mesmo perfil
_locks_por_perfil: dict[UUID, asyncio.Lock] = {}
# Última execução em memória — não persiste em DB pra simplificar (em
# restart do uvicorn, o scheduler roda na primeira passada, o que é OK)
_ultima_execucao_por_perfil: dict[UUID, datetime] = {}


def scheduler_habilitado() -> bool:
    """True se a env var ENABLE_MIGRATION_SCHEDULER=true."""
    return os.getenv("ENABLE_MIGRATION_SCHEDULER", "").lower() in (
        "true", "1", "yes",
    )


def _lock_para(profile_id: UUID) -> asyncio.Lock:
    if profile_id not in _locks_por_perfil:
        _locks_por_perfil[profile_id] = asyncio.Lock()
    return _locks_por_perfil[profile_id]


class MigrationScheduler:
    """Loop async que verifica e executa migrações automáticas por perfil."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        """Inicia loop se a env var permitir. Idempotente."""
        if not scheduler_habilitado():
            logger.info(
                "migration_scheduler_disabled",
                env_var="ENABLE_MIGRATION_SCHEDULER",
                hint="set ENABLE_MIGRATION_SCHEDULER=true to enable",
            )
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="migration-scheduler",
        )
        logger.info(
            "migration_scheduler_started",
            check_interval_s=SCHEDULER_CHECK_INTERVAL_S,
        )

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except TimeoutError:
            self._task.cancel()
            logger.warning("migration_scheduler_force_cancelled")
        finally:
            self._task = None
        logger.info("migration_scheduler_stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("migration_scheduler_tick_failed")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=SCHEDULER_CHECK_INTERVAL_S,
                )

    async def _tick(self) -> None:
        """Lista perfis com migração ativa e processa os que devem rodar agora."""
        async with session_scope() as session:
            profile_repo = SQLAlchemyProfileRepository(session)
            todos_perfis = await profile_repo.list_all()

        perfis_ativos = [
            p for p in todos_perfis
            if p.status.value == "connected"
            and p.config.migracao_automatica_ativa
        ]

        if not perfis_ativos:
            logger.debug(
                "migration_scheduler_tick_no_active_profiles",
            )
            return

        agora = datetime.now(UTC)
        rodaram = 0
        for profile in perfis_ativos:
            intervalo = timedelta(hours=profile.config.migracao_intervalo_horas)
            ultima = _ultima_execucao_por_perfil.get(profile.id)
            if ultima is not None and agora - ultima < intervalo:
                continue  # ainda dentro do intervalo, pula

            # Tenta pegar lock — se já tá rodando, pula
            lock = _lock_para(profile.id)
            if lock.locked():
                logger.info(
                    "migration_scheduler_skip_locked",
                    profile_id=str(profile.id),
                )
                continue

            async with lock:
                _ultima_execucao_por_perfil[profile.id] = agora
                try:
                    await _processar_perfil(profile.id)
                    rodaram += 1
                except Exception:
                    logger.exception(
                        "migration_scheduler_perfil_falhou",
                        profile_id=str(profile.id),
                    )

        if rodaram > 0:
            logger.info(
                "migration_scheduler_tick_done",
                perfis_processados=rodaram,
                perfis_ativos_total=len(perfis_ativos),
            )


async def _processar_perfil(profile_id: UUID) -> None:
    """Roda 2 ciclos pra todas as campanhas origem=ml do perfil:
    1. Detector + Executor
    2. Watchdog cobertura (verifica + corrige)

    Cada campanha é processada independente. Erro em uma não para as outras.
    """
    logger.info(
        "migration_scheduler_perfil_iniciando",
        profile_id=str(profile_id),
    )

    # ─── Leva 5.11 — Ciclos A (renovação), B (bootstrap), C (onboarding) ─
    # Rodam ANTES do loop de campanhas existente porque podem criar
    # guarda-chuvas novas que devem entrar no loop também.
    async with session_scope() as session:
        creds_repo = PerProfileCredentialsRepository()
        profile_repo = SQLAlchemyProfileRepository(session)
        campaign_repo = CampaignRepository(session)
        historico_repo = MigracaoExecutadaRepository(session)
        # Snapshot pra a Fase 1 (inflar) do Ciclo C — persiste no DB pra
        # permitir reverter via UI "Reprecificar tudo" → Reverter sessão.
        from liraz_tools.infrastructure.repositories.repricing_snapshot_repository import (
            RepricingSnapshotRepository,
        )
        snapshot_repo = RepricingSnapshotRepository(session)

        profile = await profile_repo.get_by_id(profile_id)
        dry_run = profile.config.migracao_dry_run
        resultado_5_11 = ResultadoCiclo511(profile_id=profile_id, dry_run=dry_run)

        # Ciclo A — renovação antecipada
        try:
            await ciclo_a_renovar_guarda_chuvas(
                profile_id=profile_id,
                profile_repo=profile_repo,
                campaign_repo=campaign_repo,
                creds_repo=creds_repo,
                dry_run=dry_run,
                resultado=resultado_5_11,
            )
        except Exception:
            logger.exception("ciclo_a_falhou", profile_id=str(profile_id))

        # Ciclo B — bootstrap se não há guarda-chuva
        try:
            guarda_chuva = await ciclo_b_bootstrap_guarda_chuva(
                profile_id=profile_id,
                profile_repo=profile_repo,
                campaign_repo=campaign_repo,
                creds_repo=creds_repo,
                dry_run=dry_run,
                resultado=resultado_5_11,
            )
        except Exception:
            logger.exception("ciclo_b_falhou", profile_id=str(profile_id))
            guarda_chuva = None

        # Ciclo C — onboarding total (precisa de guarda-chuva ativa)
        if guarda_chuva is not None:
            try:
                await ciclo_c_onboarding_total(
                    profile_id=profile_id,
                    guarda_chuva=guarda_chuva,
                    profile_repo=profile_repo,
                    campaign_repo=campaign_repo,
                    creds_repo=creds_repo,
                    historico_repo=historico_repo,
                    snapshot_repo=snapshot_repo,
                    dry_run=dry_run,
                    resultado=resultado_5_11,
                )
            except Exception:
                logger.exception("ciclo_c_falhou", profile_id=str(profile_id))
        else:
            logger.info(
                "ciclo_c_skip_sem_guarda_chuva",
                profile_id=str(profile_id),
            )

        logger.info(
            "ciclos_5_11_concluido",
            profile_id=str(profile_id),
            dry_run=dry_run,
            renovacao_criadas=resultado_5_11.renovacao_criadas,
            renovacao_falhas=resultado_5_11.renovacao_falhas,
            bootstrap_criada=resultado_5_11.bootstrap_criada,
            onboarding_descobertos=resultado_5_11.onboarding_descobertos,
            onboarding_adicionados=resultado_5_11.onboarding_adicionados,
            onboarding_sem_custo=resultado_5_11.onboarding_sem_custo,
        )

    # ─── Loop existente: detector+executor + watchdog por campanha ─────
    # Lista campanhas a processar (uma única sessão, transação curta)
    async with session_scope() as session:
        campaign_repo = CampaignRepository(session)
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(profile_id)
        # Pega todas as campanhas origem='ml' do perfil que não estão arquivadas
        campanhas = [
            c for c in await campaign_repo.list_by_profile(profile_id)
            if c.origem == "ml" and c.archived_at is None
        ]

    if not campanhas:
        logger.info(
            "migration_scheduler_perfil_sem_campanhas",
            profile_id=str(profile_id),
        )
        return

    dry_run = profile.config.migracao_dry_run

    for c in campanhas:
        try:
            await _processar_campanha(profile_id, c.id, dry_run=dry_run)
        except Exception:
            logger.exception(
                "migration_scheduler_campanha_falhou",
                profile_id=str(profile_id),
                campaign_id=str(c.id),
            )

    logger.info(
        "migration_scheduler_perfil_concluido",
        profile_id=str(profile_id),
        campanhas_processadas=len(campanhas),
        dry_run=dry_run,
    )


async def _processar_campanha(
    profile_id: UUID, campaign_id: UUID, *, dry_run: bool,
) -> None:
    """Roda detector + executor + watchdog pra UMA campanha.

    Sessões/transações são curtas — cada use case com sua própria sessão.
    """
    logger.info(
        "migration_scheduler_campanha_iniciando",
        profile_id=str(profile_id),
        campaign_id=str(campaign_id),
        dry_run=dry_run,
    )

    # ─── Ciclo 1: detector + executor ──────────────────────────
    async with session_scope() as session:
        creds_repo = PerProfileCredentialsRepository()
        profile_repo = SQLAlchemyProfileRepository(session)
        campaign_repo = CampaignRepository(session)
        historico_repo = MigracaoExecutadaRepository(session)
        descarte_cache = DescarteMigracaoCacheRepository(session)

        detector = DetectarOportunidadesMigracaoUseCase(
            profile_repo, campaign_repo, creds_repo,
            descarte_cache_repo=descarte_cache,
            historico_migracoes_repo=historico_repo,
        )
        executor = ExecutarMigracoesUseCase(
            detector=detector,
            profile_repo=profile_repo,
            campaign_repo=campaign_repo,
            creds_repo=creds_repo,
            historico_repo=historico_repo,
        )
        try:
            resultado_exec = await executor.execute(
                profile_id, campaign_id, dry_run=dry_run,
            )
            logger.info(
                "migration_scheduler_executor_concluido",
                profile_id=str(profile_id),
                campaign_id=str(campaign_id),
                dry_run=dry_run,
                sucesso=resultado_exec.total_sucesso,
                erros=resultado_exec.total_erros,
                ja_estava=resultado_exec.total_ja_estava,
            )
        except Exception:
            logger.exception(
                "migration_scheduler_executor_falhou",
                profile_id=str(profile_id),
                campaign_id=str(campaign_id),
            )

    # ─── Ciclo 2: watchdog cobertura ──────────────────────────
    async with session_scope() as session:
        creds_repo = PerProfileCredentialsRepository()
        profile_repo = SQLAlchemyProfileRepository(session)
        campaign_repo = CampaignRepository(session)
        historico_repo = MigracaoExecutadaRepository(session)

        verificador = VerificarCoberturaUseCase(
            profile_repo, campaign_repo, creds_repo,
        )
        corretor = CorrigirCoberturaUseCase(
            verificador=verificador,
            profile_repo=profile_repo,
            campaign_repo=campaign_repo,
            creds_repo=creds_repo,
            historico_repo=historico_repo,
        )
        try:
            resultado_cob = await corretor.execute(
                profile_id, campaign_id, dry_run=dry_run,
            )
            logger.info(
                "migration_scheduler_cobertura_concluido",
                profile_id=str(profile_id),
                campaign_id=str(campaign_id),
                dry_run=dry_run,
                descobertos=resultado_cob.total_descobertos,
                corrigidos=resultado_cob.total_corrigidos,
                falhas=resultado_cob.total_falhas,
                skip_circuit_breaker=resultado_cob.total_skip_circuit_breaker,
            )
        except Exception:
            logger.exception(
                "migration_scheduler_cobertura_falhou",
                profile_id=str(profile_id),
                campaign_id=str(campaign_id),
            )


# Singleton
_singleton: MigrationScheduler | None = None


def get_migration_scheduler() -> MigrationScheduler:
    global _singleton
    if _singleton is None:
        _singleton = MigrationScheduler()
    return _singleton
