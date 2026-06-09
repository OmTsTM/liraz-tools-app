"""Helpers de datetime pra contornar SQLite naive datetime.

SQLite não armazena timezone — guarda só o texto da datetime sem info de tz.
Quando lemos de volta via SQLAlchemy, recebemos datetimes naive (sem tzinfo).

Comparar naive com aware explode com:
    TypeError: can't compare offset-naive and offset-aware datetimes

Solução: sempre normalizar pra UTC aware na leitura. Como TODOS os datetimes
do app são gravados em UTC, podemos assumir UTC quando lemos sem tz.
"""
from __future__ import annotations

from datetime import UTC, datetime


def ensure_aware_utc(dt: datetime | None) -> datetime | None:
    """Garante que datetime tem tzinfo=UTC.

    - Se já é aware → retorna como está
    - Se é naive → assume UTC (todos nossos saves são em UTC)
    - Se é None → retorna None
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
