"""Use cases pra editor de margens dentro de uma campanha SELLER_CAMPAIGN.

- `ListarMargensCampanhaUseCase`: pra cada SKU da campanha, retorna deal_price
  ativo, preço-base (`original_price`), desconto%, margem líquida real e os
  limites `ml_min`/`ml_max` do ML. Permite à UI mostrar o que está sangrando
  margem dentro da campanha sem o usuário ter que sair pra outra tela.

- `EditarMargemSkusCampanhaUseCase`: recebe uma lista de SKUs e uma margem
  alvo (%), calcula o novo `deal_price` por item pela fórmula passo3, e
  aplica via POST /seller-promotions/items (upsert do deal). Quando o deal
  calculado ultrapassa o `ml_max`, o item é **pulado** (decisão de design
  conservadora — evita inflar U automaticamente).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotion_items import (
    ItemNotInCampaignError,
    MLPromotionError,
    adicionar_sku_em_campanha,
    remover_sku_de_campanha,
)
from liraz_tools.infrastructure.ml.promotions_lookup import (
    buscar_limites_credibilidade,
)
from liraz_tools.infrastructure.pricing.calculator import calcular_taxas_anuncio
from liraz_tools.infrastructure.pricing.costs_loader import (
    buscar_custo,
    carregar_custos,
    carregar_tarifas_ml,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.repricing_calculator import (
    DEGRAU_TARIFA_FIXA,
    margem_liquida_pct,
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
class MargemItemResult:
    """Linha de cada SKU pra UI."""

    item_id: str
    sku: str | None
    titulo: str | None
    # Preços ativos no ML (None quando o item não está mais na promoção)
    preco_base: float | None  # original_price (= U inflado)
    deal_price: float | None  # price (= o que o cliente paga)
    desconto_pct: float | None  # 1 - deal/original
    # Margem líquida real calculada no deal_price
    margem_pct: float | None
    # Limites de credibilidade do ML pra esse item nessa campanha
    ml_min: float | None
    ml_max: float | None
    # Componentes do cálculo (útil pra debug + pro editor calcular dp novo)
    custo: float | None
    comissao_pct: float | None
    tarifa_fixa: float | None  # H = custo fixo regime <79
    frete: float | None       # I = frete regime ≥79
    aliquota: float | None
    erro: str | None


@dataclass
class EditarMargemItemResult:
    item_id: str
    sku: str | None
    deal_anterior: float | None
    deal_novo: float | None
    margem_anterior_pct: float | None
    margem_nova_pct: float | None
    status: str   # "aplicado" | "pulado_ml_max" | "pulado_ml_min" | "erro" | "sem_mudanca"
    motivo: str | None


class CampaignNaoNoMLError(Exception):
    """Campanha local sem ml_campaign_id - não dá pra editar margens via ML."""


@dataclass
class _ItemTaxas:
    custo: float
    comissao_pct: float
    tarifa_fixa: float
    frete: float
    list_cost: float | None


async def _calcular_componentes(
    *,
    ml: MLClient,
    item_id: str,
    sku: str | None,
    deal_price: float,
    cep: str,
    freight_cache: FreightCache,
    custos_map: dict[str, float],
    overrides: dict[str, float] | None,
    tarifas_overrides: dict[str, dict[str, float]],
) -> tuple[_ItemTaxas | None, str | None]:
    """Busca custo + comissão + tarifa fixa + frete pra um item.

    Comissão vem de `/listing_prices` no `deal_price` (não no preço-base) — é
    nesse preço que o cliente compra e a comissão incide.
    """
    custo, _ = buscar_custo(sku, item_id, custos_map, overrides)
    if custo is None or custo <= 0:
        return None, "sem custo válido no custos.xlsx"

    try:
        taxas = await calcular_taxas_anuncio(
            ml=ml, item_id=item_id, cep_destino=cep,
            freight_cache=freight_cache,
            preco_simulado=deal_price,
            tarifas_override=tarifas_overrides,
        )
    except Exception as e:
        return None, f"falha ao consultar taxas: {e}"
    if "erro" in taxas:
        return None, f"taxas indisponíveis: {taxas['erro']}"

    comissao_pct = (taxas.get("comissao_percentual", 0.0) or 0.0) / 100.0
    list_cost = taxas.get("list_cost")
    # Overrides por regime (do passo3)
    lc_below = taxas.get("list_cost_below_79_override")
    lc_above = taxas.get("list_cost_above_79_override")
    # Tarifa fixa (regime <79): se override tem cf, usa; senão teto/lc cru.
    if lc_below is not None:
        tarifa_fixa = round(float(lc_below), 2)
    elif list_cost is not None:
        tarifa_fixa = min(float(list_cost), 8.55)
    else:
        tarifa_fixa = 8.55
    # Frete (regime ≥79): se override tem fr, usa; senão list_cost.
    if lc_above is not None:
        frete = round(float(lc_above), 2)
    elif list_cost is not None:
        frete = float(list_cost)
    else:
        frete = 0.0

    return _ItemTaxas(
        custo=float(custo), comissao_pct=comissao_pct,
        tarifa_fixa=tarifa_fixa, frete=frete, list_cost=list_cost,
    ), None


class ListarMargensCampanhaUseCase:
    """Lista os SKUs de uma campanha com deal/original/desconto/margem/limites."""

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
        self, profile_id: UUID, campaign_id: UUID,
    ) -> list[MargemItemResult]:
        profile = await self._profile_repo.get_by_id(profile_id)
        campaign = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        if not campaign.ml_campaign_id:
            raise CampaignNaoNoMLError(
                "campanha ainda não foi disparada no ML — sem deal_prices "
                "pra consultar. Aguarde o disparo ou veja a simulação."
            )

        skus = list(campaign.skus_selecionados or [])
        if not skus:
            return []

        if not profile.config.custos_xlsx_path:
            raise CampaignNaoNoMLError(
                "perfil sem custos.xlsx configurado — não dá pra calcular margens"
            )
        if profile.ml_user_id is None:
            raise CampaignNaoNoMLError("perfil não conectado ao ML")
        aliquota = profile.config.aliquota_imposto
        custos_map = carregar_custos(profile.config.custos_xlsx_path)
        tarifas_overrides = carregar_tarifas_ml(
            profile.config.tarifas_ml_xlsx_path,
        )
        cep = profile.config.cep_destino
        freight_cache = FreightCache(profile.slug)

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        # Mapa de SKU pra cada MLB (carregamos do /items em batch via taxas)
        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
            max_concurrent=12,
        ) as ml:
            # 1) Limites + deal/original ativos no ML (1 chamada por item, paralelo)
            limites = await buscar_limites_credibilidade(
                ml, promotion_id=campaign.ml_campaign_id,
                promotion_type="SELLER_CAMPAIGN", item_ids=skus,
            )

            # 2) Pra cada item: taxas (custo+comissão+tarifa+frete)
            async def _calc_um(item_id: str) -> MargemItemResult:
                info = limites.get(item_id) or {}
                deal = info.get("price")
                orig = info.get("original")
                ml_min = info.get("min")
                ml_max = info.get("max")

                # Pra info do SKU/titulo + comissão, usa /items + listing_prices.
                # Se deal_price não tá disponível (item ausente da promoção),
                # usa orig pra calcular comissão; se ambos faltam, pula.
                preco_pra_comissao = deal or orig
                if not preco_pra_comissao:
                    return MargemItemResult(
                        item_id=item_id, sku=None, titulo=None,
                        preco_base=None, deal_price=None, desconto_pct=None,
                        margem_pct=None, ml_min=None, ml_max=None,
                        custo=None, comissao_pct=None, tarifa_fixa=None,
                        frete=None, aliquota=aliquota,
                        erro="item não encontrado na promoção do ML",
                    )

                # buscar sku/titulo + chamar taxas
                try:
                    item_raw = await ml.get(f"/items/{item_id}", max_retries=3)
                except Exception as e:
                    return MargemItemResult(
                        item_id=item_id, sku=None, titulo=None,
                        preco_base=orig, deal_price=deal, desconto_pct=None,
                        margem_pct=None, ml_min=ml_min, ml_max=ml_max,
                        custo=None, comissao_pct=None, tarifa_fixa=None,
                        frete=None, aliquota=aliquota,
                        erro=f"falha ao buscar /items/{item_id}: {e}",
                    )
                sku = item_raw.get("seller_custom_field")
                if not sku:
                    for a in item_raw.get("attributes") or []:
                        if a.get("id") == "SELLER_SKU":
                            sku = a.get("value_name")
                            break
                titulo = item_raw.get("title")

                taxas, err = await _calcular_componentes(
                    ml=ml, item_id=item_id, sku=sku,
                    deal_price=preco_pra_comissao, cep=cep,
                    freight_cache=freight_cache,
                    custos_map=custos_map, overrides=None,
                    tarifas_overrides=tarifas_overrides,
                )
                if err is not None or taxas is None:
                    return MargemItemResult(
                        item_id=item_id, sku=sku, titulo=titulo,
                        preco_base=orig, deal_price=deal,
                        desconto_pct=(
                            round((1 - deal/orig) * 100, 2)
                            if deal and orig else None
                        ),
                        margem_pct=None, ml_min=ml_min, ml_max=ml_max,
                        custo=None, comissao_pct=None, tarifa_fixa=None,
                        frete=None, aliquota=aliquota, erro=err,
                    )

                # Calcula margem no deal_price
                if deal and deal > 0:
                    m = margem_liquida_pct(
                        deal, custo=taxas.custo,
                        tarifa_pct=taxas.comissao_pct,
                        list_cost=taxas.list_cost, aliquota=aliquota,
                        sem_teto_custo_fixo=True,
                        list_cost_below_79=taxas.tarifa_fixa,
                        list_cost_above_79=taxas.frete,
                    )
                    margem_pct = round(m * 100, 2)
                else:
                    margem_pct = None

                desconto = (
                    round((1 - deal / orig) * 100, 2)
                    if deal and orig else None
                )

                return MargemItemResult(
                    item_id=item_id, sku=sku, titulo=titulo,
                    preco_base=orig, deal_price=deal, desconto_pct=desconto,
                    margem_pct=margem_pct,
                    ml_min=ml_min, ml_max=ml_max,
                    custo=round(taxas.custo, 2),
                    comissao_pct=round(taxas.comissao_pct, 4),
                    tarifa_fixa=round(taxas.tarifa_fixa, 2),
                    frete=round(taxas.frete, 2),
                    aliquota=aliquota,
                    erro=None,
                )

            results = await asyncio.gather(*[_calc_um(s) for s in skus])

        return list(results)


class EditarMargemSkusCampanhaUseCase:
    """Edita o `deal_price` de N SKUs pra atingir uma margem alvo.

    Pra cada item:
      1. Carrega componentes (custo, comissão, tarifa).
      2. Calcula `deal_novo = (custo + tarifa_no_regime) / (1 - G - K - margem_alvo)`.
      3. Se `deal_novo > ml_max`: **pula** o item (decisão: não inflar U).
      4. Se `deal_novo < ml_min`: ajusta pro `ml_min` (margem fica acima do alvo).
      5. POST /seller-promotions/items/{id} com o novo deal (upsert).
    """

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
        self, profile_id: UUID, campaign_id: UUID,
        *, item_ids: list[str], margem_alvo: float,
    ) -> list[EditarMargemItemResult]:
        if not item_ids:
            return []
        if not (0.01 <= margem_alvo <= 0.80):
            raise ValueError(
                f"margem_alvo deve estar entre 1% e 80% (recebi "
                f"{margem_alvo * 100:.1f}%)"
            )

        profile = await self._profile_repo.get_by_id(profile_id)
        campaign = await self._campaign_repo.get_by_id(profile_id, campaign_id)
        if not campaign.ml_campaign_id:
            raise CampaignNaoNoMLError(
                "campanha sem ml_campaign_id - não foi disparada no ML"
            )
        ml_promo_id: str = campaign.ml_campaign_id

        if not profile.config.custos_xlsx_path:
            raise CampaignNaoNoMLError(
                "perfil sem custos.xlsx configurado — não dá pra calcular margens"
            )
        if profile.ml_user_id is None:
            raise CampaignNaoNoMLError("perfil não conectado ao ML")
        aliquota = profile.config.aliquota_imposto
        custos_map = carregar_custos(profile.config.custos_xlsx_path)
        tarifas_overrides = carregar_tarifas_ml(
            profile.config.tarifas_ml_xlsx_path,
        )
        cep = profile.config.cep_destino
        freight_cache = FreightCache(profile.slug)

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
            max_concurrent=8,
        ) as ml:
            limites = await buscar_limites_credibilidade(
                ml, promotion_id=ml_promo_id,
                promotion_type="SELLER_CAMPAIGN", item_ids=item_ids,
            )

            async def _editar_um(item_id: str) -> EditarMargemItemResult:
                info = limites.get(item_id) or {}
                deal_anterior = info.get("price")
                ml_min = info.get("min")
                ml_max = info.get("max")

                if not deal_anterior:
                    return EditarMargemItemResult(
                        item_id=item_id, sku=None,
                        deal_anterior=None, deal_novo=None,
                        margem_anterior_pct=None, margem_nova_pct=None,
                        status="erro",
                        motivo="item não está na promoção (ausente do ML)",
                    )

                # Carrega SKU + componentes
                try:
                    item_raw = await ml.get(f"/items/{item_id}", max_retries=3)
                except Exception as e:
                    return EditarMargemItemResult(
                        item_id=item_id, sku=None,
                        deal_anterior=deal_anterior, deal_novo=None,
                        margem_anterior_pct=None, margem_nova_pct=None,
                        status="erro",
                        motivo=f"falha ao buscar /items: {e}",
                    )
                sku = item_raw.get("seller_custom_field")
                if not sku:
                    for a in item_raw.get("attributes") or []:
                        if a.get("id") == "SELLER_SKU":
                            sku = a.get("value_name")
                            break

                taxas, err = await _calcular_componentes(
                    ml=ml, item_id=item_id, sku=sku,
                    deal_price=deal_anterior, cep=cep,
                    freight_cache=freight_cache,
                    custos_map=custos_map, overrides=None,
                    tarifas_overrides=tarifas_overrides,
                )
                if err is not None or taxas is None:
                    return EditarMargemItemResult(
                        item_id=item_id, sku=sku,
                        deal_anterior=deal_anterior, deal_novo=None,
                        margem_anterior_pct=None, margem_nova_pct=None,
                        status="erro", motivo=err,
                    )

                # Margem ANTES (no deal anterior)
                m_ant = margem_liquida_pct(
                    deal_anterior, custo=taxas.custo,
                    tarifa_pct=taxas.comissao_pct, list_cost=taxas.list_cost,
                    aliquota=aliquota, sem_teto_custo_fixo=True,
                    list_cost_below_79=taxas.tarifa_fixa,
                    list_cost_above_79=taxas.frete,
                )
                margem_anterior_pct = round(m_ant * 100, 2)

                # Cálculo do deal NOVO pra atingir `margem_alvo`. Resolvemos
                # iterativamente porque a tarifa muda com o regime (cruzar 79):
                # - tenta ramo <79: dp = (custo + H) / (1 - G - K - margem)
                #   se dp < 79: usa. Senão tenta o ramo ≥79.
                # - ramo ≥79: dp = (custo + I) / (1 - G - K - margem)
                #   se dp ≥ 79: usa. Senão fica indefinido (target inviável).
                denom = 1.0 - taxas.comissao_pct - aliquota - margem_alvo
                if denom <= 0:
                    return EditarMargemItemResult(
                        item_id=item_id, sku=sku,
                        deal_anterior=deal_anterior, deal_novo=None,
                        margem_anterior_pct=margem_anterior_pct,
                        margem_nova_pct=None,
                        status="erro",
                        motivo=(
                            f"margem alvo {margem_alvo:.0%} inviável "
                            f"(alíquota {aliquota:.0%} + comissão "
                            f"{taxas.comissao_pct:.0%})"
                        ),
                    )

                dp_below = (taxas.custo + taxas.tarifa_fixa) / denom
                dp_above = (taxas.custo + taxas.frete) / denom
                if dp_below < DEGRAU_TARIFA_FIXA:
                    deal_novo = round(dp_below, 2)
                elif dp_above >= DEGRAU_TARIFA_FIXA:
                    deal_novo = round(dp_above, 2)
                else:
                    # Nem <79 nem ≥79 cabem -> usa piso R$ 79
                    deal_novo = DEGRAU_TARIFA_FIXA

                # Clamp do ML
                if ml_max is not None and deal_novo > ml_max:
                    return EditarMargemItemResult(
                        item_id=item_id, sku=sku,
                        deal_anterior=deal_anterior, deal_novo=deal_novo,
                        margem_anterior_pct=margem_anterior_pct,
                        margem_nova_pct=None,
                        status="pulado_ml_max",
                        motivo=(
                            f"deal calculado R$ {deal_novo:.2f} > ml_max "
                            f"R$ {ml_max:.2f}. Pra atingir essa margem "
                            "precisaria subir o preço-base (não automatico)."
                        ),
                    )
                if ml_min is not None and deal_novo < ml_min:
                    # Sobe pro mínimo (margem fica acima do alvo — OK)
                    deal_novo = float(ml_min)

                # Se o deal não mudou (mesmo valor já praticado), pula POST
                if abs(deal_novo - deal_anterior) < 0.01:
                    return EditarMargemItemResult(
                        item_id=item_id, sku=sku,
                        deal_anterior=deal_anterior, deal_novo=deal_novo,
                        margem_anterior_pct=margem_anterior_pct,
                        margem_nova_pct=margem_anterior_pct,
                        status="sem_mudanca",
                        motivo="deal já está no valor calculado",
                    )

                # SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE: POST em item já `started`
                # é NO-OP — o ML aceita (200) mas mantém o deal_price antigo.
                # Pra atualizar o deal de fato, precisa DELETE + re-POST.
                # Validado empiricamente jun/2026 (jornada: status "aplicado" mas
                # listar_margens continuava mostrando o deal velho até o
                # DELETE+POST manual).
                status_atual = str(info.get("status") or "").lower()
                if status_atual == "started":
                    try:
                        await remover_sku_de_campanha(
                            ml, item_id=item_id,
                            promotion_id=ml_promo_id,
                            promotion_type="SELLER_CAMPAIGN",
                        )
                    except ItemNotInCampaignError:
                        # Race: outro processo removeu antes — segue pro POST.
                        pass
                    except MLPromotionError as e:
                        return EditarMargemItemResult(
                            item_id=item_id, sku=sku,
                            deal_anterior=deal_anterior, deal_novo=deal_novo,
                            margem_anterior_pct=margem_anterior_pct,
                            margem_nova_pct=None,
                            status="erro",
                            motivo=f"falha ao remover do ML pra re-adicionar: {e}",
                        )

                try:
                    await adicionar_sku_em_campanha(
                        ml, item_id=item_id,
                        promotion_id=ml_promo_id,
                        promotion_type="SELLER_CAMPAIGN",
                        deal_price=deal_novo,
                    )
                except MLPromotionError as e:
                    # POST falhou DEPOIS do DELETE → item fora da campanha.
                    # Retorna erro detalhado; UI pode oferecer re-tentar com
                    # deal mais conservador.
                    return EditarMargemItemResult(
                        item_id=item_id, sku=sku,
                        deal_anterior=deal_anterior, deal_novo=deal_novo,
                        margem_anterior_pct=margem_anterior_pct,
                        margem_nova_pct=None,
                        status="erro",
                        motivo=(
                            f"ML rejeitou re-adição: {e}. Item saiu da campanha "
                            f"— re-adicione manualmente ou re-tente com margem "
                            f"maior."
                            if status_atual == "started"
                            else f"ML rejeitou: {e}"
                        ),
                    )

                # Margem real prevista no novo deal
                m_novo = margem_liquida_pct(
                    deal_novo, custo=taxas.custo,
                    tarifa_pct=taxas.comissao_pct, list_cost=taxas.list_cost,
                    aliquota=aliquota, sem_teto_custo_fixo=True,
                    list_cost_below_79=taxas.tarifa_fixa,
                    list_cost_above_79=taxas.frete,
                )
                logger.info(
                    "campaign_editar_margem_ok",
                    item_id=item_id, ml_campaign_id=campaign.ml_campaign_id,
                    deal_anterior=deal_anterior, deal_novo=deal_novo,
                    margem_alvo=margem_alvo,
                    margem_real_pct=round(m_novo * 100, 2),
                )
                return EditarMargemItemResult(
                    item_id=item_id, sku=sku,
                    deal_anterior=deal_anterior, deal_novo=deal_novo,
                    margem_anterior_pct=margem_anterior_pct,
                    margem_nova_pct=round(m_novo * 100, 2),
                    status="aplicado", motivo=None,
                )

            return list(await asyncio.gather(*[_editar_um(i) for i in item_ids]))
