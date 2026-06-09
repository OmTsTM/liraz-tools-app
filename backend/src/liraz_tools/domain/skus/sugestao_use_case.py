"""Use case: sugerir preço/deal por SKU (modelo planilha passo3).

Para cada item:
  1. **Preço final de venda P** = `preco_recomendado` (margem alvo Q2, escada
     Filosofia B, piso inviolável R2). É o preço que o cliente paga — com ou
     sem campanha. Comissão é G% (escala com o preço); H/I (tarifa fixa/frete)
     vêm derivados da API do ML.
  2. **Campanha** = publica `U = P*(1+T2)` e desconta de volta a P
     (`inflar_para_campanha`). `deal_price` = P; `preco_inflado` = U;
     `desconto_pct` = V. A campanha não come margem real — é vitrine.

Parâmetros (config do perfil):
  - margem_alvo_campanha (Q2) — sobrescrito pelo `margem_alvo` da req
  - margem_minima (R2) — piso inviolável
  - pct_inflacao_campanha (T2) — % de inflação da vitrine
  - aliquota_imposto (K2), custo, CEP idem.

Fallback ERROR_CREDIBILITY preservado: se o ML recusar o desconto, retry com
desconto menor (deal mais alto, margem >= Q2) via `fallback_deal_price`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.items_fetcher import fetch_items_basic_info
from liraz_tools.infrastructure.pricing.calculator import calcular_taxas_anuncio
from liraz_tools.infrastructure.pricing.costs_loader import (
    buscar_custo,
    carregar_custos,
    carregar_tarifas_ml,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.repricing_calculator import (
    DEGRAU_TARIFA_FIXA,
    inflar_para_campanha,
    margem_liquida_pct,
    preco_recomendado,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
        CostOverridesRepository,
    )
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )
    from liraz_tools.infrastructure.repositories.profile_repository import (
        SQLAlchemyProfileRepository,
    )

logger = get_logger(__name__)


@dataclass
class SugestaoDealPrice:
    item_id: str
    sku: str | None
    preco_atual: float | None
    margem_atual_pct: float | None  # margem sem campanha em preço atual
    precisa_inflacao: bool
    preco_inflado: float | None  # = preco_atual se !precisa_inflacao
    deal_price: float | None
    desconto_pct: float | None  # relativo ao preco_inflado
    margem_real_pct: float | None
    min_aplicado: bool
    aviso_degrau: dict[str, Any] | None
    erro: str | None
    # ─── Leva 5.13: estratégia de quebra de frete grátis ───────────────
    # Quando aplicada (deal natural entre 79 e teto_quebra_frete_gratis),
    # o deal_price retornado já é o agressivo (<79 sem frete grátis).
    # O fallback_deal_price guarda o deal conservador (>=79 com frete)
    # pra ser usado em retry caso o ML rejeite a adição com
    # ERROR_CREDIBILITY_DISCOUNTED_PRICE.
    quebra_frete_gratis_aplicada: bool = False
    fallback_deal_price: float | None = None
    # ─── Frete a confirmar (modelo passo3) ─────────────────────────────
    # True quando o preço recomendado cruza pra ≥ R$ 79 mas o item está hoje
    # < 79: o frete do regime ≥79 não é confiável (a API só dá o frete do preço
    # atual, que subestima — frete grátis ≥79 escala com o preço). Idem quando
    # não há frete de tabela (list_cost ausente). Esses itens NÃO entram na
    # campanha pelo app — exigem ajuste manual (ver frete real no painel ML).
    frete_a_confirmar: bool = False
    # ─── Componentes de custo (passo3) ─────────────────────────────────
    # Usados pra recomputar margem em qualquer deal arbitrário — necessário
    # pro CLAMP da faixa de credibilidade do ML (campaign_skus_apply).
    # Sem isso, callers que querem clampar dP teriam que recomputar sugestao.
    custo_unit: float | None = None  # custo do xlsx
    list_cost: float | None = None  # frete de tabela CRU (independente do preço)
    comissao_pct: float | None = None  # G — fração 0..1
    aliquota: float | None = None  # K2 — fração 0..1


def _erro(
    item_id: str, msg: str, sku: str | None = None,
    preco_atual: float | None = None,
) -> SugestaoDealPrice:
    """Helper pra criar SugestaoDealPrice de erro com todos os campos default."""
    return SugestaoDealPrice(
        item_id=item_id, sku=sku, preco_atual=preco_atual,
        margem_atual_pct=None, precisa_inflacao=False, preco_inflado=None,
        deal_price=None, desconto_pct=None, margem_real_pct=None,
        min_aplicado=False, aviso_degrau=None, erro=msg,
        quebra_frete_gratis_aplicada=False, fallback_deal_price=None,
        frete_a_confirmar=False,
    )


class SugerirDealPricesPorMargemUseCase:
    """Pra um grupo de item_ids, calcula Fase 1 (inflação) + Fase 2 (desconto)."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        overrides_repo: CostOverridesRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._overrides_repo = overrides_repo

    async def execute(
        self,
        profile_id: UUID,
        *,
        ml_campaign_id: str | None,  # None = preview sem campanha existente
        item_ids: list[str],
        margem_alvo: float,  # margem da campanha (Fase 2) - fração 0-1
    ) -> list[SugestaoDealPrice]:
        if not item_ids:
            return []

        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            return [_erro(iid, "loja não conectada") for iid in item_ids]

        if not profile.config.custos_xlsx_path:
            return [
                _erro(iid, "planilha de custos não configurada no perfil")
                for iid in item_ids
            ]

        try:
            custos_map = carregar_custos(profile.config.custos_xlsx_path)
        except Exception as e:
            return [_erro(iid, f"erro lendo custos.xlsx: {e}") for iid in item_ids]

        # Planilha de tarifas reais (separada do custos.xlsx, alimentada pela
        # extensão Chrome). Path opcional no perfil — ausente = dict vazio,
        # cálculo segue o teto teórico (6,75/8,55).
        tarifas_overrides = carregar_tarifas_ml(profile.config.tarifas_ml_xlsx_path)

        overrides = self._overrides_repo.load_all(profile.slug)
        aliquota = profile.config.aliquota_imposto          # K2
        cep = profile.config.cep_destino
        margem_minima = profile.config.margem_minima        # R2 (piso inviolável)
        pct_inflacao = profile.config.pct_inflacao_campanha  # T2 (% inflação)
        freight_cache = FreightCache(profile.slug)

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed, max_concurrent=8,
        ) as ml:
            # 1) Info básica em batch (sku, preço atual)
            info = await fetch_items_basic_info(ml, item_ids)

            # 2) Limites min/max % da campanha. Em modo preview (sem campanha
            # criada ainda), assume 5% como min conservador — bate com a
            # política mais comum do ML em SELLER_CAMPAIGN. O preview NÃO
            # bloqueia: se o user criar a campanha com min diferente, o
            # cálculo é refeito no momento da adição via endpoint normal.
            if ml_campaign_id:
                min_pct, _ = await _fetch_limites_promocao(ml, ml_campaign_id)
            else:
                min_pct = 5.01

            # 3) Pra cada item: pipeline Fase 1 + Fase 2 em paralelo
            async def _processar_um(item_id: str) -> SugestaoDealPrice:
                info_item = info.get(item_id, {})
                sku = info_item.get("sku")
                preco_raw = info_item.get("preco")
                try:
                    preco_atual = float(preco_raw) if preco_raw is not None else None
                except (TypeError, ValueError):
                    preco_atual = None

                if not preco_atual or preco_atual <= 0:
                    return _erro(item_id, "item sem preço no ML", sku=sku,
                                 preco_atual=preco_atual)

                custo, fonte = buscar_custo(sku, item_id, custos_map, overrides)
                # Custo 0/negativo é erro de cadastro (nenhum produto é grátis).
                # Pior: abaixo de R$ 12,50 a tarifa fixa é 50% do preço e
                # comissão/imposto são percentuais — com custo 0 a margem fica
                # CONSTANTE qualquer que seja o preço, então a busca binária por
                # margem-alvo despenca até o piso e sugere um deal absurdo (ex.:
                # R$ 0,51 a -97%). Trata como sem custo: flag + auto-desmarcado
                # na UI (a string "sem custo" é o que o front detecta).
                if custo is None or custo <= 0:
                    valor = "ausente" if custo is None else f"R$ {custo:.2f}"
                    return _erro(
                        item_id,
                        f"sem custo válido ({valor}, fonte={fonte}) — corrija no custos.xlsx",
                        sku=sku, preco_atual=preco_atual,
                    )

                # ─── Calcula margem ATUAL (sem campanha) ──────────────
                taxas_atual = await calcular_taxas_anuncio(
                    ml=ml, item_id=item_id, cep_destino=cep,
                    freight_cache=freight_cache, preco_simulado=preco_atual,
                    tarifas_override=tarifas_overrides,
                )
                if "erro" in taxas_atual:
                    return _erro(
                        item_id, f"erro calculando margem atual: {taxas_atual['erro']}",
                        sku=sku, preco_atual=preco_atual,
                    )
                # G (tarifa %) ESCALA com o preço (modelo planilha passo3). O custo
                # logístico vem do `list_cost` cru (frete de tabela, INDEPENDENTE
                # do preço atual) — assim a escada avalia corretamente os dois
                # regimes do degrau R$ 79 (custo fixo abaixo / frete acima), mesmo
                # pra itens hoje ≥79 (que teriam tarifa_fixa=0 no preço atual).
                comissao_pct = (taxas_atual.get("comissao_percentual", 0.0) or 0.0) / 100.0  # G
                list_cost = taxas_atual.get("list_cost")
                # Overrides reais do painel ML (vindos do tarifas_ml.xlsx) por
                # regime do degrau R$ 79 — quando ambos estão cadastrados, o
                # passo3 calcula cada ramo da escada com o valor REAL, sem
                # acabar usando o do regime errado ao cruzar o degrau.
                lc_below = taxas_atual.get("list_cost_below_79_override")
                lc_above = taxas_atual.get("list_cost_above_79_override")
                eh_override = taxas_atual.get("tarifa_fixa_fonte") == "override_xlsx"

                margem_atual = margem_liquida_pct(
                    preco_atual, custo=custo, tarifa_pct=comissao_pct,
                    list_cost=list_cost, aliquota=aliquota,
                    sem_teto_custo_fixo=eh_override,
                    list_cost_below_79=lc_below,
                    list_cost_above_79=lc_above,
                )
                margem_atual_pct = round(margem_atual * 100, 2)

                # ─── Preço final de venda P (Q2, Filosofia B + piso R2) ─────
                p = preco_recomendado(
                    custo=custo, tarifa_pct=comissao_pct, list_cost=list_cost,
                    aliquota=aliquota,
                    margem_alvo=margem_alvo, margem_minima=margem_minima,
                    sem_teto_custo_fixo=eh_override,
                    list_cost_below_79=lc_below,
                    list_cost_above_79=lc_above,
                )
                if p is None:
                    return _erro(
                        item_id,
                        f"margem alvo ({margem_alvo:.0%}) inviável com "
                        f"alíquota {aliquota:.0%} + tarifa {comissao_pct:.0%}",
                        sku=sku, preco_atual=preco_atual,
                    )

                # ─── Campanha: publica U=P*(1+T2), desconta de volta a P ────
                # deal_price (preço que o cliente paga) = P. O cliente paga P
                # com ou sem campanha; a inflação é só vitrine de desconto.
                preco_inflado, desconto_frac = inflar_para_campanha(
                    p, pct_inflacao=pct_inflacao,
                )
                deal_price = p
                desconto_pct = desconto_frac * 100
                min_aplicado = False

                # Se o desconto natural ficar abaixo do mínimo da campanha, infla
                # MAIS (sobe U) pra atingir o mínimo — mantém deal=P (margem Q2).
                if min_pct is not None and desconto_pct < min_pct:
                    preco_inflado = round(p / (1 - min_pct / 100), 2)
                    desconto_pct = min_pct
                    min_aplicado = True

                margem_real_pct = round(
                    margem_liquida_pct(
                        deal_price, custo=custo, tarifa_pct=comissao_pct,
                        list_cost=list_cost,
                        aliquota=aliquota,
                        sem_teto_custo_fixo=eh_override,
                        list_cost_below_79=lc_below,
                        list_cost_above_79=lc_above,
                    ) * 100,
                    2,
                )

                # Fallback ERROR_CREDIBILITY: se o ML recusar o desconto (acha o
                # preço inflado "falso"), retry com desconto MENOR (deal mais alto
                # → margem >= Q2, seguro). Usa o desconto mínimo da campanha.
                # Só faz sentido se for de fato mais conservador que o deal atual.
                piso_desc = (min_pct or 5.01) / 100.0
                fallback_candidato = round(preco_inflado * (1 - piso_desc), 2)
                fallback_deal = (
                    fallback_candidato if fallback_candidato > deal_price else None
                )

                # Caller seta o anúncio pra U (preco_inflado) antes de adicionar.
                precisa_inflacao = abs(preco_inflado - preco_atual) > 0.01

                # Frete a confirmar: o preço recomendado cruza pra ≥ R$ 79 mas o
                # item está hoje < 79 → o frete ≥79 não é confiável (a API só dá
                # o do preço atual, que subestima). Ou não há frete de tabela.
                # EXCEÇÃO: quando o vendedor cadastrou o frete real ≥79 no
                # `tarifas_ml.xlsx` (`list_cost_above_79_override`), a fonte de
                # verdade existe — não há por que marcar como "a confirmar".
                tem_frete_acima_79_real = lc_above is not None
                frete_a_confirmar = (
                    p >= DEGRAU_TARIFA_FIXA
                    and (preco_atual < DEGRAU_TARIFA_FIXA or list_cost is None)
                    and not tem_frete_acima_79_real
                )

                return SugestaoDealPrice(
                    item_id=item_id, sku=sku, preco_atual=preco_atual,
                    margem_atual_pct=margem_atual_pct,
                    precisa_inflacao=precisa_inflacao,
                    preco_inflado=preco_inflado,
                    deal_price=deal_price,
                    desconto_pct=round(desconto_pct, 2),
                    margem_real_pct=margem_real_pct,
                    min_aplicado=min_aplicado, aviso_degrau=None,
                    erro=None,
                    quebra_frete_gratis_aplicada=False,
                    fallback_deal_price=fallback_deal,
                    frete_a_confirmar=frete_a_confirmar,
                    custo_unit=float(custo),
                    list_cost=list_cost,
                    comissao_pct=comissao_pct,
                    aliquota=aliquota,
                )

            resultados = await asyncio.gather(
                *[_processar_um(iid) for iid in item_ids],
            )

        logger.info(
            "sugestao_deal_prices_concluido",
            profile_id=str(profile_id),
            ml_campaign_id=ml_campaign_id,
            margem_alvo=margem_alvo,
            margem_minima=margem_minima,
            pct_inflacao=pct_inflacao,
            total=len(resultados),
            ok=sum(1 for r in resultados if r.erro is None),
            precisam_inflacao=sum(1 for r in resultados if r.precisa_inflacao and r.erro is None),
            min_aplicado=sum(1 for r in resultados if r.min_aplicado),
            min_pct_campanha=min_pct,
        )
        return list(resultados)


async def _fetch_limites_promocao(
    ml: MLClient, ml_campaign_id: str,
) -> tuple[float | None, float | None]:
    """Busca min/max % de desconto da promoção (0-100). Default conservador
    5.01% pra min se a API não devolver (passa a regra `more than 5`)."""
    try:
        resp = await ml.get(
            f"/seller-promotions/promotions/{ml_campaign_id}",
            params={"app_version": "v2"},
        )
    except Exception as e:
        logger.warning(
            "fetch_limites_promocao_falhou",
            ml_campaign_id=ml_campaign_id, error=str(e),
        )
        return None, None

    if not isinstance(resp, dict):
        return None, None

    candidatos_min = [
        "min_discount_percentage", "minimum_discount_percentage",
        "min_discount_percent", "discount_percentage_min",
    ]
    candidatos_max = [
        "max_discount_percentage", "maximum_discount_percentage",
        "max_discount_percent", "discount_percentage_max",
    ]
    min_pct = next(
        (resp.get(k) for k in candidatos_min if isinstance(resp.get(k), (int, float))),
        None,
    )
    max_pct = next(
        (resp.get(k) for k in candidatos_max if isinstance(resp.get(k), (int, float))),
        None,
    )
    if min_pct is None:
        min_pct = 5.01
    return float(min_pct), float(max_pct) if max_pct is not None else None
