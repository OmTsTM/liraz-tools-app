"""Store de autorizações OAuth em andamento.

Vive em memória do processo. Cada PendingAuthorization expira em 15 minutos.
Se o usuário não completar o fluxo nesse tempo, a entrada vira lixo e é
removida no próximo cleanup.

Não persiste em DB porque:
- A janela de validade é muito curta (15min)
- Reiniciar o servidor invalida sessões pending de qualquer jeito
- Reduz superfície de ataque (state CSRF não fica no disco)
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from liraz_tools.domain.oauth.entity import PendingAuthorization


class PendingAuthorizationStore:
    """Store concorrente-seguro de autorizações pendentes."""

    def __init__(self) -> None:
        self._items: dict[str, PendingAuthorization] = {}
        self._lock = asyncio.Lock()

    async def add(self, pending: PendingAuthorization) -> None:
        async with self._lock:
            self._items[pending.state] = pending

    async def pop_by_state(self, state: str) -> PendingAuthorization | None:
        """Remove e retorna a autorização. Idempotente — só usa uma vez."""
        async with self._lock:
            pending = self._items.pop(state, None)
            if pending is None:
                return None
            if pending.is_expired():
                return None
            return pending

    async def cleanup_expired(self) -> int:
        """Remove entradas expiradas. Pode ser chamado periodicamente."""
        async with self._lock:
            now = datetime.now(UTC)
            expired_keys = [k for k, v in self._items.items() if v.expires_at <= now]
            for k in expired_keys:
                del self._items[k]
            return len(expired_keys)


_store_instance: PendingAuthorizationStore | None = None


def get_pending_authorization_store() -> PendingAuthorizationStore:
    """Singleton do store."""
    global _store_instance
    if _store_instance is None:
        _store_instance = PendingAuthorizationStore()
    return _store_instance
