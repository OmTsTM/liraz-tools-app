"""Use case: play do rascunho via modelo passo3 (sem simulação).

Paralelo a `StartCampaignUseCase` (que depende de snapshot de simulação).
Aqui o rascunho tem só `skus_selecionados` — o deal_price/inflação são
computados NA HORA DO DISPARO via `SugerirDealPricesPorMargemUseCase`
(mesma lógica do seletor passo3 do front).

Fluxo:
  1. Valida perfil + campanha + janela de tempo
  2. Computa sugestao síncrono (fail-fast em loja desconectada / sem custos)
  3. Cria Application (sem Rollback — `aplicar_adicoes_skus_em_campanha`
     já tem snapshot interno pra reverter em ERROR_CREDIBILITY)
  4. Dispara job em background:
     a. POST /seller-promotions/promotions/sellers/{user_id} (cria SELLER_CAMPAIGN)
     b. `aplicar_adicoes_skus_em_campanha` (inflar + adicionar + descarte)
     c. Atualiza Application + Campaign final

Diferenças vs StartCampaignUseCase:
  - Não carrega snapshot
  - Não cria Rollback (o pipeline passo3 reverte sozinho em CREDIBILITY)
  - Application com `simulacao_id=""` (sentinela já usada pelo Revert)
  - Application.itens fica vazio (granularidade é por-resultado, não por-item
    com checkpoint de 5 em 5 — `aplicar_adicoes_skus_em_campanha` processa
    em paralelo e devolve o agregado)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.applications.entity import Application
from liraz_tools.domain.applications.use_cases import (
    CampaignNotReadyError,
    JanelaInsuficienteError,
    _datetime_fim,
    _finalizar_com_erro,
)
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.domain.skus.sugestao_use_case import (
    SugerirDealPricesPorMargemUseCase,
)
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.campaign_skus_apply import (
    DadosMargem,
    aplicar_adicoes_skus_em_campanha,
)
from liraz_tools.infrastructure.ml.client import MLAPIError, MLClient
from liraz_tools.infrastructure.ml.promotion_items import MLPromotionError
from liraz_tools.infrastructure.ml.promotions_create import (
    criar_seller_campaign,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.background.job_runner import JobRunner
    from liraz_tools.infrastructure.repositories.applications_repository import (
        ApplicationsRepository,
    )
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )
    from liraz_tools.infrastructure.repositories.profile_repository import (
        SQLAlchemyProfileRepository,
    )

logger = get_logger(__name__)

MIN_JANELA_DISPARO = timedelta(minutes=30)


def _gerar_application_id() -> str:
    """ID legível com timestamp + random."""
    now = datetime.now(UTC)
    rand = uuid4().hex[:4]
    return f"app_{now.strftime('%Y%m%d_%H%M%S')}_{rand}"


class StartCampaignPasso3UseCase:
    """Play do rascunho usando o modelo passo3 (sem simulação prévia).

    Usa a margem-alvo do perfil (`profile.config.margem_alvo_campanha`)
    pra computar deal_price por item via `SugerirDealPricesPorMargemUseCase`.
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        campaign_repo: CampaignRepository,
        applications_repo: ApplicationsRepository,
        creds_repo: PerProfileCredentialsRepository,
        job_runner: JobRunner,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._applications_repo = applications_repo
        self._creds_repo = creds_repo
        self._job_runner = job_runner

    async def execute(
        self,
        profile_id: UUID,
        campaign_id: UUID,
        *,
        imediato: bool = True,
    ) -> Application:
        # ─── Carrega entidades ──────────────────────────────────────────
        profile = await self._profile_repo.get_by_id(profile_id)
        campaign = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        # ─── Valida perfil ───────────────────────────────────────────────
        if profile.status.value != "connected":
            raise CampaignNotReadyError(
                f"loja '{profile.name}' está em status '{profile.status.value}' — "
                f"não pode disparar campanhas. Desarquive/reconecte a loja primeiro."
            )

        # ─── Valida status da campanha ───────────────────────────────────
        if campaign.status not in {"rascunho", "agendada"}:
            raise CampaignNotReadyError(
                f"campanha está em '{campaign.status}' — só rascunho ou "
                f"agendada podem ser iniciadas"
            )

        # ─── Valida SKUs ─────────────────────────────────────────────────
        skus = campaign.skus_selecionados or []
        if not skus:
            raise CampaignNotReadyError(
                "rascunho sem SKUs selecionados — edite a campanha e adicione "
                "anúncios antes de disparar"
            )

        # ─── Ajusta data/hora se 'imediato' ─────────────────────────────
        if imediato:
            now = datetime.now()
            campaign = campaign.model_copy(update={
                "data_inicio": now.date(),
                "hora_disparo": now.time().replace(microsecond=0),
            })
            await self._campaign_repo.update(campaign)
            logger.info(
                "campaign_play_now_passo3",
                campaign_id=str(campaign_id),
                new_data_inicio=campaign.data_inicio.isoformat(),
                new_hora=campaign.hora_disparo.isoformat(),
            )

        # ─── Valida janela de tempo ──────────────────────────────────────
        agora = datetime.now()
        fim = _datetime_fim(campaign)
        janela = fim - agora
        if janela < MIN_JANELA_DISPARO:
            raise JanelaInsuficienteError(
                f"janela até o fim da campanha é {janela} — mínimo "
                f"{MIN_JANELA_DISPARO} pra disparo. Aumente data_fim/hora_fim "
                f"antes de iniciar."
            )

        # ─── Computa sugestao (síncrono — fail-fast) ─────────────────────
        # Usa margem-alvo do perfil. SugerirDealPricesPorMargemUseCase
        # já valida loja conectada / custos / etc; se algo falhar pra
        # TODOS os itens, abortamos com erro amigável aqui mesmo.
        sugestao_uc = SugerirDealPricesPorMargemUseCase(
            self._profile_repo, self._creds_repo, CostOverridesRepository(),
        )
        sugestoes = await sugestao_uc.execute(
            profile_id,
            ml_campaign_id=None,  # campanha ainda não existe no ML
            item_ids=skus,
            margem_alvo=profile.config.margem_alvo_campanha,
        )

        # Filtra: itens com erro ou frete_a_confirmar não sobem.
        deal_prices: dict[str, float] = {}
        inflar_precos: dict[str, float] = {}
        fallback_deal_prices: dict[str, float] = {}
        dados_margem_map: dict[str, DadosMargem] = {}
        item_ids_validos: list[str] = []
        excluidos_frete: list[str] = []
        excluidos_erro: list[str] = []

        for s in sugestoes:
            if s.erro is not None:
                excluidos_erro.append(s.item_id)
                continue
            if s.frete_a_confirmar:
                excluidos_frete.append(s.item_id)
                continue
            if s.deal_price is None:
                excluidos_erro.append(s.item_id)
                continue
            item_ids_validos.append(s.item_id)
            deal_prices[s.item_id] = s.deal_price
            if s.precisa_inflacao and s.preco_inflado is not None:
                inflar_precos[s.item_id] = s.preco_inflado
            if s.quebra_frete_gratis_aplicada and s.fallback_deal_price is not None:
                fallback_deal_prices[s.item_id] = s.fallback_deal_price
            # Dados pro CLAMP (piso de margem default 17%).
            if (
                s.custo_unit is not None and s.comissao_pct is not None
                and s.aliquota is not None
            ):
                dados_margem_map[s.item_id] = DadosMargem(
                    custo=s.custo_unit, list_cost=s.list_cost,
                    comissao_pct=s.comissao_pct, aliquota=s.aliquota,
                )

        if not item_ids_validos:
            raise CampaignNotReadyError(
                f"nenhum SKU válido pra subir: {len(excluidos_erro)} com erro, "
                f"{len(excluidos_frete)} com frete a confirmar. Verifique custos "
                f"e ajuste os anúncios problemáticos manualmente."
            )

        logger.info(
            "passo3_sugestao_computada",
            campaign_id=str(campaign_id),
            total=len(skus),
            validos=len(item_ids_validos),
            excluidos_frete=len(excluidos_frete),
            excluidos_erro=len(excluidos_erro),
            com_inflacao=len(inflar_precos),
            com_fallback=len(fallback_deal_prices),
        )

        # ─── Cria Application ────────────────────────────────────────────
        app_id = _gerar_application_id()
        application = Application(
            application_id=app_id,
            campaign_id=campaign_id,
            profile_slug=profile.slug,
            simulacao_id="",  # sentinela — passo3 não usa snapshot
            estado="running",
            fase="applying_prices",  # = inflação (fase 1 do pipeline)
            total_itens=len(item_ids_validos),
        )
        self._applications_repo.save(profile.slug, application)

        # ─── Atualiza campaign pra executando ────────────────────────────
        campaign_em_execucao = campaign.model_copy(update={
            "status": "executando",
            "aplicacao_id": app_id,
        })
        await self._campaign_repo.update(campaign_em_execucao)

        # ─── Dispara job em background ───────────────────────────────────
        profile_slug = profile.slug
        profile_ml_user_id = profile.ml_user_id
        nome_campanha = campaign.nome
        data_inicio_dt = datetime.combine(campaign.data_inicio, campaign.hora_disparo)
        data_fim_dt = _datetime_fim(campaign)

        async def run_application() -> None:
            await _executar_play_passo3(
                profile_id=profile_id,
                profile_slug=profile_slug,
                ml_user_id=profile_ml_user_id,
                campaign_id=campaign_id,
                application_id=app_id,
                nome_campanha=nome_campanha,
                data_inicio=data_inicio_dt,
                data_fim=data_fim_dt,
                item_ids=item_ids_validos,
                deal_prices=deal_prices,
                inflar_precos=inflar_precos,
                fallback_deal_prices=fallback_deal_prices,
                dados_margem=dados_margem_map,
                creds_repo=self._creds_repo,
                applications_repo=self._applications_repo,
            )

        self._job_runner.start(f"app:{app_id}", run_application)
        logger.info(
            "application_started_passo3",
            application_id=app_id,
            campaign_id=str(campaign_id),
            total_itens=application.total_itens,
        )

        return application


async def _executar_play_passo3(
    *,
    profile_id: UUID,
    profile_slug: str,
    ml_user_id: int | None,
    campaign_id: UUID,
    application_id: str,
    nome_campanha: str,
    data_inicio: datetime,
    data_fim: datetime,
    item_ids: list[str],
    deal_prices: dict[str, float],
    inflar_precos: dict[str, float],
    fallback_deal_prices: dict[str, float],
    dados_margem: dict[str, DadosMargem],
    creds_repo: PerProfileCredentialsRepository,
    applications_repo: ApplicationsRepository,
) -> None:
    """Job em background: cria SELLER_CAMPAIGN + aplica SKUs via pipeline passo3."""
    if ml_user_id is None:
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            "perfil sem ml_user_id — não conectado ao ML",
        )
        return

    try:
        creds = creds_repo.get_app_credentials(profile_slug)
        tokens = creds_repo.get_tokens(profile_slug, ml_user_id)
    except Exception as e:
        logger.exception("application_passo3_creds_failed", application_id=application_id)
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            f"falha ao carregar credenciais: {e}",
        )
        return

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile_slug, new_tokens)

    try:
        async with MLClient(
            credentials=creds, tokens=tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            # ─── Fase A: cria SELLER_CAMPAIGN no ML ──────────────────────
            # Usa o wrapper canônico `criar_seller_campaign` (URL/payload
            # corretos: POST /seller-promotions/promotions?app_version=v2
            # com `promotion_type`, validado pelos endpoints "criar-ml-*").
            try:
                resp = await criar_seller_campaign(
                    ml,
                    nome=nome_campanha,
                    start_date=data_inicio,
                    finish_date=data_fim,
                )
                ml_promo_id = str(resp.get("id") or "")
                if not ml_promo_id:
                    raise MLPromotionError(
                        f"ML não retornou promotion id: {resp}"
                    )
            except (MLPromotionError, MLAPIError) as e:
                logger.exception("passo3_create_campaign_failed", application_id=application_id)
                await _finalizar_com_erro(
                    applications_repo, profile_slug, application_id,
                    f"falha ao criar campanha no ML: {e}",
                )
                await _atualizar_campaign_status(profile_id, campaign_id, "falha")
                return

            # Salva ml_campaign_id na application imediatamente
            app = applications_repo.get(profile_slug, application_id)
            if app is not None:
                app.ml_campaign_id = ml_promo_id
                app.fase = "adding_items"
                applications_repo.save(profile_slug, app)

            # ─── Fase B: inflação + adição via pipeline passo3 ───────────
            try:
                res = await aplicar_adicoes_skus_em_campanha(
                    ml,
                    ml_campaign_id=ml_promo_id,
                    item_ids=item_ids,
                    deal_prices=deal_prices,
                    inflar_precos=inflar_precos,
                    fallback_deal_prices=fallback_deal_prices,
                    dados_margem=dados_margem if dados_margem else None,
                    logger=logger,
                )
            except Exception as e:
                logger.exception("passo3_add_items_failed", application_id=application_id)
                await _finalizar_com_erro(
                    applications_repo, profile_slug, application_id,
                    f"campanha criada (id={ml_promo_id}) mas falha ao adicionar items: {e}",
                )
                # ml_campaign_id já foi obtido — registra mesmo em falha
                # pra usuário ver/limpar manualmente no painel ML.
                await _atualizar_campaign_final(
                    profile_id, campaign_id,
                    novo_status="falha",
                    ml_promo_id=ml_promo_id,
                    application_id=application_id,
                    skus_finais=[],
                )
                return

            # ─── Atualiza Application com agregados ──────────────────────
            app = applications_repo.get(profile_slug, application_id)
            if app is not None:
                app.itens_aplicados = len(res.adicionados)
                app.itens_falha = len(res.erros)
                app.itens_pulados = (
                    len(res.revertidos)
                    + len(res.reprecificados_20pct)
                    + len(res.pendentes_lock)
                )
                app.fase = "done"
                app.estado = "completed" if res.adicionados else "failed"
                if not res.adicionados:
                    app.erro = (
                        f"nenhum SKU entrou na campanha. "
                        f"{len(res.erros)} erros, "
                        f"{len(res.reprecificados_20pct)} descartados por credibilidade, "
                        f"{len(res.revertidos)} revertidos, "
                        f"{len(res.pendentes_lock)} pendentes de lock no ML."
                    )
                app.finalizado_em = datetime.now(UTC)
                applications_repo.save(profile_slug, app)

            # ─── Atualiza campanha local: skus finais + ML id + status ───
            # Inclui pendentes_lock: o ML costuma adicionar esses depois de
            # destravar (preço inflado mantido). Caller monitora via UI.
            skus_finais = (
                list(res.adicionados)
                + list(res.ja_estavam)
                + list(res.pendentes_lock)
            )
            novo_status = "ativa" if res.adicionados else "falha"
            await _atualizar_campaign_final(
                profile_id, campaign_id,
                novo_status=novo_status,
                ml_promo_id=ml_promo_id,
                application_id=application_id,
                skus_finais=skus_finais,
            )

            logger.info(
                "application_completed_passo3",
                application_id=application_id,
                ml_campaign_id=ml_promo_id,
                adicionados=len(res.adicionados),
                inflados=len(res.inflados),
                erros=len(res.erros),
                reprecificados_20pct=len(res.reprecificados_20pct),
                revertidos=len(res.revertidos),
                pendentes_lock=len(res.pendentes_lock),
            )

    except Exception as e:
        logger.exception("application_passo3_unexpected_error", application_id=application_id)
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            f"erro inesperado: {e}",
        )
        await _atualizar_campaign_status(profile_id, campaign_id, "falha")


async def _atualizar_campaign_status(
    profile_id: UUID, campaign_id: UUID, novo_status: str,
) -> None:
    """Atualiza só o status (fallback de erro)."""
    async with session_scope() as session:
        repo = CampaignRepository(session)
        c = await repo.get_by_id(profile_id, campaign_id)
        await repo.update(c.model_copy(update={"status": novo_status}))


async def _atualizar_campaign_final(
    profile_id: UUID,
    campaign_id: UUID,
    *,
    novo_status: str,
    ml_promo_id: str,
    application_id: str,
    skus_finais: list[str],
) -> None:
    """Atualiza estado final da campanha após play passo3: status, ml_id,
    aplicacao_id e a lista de SKUs com o que efetivamente entrou."""
    async with session_scope() as session:
        repo = CampaignRepository(session)
        c = await repo.get_by_id(profile_id, campaign_id)
        await repo.update(c.model_copy(update={
            "status": novo_status,
            "ml_campaign_id": ml_promo_id,
            "aplicacao_id": application_id,
            "skus_selecionados": skus_finais if skus_finais else None,
        }))
