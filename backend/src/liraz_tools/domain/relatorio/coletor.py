"""Coletor de orders do dia + agregação por SKU.

Faz UMA passada pelo `/orders/search` da ML, extrai os campos que importam
(unit_price, quantity, sale_fee, seller_sku, hora_local) e agrega por SKU.

Cuidados:
- Janela do dia é BRT (UTC-3) — passamos offset explícito pro ML.
- `sale_fee` no `order_item` já é o total do item (não por unidade). Validado
  empiricamente: order de qty=1, unit_price=78.90, sale_fee=9.07 (~11,5%
  da receita, bate com a alíquota de comissão padrão da categoria).
- `seller_sku` vem em `order_items[].item.seller_sku`. Se vier vazio, caímos
  pro `seller_custom_field` (legado).
- Frete: **V1 ignora frete pago pelo vendedor**. Pra incluir precisaríamos
  consultar `/items/{id}/shipping_options` por SKU (1 chamada extra), o que
  dobra o tempo de geração. Próxima iteração resolve. O PDF terá disclaimer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from liraz_tools.infrastructure.ml.client import MLClient


@dataclass
class _ItemLinha:
    """Uma linha de venda (1 order_item dentro de um order)."""

    order_id: int
    status: str  # paid | cancelled | etc
    timestamp_iso: str  # date_created da order
    hora_local: int  # 0-23 em BRT
    item_id: str
    titulo: str | None
    seller_sku: str | None
    category_id: str | None  # `item.category_id` do order_item
    quantity: int
    unit_price: float
    sale_fee: float  # taxa ML total do item (já vem somada)
    # Cupons / Mercado Puntos (= cashback) que afetam o quanto o vendedor
    # recebe. `coupon_amount` é o que o ML cobriu (vendedor recebe cheio);
    # `meli_promo_amount` (= total - paid_amount) é o que veio de cashback.
    coupon_amount: float  # 0 se sem cupom
    meli_promo_amount: float  # 0 se sem Mercado Puntos / cashback
    shipping_id: int | None  # `shipping.id` do order; None pra compras digitais


def _hora_local_brt(iso_ts: str) -> int:
    """Extrai hora 0-23 do timestamp ISO. ML manda em BRT (-03:00) então
    basta ler o componente hora do string. Defensivo a formatos variados.
    """
    try:
        # Trata 'Z' como UTC pra dt.fromisoformat aceitar
        normalizado = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalizado)
        # Se vier em outro fuso, converte pra BRT. astimezone precisa de tz.
        if dt.tzinfo is None:
            return dt.hour
        # ML manda -03:00, então .hour já tá em local. Se outro fuso, converte.
        from datetime import timedelta, timezone
        brt = dt.astimezone(timezone(timedelta(hours=-3)))
        return brt.hour
    except Exception:
        return 0


def _extrair_seller_sku(item: dict[str, Any]) -> str | None:
    sku = item.get("seller_sku")
    if sku:
        return str(sku)
    sku = item.get("seller_custom_field")
    if sku:
        return str(sku)
    return None


async def coletar_linhas_do_dia(
    ml: MLClient, *, seller_id: int, dia_brt: date,
) -> list[_ItemLinha]:
    """Pagina `/orders/search` e devolve TODAS as linhas (1 por order_item).

    Inclui pedidos `paid`, `cancelled` e outros estados — o calculador
    separa depois. Pedidos cancelados contam pra taxa de cancelamento mas
    NÃO entram em receita/lucro.
    """
    inicio_iso = f"{dia_brt}T00:00:00.000-03:00"
    fim_iso = f"{dia_brt}T23:59:59.999-03:00"

    linhas: list[_ItemLinha] = []
    offset = 0
    page_size = 50

    while True:
        resp = await ml.get(
            "/orders/search",
            params={
                "seller": seller_id,
                "order.date_created.from": inicio_iso,
                "order.date_created.to": fim_iso,
                "limit": page_size,
                "offset": offset,
                "sort": "date_asc",
            },
        )
        if not isinstance(resp, dict):
            break
        results = resp.get("results") or []
        paging = resp.get("paging") or {}
        total_api = int(paging.get("total") or 0) if isinstance(paging, dict) else 0
        if not isinstance(results, list) or not results:
            break

        for o in results:
            if not isinstance(o, dict):
                continue
            order_id = int(o.get("id") or 0)
            status = str(o.get("status") or "")
            date_created = str(o.get("date_created") or "")
            hora = _hora_local_brt(date_created)
            # Cupons + cashback: alocados POR ORDER no payload, então
            # rateamos proporcionalmente pelos order_items por receita.
            coupon_obj = o.get("coupon") or {}
            coupon_amount_order = (
                float(coupon_obj.get("amount") or 0)
                if isinstance(coupon_obj, dict) else 0.0
            )
            total_amount_order = float(o.get("total_amount") or 0)
            paid_amount_order = float(o.get("paid_amount") or 0)
            # cashback / Mercado Puntos = quanto saiu do bolso do ML
            # cobrindo o cliente. total - paid pode ter outras causas,
            # mas é a melhor proxy disponível no payload.
            meli_promo_order = max(0.0, total_amount_order - paid_amount_order)
            shipping_block = o.get("shipping") or {}
            shipping_id_raw = (
                shipping_block.get("id")
                if isinstance(shipping_block, dict) else None
            )
            shipping_id = (
                int(shipping_id_raw)
                if isinstance(shipping_id_raw, (int, float)) else None
            )

            # Pra ratear cupom/meli_promo, soma a receita dos items pagantes
            order_items_list = o.get("order_items") or []
            receita_items = 0.0
            for oi_calc in order_items_list:
                if isinstance(oi_calc, dict):
                    receita_items += float(oi_calc.get("unit_price") or 0) * int(
                        oi_calc.get("quantity") or 0,
                    )

            for oi in order_items_list:
                if not isinstance(oi, dict):
                    continue
                item = oi.get("item") or {}
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                if not item_id:
                    continue
                qty = int(oi.get("quantity") or 0)
                unit_price = float(oi.get("unit_price") or 0)
                sale_fee = float(oi.get("sale_fee") or 0)
                receita_item = unit_price * qty
                peso = (
                    receita_item / receita_items
                    if receita_items > 0 else 0.0
                )
                linhas.append(_ItemLinha(
                    order_id=order_id,
                    status=status,
                    timestamp_iso=date_created,
                    hora_local=hora,
                    item_id=item_id,
                    titulo=item.get("title"),
                    seller_sku=_extrair_seller_sku(item),
                    category_id=item.get("category_id"),
                    quantity=qty,
                    unit_price=unit_price,
                    sale_fee=sale_fee,
                    coupon_amount=round(coupon_amount_order * peso, 2),
                    meli_promo_amount=round(meli_promo_order * peso, 2),
                    shipping_id=shipping_id,
                ))

        if len(results) < page_size:
            break
        offset += page_size
        if total_api and offset >= total_api:
            break

    return linhas
