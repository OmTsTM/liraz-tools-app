"""Use case: criar SELLER_CAMPAIGN no ML e espelhar localmente (Leva 5.10 Fase 2).

Diferente de `ImportMLCampaignUseCase` (que importa uma campanha que JÁ EXISTE
no ML), aqui CRIAMOS uma campanha do zero:
1. Valida nome único no perfil (local + ML)
2. Chama o ML pra criar a SELLER_CAMPAIGN
3. Salva localmente como entidade com origem='ml' e ml_campaign_id preenchido

A campanha resultante fica disponível na lista de campanhas com a aba
Migração ativa (Leva 5.9.4.C.2.B) — fluxo padrão de guarda-chuva.

Fluxo de erro:
- Datas inválidas → DomainError antes de chamar ML (rápido)
- Nome conflitante local → DomainError sem chamar ML
- ML rejeita (60 dias, nome dup no ML, etc) → propaga exceção tipada
- Falha ao salvar localmente após criar no ML → log com `ml_campaign_id`
  pra recuperação manual (campanha existe no ML mas não no banco)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.entity import Campaign
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotion_items import MLPromotionError
from liraz_tools.infrastructure.ml.promotions_create import (
    CampaignNameConflictError,
    InvalidDatesError,
    StartDateTooFarError,
    criar_seller_campaign,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.repositories.campaign_repository import (
        CampaignRepository,
    )
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )
    from liraz_tools.infrastructure.repositories.profile_repository import (
        SQLAlchemyProfileRepository,
    )

logger = get_logger(__name__)


class CriarMLCampaignError(Exception):
    """Erro de domínio na criação de campanha ML."""


class NomeJaExisteLocalError(CriarMLCampaignError):
    """Já existe campanha local com esse nome neste perfil."""


@dataclass
class CriarSellerCampaignResult:
    campaign: Campaign  # campanha local espelhada
    ml_campaign_id: str
    ml_status: str  # pending | started


class CriarSellerCampaignNoMLUseCase:
    """Cria uma SELLER_CAMPAIGN no ML e espelha localmente."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        campaign_repo: CampaignRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._creds_repo = creds_repo

    async def execute(
        self,
        profile_id: UUID,
        *,
        nome: str,
        data_inicio: date,
        data_fim: date,
    ) -> CriarSellerCampaignResult:
        # 1) Validações rápidas (sem chamar ML)
        if data_fim <= data_inicio:
            raise InvalidDatesError(
                f"Data de fim ({data_fim}) deve ser maior que início "
                f"({data_inicio})."
            )

        hoje = date.today()
        if data_inicio < hoje:
            raise InvalidDatesError(
                f"Data de início ({data_inicio}) não pode ser no passado."
            )

        # Limite do ML — antecipamos pra dar mensagem melhor
        dias_no_futuro = (data_inicio - hoje).days
        if dias_no_futuro > 60:
            raise StartDateTooFarError(
                f"Data de início está a {dias_no_futuro} dias no futuro. "
                f"O ML aceita no máximo 60 dias."
            )

        # 2) Valida unicidade local
        nomes_existentes = await self._campaign_repo.list_nomes_do_perfil(
            profile_id,
        )
        if nome.strip() in {n.strip() for n in nomes_existentes}:
            raise NomeJaExisteLocalError(
                f"Já existe uma campanha local com o nome '{nome}' "
                f"neste perfil."
            )

        # 3) Carrega perfil
        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            raise CriarMLCampaignError(
                "Perfil não está conectado ao ML."
            )

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: TokenSet) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        # 4) Cria no ML
        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            try:
                ml_resp = await criar_seller_campaign(
                    ml, nome=nome,
                    start_date=data_inicio, finish_date=data_fim,
                )
            except (
                StartDateTooFarError, CampaignNameConflictError,
                InvalidDatesError, MLPromotionError,
            ):
                raise

        ml_campaign_id = ml_resp["id"]
        ml_status = ml_resp.get("status", "pending")

        # 5) Espelha localmente
        campaign = Campaign(
            id=uuid4(),
            profile_id=profile_id,
            nome=nome,
            data_inicio=data_inicio,
            data_fim=data_fim,
            skus_selecionados=[],  # vazia no início; SKUs adicionados depois
            status="ativa" if ml_status == "started" else "agendada",
            ml_campaign_id=ml_campaign_id,
            origem="ml",  # criada via API, mas vive no ML
        )

        try:
            saved = await self._campaign_repo.create(campaign)
        except Exception:
            # Falha ao salvar localmente, mas JÁ CRIAMOS NO ML.
            # Loga com ml_campaign_id pra rastreio (campanha está no ML
            # mas não no banco — user pode importá-la via fluxo de import).
            logger.exception(
                "criar_ml_campaign_falha_persistencia",
                ml_campaign_id=ml_campaign_id,
                profile_id=str(profile_id),
                hint=(
                    "Campanha criada no ML mas não persistida localmente. "
                    "Use o fluxo de import com este ml_campaign_id pra "
                    "espelhar manualmente."
                ),
            )
            raise

        logger.info(
            "criar_ml_campaign_completo",
            profile_id=str(profile_id),
            campaign_id=str(saved.id),
            ml_campaign_id=ml_campaign_id,
            nome=nome,
            ml_status=ml_status,
        )

        return CriarSellerCampaignResult(
            campaign=saved,
            ml_campaign_id=ml_campaign_id,
            ml_status=ml_status,
        )
