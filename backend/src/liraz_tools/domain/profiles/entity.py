"""Entidade Profile — representa uma loja conectada ao app.

Modelo puro do domínio, independente de persistência ou framework HTTP.
A camada de infraestrutura (SQLAlchemy) traduz isso pra/de tabela.
A camada de API (FastAPI) traduz isso pra/de JSON.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileStatus(StrEnum):
    """Estado de um perfil."""

    DRAFT = "draft"           # criado mas ainda não autorizado no ML
    CONNECTED = "connected"   # OAuth completo, pronto pra uso
    DISCONNECTED = "disconnected"  # OAuth expirou ou foi revogado
    ARCHIVED = "archived"     # soft delete


class ProfileConfig(BaseModel):
    """Configurações específicas de uma loja."""

    model_config = ConfigDict(frozen=False)

    # Imposto: decimal 0-1. Ex: 0.08 = 8%.
    aliquota_imposto: float = Field(default=0.0, ge=0.0, le=1.0)

    # CEP destino default pra cálculo de frete (só dígitos)
    cep_destino: str = Field(default="01310100", min_length=8, max_length=8)

    # Path da planilha de custos (relativo ao _shared/ ou absoluto)
    custos_xlsx_path: str | None = Field(default=None)

    # Path da planilha de TARIFAS REAIS por anúncio (alimentada pela extensão
    # Chrome que raspa o painel ML). Opcional — se ausente, o calculator usa
    # o teto teórico (6,75 / 8,55) como antes. Quando presente, override
    # substitui o valor calculado E pula a chamada `/items/{id}/shipping_options`
    # (economiza ~1 call por item + evita o 424 flaky do ML).
    tarifas_ml_xlsx_path: str | None = Field(default=None)

    # Estratégia de reprecificação
    # margem_alvo_campanha (Q2 da planilha) = margem do PREÇO FINAL DE VENDA (P).
    # A campanha infla por cima e desconta de volta a P (não come margem real).
    margem_alvo_campanha: float = Field(default=0.20, ge=0.0, le=1.0)

    # R2 (planilha): margem mínima INVIOLÁVEL. Piso usado na escada Filosofia B
    # (só fica em R$78,99 se a margem lá >= R2; senão sobe garantindo >= R2).
    margem_minima: float = Field(default=0.15, ge=0.0, le=1.0)

    # T2 (planilha): % de inflação pro preço de campanha. Publica P*(1+T2) e
    # desconta de volta a P. Editável por perfil (padrão 20%).
    pct_inflacao_campanha: float = Field(default=0.20, ge=0.0, le=1.0)

    # OBSOLETOS (modelo antigo de reprecificação a 50% — substituído pela
    # Filosofia B que mira margem_alvo_campanha como preço final). Mantidos só
    # por compatibilidade de dados/desserialização; o cálculo não os usa mais.
    margem_alvo_aumento: float = Field(default=0.50, ge=0.0, le=1.0)
    limite_margem_aumento: float = Field(default=0.30, ge=0.0, le=1.0)

    # Leva 5.13 — quebra de frete grátis via degrau R$ 79.
    # Quando o deal_price calculado pra atingir a margem-alvo cair entre
    # R$ 79 (limite_sem_tarifa, fixo no calculator.py) e este teto editável,
    # o app tenta uma estratégia agressiva: força o deal pra R$ 78,99 (abaixo
    # do degrau), o que retira o frete grátis subsidiado pelo vendedor e
    # entra na tarifa fixa de R$ 6,75. Resultado: desconto mais atrativo
    # pro cliente sem perda de margem real. Se o ML rejeitar com
    # ERROR_CREDIBILITY_DISCOUNTED_PRICE no momento da adição, o app faz
    # fallback automático pro deal_price conservador (>=79 com frete).
    #
    # Default R$ 85: tipicamente o intervalo onde a estratégia compensa.
    # Aumente pra ser mais agressivo, diminua pra ser mais conservador.
    # Setar igual a R$ 79 desativa a estratégia.
    teto_quebra_frete_gratis: float = Field(default=85.00, ge=79.00, le=200.00)

    # Faixa de margem aceitável pra migração entre campanhas (Leva 5.9.4).
    # SKUs que se encaixarem nessa faixa em campanhas do ML são candidatos
    # a migrar pra lá; os que não, ficam na guarda-chuva nossa.
    # Default 15-20%: típico pra campanhas ML que oferecem comissão reduzida
    # ou exposição extra. Margem na guarda-chuva geralmente é maior (ex 20%+),
    # e topa-se abrir mão de margem em troca dos benefícios da campanha ML.
    margem_min_migracao: float = Field(default=0.15, ge=0.0, le=1.0)
    margem_max_migracao: float = Field(default=0.20, ge=0.0, le=1.0)

    # Leva 5.9.4.C — execução automática de migrações.
    migracao_automatica_ativa: bool = Field(default=False)
    """Se True, scheduler (C.3) executa migrações periodicamente. Default
    False — usuário liga só depois de testar manualmente."""

    migracao_dry_run: bool = Field(default=True)
    """Se True, executor só simula (loga decisões) sem chamar POST/DELETE
    no ML. Default True por segurança — usuário desliga quando confiar."""

    migracao_intervalo_horas: int = Field(default=6, ge=1, le=24)
    """Intervalo entre varreduras do scheduler de migração (Leva C.3)."""

    # Relatório diário automático (ago/2026 — Fatia 2).
    relatorio_diario_ativo: bool = Field(default=False)
    """Se True, o scheduler de relatórios gera 1 PDF por dia (do dia anterior)
    no horário configurado. Default False — usuário liga depois de testar
    manualmente via endpoint `gerar-agora`."""

    relatorio_diario_hora_brt: str = Field(default="07:00", pattern=r"^\d{2}:\d{2}$")
    """Horário em BRT (HH:MM) pra gerar o relatório do dia anterior. Default
    07:00 — manhã antes de operar."""

    @field_validator("cep_destino", mode="before")
    @classmethod
    def _strip_cep(cls, v: str) -> str:
        """Remove hífens e espaços do CEP antes de validar."""
        if not isinstance(v, str):
            return v
        return re.sub(r"[^0-9]", "", v)

    @field_validator(
        "aliquota_imposto", "margem_alvo_aumento",
        "limite_margem_aumento", "margem_alvo_campanha",
        mode="before",
    )
    @classmethod
    def _saneia_pct_existentes(cls, v: object) -> object:
        """Substitui None/NaN/inf pelo default (0.0 ou definido por campo).
        Defesa contra dados corrompidos vindos do frontend antigo.
        Como esses campos têm defaults variados, devolvemos None aqui pra
        deixar o Pydantic aplicar o default declarado.
        """
        if v is None:
            return None  # Pydantic não aceita None aqui, mas valida abaixo
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            # NaN/inf não passam — força default via raise que retomamos
            # com model_validator. Por simplicidade, retorna 0.0 que é
            # válido pra todos esses campos (ge=0.0).
            return 0.0
        return v

    @field_validator("margem_min_migracao", mode="before")
    @classmethod
    def _saneia_margem_min(cls, v: object) -> object:
        """Substitui NaN/None pelo default 0.15."""
        if v is None:
            return 0.15
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return 0.15
        return v

    @field_validator("margem_max_migracao", mode="before")
    @classmethod
    def _saneia_margem_max(cls, v: object) -> object:
        """Substitui NaN/None pelo default 0.20."""
        if v is None:
            return 0.20
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return 0.20
        return v

    @field_validator("margem_minima", mode="before")
    @classmethod
    def _saneia_margem_minima(cls, v: object) -> object:
        """Substitui NaN/None pelo default 0.15 (R2)."""
        if v is None:
            return 0.15
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return 0.15
        return v

    @field_validator("pct_inflacao_campanha", mode="before")
    @classmethod
    def _saneia_pct_inflacao(cls, v: object) -> object:
        """Substitui NaN/None pelo default 0.20 (T2)."""
        if v is None:
            return 0.20
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return 0.20
        return v


class Profile(BaseModel):
    """Uma loja Mercado Livre conectada (ou em processo)."""

    model_config = ConfigDict(frozen=False)

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=80, description="Nome de exibição da loja.")
    slug: str = Field(min_length=1, max_length=80, description="Identificador URL-safe.")
    status: ProfileStatus = ProfileStatus.DRAFT
    config: ProfileConfig = Field(default_factory=ProfileConfig)

    # Dados do ML quando conectado
    ml_user_id: int | None = None
    ml_nickname: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("nome não pode ser vazio")
        return cleaned

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError("slug deve conter só letras minúsculas, números, '-' e '_'")
        return v

    @classmethod
    def create_draft(cls, name: str) -> Self:
        """Cria um perfil novo em estado DRAFT (sem OAuth ainda)."""
        return cls(name=name, slug=_make_slug(name))

    def is_active_for_operations(self) -> bool:
        """Pode ser usado pra operações reais (não é draft nem arquivado)."""
        return self.status == ProfileStatus.CONNECTED

    def archive(self) -> None:
        """Marca como arquivado (soft delete)."""
        self.status = ProfileStatus.ARCHIVED
        self.archived_at = datetime.now(UTC)
        self.updated_at = self.archived_at

    def unarchive_to_connected(self) -> None:
        """Restaura como CONNECTED — quando ainda tem credenciais válidas."""
        self.status = ProfileStatus.CONNECTED
        self.archived_at = None
        self.updated_at = datetime.now(UTC)

    def unarchive_to_draft(self) -> None:
        """Restaura como DRAFT — quando perdeu credenciais e precisa reconfigurar."""
        self.status = ProfileStatus.DRAFT
        self.archived_at = None
        self.ml_user_id = None
        self.ml_nickname = None
        self.updated_at = datetime.now(UTC)

    def mark_connected(self, ml_user_id: int, ml_nickname: str) -> None:
        """Chamado quando OAuth completa com sucesso."""
        self.status = ProfileStatus.CONNECTED
        self.ml_user_id = ml_user_id
        self.ml_nickname = ml_nickname
        self.updated_at = datetime.now(UTC)

    def mark_disconnected(self) -> None:
        """Chamado quando o token é revogado ou expira sem refresh possível."""
        self.status = ProfileStatus.DISCONNECTED
        self.updated_at = datetime.now(UTC)


def _make_slug(name: str) -> str:
    """Converte nome livre em slug. Ex: 'Toque Rico!' -> 'toque-rico'."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if not s:
        return "store"
    return s[:60]
