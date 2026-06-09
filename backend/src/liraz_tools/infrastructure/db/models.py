"""Models SQLAlchemy. Schema relacional do banco SQLite local.

Vivem na camada de infraestrutura. O domínio NÃO importa daqui —
um mapper traduz entre o ORM model e a entidade Pydantic.

NOTA: a partir da migração pra credenciais por perfil, este banco guarda
apenas metadados (perfis, settings, campanhas). Snapshots de simulação
e credenciais OAuth vivem em arquivos por perfil (`profiles/<slug>/`).
"""
from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from liraz_tools.infrastructure.db.database import Base


class ProfileModel(Base):
    """Tabela de perfis (lojas)."""

    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)

    # Config serializada como JSON
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Dados do ML (preenchidos após OAuth)
    ml_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ml_nickname: Mapped[str | None] = mapped_column(String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SettingsModel(Base):
    """Tabela key-value pra settings globais (perfil ativo, tema, etc)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CampaignModel(Base):
    """Tabela de campanhas (scheduler).

    Uma campanha representa um evento promocional planejado:
    aplicação de preços novos + criação de SELLER_CAMPAIGN no ML
    com deal_prices da simulação associada.

    Status:
      - rascunho:   criada, ainda não pronta (pode mexer livremente)
      - agendada:   programada pra disparar (pode editar até disparar)
      - executando: dispatch em andamento (futuro — Leva 5.4)
      - ativa:      campanha ativa no ML (futuro)
      - finalizada: passou data_fim
      - cancelada:  cancelada manualmente antes/durante
      - falha:      algo deu errado na execução

    `simulacao_id` é o ID do snapshot de simulação (arquivo JSON em
    `profiles/<slug>/snapshots/`). É string porque snapshots são
    arquivos, não rows num banco.

    `ml_campaign_id`, `aplicacao_id`, `rollback_id`, `erro` ficam None
    até as Levas 5.4+ preencherem.
    """

    __tablename__ = "campaigns"

    id: Mapped[UUID] = mapped_column(String(36), primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        String(36), ForeignKey("profiles.id"), nullable=False, index=True,
    )

    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    simulacao_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    data_inicio: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    data_fim: Mapped[date] = mapped_column(Date, nullable=False)
    hora_disparo: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(9, 0),
    )
    """Hora do dia em que o scheduler vai disparar (Leva 5.5). Default 09:00."""

    hora_fim: Mapped[time | None] = mapped_column(Time, nullable=True)
    """Hora do dia em que a campanha deve terminar no `data_fim`.

    Se None, a campanha vai até o fim do dia (23:59:59). Se preenchida,
    o scheduler (Leva 5.5) vai finalizar/cancelar no horário marcado.
    Opcional pra permitir restrições mais finas (ex: campanha de 9h às 18h)."""

    skus_selecionados_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Lista de item_ids selecionados pra entrar na campanha, serializada
    como JSON. None = todos os itens da simulação com fase1_acao != mantido
    (comportamento default — mantém compat). Lista vazia = bloqueia disparo
    no use case."""

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="rascunho", index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Campos futuros — preenchidos a partir da Leva 5.4
    ml_campaign_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    aplicacao_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rollback_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Leva 5.7.1 — soft delete automático 15 dias após data_fim
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Leva 5.9.2 — origem da campanha
    # `local` = criada no app | `ml` = importada via 5.9.1/5.9.2
    origem: Mapped[str] = mapped_column(String(16), nullable=False, default="local")

    # Leva 5.9.4.B — faixa de margem pra detecção de migração ML.
    # NULL = herda do perfil (config.margem_min/max_migracao)
    margem_min_migracao: Mapped[float | None] = mapped_column(nullable=True)
    margem_max_migracao: Mapped[float | None] = mapped_column(nullable=True)

    # Leva 5.11 — flag de renovação (impede criar sucessora duplicada)
    renovada: Mapped[bool] = mapped_column(nullable=False, default=False)


class MigracaoExecutadaModel(Base):
    """Histórico de migrações de SKUs entre campanhas (Leva 5.9.4.C.1).

    Cada linha = 1 operação de adicionar ou remover SKU duma campanha ML.
    Ações em dry_run também são salvas com `dry_run=True` pra auditoria.
    """

    __tablename__ = "migracoes_executadas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("profiles.id"), nullable=False, index=True,
    )
    # Campanha origem local (guarda-chuva). NULL pra ações standalone.
    campanha_origem_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Campanha destino no ML (ex: P-MLB17511030).
    campanha_destino_ml_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campanha_destino_ml_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    campanha_destino_ml_tipo: Mapped[str] = mapped_column(String(32), nullable=False)

    item_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tipo da operação: add | remove
    operacao: Mapped[str] = mapped_column(String(16), nullable=False)
    # Status atual da promoção destino no momento: started | pending
    destino_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Preço/margem calculados (snapshot do momento)
    deal_price: Mapped[float | None] = mapped_column(nullable=True)
    margem_pct_prevista: Mapped[float | None] = mapped_column(nullable=True)

    # Resultado da operação
    sucesso: Mapped[bool] = mapped_column(nullable=False, default=False)
    dry_run: Mapped[bool] = mapped_column(nullable=False, default=False)
    erro_detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


class DescarteMigracaoCacheModel(Base):
    """Cache de descartes do detector de migração (Leva 5.9.4 - opt 4).

    Quando o detector avalia (SKU, promoção) e decide não migrar, salva
    aqui pra pular avaliações repetidas em runs próximos. TTL configurável
    (default 24h) porque ranges ML não mudam frequentemente.

    Chave única lógica: (profile_id, item_id, promotion_id). Próxima
    avaliação do mesmo par dentro do TTL é pulada.
    """

    __tablename__ = "migracao_descartes_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("profiles.id"), nullable=False, index=True,
    )
    item_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    promotion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    motivo: Mapped[str] = mapped_column(String(64), nullable=False)
    """Códigos: 'preco_acima_max', 'preco_abaixo_min', 'margem_pior',
    'margem_fora_faixa', 'sem_custo', 'sem_range', 'item_fetch_falhou'."""

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    expira_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )


class RepricingSnapshotModel(Base):
    """Snapshot por item do botão 'Reprecificar Tudo' (Fatia undo, jun/2026).

    Toda execução do `ApplyRepricingMassaUseCase` agrupa seus PUTs sob uma
    `session_id` (UUID gerado no início). Pra cada PUT bem-sucedido, cria
    uma linha com `preco_anterior` (do snapshot pré-PUT) + `preco_novo`.

    Permite "Reverter última execução": lista as sessões mais recentes,
    olha os snapshots, aplica PUT inverso pra cada um. Sobrevive a restart.

    `revertido_em` marca quando a sessão inteira foi revertida — evita
    reverter de novo a mesma execução.
    """

    __tablename__ = "repricing_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("profiles.id"), nullable=False, index=True,
    )
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    preco_anterior: Mapped[float] = mapped_column(nullable=False)
    preco_novo: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    revertido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class RelatorioDiarioKpisModel(Base):
    """KPIs agregados do relatório diário (Fatia 1 de relatórios, jun/2026).

    Persiste só os 4 números agregados que alimentam comparativos de outros
    dias (delta vs ontem, vs média 7d). O snapshot completo do dia (SKUs,
    sugestões) é regenerado on-demand toda vez que o user pede o PDF — orders
    do passado são estáveis no ML, o ricálculo é barato e evita schema gordo.

    Chave única lógica: (profile_id, dia). Upsert na geração.
    """

    __tablename__ = "relatorios_diarios_kpis"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("profiles.id"), nullable=False, index=True,
    )
    dia: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    receita_bruta: Mapped[float] = mapped_column(nullable=False)
    lucro_liquido: Mapped[float] = mapped_column(nullable=False)
    total_pedidos: Mapped[int] = mapped_column(Integer, nullable=False)
    total_unidades: Mapped[int] = mapped_column(Integer, nullable=False)
    gerado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
