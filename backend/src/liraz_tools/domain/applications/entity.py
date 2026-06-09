"""Entidades de execução de campanha (Leva 5.4).

Um `Application` representa UMA execução de uma campanha — quando o usuário
clica "Iniciar agora" (manual) ou quando o scheduler dispara (Leva 5.5,
futuro).

Persiste como JSON em `profiles/<slug>/applications/<app_id>.json` pra
permitir:
1. Polling de progresso pelo frontend sem segurar HTTP
2. Retomada após crash (no futuro — Leva 5.5)
3. Histórico/auditoria de tudo que foi aplicado

Um `Rollback` é o snapshot do estado anterior dos preços, gerado
incrementalmente durante a Fase 1 pra permitir reverter. Persistido em
`profiles/<slug>/rollbacks/<rollback_id>.json`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ApplicationStatus = Literal[
    "running",      # Fase 1, 2 ou 3 em andamento
    "completed",    # Campanha criada no ML com sucesso
    "failed",       # Erro irrecuperável em qualquer fase
    "interrupted",  # Interrompida (server crash, cancel — Leva 5.5)
]


ApplicationPhase = Literal[
    "applying_prices",       # Fase 1: PUT /items/{id} pra cada SKU
    "creating_campaign",     # Fase 2: POST /seller-promotions/promotions
    "adding_items",          # Fase 3: PUT /seller-promotions/items
    "reverting",             # Reversão: PUT /items/{id} com preco_anterior (Leva 5.6)
    "done",                  # Tudo OK
]


class ApplicationItem(BaseModel):
    """Resultado da aplicação de um item específico."""

    item_id: str
    sku: str | None = None
    title: str | None = None
    preco_anterior: float
    preco_novo: float
    deal_price: float | None = None
    status: Literal["pendente", "aplicado", "falha", "pulado"] = "pendente"
    """- pendente: ainda não foi processado
    - aplicado: PUT /items deu OK
    - falha: PUT /items deu erro (mensagem em `erro`)
    - pulado: item da simulação tinha fase1_acao=mantido, não precisa aplicar"""

    erro: str | None = None
    """Mensagem amigável de erro, se status=falha."""

    aplicado_em: datetime | None = None
    """Timestamp do PUT, None se ainda não rodou."""


class Application(BaseModel):
    """Execução completa de uma campanha."""

    model_config = ConfigDict(extra="ignore")

    application_id: str = Field(min_length=1, max_length=80)
    """UID legível, ex: 'app_20260522_103000_a1b2'."""

    campaign_id: UUID
    profile_slug: str
    simulacao_id: str

    estado: ApplicationStatus = "running"
    fase: ApplicationPhase = "applying_prices"

    criado_em: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finalizado_em: datetime | None = None

    # Progresso de Fase 1
    total_itens: int = 0
    itens_aplicados: int = 0
    itens_pulados: int = 0
    itens_falha: int = 0

    # Itens completos
    itens: list[ApplicationItem] = Field(default_factory=list)

    # IDs gerados durante a execução
    rollback_id: str | None = None
    """Sempre None até Fase 1 começar. Depois preenchido SEM CONDIÇÃO —
    o rollback existe mesmo em caso de falha parcial."""

    ml_campaign_id: str | None = None
    """Preenchido só após Fase 2 OK."""

    erro: str | None = None
    """Mensagem geral de erro, se estado=failed. Texto amigável."""


class RollbackItem(BaseModel):
    """Snapshot individual de preço pra rollback."""

    item_id: str
    sku: str | None = None
    preco_anterior: float
    preco_novo: float
    aplicado_em: datetime


class Rollback(BaseModel):
    """Snapshot completo pra reverter uma aplicação.

    Gerado incrementalmente durante a Fase 1: cada item é APPEND aqui
    ANTES de fazer PUT /items. Se a aplicação cai no meio, esse arquivo
    ainda permite reverter o que foi aplicado.
    """

    model_config = ConfigDict(extra="ignore")

    rollback_id: str = Field(min_length=1, max_length=80)
    """UID legível, ex: 'rb_20260522_103000_a1b2'."""

    application_id: str
    campaign_id: UUID
    profile_slug: str
    simulacao_id: str

    criado_em: datetime = Field(default_factory=lambda: datetime.now(UTC))
    itens: list[RollbackItem] = Field(default_factory=list)
