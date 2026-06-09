"""Calcula custos logísticos (frete + tarifa fixa pagos pelo vendedor) por SKU.

`sale_fee` no order_item do ML é só a COMISSÃO comercial — não inclui frete
nem tarifa fixa. Pra ter o lucro líquido REAL, precisamos somar:
- **tarifa fixa** (R$ 4-8.55) pra anúncios com unit_price < R$ 79
- **frete** (R$ 17-25) pra anúncios com unit_price ≥ R$ 79 com `free_shipping=true`
  (regra ML: vendedor paga o frete na faixa de frete grátis obrigatório)

Esses valores vêm do mesmo motor de pricing que o app já usa pra cálculo de
preço (`calcular_taxas_anuncio`). Pra economizar chamadas ML, cacheamos por
(item_id, regime_de_preco): se 3 unidades do mesmo SKU foram vendidas no mesmo
preço, só consultamos uma vez.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from liraz_tools.core.logging import get_logger
from liraz_tools.infrastructure.pricing.calculator import calcular_taxas_anuncio

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient
    from liraz_tools.infrastructure.pricing.freight_cache import FreightCache

logger = get_logger(__name__)

# Limite ML pra frete grátis obrigatório (vendedor paga acima dessa faixa).
LIMITE_FRETE_GRATIS = 79.0


@dataclass(frozen=True)
class CustoLogisticoPorVenda:
    """Custo logístico que o vendedor paga POR UNIDADE vendida desse SKU.

    `tarifa_fixa` e `frete` são mutualmente exclusivos pelo regime:
    - Unit price < R$ 79 → vendedor paga `tarifa_fixa`; `frete` = 0
    - Unit price ≥ R$ 79 + free_shipping → vendedor paga `frete`; `tarifa_fixa` = 0
    - Unit price ≥ R$ 79 sem free_shipping → cliente paga; ambos = 0

    `por_unidade` é o total (= tarifa_fixa + frete) que sai do lucro a cada
    unidade vendida desse SKU nesse preço.
    """

    tarifa_fixa: float
    frete: float

    @property
    def por_unidade(self) -> float:
        return round(self.tarifa_fixa + self.frete, 2)


async def calcular_custos_logisticos_por_sku(
    ml: MLClient,
    *,
    skus_vendidos: dict[str, float],
    cep: str,
    freight_cache: FreightCache,
    tarifas_overrides: dict[str, dict[str, float]],
    max_concurrent: int = 6,
) -> dict[str, CustoLogisticoPorVenda]:
    """Pra cada SKU vendido, calcula o custo logístico unitário.

    `skus_vendidos` é um mapa `{item_id: unit_price_medio}` — usamos o preço
    pra determinar o regime (< R$ 79 vs ≥ R$ 79). Quando há mais de uma venda
    do mesmo item por preços diferentes (raro mas possível), use a média.

    Roda em paralelo limitado pelo semáforo. SKUs onde o ML falha entram com
    custo zero (= lucro fica inflado conservadoramente, mas não quebra).
    """
    if not skus_vendidos:
        return {}

    sem = asyncio.Semaphore(max(1, max_concurrent))
    out: dict[str, CustoLogisticoPorVenda] = {}

    async def _calc_um(item_id: str, preco: float) -> None:
        async with sem:
            try:
                taxas = await calcular_taxas_anuncio(
                    ml=ml, item_id=item_id, cep_destino=cep,
                    freight_cache=freight_cache,
                    preco_simulado=preco,
                    tarifas_override=tarifas_overrides,
                )
            except Exception as e:
                logger.debug(
                    "custo_logistico_falha", item_id=item_id, erro=str(e),
                )
                out[item_id] = CustoLogisticoPorVenda(tarifa_fixa=0.0, frete=0.0)
                return
        if "erro" in taxas:
            out[item_id] = CustoLogisticoPorVenda(tarifa_fixa=0.0, frete=0.0)
            return
        tarifa_fixa = float(taxas.get("tarifa_fixa") or 0.0)
        frete = float(taxas.get("frete") or 0.0)
        # Defensivo: se ambos não-zero ao mesmo tempo (shouldn't happen no
        # modelo do calculator), confia no regime pelo preço.
        if preco < LIMITE_FRETE_GRATIS:
            frete = 0.0
        else:
            tarifa_fixa = 0.0
        out[item_id] = CustoLogisticoPorVenda(
            tarifa_fixa=round(tarifa_fixa, 2),
            frete=round(frete, 2),
        )

    await asyncio.gather(*[
        _calc_um(iid, preco) for iid, preco in skus_vendidos.items()
    ])
    return out
