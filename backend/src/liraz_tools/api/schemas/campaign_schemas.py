"""Schemas Pydantic dos endpoints de campanhas."""
from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, Field


class CreateCampaignRequest(BaseModel):
    """Body do POST de criação."""

    nome: str = Field(min_length=1, max_length=200)
    data_inicio: date
    data_fim: date
    hora_disparo: time = Field(default_factory=lambda: time(9, 0))
    """Hora do dia em que a campanha deve disparar. Default: 09:00."""

    hora_fim: time | None = None
    """Hora de término (opcional). None = até 23:59:59 do data_fim."""

    simulacao_id: str | None = Field(default=None, max_length=80)

    skus_selecionados: list[str] | None = None
    """Lista de item_ids que entram na campanha. None = todos da simulação."""

    forcar_nome: bool = False
    """Pula validação de nome único — usado quando user já confirmou no modal."""


class UpdateCampaignRequest(BaseModel):
    """Body do PATCH (todos os campos opcionais — atualização parcial).

    `simulacao_id`, `hora_fim` e `skus_selecionados` aceitam null explícito
    pra desassociar. Pra "não mudar", omita o campo.
    """

    nome: str | None = Field(default=None, min_length=1, max_length=200)
    data_inicio: date | None = None
    data_fim: date | None = None
    hora_disparo: time | None = None
    hora_fim: time | None = None
    simulacao_id: str | None = Field(default=None)
    skus_selecionados: list[str] | None = None
    forcar_nome: bool = False


class CampaignResponse(BaseModel):
    """Representação completa duma campanha."""

    id: UUID
    profile_id: UUID
    nome: str
    simulacao_id: str | None
    data_inicio: date
    data_fim: date
    hora_disparo: time
    hora_fim: time | None
    skus_selecionados: list[str] | None
    status: str
    # Leva 5.9.1: opcionais porque campanhas-do-ML (origem="ml") não têm
    # esses timestamps — vêm de fora do banco. Campanhas locais sempre têm.
    created_at: datetime | None = None
    updated_at: datetime | None = None

    ml_campaign_id: str | None = None
    aplicacao_id: str | None = None
    rollback_id: str | None = None
    erro: str | None = None

    archived_at: datetime | None = None

    duracao_dias: int
    is_editable: bool

    # Leva 5.9.1 — origem da campanha:
    #   "local" = criada no app (editável normalmente)
    #   "ml"    = criada direto no ML, importada (read-only no app)
    origem: str = "local"


class ImportMLCampaignRequest(BaseModel):
    """Body do POST de importação de campanha ML (Leva 5.9.2)."""

    ml_promotion_id: str = Field(min_length=1, max_length=80)
    """Identificador da SELLER_CAMPAIGN no ML (ex: 'C-MLB12345')."""

    full_scan: bool = False
    """Se True, faz a varredura COMPLETA do catálogo (descobre SKUs adicionados
    direto no painel do ML, inclusive legados MLB4xxx). Caro (~N anúncios da loja).
    Default False = sync DIRECIONADO: confere só os SKUs já conhecidos da campanha
    (rápido) + status. A primeira importação sempre faz varredura completa (não há
    SKUs conhecidos ainda). Use True no botão 'Atualizar do ML'."""


class CampaignItemInfo(BaseModel):
    """Info enriquecida de um item participante de campanha (Leva 5.9.3).

    Campos podem vir nulos se o cache do fee_report estiver frio — frontend
    mostra placeholder e oferece "Atualizar relatório de margens".
    """

    item_id: str
    sku: str | None = None
    titulo: str | None = None
    preco: float | None = None
    modalidade: str | None = None
    margem_liquida_pct: float | None = None


class CampaignItemEligible(CampaignItemInfo):
    """Item com flag de elegibilidade pra ser adicionado em campanha (5.9.3)."""

    em_outra_campanha: bool = False
    nomes_outras_campanhas: list[str] = []


class UpdateCampaignSkusRequest(BaseModel):
    """Body do PATCH de skus_selecionados de uma campanha (Leva 5.9.3)."""

    skus_selecionados: list[str] | None = None
    """Nova lista de item_ids. Null = todos os elegíveis (mantém compat)."""
