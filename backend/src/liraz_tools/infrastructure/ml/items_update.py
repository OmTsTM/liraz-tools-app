"""Atualizar preço de um item ML via PUT /items/{ITEM_ID}.

Wrapper sobre o endpoint do ML. Trata erros conhecidos como exceções
tipadas pra o caller mapear pra HTTP status apropriado.

Diferente da reprecificação Fase 1 atual (que só gera XLSX), aqui o
app altera de fato o preço no ML — usado na rev8 quando o vendedor
quer inflar antes de adicionar à campanha.

NÃO valida regras locais (margem mínima, etc) — quem usa esse wrapper
deve ter calculado o preço alvo via use case e respeitar políticas do
perfil. O ML por sua vez aplica suas próprias regras (preço mínimo da
categoria, anti-fraude de variação brusca).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

logger = structlog.get_logger(__name__)


class MLItemUpdateError(Exception):
    """Falha ao atualizar item no ML (base)."""


class ItemNotFoundError(MLItemUpdateError):
    """Item não existe ou usuário não tem permissão."""


class PriceVariationTooBigError(MLItemUpdateError):
    """ML rejeitou alteração — variação de preço acima do permitido por antifraude."""


class PriceInvalidError(MLItemUpdateError):
    """Preço inválido (zero/negativo/fora do limite da categoria)."""


async def atualizar_preco_item(
    ml: MLClient,
    item_id: str,
    novo_preco: float,
) -> dict[str, Any]:
    """Atualiza o preço de um item no ML via PUT.

    Args:
        ml: cliente MLClient autenticado
        item_id: MLB do item
        novo_preco: preço novo (deve ser > 0; ML também valida limite
            mínimo/máximo da categoria)

    Returns:
        Resposta crua do ML (item atualizado).

    Raises:
        ItemNotFoundError, PriceVariationTooBigError, PriceInvalidError,
        MLItemUpdateError: erros conhecidos do ML mapeados.
    """
    if novo_preco <= 0:
        raise PriceInvalidError(f"preço inválido: {novo_preco}")

    try:
        resp = await ml.put(
            f"/items/{item_id}",
            {"price": round(novo_preco, 2)},
        )
    except Exception as e:
        msg = str(e).lower()
        # Mapeia erros conhecidos do ML
        if "404" in msg or "item_not_found" in msg:
            raise ItemNotFoundError(f"item {item_id} não encontrado") from e
        if "price_variation" in msg or "variation_too_big" in msg:
            raise PriceVariationTooBigError(
                f"ML rejeitou alteração de preço — variação excede limite "
                f"anti-fraude pra item {item_id}: {e}"
            ) from e
        if "price" in msg and ("invalid" in msg or "minimum" in msg or "maximum" in msg):
            raise PriceInvalidError(
                f"preço {novo_preco} rejeitado pelo ML pra item {item_id}: {e}"
            ) from e
        raise MLItemUpdateError(
            f"erro inesperado atualizando preço de {item_id}: {e}"
        ) from e

    logger.info(
        "item_price_updated",
        item_id=item_id,
        novo_preco=novo_preco,
    )
    return cast("dict[str, Any]", resp)
