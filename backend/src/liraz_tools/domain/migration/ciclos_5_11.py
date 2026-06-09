"""Ciclos automáticos da Leva 5.11 — cobertura total da loja.

Três ciclos novos plugados no `MigrationScheduler` da 5.9.4.C.3:

- **Ciclo A** (renovação antecipada): cria guarda-chuva sucessora 1 dia
  antes da atual terminar. Garante zero buraco de cobertura na transição.

- **Ciclo B** (bootstrap automático): se o perfil não tem nenhuma
  guarda-chuva ativa/agendada, cria uma agora com data_inicio=hoje.

- **Ciclo C** (onboarding total): lista TODOS os anúncios ativos da loja
  e adiciona os descobertos na guarda-chuva ativa. Pega novos cadastros
  e órfãos legados.

Todos respeitam `profile.config.migracao_dry_run` (default true = freio).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.criar_ml_use_case import (
    CriarMLCampaignError,
    CriarSellerCampaignNoMLUseCase,
    NomeJaExisteLocalError,
)
from liraz_tools.domain.migration.cobertura import (
    _consultar_cobertura_sku,
)
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.items_update import (
    MLItemUpdateError,
    atualizar_preco_item,
)
from liraz_tools.infrastructure.ml.promotion_items import (
    ItemAlreadyInCampaignError,
    MLPromotionError,
    adicionar_sku_em_campanha,
)
from liraz_tools.infrastructure.ml.promotions_create import (
    InvalidDatesError,
    StartDateTooFarError,
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
    buscar_preco_para_margem,
    margem_liquida_pct,
)
from liraz_tools.infrastructure.repositories.migracao_executada_repository import (
    MigracaoExecutadaRecord,
    agora_utc,
    novo_record_id,
)

if TYPE_CHECKING:
    from liraz_tools.domain.campaigns.entity import Campaign
    from liraz_tools.infrastructure.repositories.campaign_repository import (
        CampaignRepository,
    )
    from liraz_tools.infrastructure.repositories.migracao_executada_repository import (
        MigracaoExecutadaRepository,
    )
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )
    from liraz_tools.infrastructure.repositories.profile_repository import (
        SQLAlchemyProfileRepository,
    )
    from liraz_tools.infrastructure.repositories.repricing_snapshot_repository import (
        RepricingSnapshotRepository,
    )

logger = get_logger(__name__)


DURACAO_GUARDA_CHUVA_DIAS = 30
DIAS_ANTES_PRA_RENOVAR = 1


# ────────────────────────────────────────────────────────────────────────────
# Resultados
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class ResultadoCiclo511:
    """Resumo combinado dos 3 ciclos."""

    profile_id: UUID
    dry_run: bool
    # Ciclo A — renovação
    renovacao_tentadas: int = 0
    renovacao_criadas: int = 0
    renovacao_falhas: int = 0
    renovacao_erros: list[str] = field(default_factory=list)
    # Ciclo B — bootstrap
    bootstrap_criada: bool = False
    bootstrap_ml_id: str | None = None
    bootstrap_erro: str | None = None
    # Ciclo C — onboarding
    onboarding_total_anuncios: int = 0
    onboarding_descobertos: int = 0
    onboarding_adicionados: int = 0
    onboarding_falhas: int = 0
    onboarding_sem_custo: int = 0
    # Sub-categorias de "adicionados" e "falhas" pra dar visibilidade ao
    # comportamento do clamp+retry de credibilidade (jun/2026):
    onboarding_adicionados_clamp: int = 0  # entrou após clamp por ml_sug/ml_max
    onboarding_adicionados_retry: int = 0  # entrou no retry após ERROR_CREDIBILITY
    onboarding_pulados_credibility: int = 0  # rejeitado mesmo após retry
    onboarding_pulados_margem: int = 0  # clamp/retry derrubaria margem abaixo do piso
    # Fase 1 (inflar) — Ciclo C agora infla preço-base quando o desconto
    # necessário ficaria abaixo de ~10% (= ML rejeita por credibilidade).
    onboarding_inflados: int = 0  # preço-base subido pra criar margem de desconto
    onboarding_revertidos_pos_falha: int = 0  # PUT pro preço antigo quando POST falhou
    inflacao_session_id: UUID | None = None  # session_id pra reverter inflações no DB


# ────────────────────────────────────────────────────────────────────────────
# Nome convencional de guarda-chuva
# ────────────────────────────────────────────────────────────────────────────

_MESES_PT = [
    "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def nome_guarda_chuva(profile_name: str, data_inicio: date) -> str:
    """Nome convencional: '{Loja} {Mes}' (ex: 'LiraZ Junho', 'Toque Rico Julho').

    Usa o mês que cobre a maior parte da janela (`data_inicio + 15 dias`).
    Isso resolve o caso da sucessora de Ciclo A: ela tem
    `data_inicio = data_fim da antiga` (zero overlap), então `data_inicio`
    cai no último dia do mês corrente; mid-window cai no mês seguinte
    (ex: data_inicio=2026-06-30 → mid=2026-07-15 → "Julho"). Pro bootstrap
    (Ciclo B, data_inicio=hoje), mid também cai no mês corrente.

    Marco sem til de propósito — evita problemas de encoding em URLs/logs.
    """
    mid = data_inicio + timedelta(days=15)
    return f"{profile_name} {_MESES_PT[mid.month - 1]}"


# ────────────────────────────────────────────────────────────────────────────
# Ciclo A — Renovação antecipada
# ────────────────────────────────────────────────────────────────────────────


async def ciclo_a_renovar_guarda_chuvas(
    *,
    profile_id: UUID,
    profile_repo: SQLAlchemyProfileRepository,
    campaign_repo: CampaignRepository,
    creds_repo: PerProfileCredentialsRepository,
    dry_run: bool,
    resultado: ResultadoCiclo511,
) -> None:
    """Pra cada guarda-chuva ativa que está pra terminar nas próximas
    `DIAS_ANTES_PRA_RENOVAR` (default: 1 dia), cria uma sucessora.

    Idempotência: se a campanha já tem `renovada=True`, pula.
    """
    hoje = date.today()
    limite_renovacao = hoje + timedelta(days=DIAS_ANTES_PRA_RENOVAR)

    profile = await profile_repo.get_by_id(profile_id)
    campanhas = await campaign_repo.list_by_profile(profile_id)
    a_renovar = [
        c for c in campanhas
        if c.origem == "ml"
        and c.archived_at is None
        and c.status in ("ativa", "agendada")
        and not c.renovada
        and c.data_fim <= limite_renovacao
    ]

    if not a_renovar:
        logger.info(
            "ciclo_a_sem_renovacao_necessaria",
            profile_id=str(profile_id),
            total_campanhas=len(campanhas),
        )
        return

    use_case_criar = CriarSellerCampaignNoMLUseCase(
        profile_repo, campaign_repo, creds_repo,
    )

    for c in a_renovar:
        resultado.renovacao_tentadas += 1
        # data_inicio_nova = c.data_fim (zero overlap, definido abaixo). O
        # nome usa o mid-window dessa data_inicio → cai no MÊS da sucessora
        # (ex: c.data_fim=06-30 → nome inclui "Julho", não "Junho").
        nome_nova = nome_guarda_chuva(profile.name, c.data_fim)
        data_inicio_nova = c.data_fim  # zero overlap
        data_fim_nova = data_inicio_nova + timedelta(
            days=DURACAO_GUARDA_CHUVA_DIAS,
        )

        logger.info(
            "ciclo_a_renovando",
            campaign_id=str(c.id),
            campaign_atual_nome=c.nome,
            sucessora_nome=nome_nova,
            sucessora_inicio=data_inicio_nova.isoformat(),
            sucessora_fim=data_fim_nova.isoformat(),
            dry_run=dry_run,
        )

        if dry_run:
            # Em dry_run só marca como "teria renovado" no log.
            # Não toca em renovada nem cria no ML.
            resultado.renovacao_criadas += 1
            continue

        try:
            result = await use_case_criar.execute(
                profile_id,
                nome=nome_nova,
                data_inicio=data_inicio_nova,
                data_fim=data_fim_nova,
            )
            # Marca original como renovada pra não recriar
            await campaign_repo.update(c.model_copy(update={"renovada": True}))
            resultado.renovacao_criadas += 1
            logger.info(
                "ciclo_a_sucessora_criada",
                original_id=str(c.id),
                sucessora_id=str(result.campaign.id),
                sucessora_ml_id=result.ml_campaign_id,
            )
        except (
            NomeJaExisteLocalError, StartDateTooFarError,
            InvalidDatesError, CriarMLCampaignError, MLPromotionError,
        ) as e:
            resultado.renovacao_falhas += 1
            msg = f"{c.nome}→{nome_nova}: {e}"
            resultado.renovacao_erros.append(msg)
            logger.warning(
                "ciclo_a_renovacao_falhou",
                campaign_id=str(c.id),
                erro=str(e),
            )


# ────────────────────────────────────────────────────────────────────────────
# Ciclo B — Bootstrap automático
# ────────────────────────────────────────────────────────────────────────────


async def ciclo_b_bootstrap_guarda_chuva(
    *,
    profile_id: UUID,
    profile_repo: SQLAlchemyProfileRepository,
    campaign_repo: CampaignRepository,
    creds_repo: PerProfileCredentialsRepository,
    dry_run: bool,
    resultado: ResultadoCiclo511,
) -> Campaign | None:
    """Se não há guarda-chuva ativa/agendada, cria uma nova.

    Retorna a guarda-chuva ativa (existente ou recém-criada) pra que o
    Ciclo C possa usar como destino dos órfãos.
    """
    campanhas = await campaign_repo.list_by_profile(profile_id)
    ativas = [
        c for c in campanhas
        if c.origem == "ml"
        and c.archived_at is None
        and c.status in ("ativa", "agendada")
    ]

    if ativas:
        # Já tem guarda-chuva — escolhe a "melhor" pra ser destino do Ciclo C.
        # Critério: a que tem data_fim mais distante (rede mais durável).
        ativa = max(ativas, key=lambda c: c.data_fim)
        logger.info(
            "ciclo_b_guarda_chuva_ja_existe",
            campaign_id=str(ativa.id),
            ml_id=ativa.ml_campaign_id,
            data_fim=ativa.data_fim.isoformat(),
        )
        return ativa

    hoje = date.today()
    profile = await profile_repo.get_by_id(profile_id)
    nome = nome_guarda_chuva(profile.name, hoje)
    data_fim = hoje + timedelta(days=DURACAO_GUARDA_CHUVA_DIAS)

    logger.info(
        "ciclo_b_criando_bootstrap",
        profile_id=str(profile_id),
        nome=nome,
        data_inicio=hoje.isoformat(),
        data_fim=data_fim.isoformat(),
        dry_run=dry_run,
    )

    if dry_run:
        resultado.bootstrap_criada = True  # marcaria
        return None

    use_case_criar = CriarSellerCampaignNoMLUseCase(
        profile_repo, campaign_repo, creds_repo,
    )
    try:
        r = await use_case_criar.execute(
            profile_id, nome=nome, data_inicio=hoje, data_fim=data_fim,
        )
        resultado.bootstrap_criada = True
        resultado.bootstrap_ml_id = r.ml_campaign_id
        logger.info(
            "ciclo_b_bootstrap_criado",
            campaign_id=str(r.campaign.id),
            ml_id=r.ml_campaign_id,
        )
        return r.campaign
    except (
        NomeJaExisteLocalError, StartDateTooFarError,
        InvalidDatesError, CriarMLCampaignError, MLPromotionError,
    ) as e:
        resultado.bootstrap_erro = str(e)
        logger.warning(
            "ciclo_b_bootstrap_falhou",
            profile_id=str(profile_id),
            erro=str(e),
        )
        return None


# ────────────────────────────────────────────────────────────────────────────
# Ciclo C — Onboarding total
# ────────────────────────────────────────────────────────────────────────────


async def ciclo_c_onboarding_total(
    *,
    profile_id: UUID,
    guarda_chuva: Campaign,
    profile_repo: SQLAlchemyProfileRepository,
    campaign_repo: CampaignRepository,
    creds_repo: PerProfileCredentialsRepository,
    historico_repo: MigracaoExecutadaRepository,
    snapshot_repo: RepricingSnapshotRepository | None,
    dry_run: bool,
    resultado: ResultadoCiclo511,
) -> None:
    """Lista TODOS os anúncios ativos da loja e adiciona os descobertos
    na guarda-chuva.

    "Descoberto" = não está started em nenhuma promoção (SELLER_CAMPAIGN,
    DEAL, PRICE_DISCOUNT, etc). SKUs já cobertos por outras promoções
    são deixados em paz.
    """
    profile = await profile_repo.get_by_id(profile_id)
    if profile.status.value != "connected" or profile.ml_user_id is None:
        logger.info("ciclo_c_skip_loja_desconectada")
        return

    if not guarda_chuva.ml_campaign_id:
        logger.warning(
            "ciclo_c_skip_guarda_chuva_sem_ml_id",
            campaign_id=str(guarda_chuva.id),
        )
        return

    # Carrega custos pra calcular deal_price
    if not profile.config.custos_xlsx_path:
        logger.warning(
            "ciclo_c_skip_sem_custos_xlsx",
            profile_id=str(profile_id),
        )
        return

    try:
        custos = carregar_custos(profile.config.custos_xlsx_path)
    except Exception as e:
        logger.warning("ciclo_c_falha_custos", erro=str(e))
        return

    # Tarifas-override do XLSX (cf_ovr/fr_ovr per item ID) pra alinhar com o
    # mesmo motor de cálculo usado em `aplicar_adicoes_skus_em_campanha`.
    tarifas_overrides = carregar_tarifas_ml(profile.config.tarifas_ml_xlsx_path)

    freight_cache = FreightCache(profile.slug)
    margem_alvo = profile.config.margem_alvo_campanha
    # Piso de margem: se o clamp por ml_sug/ml_max ou o retry com candidato
    # conservador resultaria em margem real abaixo desse valor, o item é
    # pulado em vez de adicionado sangrando. Espelha `aplicar_adicoes_skus_em_campanha`.
    margem_minima = profile.config.margem_minima
    # Fase 1: % a inflar o preço-base quando o desconto necessário ficaria
    # abaixo de ~10% e o ML rejeitaria por credibilidade. Mesma config usada
    # pelo fluxo de criação manual de campanha (`pct_inflacao_campanha`).
    pct_inflacao = profile.config.pct_inflacao_campanha
    # Acumula snapshots (preco_base_antes, preco_inflado) por item pra
    # persistir no `repricing_snapshots` no fim do Ciclo C — permite o user
    # reverter via UI quando quiser. Só cria session_id se houver inflação.
    inflacoes_realizadas: list[tuple[str, float, float]] = []

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    async with MLClient(
        creds, tokens, on_tokens_refreshed=save_refreshed,
    ) as ml:
        # 1) Lista TODOS anúncios ativos
        ativos = await _listar_anuncios_ativos(ml, profile.ml_user_id)
        resultado.onboarding_total_anuncios = len(ativos)

        if not ativos:
            return

        # 2) Pra cada anúncio, verifica cobertura (paralelizado)
        sem = asyncio.Semaphore(8)

        async def verificar(item_id: str) -> tuple[str, bool]:
            """Retorna (item_id, está_descoberto)."""
            async with sem:
                cob = await _consultar_cobertura_sku(
                    ml, item_id, guarda_chuva.ml_campaign_id or "",
                )
            # Descoberto: nenhuma promoção em STATUS_ATIVO_AGORA
            esta_descoberto = (
                not cob.coberto_na_origem
                and not cob.coberto_em_outras
                and cob.erro_consulta is None
            )
            return item_id, esta_descoberto

        resultados_cob = await asyncio.gather(
            *[verificar(it) for it in ativos],
            return_exceptions=False,
        )
        descobertos = [it for it, desc in resultados_cob if desc]
        resultado.onboarding_descobertos = len(descobertos)

        logger.info(
            "ciclo_c_cobertura_calculada",
            total_ativos=len(ativos),
            descobertos=len(descobertos),
        )

        if not descobertos:
            return

        # 3) Pra cada descoberto, calcula deal_price e adiciona.
        # Os item_ids efetivamente adicionados na campanha entram nessa lista,
        # depois usada pra atualizar `guarda_chuva.skus_selecionados` no DB —
        # sem isso o app mostra contagem defasada até alguém forçar resync.
        adicionados_ml: list[str] = []
        for item_id in descobertos:
            try:
                item_info = await ml.get(f"/items/{item_id}")
            except Exception as e:
                resultado.onboarding_falhas += 1
                logger.debug(
                    "ciclo_c_falha_item_info",
                    item_id=item_id, erro=str(e),
                )
                continue
            if not isinstance(item_info, dict):
                resultado.onboarding_falhas += 1
                continue

            sku = _extrair_sku(item_info)
            custo, _ = buscar_custo(sku, item_id, custos, None)
            if custo is None:
                resultado.onboarding_sem_custo += 1
                await _salvar_historico_onboarding(
                    historico_repo,
                    profile_id=profile_id,
                    ml_campaign_id=guarda_chuva.ml_campaign_id,
                    item_id=item_id, sku=sku,
                    deal_price=None, margem=None,
                    sucesso=False, dry_run=dry_run,
                    erro="sem_custo_xlsx",
                )
                continue

            preco_alvo, erro = await buscar_preco_para_margem(
                ml=ml,
                item_id=item_id,
                custo=custo,
                margem_alvo=margem_alvo,
                aliquota=profile.config.aliquota_imposto,
                cep=profile.config.cep_destino,
                freight_cache=freight_cache,
            )
            if preco_alvo is None:
                resultado.onboarding_falhas += 1
                await _salvar_historico_onboarding(
                    historico_repo,
                    profile_id=profile_id,
                    ml_campaign_id=guarda_chuva.ml_campaign_id,
                    item_id=item_id, sku=sku,
                    deal_price=None, margem=None,
                    sucesso=False, dry_run=dry_run,
                    erro=f"busca_preco: {erro}",
                )
                continue

            sucesso = False
            erro_detalhe: str | None = None
            deal_efetivo: float = preco_alvo
            via_clamp = False
            via_retry = False
            credibility_pulado = False
            pulado_por_margem = False
            # Fase 1 — inflação tracking. Se >0, fizemos PUT pra subir preço-base
            # antes do POST; se POST falhar, precisa reverter.
            preco_base_antes_da_inflacao: float | None = None
            preco_inflado_aplicado: float | None = None

            async def _margem_real_em(
                deal: float, *, iid: str, custo_un: float,
            ) -> float | None:
                """Recalcula margem real em `deal` específico via /listing_prices.
                Devolve None quando ML não responde (sem dado pra decidir).
                """
                try:
                    taxas = await calcular_taxas_anuncio(
                        ml=ml, item_id=iid,
                        cep_destino=profile.config.cep_destino,
                        freight_cache=freight_cache,
                        preco_simulado=deal,
                        tarifas_override=tarifas_overrides,
                    )
                except Exception:
                    return None
                if "erro" in taxas:
                    return None
                comissao_pct = (taxas.get("comissao_percentual", 0.0) or 0.0) / 100.0
                list_cost = taxas.get("list_cost")
                lc_below = taxas.get("list_cost_below_79_override")
                lc_above = taxas.get("list_cost_above_79_override")
                tf = (
                    float(lc_below) if lc_below is not None
                    else (min(float(list_cost), 8.55) if list_cost is not None else 8.55)
                )
                fr = (
                    float(lc_above) if lc_above is not None
                    else (float(list_cost) if list_cost is not None else 0.0)
                )
                return margem_liquida_pct(
                    deal, custo=custo_un,
                    tarifa_pct=comissao_pct, list_cost=list_cost,
                    aliquota=profile.config.aliquota_imposto,
                    sem_teto_custo_fixo=True,
                    list_cost_below_79=tf,
                    list_cost_above_79=fr,
                )

            if dry_run:
                sucesso = True
            else:
                # CLAMP por suggested_discounted_price + retry com deal
                # conservador em ERROR_CREDIBILITY. Espelha a estratégia
                # de `aplicar_adicoes_skus_em_campanha`: o teto efetivo é
                # min(ml_max-0.01, ml_sug); enviar acima de `suggested`
                # dispara ERROR_CREDIBILITY_DISCOUNTED_PRICE (regra ~10%
                # do ML). Sem isso, anúncios legados quase sempre falhavam.
                # ml_max/ml_sug inicializados como None — sobrescritos pelo
                # bloco de limites após a Fase 1 (se chegar lá).
                ml_max: float | None = None
                ml_sug: float | None = None

                # ── Fase 1: inflar preço-base ANTES do clamp.
                # Estratégia: se o preço-base atual não dá espaço pra um
                # desconto ≥ 10% até preco_alvo (= ml_sug ficaria abaixo de
                # preco_alvo, forçando clamp + provável pulado_por_margem),
                # INFLA o preço-base pra que preco_alvo seja um desconto
                # confortável (~13%). Sem isso o clamp derruba a margem.
                preco_base_atual: float | None = None
                try:
                    preco_base_atual_raw = await ml.get(f"/items/{item_id}")
                    if isinstance(preco_base_atual_raw, dict):
                        price_raw = preco_base_atual_raw.get("price")
                        if isinstance(price_raw, (int, float)):
                            preco_base_atual = float(price_raw)
                except Exception:
                    preco_base_atual = None
                if (
                    not dry_run
                    and preco_base_atual is not None
                    and preco_base_atual > 0
                    and preco_alvo > preco_base_atual * 0.9
                ):
                    # Desconto natural seria < 10% → ML rejeitaria. Inflamos pra
                    # `preco_alvo * (1 + pct_inflacao)` (default +20%), resultando
                    # em desconto efetivo ~16-20% — confortável pro ML aceitar.
                    alvo_inflado = round(preco_alvo * (1 + pct_inflacao), 2)
                    if alvo_inflado > preco_base_atual + 0.01:
                        try:
                            await atualizar_preco_item(
                                ml, item_id, alvo_inflado,
                            )
                            preco_base_antes_da_inflacao = preco_base_atual
                            preco_inflado_aplicado = alvo_inflado
                            resultado.onboarding_inflados += 1
                        except MLItemUpdateError as e:
                            erro_detalhe = (
                                f"falha ao inflar preço-base pra "
                                f"R$ {alvo_inflado:.2f}: {e}"
                            )

                # ── CLAMP + check de margem (acontece DEPOIS da Fase 1).
                # Quando NÃO inflamos: lê limites do ML, aplica clamp, verifica
                # margem. Quando inflamos: PULAMOS esse bloco — o ML retorna
                # limites STALE após o PUT (ml_max ainda baseado no preço
                # antigo), fazendo o clamp baixar o deal indevidamente. Como
                # `preco_alvo` já foi dimensionado com inflação suficiente,
                # postamos direto e deixamos o ML aceitar/rejeitar.
                if erro_detalhe is None and preco_inflado_aplicado is None:
                    limites = await buscar_limites_credibilidade(
                        ml, promotion_id=guarda_chuva.ml_campaign_id,
                        item_ids=[item_id],
                    )
                    info = limites.get(item_id) or {}
                    ml_max_raw = info.get("max")
                    ml_sug_raw = info.get("suggested")
                    if isinstance(ml_max_raw, (int, float)):
                        ml_max = float(ml_max_raw)
                    if isinstance(ml_sug_raw, (int, float)):
                        ml_sug = float(ml_sug_raw)
                    teto_efetivo: float | None = None
                    if ml_max is not None:
                        teto_efetivo = ml_max - 0.01
                    if ml_sug is not None:
                        teto_efetivo = (
                            ml_sug if teto_efetivo is None
                            else min(teto_efetivo, ml_sug)
                        )
                    if teto_efetivo is not None and deal_efetivo > teto_efetivo:
                        deal_efetivo = round(teto_efetivo, 2)
                        via_clamp = True

                    # Pós-clamp: confere margem real (= continua valendo a pena?)
                    if via_clamp:
                        margem_real = await _margem_real_em(
                            deal_efetivo, iid=item_id, custo_un=custo,
                        )
                        if margem_real is not None and margem_real < margem_minima:
                            erro_detalhe = (
                                f"clamp por ml_sug/ml_max derrubaria margem pra "
                                f"{margem_real * 100:.1f}% (< piso {margem_minima * 100:.0f}%)"
                            )
                            pulado_por_margem = True
                if not pulado_por_margem and erro_detalhe is None:
                    try:
                        await adicionar_sku_em_campanha(
                            ml,
                            item_id=item_id,
                            promotion_id=guarda_chuva.ml_campaign_id,
                            promotion_type="SELLER_CAMPAIGN",
                            deal_price=deal_efetivo,
                        )
                        sucesso = True
                    except ItemAlreadyInCampaignError:
                        sucesso = True  # idempotente
                    except MLPromotionError as e:
                        erro_str = str(e)
                        is_credibility = (
                            "ERROR_CREDIBILITY_DISCOUNTED_PRICE" in erro_str
                        )
                        # Retry com deal mais conservador: ml_sug (= o que o
                        # ML sugere) ou ml_max - 0.01. Só faz sentido se
                        # `deal_efetivo` ainda está abaixo do alvo conservador
                        # (= o clamp inicial não foi suficiente).
                        candidato_retry: float | None = None
                        if is_credibility:
                            if ml_sug is not None and deal_efetivo < ml_sug:
                                candidato_retry = round(ml_sug, 2)
                            elif ml_max is not None and deal_efetivo < ml_max:
                                candidato_retry = round(ml_max - 0.01, 2)
                        # Antes de tentar o retry, verifica se o candidato
                        # também resultaria em margem viável. Sem isso, o
                        # retry "salva" o POST mas adiciona o item sangrando.
                        if candidato_retry is not None:
                            margem_candidato = await _margem_real_em(
                                candidato_retry, iid=item_id, custo_un=custo,
                            )
                            if (
                                margem_candidato is not None
                                and margem_candidato < margem_minima
                            ):
                                erro_detalhe = (
                                    f"retry candidato R$ {candidato_retry:.2f} "
                                    f"daria margem {margem_candidato * 100:.1f}% "
                                    f"(< piso {margem_minima * 100:.0f}%)"
                                )
                                pulado_por_margem = True
                                candidato_retry = None
                        if candidato_retry is not None:
                            try:
                                await adicionar_sku_em_campanha(
                                    ml,
                                    item_id=item_id,
                                    promotion_id=guarda_chuva.ml_campaign_id,
                                    promotion_type="SELLER_CAMPAIGN",
                                    deal_price=candidato_retry,
                                )
                                sucesso = True
                                via_retry = True
                                deal_efetivo = candidato_retry
                            except ItemAlreadyInCampaignError:
                                sucesso = True
                            except MLPromotionError as e2:
                                erro_detalhe = (
                                    f"credibility persistente após retry "
                                    f"@ R$ {candidato_retry:.2f}: {e2}"
                                )
                                credibility_pulado = True
                        elif is_credibility and not pulado_por_margem:
                            credibility_pulado = True
                            erro_detalhe = (
                                f"credibility sem alvo de retry "
                                f"(ml_sug={ml_sug}, ml_max={ml_max}): {erro_str}"
                            )
                        elif not pulado_por_margem:
                            erro_detalhe = erro_str

            # ── Pós-POST: tratar inflação que ficou pendente ───────────
            # Se inflamos preço-base e o POST deu CERTO → mantém inflado e
            # registra no snapshot pra permitir undo via UI depois.
            # Se inflamos e o POST FALHOU → reverte preço-base ao valor antes
            # da inflação (evita anúncio com preço inflado fora de campanha).
            if preco_base_antes_da_inflacao is not None and preco_inflado_aplicado is not None:
                if sucesso:
                    inflacoes_realizadas.append((
                        item_id,
                        preco_base_antes_da_inflacao,
                        preco_inflado_aplicado,
                    ))
                else:
                    try:
                        await atualizar_preco_item(
                            ml, item_id, preco_base_antes_da_inflacao,
                        )
                        resultado.onboarding_revertidos_pos_falha += 1
                        logger.info(
                            "ciclo_c_inflacao_revertida_pos_falha",
                            item_id=item_id,
                            preco_revertido=preco_base_antes_da_inflacao,
                        )
                    except MLItemUpdateError as rev_err:
                        # Reverter falhou. Item fica inflado E fora da campanha
                        # — pior cenário. Logamos pro user resolver manualmente.
                        logger.warning(
                            "ciclo_c_inflacao_revert_falhou",
                            item_id=item_id,
                            preco_inflado=preco_inflado_aplicado,
                            preco_alvo_revert=preco_base_antes_da_inflacao,
                            erro=str(rev_err),
                        )

            await _salvar_historico_onboarding(
                historico_repo,
                profile_id=profile_id,
                ml_campaign_id=guarda_chuva.ml_campaign_id,
                item_id=item_id, sku=sku,
                deal_price=deal_efetivo, margem=margem_alvo,
                sucesso=sucesso, dry_run=dry_run,
                erro=erro_detalhe,
            )

            if sucesso:
                resultado.onboarding_adicionados += 1
                # Em dry_run não chega ao POST real; não inclui no
                # `adicionados_ml` pra não mentir pro DB.
                if not dry_run:
                    adicionados_ml.append(item_id)
                if via_retry:
                    resultado.onboarding_adicionados_retry += 1
                elif via_clamp:
                    resultado.onboarding_adicionados_clamp += 1
            else:
                resultado.onboarding_falhas += 1
                if pulado_por_margem:
                    resultado.onboarding_pulados_margem += 1
                if credibility_pulado:
                    resultado.onboarding_pulados_credibility += 1

        # 4) Persiste snapshot das inflações de Fase 1 (= permite reverter via
        # UI depois). Faz UMA escrita só, com session_id único por execução do
        # Ciclo C. Sem snapshot_repo (= caller não passou), pula silenciosamente.
        if inflacoes_realizadas and snapshot_repo is not None:
            from liraz_tools.infrastructure.repositories.repricing_snapshot_repository import (
                SnapshotItem,
            )
            session_id = snapshot_repo.nova_sessao()
            await snapshot_repo.gravar_em_lote(
                profile_id,
                session_id,
                [
                    SnapshotItem(
                        item_id=iid,
                        preco_anterior=preco_antes,
                        preco_novo=preco_depois,
                    )
                    for (iid, preco_antes, preco_depois) in inflacoes_realizadas
                ],
            )
            resultado.inflacao_session_id = session_id
            logger.info(
                "ciclo_c_inflacao_session_gravada",
                profile_id=str(profile_id),
                session_id=str(session_id),
                qtd_itens=len(inflacoes_realizadas),
            )

        # 5) Atualiza o DB local com os SKUs efetivamente onboardados.
        # Faz UMA escrita no fim do loop, não por item — menor pressão no DB.
        if adicionados_ml:
            atuais = set(guarda_chuva.skus_selecionados or [])
            atuais.update(adicionados_ml)
            atualizada = guarda_chuva.model_copy(
                update={"skus_selecionados": sorted(atuais)},
            )
            await campaign_repo.update(atualizada)
            logger.info(
                "ciclo_c_skus_selecionados_atualizados",
                campaign_id=str(guarda_chuva.id),
                adicionados=len(adicionados_ml),
                total_apos=len(atuais),
            )


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


async def _listar_anuncios_ativos(
    ml: MLClient, user_id: int,
) -> list[str]:
    """Lista todos os anúncios ativos (active status) do vendedor."""
    items: list[str] = []
    offset = 0
    while True:
        try:
            resp = await ml.get(
                f"/users/{user_id}/items/search",
                params={"status": "active", "limit": 50, "offset": offset},
            )
        except Exception as e:
            logger.warning(
                "listar_anuncios_ativos_falha",
                offset=offset, erro=str(e),
            )
            break
        if not isinstance(resp, dict):
            break
        results = resp.get("results", []) or []
        if not isinstance(results, list) or not results:
            break
        items.extend(str(r) for r in results if r)
        paging = resp.get("paging", {}) or {}
        total = paging.get("total", 0) if isinstance(paging, dict) else 0
        offset += 50
        if offset >= total or offset > 5000:
            break
    return items


def _extrair_sku(item: dict[str, Any]) -> str | None:
    sku = item.get("seller_custom_field")
    if sku:
        return str(sku)
    for attr in item.get("attributes") or []:
        if attr.get("id") == "SELLER_SKU":
            val = attr.get("value_name") or attr.get("value_id")
            if val:
                return str(val)
    return None


async def _salvar_historico_onboarding(
    historico_repo: MigracaoExecutadaRepository,
    *,
    profile_id: UUID,
    ml_campaign_id: str,
    item_id: str,
    sku: str | None,
    deal_price: float | None,
    margem: float | None,
    sucesso: bool,
    dry_run: bool,
    erro: str | None,
) -> None:
    await historico_repo.add(record=MigracaoExecutadaRecord(
        id=novo_record_id(),
        profile_id=profile_id,
        campanha_origem_id=None,  # onboarding não tem origem local
        campanha_destino_ml_id=ml_campaign_id,
        campanha_destino_ml_nome="(ciclo C: onboarding total)",
        campanha_destino_ml_tipo="SELLER_CAMPAIGN",
        item_id=item_id,
        sku=sku,
        operacao="onboarding_total",
        destino_status="started",
        deal_price=deal_price,
        margem_pct_prevista=margem,
        sucesso=sucesso,
        dry_run=dry_run,
        erro_detalhe=erro,
        timestamp=agora_utc(),
    ))
