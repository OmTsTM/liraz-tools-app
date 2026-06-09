"""Entry point do servidor FastAPI."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from liraz_tools import __version__
from liraz_tools.api.routes import (
    applications,
    campaigns,
    config_files,
    cost_overrides,
    health,
    listings,
    oauth,
    oauth_callback,
    profiles,
    relatorios,
    repricing_massa,
    simulations,
)
from liraz_tools.core.config import get_settings
from liraz_tools.core.logging import configure_logging, get_logger
from liraz_tools.domain.pricing.repricing_use_cases import (
    ResumeInterruptedSimulationsUseCase,
)
from liraz_tools.infrastructure.background.job_runner import get_job_runner
from liraz_tools.infrastructure.background.migration_scheduler import (
    get_migration_scheduler,
)
from liraz_tools.infrastructure.background.relatorio_scheduler import (
    get_relatorio_scheduler,
)
from liraz_tools.infrastructure.background.scheduler import get_scheduler
from liraz_tools.infrastructure.db.database import (
    create_all_tables,
    dispose_engine,
    session_scope,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Setup/teardown da aplicação."""
    configure_logging()
    logger = get_logger("startup")
    settings = get_settings()
    logger.info(
        "liraz_tools_starting",
        version=__version__,
        environment=settings.environment,
        host=settings.host,
        port=settings.port,
    )

    await create_all_tables()
    logger.info("database_initialized")

    # Detecta simulações órfãs (estado=running mas sem task in-memory após restart)
    # e marca como `interrupted` pra usuário decidir se retoma.
    try:
        async with session_scope() as session:
            profile_repo = SQLAlchemyProfileRepository(session)
            profiles_list = await profile_repo.list_all(include_archived=True)
            slugs = [p.slug for p in profiles_list]

        snapshots_repo = SnapshotsRepository()
        use_case = ResumeInterruptedSimulationsUseCase(snapshots_repo)
        marked = await use_case.execute(slugs)
        if marked > 0:
            logger.info("orphan_simulations_marked", count=marked)
    except Exception as e:
        logger.warning("orphan_simulation_check_failed", error=str(e))

    # Scheduler de campanhas (Leva 5.5) — dispara campanhas agendadas
    # quando chega o horário, finaliza campanhas que passaram do data_fim.
    scheduler = get_scheduler(get_job_runner())
    scheduler.start()

    # Scheduler de migrações automáticas (Leva 5.9.4.C.3) — opcional, liga
    # via env var ENABLE_MIGRATION_SCHEDULER=true. Em produção, fica off por
    # padrão. Quando ligado, roda detector/executor + watchdog cobertura
    # pra cada perfil com migracao_automatica_ativa=True, no intervalo
    # configurado em config.migracao_intervalo_horas (default 6h).
    migration_scheduler = get_migration_scheduler()
    migration_scheduler.start()

    # Scheduler do relatório diário (Fatia 2) — opcional, liga via env
    # ENABLE_RELATORIO_SCHEDULER=true. Tick a cada 5min; gera PDF do dia
    # anterior pra cada perfil com `relatorio_diario_ativo=True` e
    # horário (`relatorio_diario_hora_brt`) já atingido.
    relatorio_scheduler = get_relatorio_scheduler()
    relatorio_scheduler.start()

    yield

    logger.info("liraz_tools_shutdown")
    await scheduler.stop()
    await migration_scheduler.stop()
    await relatorio_scheduler.stop()
    await dispose_engine()


app = FastAPI(
    title="LiraZ Tools",
    description="API REST do app desktop LiraZ Tools.",
    version=__version__,
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas
app.include_router(health.router)
app.include_router(profiles.router)
app.include_router(oauth.router)
app.include_router(oauth_callback.router)
app.include_router(listings.router)
app.include_router(config_files.router)
app.include_router(cost_overrides.router)
app.include_router(simulations.router)
app.include_router(campaigns.router)
app.include_router(applications.router)
app.include_router(repricing_massa.router)
app.include_router(relatorios.router)
