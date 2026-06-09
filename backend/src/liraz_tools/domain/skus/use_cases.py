"""Use case: listar anúncios ativos do vendedor + promoções de cada (Leva 5.12).

Substitui a dependência de "simulação obrigatória" na tela de criação de
campanha. Antes da 5.12, pra montar a lista de SKUs disponíveis pra entrar
numa campanha nova, o app dependia da simulação. Agora a lista vem direto
do ML: todos os anúncios ativos + flag "já está em alguma promoção?".

**Estratégia (correção do bug "MLB4xxx legado some")**: pra cada item ativo,
consulta `/seller-promotions/items/{item_id}` que retorna TODAS as promoções
em que ele participa. Esse endpoint funciona pra MLB4 e MLB6, diferente do
`/seller-promotions/promotions/{id}/items` (caminho direto) que perde os
legados — bug que afetava a versão anterior deste use case.

Custo (loja com ~200 anúncios):
- 1 request paginado pra listar items ativos
- ~10 requests paralelos pra batch fetch de detalhes (20 ids/chamada)
- ~200 requests pra promoções por item, paralelizados pelo semáforo
  de 8 do MLClient → ~25 lotes, ~500ms cada = ~12s
Total ~15-20s. React Query cacheia 5min.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.items_fetcher import fetch_items_basic_info
from liraz_tools.infrastructure.ml.promotions_lookup import (
    _listar_todos_items_ativos_do_vendedor,
    _normalizar_status,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )
    from liraz_tools.infrastructure.repositories.profile_repository import (
        SQLAlchemyProfileRepository,
    )


logger = get_logger(__name__)


@dataclass
class PromoNoItem:
    """Uma promoção em que um item está participando."""

    promotion_id: str
    nome: str | None
    tipo: str
    status: str
    start_date: str | None
    finish_date: str | None


@dataclass
class SkuComPromocoes:
    """Um anúncio ativo + lista de promoções em que participa."""

    item_id: str
    sku: str | None
    titulo: str | None
    preco: float | None
    promocoes: list[PromoNoItem] = field(default_factory=list)


class ListarSkusComPromocoesUseCase:
    """Lista anúncios ativos do vendedor cruzados com promoções (started+pending)."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo

    async def execute(
        self,
        profile_id: UUID,
        *,
        incluir_programadas: bool = True,
    ) -> list[SkuComPromocoes]:
        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            logger.info(
                "skus_com_promocoes_skip_loja_desconectada",
                profile_id=str(profile_id),
            )
            return []

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        status_aceitos = {"started"}
        if incluir_programadas:
            status_aceitos.add("pending")

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            # 1) Lista items ativos (pega MLB4 + MLB6)
            items_ativos = await _listar_todos_items_ativos_do_vendedor(
                ml, profile.ml_user_id,
            )

            if not items_ativos:
                logger.info(
                    "skus_com_promocoes_loja_sem_items",
                    profile_id=str(profile_id),
                )
                return []

            # 2) Paraleliza:
            #    a) detalhes (titulo/preço/sku) via batch
            #    b) promoções de CADA item via endpoint reverso (funciona
            #       pra MLB4 + MLB6)
            info_basica_task = fetch_items_basic_info(ml, items_ativos)
            promos_por_item_task = _promos_de_todos_items(
                ml, items_ativos, status_aceitos,
            )

            info_basica, promos_por_item = await asyncio.gather(
                info_basica_task, promos_por_item_task,
            )

        # 3) Constrói lista final
        resultado: list[SkuComPromocoes] = []
        for iid in items_ativos:
            info = info_basica.get(iid, {})
            preco = info.get("preco")
            try:
                preco_float = float(preco) if preco is not None else None
            except (TypeError, ValueError):
                preco_float = None

            resultado.append(SkuComPromocoes(
                item_id=iid,
                sku=info.get("sku"),
                titulo=info.get("titulo"),
                preco=preco_float,
                promocoes=promos_por_item.get(iid, []),
            ))

        logger.info(
            "skus_com_promocoes_concluido",
            profile_id=str(profile_id),
            total_items=len(resultado),
            items_em_promocao=sum(1 for r in resultado if r.promocoes),
            incluir_programadas=incluir_programadas,
        )
        return resultado


async def _promos_de_todos_items(
    ml: MLClient,
    item_ids: list[str],
    status_aceitos: set[str],
) -> dict[str, list[PromoNoItem]]:
    """Pra cada item_id, busca promoções via endpoint reverso
    `/seller-promotions/items/{id}`. Paraleliza usando o semáforo do
    MLClient (default max_concurrent=8).
    """
    if not item_ids:
        return {}

    async def _fetch_um(item_id: str) -> tuple[str, list[PromoNoItem]]:
        try:
            resp = await ml.get(
                f"/seller-promotions/items/{item_id}",
                params={"app_version": "v2"},
            )
        except Exception as e:
            logger.debug(
                "promos_do_item_falhou",
                item_id=item_id,
                error=str(e),
            )
            return item_id, []

        # Endpoint pode retornar list direto OU dict com `results`
        if isinstance(resp, list):
            promocoes_raw = resp
        elif isinstance(resp, dict):
            promocoes_raw = resp.get("results", []) or []
            if not isinstance(promocoes_raw, list):
                return item_id, []
        else:
            return item_id, []

        promos: list[PromoNoItem] = []
        for p in promocoes_raw:
            if not isinstance(p, dict):
                continue
            status_norm = _normalizar_status(p.get("status"))
            if status_norm not in status_aceitos:
                continue
            promo_id = p.get("id")
            if not promo_id:
                continue
            promos.append(PromoNoItem(
                promotion_id=str(promo_id),
                nome=p.get("name"),
                tipo=str(p.get("type") or ""),
                status=status_norm,
                start_date=_extrair_data(p.get("start_date")),
                finish_date=_extrair_data(p.get("finish_date")),
            ))
        return item_id, promos

    results = await asyncio.gather(*[_fetch_um(iid) for iid in item_ids])
    return dict(results)


def _extrair_data(iso: Any) -> str | None:
    if not iso or not isinstance(iso, str):
        return None
    return iso[:10] if len(iso) >= 10 else None
