"""Busca info básica (sku, titulo, preço) de items via /items?ids=... (multiget).

ML aceita até 20 IDs por chamada. Pra listas maiores, paginamos em paralelo.

Resposta verbose:
[
  {"code": 200, "body": {"id": "MLB...", "title": "...", "price": ..., ...}},
  {"code": 404, "body": {...}},
  ...
]

Items com erro são pulados — não derrubam a chamada inteira.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

logger = structlog.get_logger(__name__)

# ML aceita até 20 ids por request multiget
BATCH_SIZE = 20


def _extrair_sku(item: dict[str, Any]) -> str | None:
    """Extrai SKU do item: seller_custom_field → atributo SELLER_SKU → variações.

    Copia da implementação canônica em
    `infrastructure/pricing/calculator._extrair_sku` pra evitar import circular
    (pricing depende de coisas que não queremos arrastar pra dentro do ml).
    """
    sku = item.get("seller_custom_field")
    if sku:
        return str(sku)
    for attr in item.get("attributes") or []:
        if attr.get("id") == "SELLER_SKU":
            value = attr.get("value_name") or attr.get("value_id")
            if value:
                return str(value)
    # Variações por cor/tamanho
    variations = item.get("variations") or []
    if variations:
        skus = []
        for var in variations:
            for attr in var.get("attributes") or []:
                if attr.get("id") == "SELLER_SKU":
                    val = attr.get("value_name")
                    if val:
                        skus.append(str(val))
        if skus:
            return " | ".join(skus)
    return None


def _extrair_modalidade(item: dict[str, Any]) -> str | None:
    """Deriva modalidade humana do listing_type_id.

    Mapeamento simplificado:
    - gold_pro     → Premium
    - gold_special → Clássico
    - free / outros → None
    """
    lti = item.get("listing_type_id")
    if lti == "gold_pro":
        return "Premium"
    if lti == "gold_special":
        return "Clássico"
    return None


async def fetch_items_basic_info(
    ml: MLClient, item_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Busca info básica de uma lista de items via multiget paralelo.

    Retorna dict {item_id -> {item_id, sku, titulo, preco, modalidade}}.
    Items que falharem (404, erro de rede) ficam fora do dict — caller
    detecta missing e mostra placeholder.
    """
    if not item_ids:
        return {}

    # Dedup pra não pedir o mesmo id 2x
    unique_ids = list({iid for iid in item_ids if iid})

    # Quebra em batches de 20
    batches = [
        unique_ids[i: i + BATCH_SIZE]
        for i in range(0, len(unique_ids), BATCH_SIZE)
    ]

    async def _fetch_batch(batch: list[str]) -> list[dict[str, Any]]:
        ids_param = ",".join(batch)
        try:
            resp = await ml.get("/items", params={"ids": ids_param})
        except Exception as e:
            logger.warning(
                "items_batch_failed",
                ids_count=len(batch),
                error=str(e),
            )
            return []
        if not isinstance(resp, list):
            return []
        return resp

    # Paraleliza batches
    results = await asyncio.gather(*[_fetch_batch(b) for b in batches])

    info_por_id: dict[str, dict[str, Any]] = {}
    for batch_resp in results:
        for entry in batch_resp:
            if not isinstance(entry, dict):
                continue
            code = entry.get("code")
            body = entry.get("body")
            if code != 200 or not isinstance(body, dict):
                continue
            iid = body.get("id")
            if not iid:
                continue
            info_por_id[str(iid)] = {
                "item_id": str(iid),
                "sku": _extrair_sku(body),
                "titulo": body.get("title"),
                # Pra item em SELLER_CAMPAIGN ativa, o ML às vezes retorna
                # `price` JÁ COM DESCONTO da campanha aplicado, e move o
                # preço-base verdadeiro pra `original_price`. O campo é
                # preenchido só quando há diferença — quando ausente, `price`
                # é o preço-base mesmo. Comportamento é intermitente e
                # depende de cache do ML, então defendemos sempre olhando
                # original_price primeiro. Sem isso, simulações de campanha
                # em items já em outra promoção calculam descontos sobre
                # o preço descontado.
                "preco": body.get("original_price") or body.get("price"),
                "modalidade": _extrair_modalidade(body),
            }

    logger.info(
        "items_batch_fetched",
        pedidos=len(unique_ids),
        ok=len(info_por_id),
        batches=len(batches),
    )
    return info_por_id
