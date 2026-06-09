"""Criação de SELLER_CAMPAIGN no ML (Leva 5.10).

Wrapper de `POST /seller-promotions/promotions?app_version=v2`. Cria uma
campanha do tipo SELLER_CAMPAIGN / FLEXIBLE_PERCENTAGE — o tipo "guarda-chuva"
que aceita SKUs com `deal_price` por item.

Após criar, use `infrastructure.ml.promotion_items.adicionar_sku_em_campanha`
pra popular os SKUs.

Datas: o ML aceita formato local sem timezone (ISO 8601 truncado).
Termina o dia automaticamente em `23:59:59` na resposta.

Errors conhecidos:
- 400 com mensagem "name already exists" ou similar → CampaignNameConflictError
- 400 com "invalid dates" → InvalidDatesError
- 4xx outros → MLPromotionError genérico
- 5xx → MLServerError
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

import structlog

from liraz_tools.infrastructure.ml.promotion_items import (
    MLPromotionError,
    MLServerError,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

logger = structlog.get_logger(__name__)


class CampaignNameConflictError(MLPromotionError):
    """Já existe uma campanha com o mesmo nome no perfil."""


class InvalidDatesError(MLPromotionError):
    """Datas inválidas (passadas, finish_date < start_date, etc)."""


class StartDateTooFarError(MLPromotionError):
    """`start_date` está além do limite do ML (60 dias no futuro)."""


def _extrair_mensagem_ml(err: Exception) -> str:
    """Extrai a mensagem `message` do JSON de erro do ML quando possível.

    O ML retorna erros como:
    `HTTP 400 {"message":"the date start_date cannot be greater than 60`
    `days from now.","error":"bad_request","status":400,"cause":[]}`

    Esta função pega só o texto da `message`. Se não encontrar o padrão,
    retorna a string original.
    """
    raw = str(err)
    # Procura pelo padrão {"message":"..." ou {'message':'...'
    m = re.search(r'["\']message["\']\s*:\s*["\']([^"\']+)["\']', raw)
    if m:
        return m.group(1)
    return raw


def _formatar_data_iso_local(d: date | datetime, hora: time | None = None) -> str:
    """Formata pra ISO 8601 sem timezone, formato local que o ML aceita.

    Exemplo: "2026-05-21T00:00:00".
    """
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%dT%H:%M:%S")
    # date
    h = hora or time(0, 0, 0)
    return datetime.combine(d, h).strftime("%Y-%m-%dT%H:%M:%S")


async def criar_seller_campaign(
    ml: MLClient,
    *,
    nome: str,
    start_date: date | datetime,
    finish_date: date | datetime,
) -> dict[str, Any]:
    """Cria uma SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE no ML.

    Args:
        nome: nome único da campanha (validar no caller que não conflita)
        start_date: data ou datetime de início (sempre tratado como início do dia)
        finish_date: data ou datetime de fim (ML completa com 23:59:59)

    Returns:
        Resposta do ML com `id` (ex: "C-MLB360923"), `status`, etc.
    """
    payload = {
        "promotion_type": "SELLER_CAMPAIGN",
        "name": nome,
        "sub_type": "FLEXIBLE_PERCENTAGE",
        "start_date": _formatar_data_iso_local(start_date),
        "finish_date": _formatar_data_iso_local(finish_date),
    }

    logger.info(
        "criar_seller_campaign_iniciando",
        nome=nome,
        start_date=payload["start_date"],
        finish_date=payload["finish_date"],
    )

    try:
        resp = await ml.post(
            "/seller-promotions/promotions",
            json=payload,
            params={"app_version": "v2"},
        )
    except Exception as e:
        # Extrai mensagem amigável do JSON de erro do ML
        msg_amigavel = _extrair_mensagem_ml(e)
        msg_lower = msg_amigavel.lower()
        raw_lower = str(e).lower()

        # Classifica pelos padrões conhecidos do ML
        if "60 days" in msg_lower or "60 dias" in msg_lower:
            raise StartDateTooFarError(
                f"Data de início está fora do limite do ML (máx 60 dias no "
                f"futuro). ML: {msg_amigavel}"
            ) from e
        if "name" in msg_lower and (
            "exist" in msg_lower or "duplicate" in msg_lower
            or "already" in msg_lower
        ):
            raise CampaignNameConflictError(
                f"Nome '{nome}' já existe no ML. ML: {msg_amigavel}"
            ) from e
        if "date" in msg_lower or "invalid" in msg_lower:
            raise InvalidDatesError(
                f"Datas inválidas. ML: {msg_amigavel}"
            ) from e
        if any(c in raw_lower for c in ("500", "502", "503", "504")):
            raise MLServerError(
                f"ML indisponível temporariamente. ML: {msg_amigavel}"
            ) from e
        raise MLPromotionError(
            f"Erro ao criar SELLER_CAMPAIGN. ML: {msg_amigavel}"
        ) from e

    if not isinstance(resp, dict):
        raise MLPromotionError(
            f"resposta inesperada do ML ao criar campanha: {type(resp).__name__}"
        )

    campaign_id = resp.get("id")
    if not campaign_id:
        raise MLPromotionError(
            f"ML retornou sem id na criação: {resp}"
        )

    logger.info(
        "criar_seller_campaign_concluido",
        ml_campaign_id=campaign_id,
        status=resp.get("status"),
        nome=nome,
    )
    return resp
