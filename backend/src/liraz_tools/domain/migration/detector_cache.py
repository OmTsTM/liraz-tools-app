"""Cache em memória de oportunidades detectadas (Leva 5.9.4 - opt 2).

Detecção é cara (várias chamadas ML), então cacheamos por (profile, campanha,
faixa-skus) por TTL curto. Útil quando UI chama detector múltiplas vezes
em sequência ou scheduler roda em intervalos pequenos.

Cache não é persistido — vive só na vida do processo uvicorn. Cada restart
limpa. Isso é OK porque os dados do ML mudam rápido e queremos info fresca
nesse caso.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import TYPE_CHECKING
from uuid import UUID

from liraz_tools.core.logging import get_logger

if TYPE_CHECKING:
    from liraz_tools.domain.migration.use_cases import OportunidadeMigracao

logger = get_logger(__name__)


@dataclass
class _CacheEntry:
    oportunidades: list[OportunidadeMigracao]
    salvo_em: datetime
    skus_avaliados: frozenset[str]


class DetectorCache:
    """Singleton em memória do cache de detecções.

    Key = (profile_id, campaign_id, frozenset(skus_avaliados)).
    Value = lista de oportunidades.

    Quando recebemos um pedido com `apenas_skus`, se o cache tem uma
    detecção FEITA com SUPERSET dos skus pedidos, podemos filtrar dele
    em vez de re-detectar.
    """

    def __init__(self, ttl_minutos: int = 10) -> None:
        self._ttl = timedelta(minutes=ttl_minutos)
        self._store: dict[tuple[UUID, UUID, frozenset[str]], _CacheEntry] = {}
        self._lock = Lock()

    def get(
        self,
        profile_id: UUID,
        campaign_id: UUID,
        skus_solicitados: frozenset[str],
    ) -> list[OportunidadeMigracao] | None:
        """Retorna oportunidades cacheadas se válidas, senão None.

        Considera HIT também se existe cache com SUPERSET dos SKUs pedidos:
        - skus_solicitados ⊆ skus_avaliados anteriores → filtra do cache
        """
        agora = datetime.now(UTC)
        with self._lock:
            # Procura match exato primeiro (mais rápido)
            key_exato = (profile_id, campaign_id, skus_solicitados)
            entry = self._store.get(key_exato)
            if entry and agora - entry.salvo_em < self._ttl:
                logger.info(
                    "detector_cache_hit_exato",
                    profile_id=str(profile_id),
                    campaign_id=str(campaign_id),
                    qtd_oportunidades=len(entry.oportunidades),
                )
                return list(entry.oportunidades)

            # Procura superset (cache mais amplo que cobre o pedido)
            for (pid, cid, skus_cached), e in self._store.items():
                if pid != profile_id or cid != campaign_id:
                    continue
                if agora - e.salvo_em >= self._ttl:
                    continue
                if skus_solicitados.issubset(skus_cached):
                    # Filtra cache pelos skus solicitados
                    filtradas = [
                        o for o in e.oportunidades
                        if o.item_id in skus_solicitados
                    ]
                    logger.info(
                        "detector_cache_hit_superset",
                        profile_id=str(profile_id),
                        campaign_id=str(campaign_id),
                        skus_pedidos=len(skus_solicitados),
                        skus_cache=len(skus_cached),
                        oportunidades_filtradas=len(filtradas),
                    )
                    return filtradas

        return None

    def set(
        self,
        profile_id: UUID,
        campaign_id: UUID,
        skus_avaliados: frozenset[str],
        oportunidades: list[OportunidadeMigracao],
    ) -> None:
        """Salva resultado da detecção. Trim entradas velhas se cache grande."""
        agora = datetime.now(UTC)
        with self._lock:
            # Limpa expiradas se cache cresceu muito (>50 perfis e campanhas)
            if len(self._store) > 50:
                self._store = {
                    k: v for k, v in self._store.items()
                    if agora - v.salvo_em < self._ttl
                }
            self._store[(profile_id, campaign_id, skus_avaliados)] = _CacheEntry(
                oportunidades=list(oportunidades),
                salvo_em=agora,
                skus_avaliados=skus_avaliados,
            )
            logger.info(
                "detector_cache_set",
                profile_id=str(profile_id),
                campaign_id=str(campaign_id),
                qtd_skus_avaliados=len(skus_avaliados),
                qtd_oportunidades=len(oportunidades),
            )

    def invalidar(self, profile_id: UUID, campaign_id: UUID) -> int:
        """Remove todas as entradas de uma campanha. Retorna quantas."""
        with self._lock:
            antes = len(self._store)
            self._store = {
                k: v for k, v in self._store.items()
                if k[0] != profile_id or k[1] != campaign_id
            }
            removidas = antes - len(self._store)
        if removidas > 0:
            logger.info(
                "detector_cache_invalidado",
                profile_id=str(profile_id),
                campaign_id=str(campaign_id),
                removidas=removidas,
            )
        return removidas


# Singleton de processo
_cache_singleton: DetectorCache | None = None


def get_detector_cache() -> DetectorCache:
    """Retorna o singleton de cache. Lazy init."""
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = DetectorCache()
    return _cache_singleton
