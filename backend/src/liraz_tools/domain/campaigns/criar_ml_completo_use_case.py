"""Use case: criar SELLER_CAMPAIGN no ML JÁ COM os SKUs adicionados (Leva 5.12).

Orquestra dois passos do ML em sequência:
1. Cria a campanha vazia (reusa `CriarSellerCampaignNoMLUseCase`)
2. Pra cada SKU selecionado, faz POST /seller-promotions/items/{id}

Diferente do `criar-ml-direto` puro, este endpoint é o que a UI da Leva 5.12
usa quando o user clica em "Iniciar agora" — quer a campanha PRONTA com
todos os SKUs já dentro, não vazia.

Política de falhas parciais:
- Falha em criar a campanha → propaga exceção (nada criado, nada a fazer)
- Falha em adicionar SKU X → continua tentando os outros e devolve a lista
  de erros. NÃO faz rollback (deletar campanha do ML) porque o user pode
  preferir manter a campanha vazia e tentar adicionar SKUs de novo depois.
  A campanha local fica criada com os SKUs que entraram com sucesso.

Para SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE (tipo padrão criado), o ML ignora
deal_price passado — aplica a regra de desconto da campanha. Então não
pedimos deal_price por item; basta o item_id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.criar_ml_use_case import (
    CriarSellerCampaignNoMLUseCase,
    CriarSellerCampaignResult,
)
from liraz_tools.domain.campaigns.entity import Campaign
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.domain.skus.sugestao_use_case import (
    SugerirDealPricesPorMargemUseCase,
)
from liraz_tools.infrastructure.ml.campaign_skus_apply import (
    DadosMargem,
    aplicar_adicoes_skus_em_campanha,
)
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
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


@dataclass
class ErroAdicaoSku:
    item_id: str
    erro: str


@dataclass
class CriarSellerCampaignCompletoResult:
    """Resultado da criação completa de uma SELLER_CAMPAIGN com SKUs."""

    campaign: Campaign
    ml_campaign_id: str
    ml_status: str
    skus_adicionados: list[str] = field(default_factory=list)
    skus_ja_estavam: list[str] = field(default_factory=list)
    inflados: list[str] = field(default_factory=list)
    revertidos: list[str] = field(default_factory=list)
    reprecificados_20pct: list[str] = field(default_factory=list)
    fallbacks_usados: list[str] = field(default_factory=list)
    # Itens onde o ML retornou 423 LockedEntity mesmo após retries — preço-base
    # ficou inflado (NÃO foi revertido) porque o ML deve adicionar depois.
    pendentes_lock: list[str] = field(default_factory=list)
    erros: list[ErroAdicaoSku] = field(default_factory=list)


class CriarSellerCampaignCompletoUseCase:
    """Cria SELLER_CAMPAIGN no ML + adiciona SKUs em um único fluxo."""

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
        skus_selecionados: list[str],
        deal_prices: dict[str, float] | None = None,
        inflar_precos: dict[str, float] | None = None,
        fallback_deal_prices: dict[str, float] | None = None,
    ) -> CriarSellerCampaignCompletoResult:
        # ─── 1) Cria a campanha (reusa use case existente) ──────────────
        criar_uc = CriarSellerCampaignNoMLUseCase(
            self._profile_repo, self._campaign_repo, self._creds_repo,
        )
        criado: CriarSellerCampaignResult = await criar_uc.execute(
            profile_id, nome=nome,
            data_inicio=data_inicio, data_fim=data_fim,
        )

        if not skus_selecionados:
            logger.info(
                "criar_ml_completo_sem_skus",
                profile_id=str(profile_id),
                ml_campaign_id=criado.ml_campaign_id,
            )
            return CriarSellerCampaignCompletoResult(
                campaign=criado.campaign,
                ml_campaign_id=criado.ml_campaign_id,
                ml_status=criado.ml_status,
            )

        # ─── 2) Pipeline compartilhado: infla (com snapshot) → adiciona com
        #       retry de fallback → descarta negados (reprecifica pro preço de
        #       20% de margem em ERROR_CREDIBILITY, ou reverte ao original).
        #       Mesma lógica do sync-batch (editar campanha existente). ───
        profile = await self._profile_repo.get_by_id(profile_id)
        # Invariante: campanha foi criada no ML logo acima, então está conectado.
        assert profile.ml_user_id is not None
        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: TokenSet) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        # CLAMP: recomputa sugestao server-side pros itens a adicionar pra obter
        # os componentes de custo necessários ao clamp da faixa do ML. Mesma
        # estratégia do sync-batch (campaigns.py). Sem isso o clamp fica off.
        dados_margem_map: dict[str, DadosMargem] = {}
        sugestao_uc = SugerirDealPricesPorMargemUseCase(
            self._profile_repo, self._creds_repo, CostOverridesRepository(),
        )
        sugs = await sugestao_uc.execute(
            profile_id, ml_campaign_id=criado.ml_campaign_id,
            item_ids=skus_selecionados,
            margem_alvo=profile.config.margem_alvo_campanha,
        )
        for s in sugs:
            if (
                s.custo_unit is not None and s.comissao_pct is not None
                and s.aliquota is not None
            ):
                dados_margem_map[s.item_id] = DadosMargem(
                    custo=s.custo_unit, list_cost=s.list_cost,
                    comissao_pct=s.comissao_pct, aliquota=s.aliquota,
                )

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            res_add = await aplicar_adicoes_skus_em_campanha(
                ml,
                ml_campaign_id=criado.ml_campaign_id,
                item_ids=skus_selecionados,
                deal_prices=deal_prices or {},
                inflar_precos=inflar_precos or {},
                fallback_deal_prices=fallback_deal_prices or {},
                dados_margem=dados_margem_map if dados_margem_map else None,
                logger=logger,
            )

        adicionados = res_add.adicionados
        ja_estavam = res_add.ja_estavam
        erros = [
            ErroAdicaoSku(item_id=e["item_id"], erro=f"[{e['operacao']}] {e['erro']}")
            for e in res_add.erros
        ]

        # ─── 3) Atualiza skus_selecionados da campanha local com o que
        #       efetivamente entrou (adicionados + ja_estavam — ambos
        #       estão na campanha do ML agora). Inclui `pendentes_lock`
        #       porque o ML costuma adicionar esses depois de destravar
        #       (preço-base inflado mantido). ─────────────────────────
        skus_finais = (
            adicionados + ja_estavam + res_add.pendentes_lock
        )
        if skus_finais:
            updated_campaign = criado.campaign.model_copy(
                update={"skus_selecionados": skus_finais},
            )
            saved = await self._campaign_repo.update(updated_campaign)
        else:
            saved = criado.campaign

        logger.info(
            "criar_ml_completo_concluido",
            profile_id=str(profile_id),
            ml_campaign_id=criado.ml_campaign_id,
            total_pedidos=len(skus_selecionados),
            adicionados=len(adicionados),
            ja_estavam=len(ja_estavam),
            inflados=len(res_add.inflados),
            revertidos=len(res_add.revertidos),
            reprecificados_20pct=len(res_add.reprecificados_20pct),
            pendentes_lock=len(res_add.pendentes_lock),
            erros=len(erros),
        )

        return CriarSellerCampaignCompletoResult(
            campaign=saved,
            ml_campaign_id=criado.ml_campaign_id,
            ml_status=criado.ml_status,
            skus_adicionados=adicionados,
            skus_ja_estavam=ja_estavam,
            inflados=res_add.inflados,
            revertidos=res_add.revertidos,
            reprecificados_20pct=res_add.reprecificados_20pct,
            fallbacks_usados=res_add.fallbacks_usados,
            pendentes_lock=res_add.pendentes_lock,
            erros=erros,
        )
