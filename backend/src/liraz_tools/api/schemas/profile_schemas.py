"""DTOs da API HTTP — separados das entidades de domínio.

Por que separar: o que aparece no JSON da API pode ser diferente do que
existe no domínio. Aqui você pode omitir campos sensíveis, formatar
datas, expor IDs como string, etc.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from liraz_tools.domain.profiles.entity import Profile, ProfileConfig, ProfileStatus


class ProfileConfigDTO(BaseModel):
    """DTO de ProfileConfig — espelha a entidade."""

    aliquota_imposto: float = 0.0
    cep_destino: str = "01310100"
    custos_xlsx_path: str | None = None
    # Path da planilha de tarifas reais por anúncio (extensão Chrome).
    # Opcional — quando presente, o calculator usa os valores reais por MLB
    # em vez do teto teórico (R$ 8,55) e pula a chamada /shipping_options.
    tarifas_ml_xlsx_path: str | None = None
    margem_alvo_aumento: float = 0.50
    limite_margem_aumento: float = 0.30
    # Parâmetros do modelo passo3 (mai/2026):
    margem_alvo_campanha: float = 0.20   # Q2 — margem do preço final de venda
    margem_minima: float = 0.15          # R2 — piso inviolável (escada Filosofia B)
    pct_inflacao_campanha: float = 0.20  # T2 — % de inflação pra vitrine de campanha
    teto_quebra_frete_gratis: float = 85.00
    # Migração automática (Leva 5.9.4)
    margem_min_migracao: float = 0.15
    margem_max_migracao: float = 0.20
    migracao_automatica_ativa: bool = False
    migracao_dry_run: bool = True
    migracao_intervalo_horas: int = 6
    # Adesão automática de SKUs novos a campanhas locais (ago/2026)
    adesao_automatica_ativa: bool = False
    # Relatório diário automático (Fatia 2)
    relatorio_diario_ativo: bool = False
    relatorio_diario_hora_brt: str = "07:00"

    @classmethod
    def from_domain(cls, config: ProfileConfig) -> ProfileConfigDTO:
        return cls(**config.model_dump())

    def to_domain(self) -> ProfileConfig:
        return ProfileConfig(**self.model_dump())


class ProfileResponse(BaseModel):
    """Resposta da API ao retornar um perfil."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    status: ProfileStatus
    config: ProfileConfigDTO
    ml_user_id: int | None = None
    ml_nickname: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    @classmethod
    def from_domain(cls, profile: Profile) -> ProfileResponse:
        return cls(
            id=profile.id,
            name=profile.name,
            slug=profile.slug,
            status=profile.status,
            config=ProfileConfigDTO.from_domain(profile.config),
            ml_user_id=profile.ml_user_id,
            ml_nickname=profile.ml_nickname,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            archived_at=profile.archived_at,
        )


class CreateProfileRequest(BaseModel):
    """Body do POST /api/profiles."""

    name: str = Field(min_length=1, max_length=80, description="Nome da loja.")


class UpdateProfileRequest(BaseModel):
    """Body do PATCH /api/profiles/{id}."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    config: ProfileConfigDTO | None = None


class ListProfilesResponse(BaseModel):
    """Resposta do GET /api/profiles."""

    items: list[ProfileResponse]
    total: int


class ActiveProfileResponse(BaseModel):
    """Resposta do GET /api/profiles/active."""

    profile: ProfileResponse | None = Field(
        default=None,
        description="None se nenhum perfil estiver ativo (ex: app recém-instalado).",
    )
