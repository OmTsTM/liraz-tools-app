"""Cache em memória de FeeReport por perfil.

Gerar um relatório completo leva ~2min pra ~100 anúncios (paralelizado em 8).
Pra evitar refazer toda hora que a UI re-mounta, cacheamos o resultado por
15 minutos em RAM. Cache é invalidado:
- Explicitamente via método `invalidate(profile_id)`
- Automaticamente após o TTL
- Quando o backend reinicia (cache em memória = não persiste)

Singleton — uma instância por processo backend. Thread-safe pra asyncio
(operações em dict são atômicas, mas se isso virar problema basta adicionar
asyncio.Lock).
"""
from __future__ import annotations

import time
from uuid import UUID

from liraz_tools.domain.pricing.entity import FeeReport

CACHE_TTL_SECONDS = 15 * 60  # 15 minutos


class FeeReportCache:
    """Cache em memória {profile_id → (report, timestamp)}."""

    def __init__(self) -> None:
        self._cache: dict[UUID, tuple[FeeReport, float]] = {}

    def get(self, profile_id: UUID) -> FeeReport | None:
        """Retorna o relatório se ainda fresco, senão None."""
        entry = self._cache.get(profile_id)
        if entry is None:
            return None
        report, ts = entry
        if time.time() - ts > CACHE_TTL_SECONDS:
            # Expirado — remove pra evitar acumulo
            del self._cache[profile_id]
            return None
        return report

    def set(self, profile_id: UUID, report: FeeReport) -> None:
        """Armazena relatório com timestamp atual."""
        self._cache[profile_id] = (report, time.time())

    def invalidate(self, profile_id: UUID) -> None:
        """Força refresh na próxima chamada."""
        self._cache.pop(profile_id, None)


_singleton: FeeReportCache | None = None


def get_fee_report_cache() -> FeeReportCache:
    """Acesso ao singleton — chamado via FastAPI Depends."""
    global _singleton
    if _singleton is None:
        _singleton = FeeReportCache()
    return _singleton
