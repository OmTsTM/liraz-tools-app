"""Scheduler automático de campanhas (Leva 5.5).

Roda como task asyncio no startup do FastAPI. A cada SCHEDULER_INTERVAL_S
segundos, faz duas varreduras:

1. **Disparo**: pega campanhas `agendada` com `agendado_para <= now()` e
   chama `_aplicar_e_criar_campanha` em background (mesma função usada
   pelo botão "Iniciar agora"). Imediato=False — não reescreve as datas.

2. **Finalização**: pega campanhas `ativa` cujo `data_fim + hora_fim` já
   passou e marca como `finalizada`. O ML termina as campanhas
   automaticamente na hora marcada, então isso é só housekeeping local.

Garantia de não disparar duplicado:
- O scheduler chama o mesmo `JobRunner.start(job_id=...)` que tem dedup
  por job_id. Mas o cenário "campanha disparada uma vez, depois ficou
  em 'executando', scheduler vê e tenta de novo" é evitado pelo filtro
  de status (só pega `agendada`).
- Race condition realista: scheduler pega lista de agendadas, e antes
  do dispatch o user clica "Iniciar agora" pela UI. Solução: o use case
  StartCampaign valida o status novamente (já valida — recusa se !=
  rascunho/agendada). O segundo dispatch falha graciosamente.

Recuperação após crash:
- Na inicialização, marca como `falha` todas as campanhas em status
  `executando` (server caiu no meio de uma aplicação). O rollback_id
  permite reverter o que foi parcialmente aplicado.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.applications.start_passo3_use_case import (
    StartCampaignPasso3UseCase,
)
from liraz_tools.domain.applications.use_cases import (
    StartCampaignUseCase,
)
from liraz_tools.infrastructure.background.job_runner import JobRunner
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.repositories.applications_repository import (
    ApplicationsRepository,
    RollbacksRepository,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)

if TYPE_CHECKING:
    from liraz_tools.domain.campaigns.entity import Campaign


logger = get_logger(__name__)


# Intervalo entre verificações. 60s é equilíbrio entre latência (campanha
# agendada pra 14:30:00 vai disparar entre 14:30:00 e 14:31:00) e custo
# (1 query SQL por minuto, irrelevante).
SCHEDULER_INTERVAL_S = 60

# Leva 5.7.1 — quantos dias após data_fim antes de soft-arquivar
DIAS_PARA_ARQUIVAR = 15


def _datetime_fim(c: Campaign) -> datetime:
    """Combina data_fim + hora_fim (ou 23:59:59 se hora_fim=None)."""
    fim_time = c.hora_fim or time(23, 59, 59)
    return datetime.combine(c.data_fim, fim_time)


class CampaignScheduler:
    """Singleton — uma instância por processo."""

    def __init__(self, job_runner: JobRunner) -> None:
        self._job_runner = job_runner
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # Cache em memória de campaign_ids já disparados nesta run-time, pra
        # evitar tentar dispatch antes da update do DB persistir (raro mas
        # possível em sistemas com replica lag — defensivo).
        self._disparadas_nesta_run: set[str] = set()

    def start(self) -> None:
        """Inicia o loop. Idempotente — chamadas extras são no-op."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="campaign-scheduler")
        logger.info("scheduler_started", interval_s=SCHEDULER_INTERVAL_S)

    async def stop(self) -> None:
        """Sinaliza pra parar e aguarda. Idempotente."""
        if self._task is None or self._task.done():
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
            logger.warning("scheduler_force_cancelled")
        finally:
            self._task = None
        logger.info("scheduler_stopped")

    async def _run(self) -> None:
        """Loop principal — roda enquanto _stop não foi sinalizado."""
        # Primeira recovery na inicialização (não periódica).
        await self._recover_interrupted()

        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("scheduler_tick_failed")

            # Sleep com cancelamento responsivo. Usa wait_for em vez de sleep
            # pra acordar imediato se stop() for chamado. TimeoutError é
            # esperado — significa "passou o intervalo".
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=SCHEDULER_INTERVAL_S
                )

    async def _recover_interrupted(self) -> None:
        """Marca como `falha` campanhas que estavam `executando` quando o
        servidor caiu (não tem como saber em qual fase travou).

        O `rollback_id` da campanha permite reverter manualmente — esse
        método não tenta retomar automaticamente porque o estado intermediário
        é ambíguo (pode ter PUT items mas não criado campanha, ou vice-versa).
        """
        async with session_scope() as session:
            repo = CampaignRepository(session)
            interrompidas = await repo.list_all_by_status("executando")
            for c in interrompidas:
                logger.warning(
                    "campaign_interrupted_marked_failed",
                    campaign_id=str(c.id),
                    rollback_id=c.rollback_id,
                )
                await repo.update(c.model_copy(update={
                    "status": "falha",
                    "erro": (
                        "Aplicação interrompida (server reiniciado durante "
                        "execução). Use a função 'Reverter' pra desfazer "
                        "PUTs parciais aplicados."
                    ),
                }))

    async def _tick(self) -> None:
        """Uma passada do scheduler.

        Filtros de segurança aplicados em todas as varreduras:
        - Só processa campanhas de perfis em status `connected`. Perfis
          arquivados, desconectados ou em draft NÃO disparam nem finalizam
          campanhas automaticamente. Isso protege o user de "esquecer" uma
          campanha agendada ao arquivar a loja.
        - Pra desarquivar e retomar: basta unarchive (vira `connected` de
          novo) e o scheduler passa a respeitar as campanhas como sempre.
        """
        now = datetime.now()

        # Carrega slugs dos perfis CONECTADOS uma vez por tick. Cache leve
        # — perfis raramente mudam de status durante um tick.
        async with session_scope() as session:
            profile_repo = SQLAlchemyProfileRepository(session)
            todos_perfis = await profile_repo.list_all(include_archived=True)
            perfis_conectados = {
                p.id for p in todos_perfis
                if p.status.value == "connected"
            }

        # ─── 1) Finaliza campanhas ativas que passaram do data_fim ──────
        # Mesmo de perfil arquivado, finaliza por tempo (housekeeping local,
        # não toca no ML). Decisão: garantir que o status local fica certo.
        async with session_scope() as session:
            repo = CampaignRepository(session)
            ativas = await repo.list_all_by_status("ativa")
            for c in ativas:
                fim = _datetime_fim(c)
                if now >= fim:
                    logger.info(
                        "campaign_finalized_by_time",
                        campaign_id=str(c.id),
                        fim=fim.isoformat(),
                        profile_connected=c.profile_id in perfis_conectados,
                    )
                    await repo.update(c.model_copy(update={"status": "finalizada"}))

        # ─── 2) Dispara campanhas agendadas — APENAS de perfis conectados ─
        async with session_scope() as session:
            repo = CampaignRepository(session)
            agendadas = await repo.list_all_by_status("agendada")

        # Filtra fora da session — chamar use case com nova session
        a_disparar = []
        ignoradas_por_perfil = 0
        for c in agendadas:
            if c.agendado_para > now:
                continue
            if str(c.id) in self._disparadas_nesta_run:
                continue
            if c.profile_id not in perfis_conectados:
                # Perfil arquivado/desconectado/draft — IGNORA. Não dispara
                # automaticamente. User precisa desarquivar primeiro.
                ignoradas_por_perfil += 1
                continue
            a_disparar.append(c)

        if ignoradas_por_perfil > 0:
            logger.warning(
                "scheduler_ignored_campaigns_unavailable_profile",
                count=ignoradas_por_perfil,
                motivo=(
                    "perfil não está em status 'connected' — campanha agendada "
                    "fica em standby até a loja voltar a estar conectada"
                ),
            )

        for c in a_disparar:
            self._disparadas_nesta_run.add(str(c.id))
            try:
                await self._disparar(c)
            except Exception:
                logger.exception(
                    "scheduler_dispatch_failed",
                    campaign_id=str(c.id),
                )

        # ─── 3) Arquivamento automático após 15 dias (Leva 5.7.1) ────────
        # Soft delete de campanhas `finalizada` cujo data_fim já passou
        # mais de 15 dias. Dados em disco ficam (snapshot, rollback, history),
        # só somem da lista por default na UI. User pode re-incluir via toggle.
        cutoff = (now - timedelta(days=DIAS_PARA_ARQUIVAR)).date()
        async with session_scope() as session:
            repo = CampaignRepository(session)
            pra_arquivar = await repo.list_finalizadas_para_arquivar(cutoff)
            for c in pra_arquivar:
                logger.info(
                    "campaign_auto_archived",
                    campaign_id=str(c.id),
                    nome=c.nome,
                    data_fim=c.data_fim.isoformat(),
                    dias_apos_fim=(now.date() - c.data_fim).days,
                )
                await repo.update(c.model_copy(update={"archived_at": now}))

    async def _disparar(self, campaign: Campaign) -> None:
        """Dispara uma campanha que chegou no horário agendado.

        Usa imediato=False — não muda as datas, respeita o agendado_para.

        Ramifica entre dois fluxos (mesma lógica do endpoint manual em
        `applications.py`):
        - Tem `simulacao_id` → `StartCampaignUseCase` (clássico, com snapshot).
        - Sem `simulacao_id` → `StartCampaignPasso3UseCase` (passo3,
          calcula deal_price no momento do disparo).
        """
        # Mesma cadeia de DI usada pelos endpoints
        async with session_scope() as session:
            profile_repo = SQLAlchemyProfileRepository(session)
            campaign_repo = CampaignRepository(session)
            snapshots_repo = SnapshotsRepository()
            applications_repo = ApplicationsRepository()
            rollbacks_repo = RollbacksRepository()
            creds_repo = PerProfileCredentialsRepository()

            try:
                if campaign.simulacao_id is not None:
                    classic_uc = StartCampaignUseCase(
                        profile_repo=profile_repo,
                        campaign_repo=campaign_repo,
                        snapshots_repo=snapshots_repo,
                        applications_repo=applications_repo,
                        rollbacks_repo=rollbacks_repo,
                        creds_repo=creds_repo,
                        job_runner=self._job_runner,
                    )
                    application = await classic_uc.execute(
                        profile_id=campaign.profile_id,
                        campaign_id=campaign.id,
                        imediato=False,
                    )
                else:
                    passo3_uc = StartCampaignPasso3UseCase(
                        profile_repo=profile_repo,
                        campaign_repo=campaign_repo,
                        applications_repo=applications_repo,
                        creds_repo=creds_repo,
                        job_runner=self._job_runner,
                    )
                    application = await passo3_uc.execute(
                        profile_id=campaign.profile_id,
                        campaign_id=campaign.id,
                        imediato=False,
                    )
                logger.info(
                    "scheduler_dispatched",
                    campaign_id=str(campaign.id),
                    application_id=application.application_id,
                    fluxo=(
                        "classico" if campaign.simulacao_id else "passo3"
                    ),
                )
            except Exception:
                logger.exception(
                    "scheduler_dispatch_use_case_failed",
                    campaign_id=str(campaign.id),
                )
                # Não muda status — fica em `agendada` pra próxima passada
                # tentar de novo (vai falhar de novo, mas pelo menos não
                # silencia o erro). Decisão: pior do que retry-loop é
                # esconder problema do user.


# ─── Singleton ──────────────────────────────────────────────────────────


_scheduler: CampaignScheduler | None = None


def get_scheduler(job_runner: JobRunner) -> CampaignScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CampaignScheduler(job_runner)
    return _scheduler
