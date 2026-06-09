"""Healthcheck endpoint."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from liraz_tools import __version__
from liraz_tools.core.paths import get_app_data_dir

router = APIRouter()


class HealthResponse(BaseModel):
    """Resposta do healthcheck."""

    status: str = Field(description="Sempre 'ok' se o servidor está rodando.")
    version: str = Field(description="Versão do app.")
    timestamp: datetime = Field(description="Timestamp UTC da resposta.")
    data_dir: str = Field(description="Diretório de dados configurado.")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Healthcheck simples. Use pra validar que o servidor está rodando."""
    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.now(UTC),
        data_dir=str(get_app_data_dir()),
    )
