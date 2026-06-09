"""Adição e remoção de SKUs em campanhas do ML (SELLER_CAMPAIGN, DEAL, etc).

Wrappers em torno dos endpoints:
- POST /seller-promotions/items/{ITEM_ID}?app_version=v2
- DELETE /seller-promotions/items/{ITEM_ID}?promotion_id=...&promotion_type=...

Trata erros conhecidos do ML como exceções tipadas pra que callers (endpoints,
use cases) possam mapear pra HTTP status apropriado.

**Sobre `deal_price` em SELLER_CAMPAIGN**: o teste empírico mostrou que pra
SELLER_CAMPAIGN com sub_type FLEXIBLE_PERCENTAGE, o ML aplica a regra de
desconto da campanha por cima do preço original e ignora o `deal_price`
passado. Mantemos o parâmetro pra outros tipos (DEAL, PRICE_DISCOUNT) onde
ele é respeitado.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

logger = structlog.get_logger(__name__)


class MLPromotionError(Exception):
    """Base pra erros de operação em items de campanha ML."""


class ItemAlreadyInCampaignError(MLPromotionError):
    """SKU já está nessa campanha (POST duplicado)."""


class ItemNotInCampaignError(MLPromotionError):
    """SKU não está na campanha (DELETE em algo que já saiu)."""


class DealPriceInvalidError(MLPromotionError):
    """deal_price fora do range aceito (`min_discounted_price` violado, etc)."""


class ItemNotEligibleError(MLPromotionError):
    """SKU não pode entrar (pausado, sem stock, sem reputação verde, etc)."""


class MLServerError(MLPromotionError):
    """ML retornou 5xx. Operação pode ser retentada."""


class OfferLockedError(MLPromotionError):
    """ML retornou 423 LockedEntityException — item travado por outra operação.

    Comportamento empírico: o ML responde 423 imediatamente mas, depois de
    destravar internamente (alguns segundos), processa a adição assincronamente
    e o item entra na campanha com o `deal_price` enviado. Logo, NÃO deve ser
    tratado como falha definitiva — o caller deve retentar e, se persistir,
    deixar pendente sem reverter o preço-base inflado.
    """


def _classificar_erro_ml(msg: str) -> type[MLPromotionError]:
    """Mapeia string de erro do ML pra exceção tipada.

    Heurística baseada em substrings conhecidas. Quando não bate em nada,
    devolve a base `MLPromotionError`.
    """
    low = msg.lower()
    if "already" in low and ("campaign" in low or "promotion" in low):
        return ItemAlreadyInCampaignError
    if "not in" in low or "not exist" in low or "item not found" in low:
        return ItemNotInCampaignError
    if "lockedentity" in low or "offer locked" in low or "http 423" in low:
        return OfferLockedError
    if "deal_price" in low or "discount" in low or "min_discounted" in low:
        return DealPriceInvalidError
    if "paused" in low or "inactive" in low or "stock" in low or "reputation" in low:
        return ItemNotEligibleError
    if "5xx" in low or "internal server" in low or "bad gateway" in low:
        return MLServerError
    return MLPromotionError


async def adicionar_sku_em_campanha(
    ml: MLClient,
    item_id: str,
    promotion_id: str,
    promotion_type: str,
    *,
    deal_price: float | None = None,
    top_deal_price: float | None = None,
) -> dict[str, Any]:
    """Adiciona um item a uma campanha do ML.

    Args:
        ml: cliente autenticado
        item_id: MLB do anúncio
        promotion_id: id da campanha (ex: C-MLB4216507 pra SELLER_CAMPAIGN)
        promotion_type: SELLER_CAMPAIGN, DEAL, PRICE_DISCOUNT, etc
        deal_price: preço promocional. Pra SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE
                    é ignorado pelo ML. Pra DEAL/PRICE_DISCOUNT é obrigatório.
        top_deal_price: preço pra Mercado Puntos níveis 3-6. Opcional.

    Returns:
        Dict com resposta do ML, ex: {"price": 22.7, "original_price": 28.36,
        "offer_id": "OFFER-..."}

    Raises:
        ItemAlreadyInCampaignError: item já adicionado antes
        DealPriceInvalidError: deal_price fora do range
        ItemNotEligibleError: anúncio não atende critérios da campanha
        MLPromotionError: outros erros do ML
    """
    body: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_type": promotion_type,
    }
    if deal_price is not None:
        body["deal_price"] = deal_price
    if top_deal_price is not None:
        body["top_deal_price"] = top_deal_price

    try:
        resp = await ml.post(
            f"/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
            json=body,
        )
    except Exception as e:
        err_msg = str(e)
        exc_class = _classificar_erro_ml(err_msg)
        logger.warning(
            "ml_add_sku_failed",
            item_id=item_id,
            promotion_id=promotion_id,
            promotion_type=promotion_type,
            error=err_msg,
            exception_type=exc_class.__name__,
        )
        raise exc_class(err_msg) from e

    logger.info(
        "ml_add_sku_ok",
        item_id=item_id,
        promotion_id=promotion_id,
        promotion_type=promotion_type,
        price=resp.get("price") if isinstance(resp, dict) else None,
        original_price=resp.get("original_price") if isinstance(resp, dict) else None,
    )
    return resp if isinstance(resp, dict) else {}


async def remover_sku_de_campanha(
    ml: MLClient,
    item_id: str,
    promotion_id: str,
    promotion_type: str,
    *,
    offer_id: str | None = None,
) -> dict[str, Any]:
    """Remove um item de uma campanha do ML.

    Args:
        ml: cliente autenticado
        item_id: MLB do anúncio
        promotion_id: id da campanha
        promotion_type: tipo da campanha
        offer_id: alguns tipos exigem (MARKETPLACE_CAMPAIGN, DOD, LIGHTNING).
                  Pra SELLER_CAMPAIGN normalmente não é necessário.

    Returns:
        Dict (geralmente vazio em sucesso, status 200/204).

    Raises:
        ItemNotInCampaignError: item já não estava lá
        MLPromotionError: outros erros do ML
    """
    params: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_type": promotion_type,
        "app_version": "v2",
    }
    if offer_id is not None:
        params["offer_id"] = offer_id

    try:
        resp = await ml.delete(
            f"/seller-promotions/items/{item_id}",
            params=params,
        )
    except Exception as e:
        err_msg = str(e)
        exc_class = _classificar_erro_ml(err_msg)
        logger.warning(
            "ml_remove_sku_failed",
            item_id=item_id,
            promotion_id=promotion_id,
            promotion_type=promotion_type,
            error=err_msg,
            exception_type=exc_class.__name__,
        )
        raise exc_class(err_msg) from e

    logger.info(
        "ml_remove_sku_ok",
        item_id=item_id,
        promotion_id=promotion_id,
        promotion_type=promotion_type,
    )
    return resp if isinstance(resp, dict) else {}
