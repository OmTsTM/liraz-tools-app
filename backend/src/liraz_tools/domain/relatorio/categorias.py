"""Resolve nomes de categorias ML a partir de seus IDs.

`/categories/{id}` retorna `{"name": "...", ...}`. Pra um relatório diário com
até ~10 categorias distintas, fazemos chamadas em paralelo. Sem cache externo
— a duração do request HTTP do PDF não compensa o overhead de um cache em
disco. Se virar gargalo, a gente cacheia depois.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from liraz_tools.core.logging import get_logger

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

logger = get_logger(__name__)


async def resolver_nomes_de_categorias(
    ml: MLClient, category_ids: set[str], *, max_concurrent: int = 6,
) -> dict[str, str]:
    """Devolve mapa `{category_id: nome}`. IDs sem resposta caem fora do dict."""
    if not category_ids:
        return {}
    sem = asyncio.Semaphore(max(1, max_concurrent))
    out: dict[str, str] = {}

    async def _um(cid: str) -> None:
        async with sem:
            try:
                resp = await ml.get(f"/categories/{cid}")
            except Exception as e:
                logger.debug("categoria_fetch_falha", category_id=cid, erro=str(e))
                return
        if isinstance(resp, dict):
            nome = resp.get("name")
            if isinstance(nome, str) and nome:
                out[cid] = nome

    await asyncio.gather(*[_um(cid) for cid in category_ids])
    return out
