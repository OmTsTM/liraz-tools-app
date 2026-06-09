"""Use cases pra hidratar e filtrar items pelo contexto de campanha (Leva 5.9.3).

Dois cenários:
1. **Hidratação** (`HidrarItemsUseCase`): dado lista de item_ids, retorna info
   básica (sku, titulo, preço) lendo do cache do fee_report. Usado pra mostrar
   "Anúncios incluídos" no detail de uma campanha.

2. **Elegibilidade** (`ListSkusElegiveisUseCase`): lista TODOS items do
   catálogo com flag `em_outra_campanha`. Usado pelo SkuSelectorDialog quando
   está criando/editando uma campanha sem simulação — substitui o critério
   antigo (fase1_acao != "mantido").

Ambos leem do cache do fee_report — se cache estiver frio, recomendamos abrir
o relatório de margens primeiro pra hidratar. Frontend mostra alerta se cache
está vazio.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.entity import Campaign
from liraz_tools.domain.campaigns.use_cases import CampaignNotEditableError
from liraz_tools.domain.profiles.repository import ProfileRepository
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.items_fetcher import fetch_items_basic_info
from liraz_tools.infrastructure.ml.promotions_lookup import (
    montar_mapa_items_em_promocao,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.pricing_cache import FeeReportCache

logger = get_logger(__name__)


class HidrarItemsUseCase:
    """Dado lista de item_ids, retorna info básica de cada um.

    **Estratégia em camadas**:
    1. Lê cache do fee_report (rápido, tem todas as métricas calculadas)
    2. Pra items missing no cache, faz multiget no ML (`GET /items?ids=...`)
       em batches paralelos de 20 ids cada
    3. Items que falharem na consulta ML retornam stub mínimo (só item_id)

    O cache do fee_report ainda é vantagem porque traz margem_liquida_pct
    e modalidade calculadas direito. ML só dá info básica (sku, titulo,
    preço, listing_type_id). Pra missing, frontend renderiza com o que tem.
    """

    def __init__(
        self,
        cache: FeeReportCache,
        profile_repo: ProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._cache = cache
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo

    async def execute(
        self, profile_id: UUID, item_ids: list[str],
    ) -> list[dict[str, Any]]:
        # 1) Cache do fee_report — primeira fonte
        cached_report = self._cache.get(profile_id)
        info_por_id: dict[str, dict[str, Any]] = {}
        if cached_report is not None:
            for line in cached_report.listings:
                info_por_id[line.item_id] = {
                    "item_id": line.item_id,
                    "sku": line.sku,
                    "titulo": line.title,
                    "preco": line.preco,
                    "modalidade": line.modalidade,
                    "margem_liquida_pct": line.margem_liquida_percentual,
                }

        # 2) Items que faltam: consultamos o ML diretamente
        missing = [iid for iid in item_ids if iid not in info_por_id]
        if missing:
            profile = await self._profile_repo.get_by_id(profile_id)
            if profile.status.value == "connected" and profile.ml_user_id is not None:
                try:
                    creds = self._creds_repo.get_app_credentials(profile.slug)
                    tokens = self._creds_repo.get_tokens(
                        profile.slug, profile.ml_user_id,
                    )

                    async def save_refreshed(new_tokens: Any) -> None:
                        self._creds_repo.save_tokens(profile.slug, new_tokens)

                    async with MLClient(
                        creds, tokens, on_tokens_refreshed=save_refreshed,
                    ) as ml:
                        from_ml = await fetch_items_basic_info(ml, missing)
                    # Mescla no info_por_id (ML não tem margem_liquida_pct)
                    for iid, fields in from_ml.items():
                        info_por_id[iid] = {
                            **fields,
                            "margem_liquida_pct": None,
                        }
                except Exception as e:
                    logger.warning(
                        "items_ml_fetch_failed",
                        profile_id=str(profile_id),
                        missing_count=len(missing),
                        error=str(e),
                    )
            else:
                logger.info(
                    "items_ml_skip_not_connected",
                    profile_id=str(profile_id),
                    missing_count=len(missing),
                )

        # 3) Monta resposta na ordem solicitada
        result = []
        for item_id in item_ids:
            if item_id in info_por_id:
                result.append(info_por_id[item_id])
            else:
                # Stub mínimo — item não está nem no cache nem foi possível
                # consultar (loja desconectada, item deletado, etc)
                result.append({
                    "item_id": item_id,
                    "sku": None,
                    "titulo": None,
                    "preco": None,
                    "modalidade": None,
                    "margem_liquida_pct": None,
                })
        logger.info(
            "items_hidratados",
            profile_id=str(profile_id),
            pedidos=len(item_ids),
            via_cache=sum(
                1 for iid in item_ids
                if cached_report and any(
                    line.item_id == iid for line in cached_report.listings
                )
            ),
            via_ml=len(missing) - sum(1 for iid in missing if iid not in info_por_id),
        )
        return result


class ListSkusElegiveisUseCase:
    """Lista TODOS items do catálogo com flag de elegibilidade pra campanha.

    "Elegível" significa: item NÃO está participando AGORA em nenhuma
    promoção do ML (com exceção da própria campanha, se passada via
    `exclude_campaign_ml_id` — útil pra edição).

    Retorna lista de dicts com:
    - item_id, sku, titulo, preco, modalidade, margem_liquida_pct
    - em_outra_campanha: bool
    - nomes_outras_campanhas: list[str]

    Se cache do fee_report está vazio, retorna [] — frontend pede pro user
    abrir o relatório de margens primeiro.
    """

    def __init__(
        self,
        profile_repo: ProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        cache: FeeReportCache,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._cache = cache

    async def execute(
        self,
        profile_id: UUID,
        exclude_campaign_ml_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cached = self._cache.get(profile_id)
        if cached is None:
            logger.warning(
                "skus_eligible_cache_miss",
                profile_id=str(profile_id),
            )
            return []

        # Mapa de items em campanhas ativas (started/active no item)
        profile = await self._profile_repo.get_by_id(profile_id)
        mapa: dict[str, list[str]] = {}
        if profile.status.value == "connected" and profile.ml_user_id is not None:
            try:
                creds = self._creds_repo.get_app_credentials(profile.slug)
                tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

                async def save_refreshed(new_tokens: Any) -> None:
                    self._creds_repo.save_tokens(profile.slug, new_tokens)

                async with MLClient(
                    creds, tokens, on_tokens_refreshed=save_refreshed,
                ) as ml:
                    mapa = await montar_mapa_items_em_promocao(
                        ml, profile.ml_user_id,
                    )
            except Exception as e:
                logger.warning(
                    "skus_eligible_promotions_failed",
                    profile_id=str(profile_id),
                    error=str(e),
                )

        # Quando estamos editando uma campanha ML, queremos EXCLUIR o nome
        # dela do mapa pra não marcar seus SKUs como "em outra campanha".
        # Isso permite que o user "veja" eles como elegíveis (já estão lá).
        # Como exclude_campaign_ml_id é o id da promoção, mas o mapa tem
        # nomes... precisamos resolver. Por simplicidade, frontend filtra.
        # O backend só passa o flag bruto.
        _ = exclude_campaign_ml_id  # reservado pra evoluções futuras

        result = []
        for line in cached.listings:
            nomes = mapa.get(line.item_id, [])
            result.append({
                "item_id": line.item_id,
                "sku": line.sku,
                "titulo": line.title,
                "preco": line.preco,
                "modalidade": line.modalidade,
                "margem_liquida_pct": line.margem_liquida_percentual,
                "em_outra_campanha": len(nomes) > 0,
                "nomes_outras_campanhas": nomes,
            })

        logger.info(
            "skus_eligible_listed",
            profile_id=str(profile_id),
            total=len(result),
            em_campanha=sum(1 for r in result if r["em_outra_campanha"]),
        )
        return result


class UpdateCampaignSkusUseCase:
    """Atualiza apenas a lista `skus_selecionados` de uma campanha.

    Operação mais permissiva que `UpdateCampaignUseCase` — funciona em
    qualquer campanha LOCAL editável (rascunho/agendada). Pra campanhas
    com origem='ml' bloqueia até a Leva 5.9.4 (que vai propagar mudanças
    ao ML via PUT /seller-promotions/items).
    """

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(
        self,
        profile_id: UUID,
        campaign_id: UUID,
        skus_selecionados: list[str] | None,
    ) -> Campaign:
        existing = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        if existing.origem == "ml":
            raise CampaignNotEditableError(
                "edição de SKUs em campanha ML virá na Leva 5.9.4 — "
                "vai propagar mudanças via PUT /seller-promotions/items"
            )

        if not existing.is_editable:
            raise CampaignNotEditableError(
                f"campanha em status '{existing.status}' não é editável"
            )

        updated = existing.model_copy(update={"skus_selecionados": skus_selecionados})
        return await self._campaign_repo.update(updated)
