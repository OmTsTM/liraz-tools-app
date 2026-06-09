"""Cache em memória do merge `GET /campaigns` (local + ML).

Listar campanhas re-sincroniza TODAS as campanhas com o ML em tempo real
(ListCampaignsWithMLMergeUseCase) — uma chamada vira centenas de requests ao
ML. Sem cache, qualquer rajada de refetch (várias invalidações seguidas no
front, navegação lista↔detalhe) re-sincroniza tudo várias vezes → martela o ML.

Estratégia: cache curto (TTL 45s) por (profile_id, include_archived), com
INVALIDAÇÃO POR VERSÃO. O `CampaignRepository` chama `bump(profile_id)` em
toda escrita local (create/update/delete), então qualquer ação do usuário
sobre campanhas invalida o cache na hora. Mudanças que vêm de fora (ML direto,
scheduler) são cobertas pelo TTL.

Singleton — uma instância por processo. Cache em RAM, some no restart.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

CACHE_TTL_SECONDS = 45


class CampaignsListCache:
    """Cache {(profile_id, include_archived) -> (dados, ts, versão)}."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, bool], tuple[list[dict[str, Any]], float, int]] = {}
        self._versions: dict[str, int] = {}

    def _version(self, profile_id: UUID | str) -> int:
        return self._versions.get(str(profile_id), 0)

    def get(
        self, profile_id: UUID | str, include_archived: bool
    ) -> list[dict[str, Any]] | None:
        """Retorna o merge cacheado se ainda fresco E da versão atual, senão None."""
        key = (str(profile_id), include_archived)
        entry = self._cache.get(key)
        if entry is None:
            return None
        data, ts, ver = entry
        # Invalidado por uma escrita posterior, ou expirado pelo TTL.
        if ver != self._version(profile_id) or time.time() - ts > CACHE_TTL_SECONDS:
            del self._cache[key]
            return None
        return data

    def set(
        self,
        profile_id: UUID | str,
        include_archived: bool,
        data: list[dict[str, Any]],
    ) -> None:
        self._cache[(str(profile_id), include_archived)] = (
            data,
            time.time(),
            self._version(profile_id),
        )

    def bump(self, profile_id: UUID | str) -> None:
        """Invalida o cache do perfil — chamado em toda escrita de campanha."""
        self._versions[str(profile_id)] = self._version(profile_id) + 1


_singleton: CampaignsListCache | None = None


def get_campaigns_list_cache() -> CampaignsListCache:
    """Acesso ao singleton (FastAPI Depends e CampaignRepository)."""
    global _singleton
    if _singleton is None:
        _singleton = CampaignsListCache()
    return _singleton
