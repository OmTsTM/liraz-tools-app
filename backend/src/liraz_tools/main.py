"""Entry point do servidor FastAPI."""
from __future__ import annotations

import base64
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from liraz_tools import __version__
from liraz_tools.api.routes import (
    applications,
    auth,
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


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Auth global na borda — antes de qualquer handler.

    Quando `LIRAZ_TOOLS_BASIC_AUTH_USER` e `LIRAZ_TOOLS_BASIC_AUTH_PASSWORD`
    estão setados, todo request HTTP precisa do header `Authorization: Basic`
    válido. Caso contrário 401 com `WWW-Authenticate: Basic` (modal nativo).

    Funciona como uma "porta" extra antes do login do app — útil pra esconder
    o app inteiro de bots e scanner durante o piloto. Health check fica
    aberto pra Render conseguir monitorar.
    """

    def __init__(self, app, user: str, password: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._user = user
        self._password = password
        self._enabled = bool(user and password)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if not self._enabled or request.url.path == "/health":
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("basic "):
            return self._challenge()
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
            sent_user, _, sent_pwd = raw.partition(":")
        except (ValueError, UnicodeDecodeError):
            return self._challenge()

        # secrets.compare_digest impede timing attack na comparação
        if not (
            secrets.compare_digest(sent_user, self._user)
            and secrets.compare_digest(sent_pwd, self._password)
        ):
            return self._challenge()

        return await call_next(request)

    @staticmethod
    def _challenge() -> Response:
        return PlainTextResponse(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="LiraZ Tools"'},
        )


app = FastAPI(
    title="LiraZ Tools",
    description="API REST do app desktop LiraZ Tools.",
    version=__version__,
    lifespan=lifespan,
)

settings = get_settings()

# Ordem dos middlewares importa — o ÚLTIMO add_middleware é o PRIMEIRO a rodar.
# Queremos: Basic Auth na borda → CORS → handlers. Por isso CORS primeiro
# e Basic Auth depois.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    BasicAuthMiddleware,
    user=settings.basic_auth_user,
    password=settings.basic_auth_password,
)

# Rotas
app.include_router(health.router)
app.include_router(auth.router)
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


# ── SPA estática (produção) ────────────────────────────────────────────────
# Em produção, `LIRAZ_TOOLS_SPA_DIST_DIR` aponta pra `frontend/dist` (build
# do Vite). O backend serve `/assets/*` direto e qualquer outro path GET que
# não bata em router cai no `index.html` (HTML5 history routing do SPA).
# Em dev, fica desligado — o Vite continua servindo na :5173 (proxy /api).
if settings.spa_dist_dir:
    _dist = Path(settings.spa_dist_dir).resolve()
    if not _dist.is_dir():
        raise RuntimeError(
            f"LIRAZ_TOOLS_SPA_DIST_DIR aponta pra '{settings.spa_dist_dir}' "
            f"que não é diretório — confira o build do frontend",
        )
    _index = _dist / "index.html"
    if not _index.is_file():
        raise RuntimeError(
            f"SPA dist '{_dist}' não tem index.html — buildou o frontend?",
        )

    # Vite empacota tudo em `assets/`. Mount direto pra cache eficiente
    # (StaticFiles seta `Cache-Control` razoável).
    app.mount(
        "/assets",
        StaticFiles(directory=str(_dist / "assets")),
        name="spa-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        """Qualquer GET fora de /api, /health, /assets vira index.html.

        Permite que rotas SPA (`/login`, `/profiles/...`) funcionem direto
        com refresh / link compartilhado.
        """
        # Reservar prefixos da API pra 404 explícito (já capturados pelos
        # routers acima — esse handler só pega o que sobrou).
        if full_path.startswith(("api/", "health")):
            return PlainTextResponse("Not Found", status_code=404)
        # Arquivos estáticos avulsos no dist (favicon, robots etc.)
        candidato = _dist / full_path
        if candidato.is_file() and _dist in candidato.resolve().parents:
            return FileResponse(candidato)
        return FileResponse(_index)
