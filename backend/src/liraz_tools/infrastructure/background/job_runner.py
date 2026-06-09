"""Job runner pra simulações em background.

Quando uma simulação é disparada, abrimos uma `asyncio.Task` que roda em
paralelo ao FastAPI. O frontend faz polling no GET /simulations/{id} pra
ver o progresso atualizado.

Registry in-memory:
    {simulation_id: asyncio.Task}

Não é persistente — quando o backend reinicia, todas as tasks somem. Mas o
snapshot JSON no disco continua refletindo o estado. Tasks em estado
`running` no snapshot que NÃO estão no registry são "órfãs" — o startup
marca como `interrupted` ou retoma automaticamente (decisão B).

Por que in-memory e não Redis/RQ/Celery? Porque o app é local. Cada usuário
tem seu próprio backend rodando. Tools pesadas seriam overkill.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from liraz_tools.core.logging import get_logger

logger = get_logger(__name__)


class JobRunner:
    """Registry de tasks asyncio em execução.

    Singleton — uma instância pra todo o app.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(
        self,
        job_id: str,
        coro_factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Dispara uma corrotina em background.

        Se já existe task com esse ID rodando, ignora a chamada (idempotente).
        Se a task antiga já terminou, remove do registry antes de iniciar a nova.
        """
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            logger.warning("job_already_running", job_id=job_id)
            return

        # Cleanup de tasks terminadas — não precisa ficar no registry
        if existing is not None:
            del self._tasks[job_id]

        task = asyncio.create_task(coro_factory(), name=f"job:{job_id}")
        self._tasks[job_id] = task

        # Auto-cleanup quando termina (não fica pendurando no dict pra sempre)
        def _on_done(t: asyncio.Task[None]) -> None:
            self._tasks.pop(job_id, None)
            if t.cancelled():
                logger.info("job_cancelled", job_id=job_id)
            elif t.exception() is not None:
                logger.exception(
                    "job_failed",
                    job_id=job_id,
                    error=str(t.exception()),
                )
            else:
                logger.info("job_completed", job_id=job_id)

        task.add_done_callback(_on_done)
        logger.info("job_started", job_id=job_id)

    def is_running(self, job_id: str) -> bool:
        """Verifica se uma simulação está rodando in-memory.

        Nota: pode retornar False mesmo pra simulações com estado=running
        no snapshot (caso a task tenha morrido ou o backend reiniciado).
        Pra estado autoritativo, sempre consulte o snapshot no disco.
        """
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    def cancel(self, job_id: str) -> bool:
        """Cancela uma task. Retorna True se estava rodando."""
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def list_running(self) -> list[str]:
        """IDs de tasks atualmente em execução."""
        return [
            job_id for job_id, task in self._tasks.items()
            if not task.done()
        ]


# Singleton — uma instância por processo backend
_runner_instance: JobRunner | None = None


def get_job_runner() -> JobRunner:
    """Acesso ao singleton."""
    global _runner_instance
    if _runner_instance is None:
        _runner_instance = JobRunner()
    return _runner_instance
