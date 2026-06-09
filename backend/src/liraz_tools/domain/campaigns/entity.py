"""Entidades do contexto Campaigns (scheduler de campanhas promocionais).

Uma `Campaign` representa um evento promocional planejado pra rodar no
ML em um intervalo de datas. Centraliza 2 ações que antes seriam
separadas:
  1. Aplicar preços novos (`aplicar_novos_precos`) — Leva 5.4
  2. Criar SELLER_CAMPAIGN no ML (`criar_campanha_promocional`) — Leva 5.4

Por que juntar? Porque na prática você nunca muda preço só por mudar —
muda porque vai entrar em campanha. Modelar como uma só entidade reflete
o uso real e centraliza histórico/auditoria.

Limite do ML BR: campanhas SELLER_CAMPAIGN duram no máximo 1 mês
(até 31 dias entre data_inicio e data_fim). Não validamos isso no app —
deixamos o ML reclamar quando criar pra ter mensagem amigável vinda
direto da fonte autoritativa.

Ciclo de vida do `status`:
  rascunho → agendada → executando → ativa → finalizada
                                          ↘  cancelada
                                          ↘  falha

  Transições válidas (Leva 5.3 só implementa rascunho ↔ agendada ↔ cancelada):
    rascunho → agendada (programar)
    agendada → rascunho (despromover)
    rascunho/agendada → cancelada
    agendada → executando (Leva 5.4: disparar)
    executando → ativa/falha
    ativa → finalizada (passou data_fim)
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CampaignStatus = Literal[
    "rascunho",
    "agendada",
    "executando",
    "ativa",
    "finalizada",
    "cancelada",
    "falha",
]


# Status que ainda podem ser editados/cancelados sem efeito colateral
EDITABLE_STATUSES: set[CampaignStatus] = {"rascunho", "agendada"}


class Campaign(BaseModel):
    """Entidade Pydantic — view do domínio.

    Não tem dependência com SQLAlchemy. Repository converte entre
    `CampaignModel` (DB) e `Campaign` (domínio).
    """

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID

    nome: str = Field(min_length=1, max_length=200)
    """Nome da campanha (ex: 'Black Friday Toque Rico'). Pode conter
    qualquer string não-vazia."""

    simulacao_id: str | None = None
    """ID do snapshot de simulação associado. Pode ser None enquanto
    a campanha está em rascunho — usuário define depois."""

    data_inicio: date
    data_fim: date
    hora_disparo: time = Field(default_factory=lambda: time(9, 0))
    """Hora do dia em que a campanha deve disparar (scheduler — Leva 5.5).

    Default 09:00. Aceita qualquer time válido. Se passar HH:MM como string
    o Pydantic converte automaticamente."""

    hora_fim: time | None = None
    """Hora do dia em que a campanha deve terminar (opcional).

    None = até 23:59:59 do data_fim. Preenchida = scheduler termina/cancela
    no horário exato."""

    skus_selecionados: list[str] | None = None
    """Lista de item_ids (do ML, não SKUs internos) escolhidos pra entrar
    na campanha. Default `None` significa 'todos os itens da simulação
    com fase1_acao != mantido' (comportamento anterior, mantido pra compat).

    Lista vazia `[]` é bloqueada no use case de start — não faz sentido
    disparar campanha sem nenhum item."""

    status: CampaignStatus = "rascunho"

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Preenchidos a partir da Leva 5.4
    ml_campaign_id: str | None = None
    aplicacao_id: str | None = None
    rollback_id: str | None = None
    erro: str | None = None

    # Leva 5.7.1 — soft delete automático 15 dias após data_fim
    archived_at: datetime | None = None
    """Quando preenchido, indica que a campanha foi auto-arquivada pelo
    scheduler (15 dias após data_fim). Some da lista por default; usuário
    pode incluí-las via toggle 'Incluir arquivadas'. Dados em disco
    (snapshots, rollback, application history) ficam preservados — só
    visibilidade na UI muda."""

    # Leva 5.9.2 — origem da campanha
    origem: Literal["local", "ml"] = "local"
    """`local` = criada no app (fluxo normal). `ml` = importada de uma
    campanha que já existia no painel do ML (Leva 5.9.2). Determina
    restrições de UI e fluxo: campanhas ml não passam pelo scheduler
    de disparo (já estão rodando no ML), não podem ser revertidas via
    rollback local (não temos snapshot dos preços anteriores)."""

    # Leva 5.9.4.B — faixa de margem pra detecção de migração ML.
    # Quando None, usa o default do perfil (profile.config.margem_min/max_migracao).
    margem_min_migracao: float | None = Field(default=None, ge=0.0, le=1.0)
    margem_max_migracao: float | None = Field(default=None, ge=0.0, le=1.0)
    """Faixa aceitável de margem líquida pra SKUs migrarem pra campanhas
    do ML. SKUs que conseguem atingir uma margem dentro dessa faixa em
    alguma promoção ajustável do ML viram candidatos a migrar. Null =
    herda do perfil (`profile.config.margem_min/max_migracao`)."""

    # Leva 5.11 — controle de renovação automática de guarda-chuva
    renovada: bool = Field(default=False)
    """True quando o scheduler já criou uma campanha sucessora pra esta
    (Ciclo A da Leva 5.11). Evita criar sucessoras duplicadas em ticks
    consecutivos enquanto a original ainda não terminou."""

    @field_validator("nome")
    @classmethod
    def _trim_nome(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("nome não pode ser vazio")
        return trimmed

    @model_validator(mode="after")
    def _validate_datas(self) -> Campaign:
        if self.data_fim < self.data_inicio:
            raise ValueError(
                f"data_fim ({self.data_fim}) não pode ser anterior a "
                f"data_inicio ({self.data_inicio})"
            )
        return self

    @property
    def is_editable(self) -> bool:
        """True se a campanha ainda pode ter campos modificados.

        Só permite edição enquanto está em rascunho ou agendada. Depois
        de disparar, vira imutável (mesmo o nome — pra preservar
        coerência do histórico).

        Leva 5.9.2: campanhas vindas do ML (origem="ml") são SEMPRE
        read-only no app — gerenciamento real é no painel do ML.
        Edição de SKUs especificamente vem em levas futuras.
        """
        if self.origem == "ml":
            return False
        return self.status in EDITABLE_STATUSES

    @property
    def duracao_dias(self) -> int:
        """Duração em dias de diferença entre as datas.

        Ex: 01/01 a 02/01 = 1 dia, 21/05 a 21/06 = 31 dias.
        Bate com a noção comum de "X dias até a data Y" e com o cálculo
        do ML, que aceita até 30 dias (1 mês de calendário entre as datas
        equivalentes).
        """
        return (self.data_fim - self.data_inicio).days

    @property
    def agendado_para(self) -> datetime:
        """Combina data_inicio + hora_disparo em datetime ingênuo (sem tz).

        Usado pelo scheduler (Leva 5.5) pra decidir quando disparar. Como
        é local time do usuário, mantém ingênuo — comparar com
        `datetime.now()` sem tz.
        """
        return datetime.combine(self.data_inicio, self.hora_disparo)

    @property
    def pronta_para_agendar(self) -> bool:
        """True quando a campanha tem o suficiente pra entrar no scheduler.

        No modelo passo3 (atual): bastam `skus_selecionados` — o `deal_price`
        de cada item é computado NO DISPARO via `StartCampaignPasso3UseCase`
        (preço atualizado de frete/custo na hora). Não precisa mais de uma
        simulação de reprecificação pré-rodada.

        `simulacao_id` ainda é aceito como critério alternativo pra compat
        com campanhas criadas no modelo antigo (que ainda podem estar em
        rascunho no banco).
        """
        return bool(self.simulacao_id) or bool(self.skus_selecionados)
