"""Scheduler do relatório diário automático (Fatia 2, ago/2026).

Loop async paralelo ao `MigrationScheduler`. Tick a cada 5 minutos: pra cada
perfil com `relatorio_diario_ativo=True`, verifica se já bateu o horário
configurado (`relatorio_diario_hora_brt`) E se o PDF do dia anterior ainda
não está salvo. Se sim, gera + salva em disco.

Liga via env var `ENABLE_RELATORIO_SCHEDULER=true`. Default desligado.

Por que dia ANTERIOR: o relatório de "hoje" não fecha antes da meia-noite.
Às 7h de um dia X, queremos o consolidado do dia X-1 (= ontem completo).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from datetime import time as time_obj
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.relatorio.use_cases import (
    GerarRelatorioDiarioUseCase,
    ProfileNaoConectadoError,
    SemCustosXlsxError,
)
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.relatorio.pdf_renderer import renderizar_pdf
from liraz_tools.infrastructure.relatorio.pdf_storage import (
    existe_pdf,
    salvar_pdf,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)
from liraz_tools.infrastructure.repositories.relatorio_diario_repository import (
    RelatorioDiarioRepository,
)

logger = get_logger(__name__)


SCHEDULER_CHECK_INTERVAL_S = 60 * 5  # 5 min — granularidade razoável pra hora cheia

# Lock global em memória pra evitar 2 gerações simultâneas pro mesmo perfil.
_locks_por_perfil: dict[UUID, asyncio.Lock] = {}


def scheduler_habilitado() -> bool:
    """True se a env var ENABLE_RELATORIO_SCHEDULER=true."""
    return os.getenv("ENABLE_RELATORIO_SCHEDULER", "").lower() in (
        "true", "1", "yes",
    )


def _lock_para(profile_id: UUID) -> asyncio.Lock:
    if profile_id not in _locks_por_perfil:
        _locks_por_perfil[profile_id] = asyncio.Lock()
    return _locks_por_perfil[profile_id]


def _agora_brt() -> datetime:
    """Hora corrente em BRT (UTC-3). O config é em BRT, comparamos em BRT."""
    brt = datetime.now(UTC).astimezone()  # local; assumido BRT
    return brt.replace(tzinfo=None)


def _parse_hora_brt(hora_str: str) -> time_obj | None:
    """Parse 'HH:MM' tolerante. None em formato inválido."""
    try:
        hh, mm = hora_str.split(":", 1)
        return time_obj(hour=int(hh), minute=int(mm))
    except Exception:
        return None


class RelatorioDiarioScheduler:
    """Loop async que dispara geração do relatório diário no horário configurado."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        """Inicia loop se a env var permitir. Idempotente."""
        if not scheduler_habilitado():
            logger.info(
                "relatorio_scheduler_disabled",
                env_var="ENABLE_RELATORIO_SCHEDULER",
                hint="set ENABLE_RELATORIO_SCHEDULER=true to enable",
            )
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="relatorio-diario-scheduler",
        )
        logger.info(
            "relatorio_scheduler_started",
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
            logger.warning("relatorio_scheduler_force_cancelled")
        finally:
            self._task = None
        logger.info("relatorio_scheduler_stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("relatorio_scheduler_tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=SCHEDULER_CHECK_INTERVAL_S,
                )

    async def _tick(self) -> None:
        """Verifica cada perfil; gera o que estiver dentro da janela."""
        async with session_scope() as session:
            profile_repo = SQLAlchemyProfileRepository(session)
            todos = await profile_repo.list_all()

        ativos = [
            p for p in todos
            if p.status.value == "connected"
            and p.config.relatorio_diario_ativo
        ]
        if not ativos:
            logger.debug("relatorio_scheduler_tick_no_active_profiles")
            return

        agora_brt = _agora_brt()
        dia_anterior_brt = (agora_brt - timedelta(days=1)).date()
        for profile in ativos:
            hora_alvo = _parse_hora_brt(profile.config.relatorio_diario_hora_brt)
            if hora_alvo is None:
                logger.warning(
                    "relatorio_scheduler_hora_invalida",
                    profile_id=str(profile.id),
                    hora_str=profile.config.relatorio_diario_hora_brt,
                )
                continue
            # Janela: agora >= horário alvo do dia atual E PDF do dia anterior
            # ainda não foi gerado. Combinação garante: dispara depois da hora,
            # uma vez por dia. Se backend ficou off durante a hora, dispara na
            # primeira passada que pegar o `agora >= hora_alvo` com PDF ausente.
            if agora_brt.time() < hora_alvo:
                continue
            if existe_pdf(profile.slug, dia_anterior_brt):
                continue

            lock = _lock_para(profile.id)
            if lock.locked():
                logger.info(
                    "relatorio_scheduler_skip_locked",
                    profile_id=str(profile.id),
                )
                continue
            async with lock:
                try:
                    await _gerar_e_salvar(profile.id, dia_anterior_brt)
                except Exception:
                    logger.exception(
                        "relatorio_scheduler_perfil_falhou",
                        profile_id=str(profile.id),
                        dia=dia_anterior_brt.isoformat(),
                    )


async def _gerar_e_salvar(profile_id: UUID, dia: date_type) -> None:
    """Gera o relatório do `dia` pra `profile_id` e salva em disco."""
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        creds_repo = PerProfileCredentialsRepository()
        relatorio_repo = RelatorioDiarioRepository(session)

        profile = await profile_repo.get_by_id(profile_id)

        use_case = GerarRelatorioDiarioUseCase(
            profile_repo=profile_repo,
            creds_repo=creds_repo,
            relatorio_repo=relatorio_repo,
        )
        try:
            rel = await use_case.execute(profile_id, dia=dia)
        except (ProfileNaoConectadoError, SemCustosXlsxError) as e:
            logger.warning(
                "relatorio_scheduler_skip_perfil",
                profile_id=str(profile_id),
                motivo=str(e),
            )
            return

    pdf_bytes = renderizar_pdf(rel)
    path = salvar_pdf(profile.slug, dia, pdf_bytes)
    logger.info(
        "relatorio_scheduler_pdf_salvo",
        profile_id=str(profile_id),
        dia=dia.isoformat(),
        path=str(path),
        tamanho_bytes=len(pdf_bytes),
    )


# Singleton
_singleton: RelatorioDiarioScheduler | None = None


def get_relatorio_scheduler() -> RelatorioDiarioScheduler:
    global _singleton
    if _singleton is None:
        _singleton = RelatorioDiarioScheduler()
    return _singleton
