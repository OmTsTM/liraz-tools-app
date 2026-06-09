"""Busca dados de shipments dos pedidos do dia pra calcular SLA.

Pra cada `shipping_id` único do dia, faz `GET /shipments/{id}` em paralelo
limitado por semáforo. Extrai `status` e `date_first_visit` (= entrega
confirmada). Calcula tempo entre criação do order e entrega.
"""
from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.relatorio.entity import MetricasEnvio

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

logger = get_logger(__name__)


@dataclass(frozen=True)
class _ShipmentInfo:
    """Snapshot de um shipment do ML."""

    shipping_id: int
    status: str  # delivered | shipped | handling | ready_to_ship | cancelled | ...
    date_first_visit_iso: str | None  # ISO timestamp da entrega ou None


async def _buscar_um_shipment(
    ml: MLClient, shipping_id: int,
) -> _ShipmentInfo | None:
    """1 chamada GET /shipments/{id}. Devolve None em falha."""
    try:
        resp = await ml.get(f"/shipments/{shipping_id}")
    except Exception as e:
        logger.debug("shipment_fetch_falha", shipping_id=shipping_id, erro=str(e))
        return None
    if not isinstance(resp, dict):
        return None
    status = str(resp.get("status") or "")
    # date_first_visit é a entrega real. Pode estar no top-level ou em
    # status_history dependendo do tipo de envio.
    dfv = resp.get("date_first_visit") or resp.get("date_first_printed")
    return _ShipmentInfo(
        shipping_id=shipping_id,
        status=status,
        date_first_visit_iso=str(dfv) if dfv else None,
    )


def _parse_iso(s: str | None) -> datetime | None:
    """Tolerante: aceita 'Z' e offsets como -03:00."""
    if not s:
        return None
    try:
        normalizado = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalizado)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


async def calcular_metricas_envio(
    ml: MLClient,
    *,
    orders_data: dict[int, tuple[int, str]],
    max_concurrent: int = 8,
) -> MetricasEnvio:
    """Pra cada (shipping_id, order_date_iso), busca o shipment e calcula SLA.

    `orders_data` mapa `order_id → (shipping_id, order_date_created_iso)`.
    Orders sem shipping_id ficam fora.
    """
    pedidos_com_envio = {
        oid: (sid, dt) for oid, (sid, dt) in orders_data.items() if sid
    }
    qtd_total = len(pedidos_com_envio)
    if qtd_total == 0:
        return MetricasEnvio(
            qtd_total_pedidos=0,
            qtd_entregue=0,
            qtd_pendente=0,
            tempo_medio_dias=None,
            tempo_mediano_dias=None,
            pior_atraso_dias=None,
        )

    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _wrap(sid: int) -> _ShipmentInfo | None:
        async with sem:
            return await _buscar_um_shipment(ml, sid)

    # Dedup por shipping_id (raro, mas evita chamada duplicada)
    shipping_ids = {sid for (sid, _) in pedidos_com_envio.values()}
    resultados = await asyncio.gather(*[_wrap(sid) for sid in shipping_ids])
    por_shipping_id: dict[int, _ShipmentInfo] = {
        r.shipping_id: r for r in resultados if r is not None
    }

    dias_entrega: list[float] = []
    qtd_entregue = 0
    for _oid, (sid, order_iso) in pedidos_com_envio.items():
        info = por_shipping_id.get(sid)
        if info is None or info.status != "delivered":
            continue
        order_dt = _parse_iso(order_iso)
        delivery_dt = _parse_iso(info.date_first_visit_iso)
        if order_dt is None or delivery_dt is None:
            continue
        delta = (delivery_dt - order_dt).total_seconds() / 86400.0
        if delta < 0:
            # Inconsistência (entrega antes do order?). Pula em vez de poluir.
            continue
        dias_entrega.append(delta)
        qtd_entregue += 1

    qtd_pendente = qtd_total - qtd_entregue
    if not dias_entrega:
        return MetricasEnvio(
            qtd_total_pedidos=qtd_total,
            qtd_entregue=0,
            qtd_pendente=qtd_pendente,
            tempo_medio_dias=None,
            tempo_mediano_dias=None,
            pior_atraso_dias=None,
        )

    tempo_medio = sum(dias_entrega) / len(dias_entrega)
    tempo_mediano = statistics.median(dias_entrega)
    pior = max(int(d) for d in dias_entrega)
    return MetricasEnvio(
        qtd_total_pedidos=qtd_total,
        qtd_entregue=qtd_entregue,
        qtd_pendente=qtd_pendente,
        tempo_medio_dias=round(tempo_medio, 1),
        tempo_mediano_dias=round(tempo_mediano, 1),
        pior_atraso_dias=pior,
    )
