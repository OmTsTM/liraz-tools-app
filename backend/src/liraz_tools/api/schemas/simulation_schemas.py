"""Schemas dos endpoints de simulação."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StartSimulationRequest(BaseModel):
    """Payload pra disparar nova simulação."""

    concorrencia: int = Field(
        default=8, ge=1, le=20,
        description="Paralelismo (1-20). Default 8.",
    )


class StartSimulationResponse(BaseModel):
    """Retorno do POST que dispara — id pra polling."""

    simulation_id: str


class SimulationSummaryResponse(BaseModel):
    """Listagem leve pra UI."""

    simulation_id: str
    criado_em: str
    estado: str
    total_ativos_analisados: int
    total_simulados: int
    total_excecoes: int
    total_processados: int
    retomado_de_checkpoint: bool


class SimulationDetailResponse(BaseModel):
    """Detalhe completo. Tem arrays de simulações e exceções."""

    simulation_id: str
    criado_em: str
    atualizado_em: str | None
    estado: str
    aliquota_imposto: float
    cep_destino: str
    custos_xlsx_path: str | None
    concorrencia_usada: int
    retomado_de_checkpoint: bool

    margem_alvo_aumento: float
    limite_margem_aumento: float
    margem_alvo_campanha: float

    total_ativos_analisados: int
    total_simulados: int
    total_excecoes: int
    total_processados: int
    erro_fatal: str | None

    simulacoes: list[dict[str, Any]]
    excecoes: list[dict[str, Any]]


class OverrideMarginRequest(BaseModel):
    """Body do endpoint de override por item."""

    margem_alvo: float = Field(
        ge=0.01, le=0.80,
        description=(
            "Margem alvo em decimal (0.01-0.80). Ex: 0.25 = 25%. "
            "Backend faz busca binária via ML pra encontrar o preço."
        ),
    )
