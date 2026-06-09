"""Use cases de execução de campanha (Leva 5.4).

Coração do app: o que efetivamente WRITE no ML.

Fluxo do StartCampaignUseCase:
  1. Valida que a campanha pode ser iniciada (status, simulação, janela tempo)
  2. Se for "Play AGORA", atualiza data_inicio + hora_disparo pra agora
  3. Marca campanha como `executando`
  4. Cria Application + Rollback vazios no disco
  5. Dispara job em background (asyncio.create_task)
  6. Retorna application_id pro frontend pollar /applications/{id}

Job em background:
  Fase 1 — Aplicar preços:
    Pra cada item da simulação com fase1_acao != "mantido":
      a. APPEND no rollback (preco_anterior, preco_novo, etc)
      b. PUT /items/{id} {price: preco_novo}
      c. Atualiza Application.itens[i] = aplicado/falha
      d. Persiste Application a cada 5 itens (checkpoint)

  Fase 2 — Criar campanha no ML:
    POST /seller-promotions/promotions/sellers/{user_id}
    Body: {name, sub_type=FLEXIBLE_PERCENTAGE, start_date, end_date, ...}
    Salva ml_campaign_id

  Fase 3 — Adicionar items à campanha:
    Pra cada item com deal_price não-None:
      PUT /seller-promotions/items/{item_id} {promotion_id, deal_price}

Em qualquer falha: estado=failed, erro=mensagem amigável, parte aplicada
fica registrada no rollback pra reverter manualmente.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.applications.entity import (
    Application,
    ApplicationItem,
    Rollback,
    RollbackItem,
)
from liraz_tools.domain.campaigns.entity import Campaign
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.domain.profiles.repository import ProfileRepository
from liraz_tools.infrastructure.background.job_runner import JobRunner
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.client import MLAPIError, MLClient
from liraz_tools.infrastructure.repositories.applications_repository import (
    ApplicationsRepository,
    RollbacksRepository,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)

logger = get_logger(__name__)


# Janela mínima entre "agora" e data_fim+hora_fim pra permitir disparo.
# Decisão da Leva 5.3.4 (30min — permissivo o suficiente pra campanhas curtas).
MIN_JANELA_DISPARO = timedelta(minutes=30)


class CampaignNotReadyError(Exception):
    """Campanha não está em estado que permite disparo."""


class JanelaInsuficienteError(Exception):
    """Tempo restante até o fim é menor que MIN_JANELA_DISPARO."""


def _gerar_application_id() -> str:
    """ID legível com timestamp + random — fácil de listar em ordem."""
    now = datetime.now(UTC)
    rand = uuid4().hex[:4]
    return f"app_{now.strftime('%Y%m%d_%H%M%S')}_{rand}"


def _gerar_rollback_id() -> str:
    now = datetime.now(UTC)
    rand = uuid4().hex[:4]
    return f"rb_{now.strftime('%Y%m%d_%H%M%S')}_{rand}"


def _datetime_fim(campanha: Campaign) -> datetime:
    """Combina data_fim + hora_fim (ou 23:59:59 se hora_fim=None).

    Naive datetime (tz local do usuário, igual o resto do app).
    """
    fim_time = campanha.hora_fim or time(23, 59, 59)
    return datetime.combine(campanha.data_fim, fim_time)


class StartCampaignUseCase:
    """Inicia uma campanha — manual ('Play Agora') ou via scheduler.

    Modos:
    - imediato=True: muda data_inicio e hora_disparo pra agora antes de
      disparar. Usado pelo botão "Iniciar agora".
    - imediato=False: usa data_inicio + hora_disparo como estão. Usado
      pelo scheduler (Leva 5.5).

    Em ambos os casos, valida que data_fim + hora_fim está pelo menos
    30 minutos à frente de agora — senão lança JanelaInsuficienteError.
    """

    def __init__(
        self,
        profile_repo: ProfileRepository,
        campaign_repo: CampaignRepository,
        snapshots_repo: SnapshotsRepository,
        applications_repo: ApplicationsRepository,
        rollbacks_repo: RollbacksRepository,
        creds_repo: PerProfileCredentialsRepository,
        job_runner: JobRunner,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._snapshots_repo = snapshots_repo
        self._applications_repo = applications_repo
        self._rollbacks_repo = rollbacks_repo
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

        # ─── Valida status do PERFIL ────────────────────────────────────
        # Defesa em profundidade: scheduler já filtra perfis não-conectados,
        # mas chamadas manuais via API (botão Play) precisam dessa validação
        # aqui também — pra cobrir o caso de loja arquivada entre o
        # carregamento da UI e o clique.
        if profile.status.value != "connected":
            raise CampaignNotReadyError(
                f"loja '{profile.name}' está em status '{profile.status.value}' — "
                f"não pode disparar campanhas. Desarquive/reconecte a loja primeiro."
            )

        # ─── Valida status da CAMPANHA ───────────────────────────────────
        # Aceita 'rascunho-com-simulacao' OU 'agendada' (decisão 5.3.4).
        if campaign.status not in {"rascunho", "agendada"}:
            raise CampaignNotReadyError(
                f"campanha está em '{campaign.status}' — só rascunho ou "
                f"agendada podem ser iniciadas"
            )
        if campaign.simulacao_id is None:
            raise CampaignNotReadyError(
                "campanha precisa ter uma simulação associada pra ser iniciada"
            )

        # ─── Carrega simulação ───────────────────────────────────────────
        snap = self._snapshots_repo.get(profile.slug, campaign.simulacao_id)
        if snap is None:
            raise CampaignNotReadyError(
                f"simulação '{campaign.simulacao_id}' não encontrada"
            )
        if snap.get("estado") != "completed":
            raise CampaignNotReadyError(
                f"simulação '{campaign.simulacao_id}' não está completa"
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
                "campaign_play_now",
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

        # ─── Cria Application + Rollback no disco ────────────────────────
        app_id = _gerar_application_id()
        rb_id = _gerar_rollback_id()

        # Conta items a aplicar — aplica 3 filtros:
        #   1. fase1_acao != mantido (não tem o que aplicar)
        #   2. preco_novo definido
        #   3. se campaign.skus_selecionados está preenchido, só os escolhidos
        #      (None = todos, comportamento default — mantém compat)
        simulacoes = snap.get("simulacoes", [])
        skus_filtro = set(campaign.skus_selecionados) if campaign.skus_selecionados else None
        items_pra_aplicar = [
            s for s in simulacoes
            if s.get("fase1_acao") != "mantido"
            and s.get("preco_novo") is not None
            and (skus_filtro is None or s.get("item_id") in skus_filtro)
        ]

        # Validação adicional: se user explicitamente esvaziou a seleção
        # (lista vazia [] vs None), bloqueia
        if campaign.skus_selecionados is not None and len(campaign.skus_selecionados) == 0:
            raise CampaignNotReadyError(
                "nenhum SKU selecionado pra entrar na campanha — "
                "edite a campanha e selecione pelo menos um item"
            )
        if not items_pra_aplicar:
            raise CampaignNotReadyError(
                "nenhum SKU válido pra aplicar — verifique se a simulação "
                "tem itens com preço novo definido e que estão na seleção"
            )

        # Já validado acima que a campanha tem simulação (model_copy perde o
        # narrowing do mypy, por isso o assert explícito aqui).
        assert campaign.simulacao_id is not None

        application = Application(
            application_id=app_id,
            campaign_id=campaign_id,
            profile_slug=profile.slug,
            simulacao_id=campaign.simulacao_id,
            estado="running",
            fase="applying_prices",
            total_itens=len(items_pra_aplicar),
            rollback_id=rb_id,
        )
        self._applications_repo.save(profile.slug, application)

        rollback = Rollback(
            rollback_id=rb_id,
            application_id=app_id,
            campaign_id=campaign_id,
            profile_slug=profile.slug,
            simulacao_id=campaign.simulacao_id,
        )
        self._rollbacks_repo.save(profile.slug, rollback)

        # ─── Atualiza campaign pra executando ────────────────────────────
        campaign_em_execucao = campaign.model_copy(update={
            "status": "executando",
            "aplicacao_id": app_id,
            "rollback_id": rb_id,
        })
        await self._campaign_repo.update(campaign_em_execucao)

        # ─── Dispara job em background ────────────────────────────────────
        # IMPORTANTE: closure captura tudo necessário porque a session SQL
        # do request HTTP atual vai fechar. O job abre nova session.
        profile_slug = profile.slug
        profile_ml_user_id = profile.ml_user_id

        async def run_application() -> None:
            await _aplicar_e_criar_campanha(
                profile_id=profile_id,
                profile_slug=profile_slug,
                ml_user_id=profile_ml_user_id,
                campaign_id=campaign_id,
                application_id=app_id,
                rollback_id=rb_id,
                snap=snap,
                skus_selecionados=campaign.skus_selecionados,
                creds_repo=self._creds_repo,
                applications_repo=self._applications_repo,
                rollbacks_repo=self._rollbacks_repo,
            )

        self._job_runner.start(f"app:{app_id}", run_application)
        logger.info(
            "application_started",
            application_id=app_id,
            campaign_id=str(campaign_id),
            total_itens=application.total_itens,
        )

        return application


async def _aplicar_e_criar_campanha(
    *,
    profile_id: UUID,
    profile_slug: str,
    ml_user_id: int | None,
    campaign_id: UUID,
    application_id: str,
    rollback_id: str,
    snap: dict[str, Any],
    skus_selecionados: list[str] | None,
    creds_repo: PerProfileCredentialsRepository,
    applications_repo: ApplicationsRepository,
    rollbacks_repo: RollbacksRepository,
) -> None:
    """Job em background. Não retorna nada — atualiza state via repos.

    Função top-level (não-método) pra ser facilmente serializável e
    testável. Recebe tudo via params.

    `skus_selecionados` é a lista de item_ids escolhidos. None = todos.
    """
    if ml_user_id is None:
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            "perfil sem ml_user_id — não conectado ao ML",
        )
        return

    # Credenciais
    try:
        creds = creds_repo.get_app_credentials(profile_slug)
        tokens = creds_repo.get_tokens(profile_slug, ml_user_id)
    except Exception as e:
        logger.exception("application_creds_failed", application_id=application_id)
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            f"falha ao carregar credenciais: {e}",
        )
        return

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile_slug, new_tokens)

    try:
        async with MLClient(
            credentials=creds,
            tokens=tokens,
            on_tokens_refreshed=save_refreshed,
            max_concurrent=4,  # writes — mais conservador que reports
        ) as ml:
            # ─── Fase 1: Aplicar preços ─────────────────────────────────
            await _fase1_aplicar_precos(
                ml=ml,
                profile_slug=profile_slug,
                application_id=application_id,
                rollback_id=rollback_id,
                snap=snap,
                skus_selecionados=skus_selecionados,
                applications_repo=applications_repo,
                rollbacks_repo=rollbacks_repo,
            )

            # Recarrega state pra checar se Fase 1 falhou em todos os items
            app = applications_repo.get(profile_slug, application_id)
            if app is None:
                logger.error("application_disappeared", application_id=application_id)
                return

            # Se TODOS os items falharam, não tem porque tentar criar campanha
            if app.itens_aplicados == 0 and app.itens_falha > 0:
                await _finalizar_com_erro(
                    applications_repo, profile_slug, application_id,
                    f"falha ao aplicar preços em todos os {app.itens_falha} itens. "
                    f"Verifique credenciais e tente de novo.",
                )
                await _atualizar_campaign_status(
                    profile_id, campaign_id, "falha",
                )
                return

            # ─── Fase 2: Criar SELLER_CAMPAIGN no ML ─────────────────────
            app.fase = "creating_campaign"
            applications_repo.save(profile_slug, app)

            campaign = await _carregar_campanha(profile_id, campaign_id)
            try:
                ml_promo_id = await _fase2_criar_campanha(
                    ml=ml,
                    ml_user_id=ml_user_id,
                    campaign=campaign,
                )
            except MLAPIError as e:
                logger.exception("phase2_create_campaign_failed", application_id=application_id)
                await _finalizar_com_erro(
                    applications_repo, profile_slug, application_id,
                    f"preços aplicados, mas falha ao criar campanha no ML: {e}",
                )
                await _atualizar_campaign_status(profile_id, campaign_id, "falha")
                return

            app.ml_campaign_id = ml_promo_id
            applications_repo.save(profile_slug, app)

            # ─── Fase 3: Adicionar items à campanha ──────────────────────
            app.fase = "adding_items"
            applications_repo.save(profile_slug, app)

            try:
                await _fase3_adicionar_items(
                    ml=ml,
                    ml_promo_id=ml_promo_id,
                    app=app,
                    applications_repo=applications_repo,
                    profile_slug=profile_slug,
                )
            except MLAPIError as e:
                logger.exception("phase3_add_items_failed", application_id=application_id)
                await _finalizar_com_erro(
                    applications_repo, profile_slug, application_id,
                    f"campanha criada (id={ml_promo_id}) mas falha ao adicionar "
                    f"items: {e}",
                )
                # ml_campaign_id já está salvo, status=falha
                await _atualizar_campaign_status_with_ml_id(
                    profile_id, campaign_id, "falha", ml_promo_id, application_id, rollback_id,
                )
                return

            # ─── Tudo OK ─────────────────────────────────────────────────
            app.fase = "done"
            app.estado = "completed"
            app.finalizado_em = datetime.now(UTC)
            applications_repo.save(profile_slug, app)

            await _atualizar_campaign_status_with_ml_id(
                profile_id, campaign_id, "ativa",
                ml_promo_id, application_id, rollback_id,
            )
            logger.info(
                "application_completed",
                application_id=application_id,
                ml_campaign_id=ml_promo_id,
                aplicados=app.itens_aplicados,
                falhas=app.itens_falha,
            )

    except Exception as e:
        logger.exception("application_unexpected_error", application_id=application_id)
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            f"erro inesperado: {e}",
        )
        await _atualizar_campaign_status(profile_id, campaign_id, "falha")


async def _fase1_aplicar_precos(
    *,
    ml: MLClient,
    profile_slug: str,
    application_id: str,
    rollback_id: str,
    snap: dict[str, Any],
    skus_selecionados: list[str] | None,
    applications_repo: ApplicationsRepository,
    rollbacks_repo: RollbacksRepository,
) -> None:
    """Itera simulações, faz PUT /items/{id} pra cada uma.

    NÃO PARALELIZADO de propósito — preferimos fail-safe (se algo dá errado,
    paramos no item N e temos rollback completo dos N-1 anteriores). Pra
    ~200 items isso leva ~30s, aceitável.

    `skus_selecionados` filtra quais item_ids entram. None = todos (compat).
    """
    simulacoes = snap.get("simulacoes", [])
    skus_filtro = set(skus_selecionados) if skus_selecionados else None
    app = applications_repo.get(profile_slug, application_id)
    if app is None:
        return

    for sim in simulacoes:
        item_id = sim.get("item_id")
        fase1_acao = sim.get("fase1_acao")
        preco_novo = sim.get("preco_novo")
        preco_anterior = sim.get("preco_atual") or 0.0
        sku = sim.get("sku")
        title = sim.get("titulo") or sim.get("title")
        deal_price = sim.get("preco_campanha") or sim.get("deal_price")

        # Filtro de seleção do usuário: items fora da seleção são pulados.
        # IMPORTANTE: registra como pulado pra dar visibilidade no progresso
        # (mas com motivo diferente — fica claro na UI/log).
        if skus_filtro is not None and item_id not in skus_filtro:
            app.itens.append(ApplicationItem(
                item_id=item_id,
                sku=sku,
                title=title,
                preco_anterior=preco_anterior,
                preco_novo=preco_anterior,
                deal_price=deal_price,
                status="pulado",
            ))
            app.itens_pulados += 1
            continue

        # Items "mantido" são pulados — não tem o que aplicar
        if fase1_acao == "mantido" or preco_novo is None:
            app.itens.append(ApplicationItem(
                item_id=item_id,
                sku=sku,
                title=title,
                preco_anterior=preco_anterior,
                preco_novo=preco_anterior,
                deal_price=deal_price,
                status="pulado",
            ))
            app.itens_pulados += 1
            continue

        # Item válido pra aplicar
        item = ApplicationItem(
            item_id=item_id,
            sku=sku,
            title=title,
            preco_anterior=preco_anterior,
            preco_novo=preco_novo,
            deal_price=deal_price,
            status="pendente",
        )
        app.itens.append(item)

        # Registra no rollback ANTES de fazer o PUT
        # (se cair aqui, ainda temos como reverter)
        rollbacks_repo.append_item(
            profile_slug,
            rollback_id,
            RollbackItem(
                item_id=item_id,
                sku=sku,
                preco_anterior=preco_anterior,
                preco_novo=preco_novo,
                aplicado_em=datetime.now(UTC),
            ),
        )

        # PUT /items/{id}  # noqa: ERA001
        try:
            await ml.put(
                f"/items/{item_id}",
                {"price": preco_novo},
                max_retries=2,
            )
            item.status = "aplicado"
            item.aplicado_em = datetime.now(UTC)
            app.itens_aplicados += 1
        except MLAPIError as e:
            item.status = "falha"
            item.erro = str(e)[:300]
            app.itens_falha += 1
            logger.warning(
                "item_apply_failed",
                application_id=application_id,
                item_id=item_id,
                error=str(e)[:200],
            )

        # Checkpoint a cada 5 items pra polling do frontend mostrar progresso
        if (app.itens_aplicados + app.itens_falha + app.itens_pulados) % 5 == 0:
            applications_repo.save(profile_slug, app)

    # Salva estado final da Fase 1
    applications_repo.save(profile_slug, app)


async def _fase2_criar_campanha(
    *,
    ml: MLClient,
    ml_user_id: int,
    campaign: Campaign,
) -> str:
    """Cria SELLER_CAMPAIGN no ML com sub_type=FLEXIBLE_PERCENTAGE.

    Retorna o promotion_id (string).

    Spec extraída do `criar_campanha_promocional` do MCP — formato ML BR
    desde jul/2025.
    """
    # Datas no formato ISO ML
    inicio = datetime.combine(campaign.data_inicio, campaign.hora_disparo)
    fim = _datetime_fim(campaign)

    body = {
        "name": campaign.nome,
        "type": "SELLER_CAMPAIGN",
        "sub_type": "FLEXIBLE_PERCENTAGE",
        "start_date": inicio.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
        "finish_date": fim.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
    }
    logger.info("creating_ml_campaign", body=body)

    response = await ml.post(
        f"/seller-promotions/promotions/sellers/{ml_user_id}",
        body,
        max_retries=2,
    )
    promo_id = response.get("id")
    if not promo_id:
        raise MLAPIError(f"ML não retornou promotion id: {response}")
    return str(promo_id)


async def _fase3_adicionar_items(
    *,
    ml: MLClient,
    ml_promo_id: str,
    app: Application,
    applications_repo: ApplicationsRepository,
    profile_slug: str,
) -> None:
    """Pra cada item aplicado (status=aplicado) com deal_price, faz
    PUT /seller-promotions/items/{item_id} pra adicionar à campanha.
    """
    aplicaveis = [
        it for it in app.itens
        if it.status == "aplicado" and it.deal_price is not None
    ]
    logger.info("phase3_starting", total=len(aplicaveis), promo_id=ml_promo_id)

    for it in aplicaveis:
        body = {
            "promotion_id": ml_promo_id,
            "promotion_type": "SELLER_CAMPAIGN",
            "deal_price": it.deal_price,
        }
        try:
            await ml.put(
                f"/seller-promotions/items/{it.item_id}",
                body,
                max_retries=1,
            )
        except MLAPIError as e:
            # Não falha geral — registra o erro no item e continua.
            # A campanha ainda foi criada, só com menos items.
            it.erro = (it.erro or "") + f" | add_to_campaign: {e!s:.200}"
            logger.warning(
                "phase3_add_item_failed",
                item_id=it.item_id,
                error=str(e)[:200],
            )

    applications_repo.save(profile_slug, app)


# ─── Helpers de DB ───────────────────────────────────────────────────────


async def _carregar_campanha(profile_id: UUID, campaign_id: UUID) -> Campaign:
    """Abre sua própria session pra ler campaign (o job não compartilha
    session com o request HTTP que originou)."""
    async with session_scope() as session:
        repo = CampaignRepository(session)
        return await repo.get_by_id(profile_id, campaign_id)


async def _atualizar_campaign_status(
    profile_id: UUID, campaign_id: UUID, novo_status: str,
) -> None:
    async with session_scope() as session:
        repo = CampaignRepository(session)
        c = await repo.get_by_id(profile_id, campaign_id)
        await repo.update(c.model_copy(update={"status": novo_status}))


async def _atualizar_campaign_status_with_ml_id(
    profile_id: UUID,
    campaign_id: UUID,
    novo_status: str,
    ml_promo_id: str,
    application_id: str,
    rollback_id: str,
) -> None:
    async with session_scope() as session:
        repo = CampaignRepository(session)
        c = await repo.get_by_id(profile_id, campaign_id)
        await repo.update(c.model_copy(update={
            "status": novo_status,
            "ml_campaign_id": ml_promo_id,
            "aplicacao_id": application_id,
            "rollback_id": rollback_id,
        }))


async def _finalizar_com_erro(
    applications_repo: ApplicationsRepository,
    profile_slug: str,
    application_id: str,
    mensagem: str,
) -> None:
    """Marca application como failed e persiste."""
    app = applications_repo.get(profile_slug, application_id)
    if app is None:
        return
    app.estado = "failed"
    app.erro = mensagem
    app.finalizado_em = datetime.now(UTC)
    applications_repo.save(profile_slug, app)
    logger.error("application_failed", application_id=application_id, erro=mensagem)


# ─── Leva 5.6: Reverter campanha ─────────────────────────────────────────


class RevertNotAvailableError(Exception):
    """Campanha não tem rollback_id ou está em estado que não permite reverter."""


class RevertCampaignUseCase:
    """Reverte uma campanha aplicada, voltando preços ao estado anterior.

    Pré-requisitos:
    - Campanha tem `rollback_id` preenchido
    - Status in {ativa, finalizada, falha} — campanhas que efetivamente
      aplicaram preços

    Fluxo:
    1. Carrega Rollback do disk (lista de preços anteriores)
    2. Cria novo Application com phase=reverting (auditoria)
    3. Dispara job em background:
       a. Pra cada RollbackItem, PUT /items/{id} com price=preco_anterior
       b. Marca campanha como `cancelada` no sucesso (ou `falha` no erro)

    Decisão importante: NÃO removemos a SELLER_CAMPAIGN do ML (se houver).
    Ela pode continuar existindo no ML, mas como os preços já voltaram,
    o desconto fica inativo na prática. Razão: o endpoint de delete da
    SELLER_CAMPAIGN tem comportamento ambíguo e arriscado. Preferimos
    o user pausar/excluir manualmente no painel do ML se quiser.
    """

    def __init__(
        self,
        profile_repo: ProfileRepository,
        campaign_repo: CampaignRepository,
        applications_repo: ApplicationsRepository,
        rollbacks_repo: RollbacksRepository,
        creds_repo: PerProfileCredentialsRepository,
        job_runner: JobRunner,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._applications_repo = applications_repo
        self._rollbacks_repo = rollbacks_repo
        self._creds_repo = creds_repo
        self._job_runner = job_runner

    async def execute(
        self,
        profile_id: UUID,
        campaign_id: UUID,
    ) -> Application:
        profile = await self._profile_repo.get_by_id(profile_id)
        campaign = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        # Defesa em profundidade: perfil precisa estar connected pra reverter
        # (a operação faz PUT no ML como qualquer outra).
        if profile.status.value != "connected":
            raise RevertNotAvailableError(
                f"loja '{profile.name}' está em status '{profile.status.value}' — "
                f"não pode reverter. Desarquive/reconecte a loja primeiro."
            )

        # Valida estado da campanha
        if campaign.status not in {"ativa", "finalizada", "falha"}:
            raise RevertNotAvailableError(
                f"campanha em '{campaign.status}' não pode ser revertida — "
                f"só {{ativa, finalizada, falha}} são reversíveis"
            )
        if not campaign.rollback_id:
            raise RevertNotAvailableError(
                "campanha não tem rollback_id — nada pra reverter"
            )

        # Carrega rollback
        rollback = self._rollbacks_repo.get(profile.slug, campaign.rollback_id)
        if rollback is None:
            raise RevertNotAvailableError(
                f"rollback '{campaign.rollback_id}' não encontrado em disco"
            )
        if not rollback.itens:
            raise RevertNotAvailableError(
                "rollback está vazio — nada pra reverter (campanha não chegou "
                "a aplicar nenhum preço)"
            )

        # Cria nova Application pra auditoria da reversão
        revert_app_id = _gerar_application_id().replace("app_", "rev_")

        # Cria items pendentes baseados no rollback
        items_pendentes = [
            ApplicationItem(
                item_id=rb_item.item_id,
                sku=rb_item.sku,
                title=None,  # não temos title no rollback, ok
                preco_anterior=rb_item.preco_novo,  # estado atual no ML
                preco_novo=rb_item.preco_anterior,  # vamos colocar isso
                status="pendente",
            )
            for rb_item in rollback.itens
        ]

        revert_app = Application(
            application_id=revert_app_id,
            campaign_id=campaign_id,
            profile_slug=profile.slug,
            simulacao_id=campaign.simulacao_id or "",
            estado="running",
            fase="reverting",
            total_itens=len(items_pendentes),
            itens=items_pendentes,
        )
        self._applications_repo.save(profile.slug, revert_app)

        # Marca campanha como executando (re-uso de status — vai virar
        # cancelada após o revert ou falha se der erro)
        await self._campaign_repo.update(campaign.model_copy(update={
            "status": "executando",
            "aplicacao_id": revert_app_id,  # aponta pra reversão agora
        }))

        # Dispara job em background
        profile_slug = profile.slug
        ml_user_id = profile.ml_user_id

        async def run_revert() -> None:
            await _reverter_precos(
                profile_id=profile_id,
                profile_slug=profile_slug,
                ml_user_id=ml_user_id,
                campaign_id=campaign_id,
                application_id=revert_app_id,
                rollback=rollback,
                creds_repo=self._creds_repo,
                applications_repo=self._applications_repo,
            )

        self._job_runner.start(f"rev:{revert_app_id}", run_revert)
        logger.info(
            "revert_started",
            application_id=revert_app_id,
            campaign_id=str(campaign_id),
            total_itens=len(items_pendentes),
        )
        return revert_app


async def _reverter_precos(
    *,
    profile_id: UUID,
    profile_slug: str,
    ml_user_id: int | None,
    campaign_id: UUID,
    application_id: str,
    rollback: Rollback,  # noqa: ARG001  (mantido na assinatura por simetria/keyword)
    creds_repo: PerProfileCredentialsRepository,
    applications_repo: ApplicationsRepository,
) -> None:
    """Job em background. Reverte preços via PUT /items/{id}."""
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
        logger.exception("revert_creds_failed", application_id=application_id)
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            f"falha ao carregar credenciais: {e}",
        )
        return

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile_slug, new_tokens)

    try:
        async with MLClient(
            credentials=creds,
            tokens=tokens,
            on_tokens_refreshed=save_refreshed,
            max_concurrent=4,
        ) as ml:
            app = applications_repo.get(profile_slug, application_id)
            if app is None:
                logger.error("revert_app_disappeared", application_id=application_id)
                return

            # Itera items, faz PUT pra cada um
            for i, item in enumerate(app.itens):
                try:
                    await ml.put(
                        f"/items/{item.item_id}",
                        {"price": item.preco_novo},  # preco_anterior original
                        max_retries=2,
                    )
                    item.status = "aplicado"
                    item.aplicado_em = datetime.now(UTC)
                    app.itens_aplicados += 1
                except MLAPIError as e:
                    item.status = "falha"
                    item.erro = str(e)[:300]
                    app.itens_falha += 1
                    logger.warning(
                        "revert_item_failed",
                        application_id=application_id,
                        item_id=item.item_id,
                        error=str(e)[:200],
                    )

                # Checkpoint a cada 5 items
                if (i + 1) % 5 == 0:
                    applications_repo.save(profile_slug, app)

            # Estado final
            app.fase = "done"
            app.finalizado_em = datetime.now(UTC)

            # Sucesso: pelo menos UM item foi revertido com sucesso
            if app.itens_aplicados > 0:
                app.estado = "completed"
                applications_repo.save(profile_slug, app)
                # Campanha vira `cancelada` — preços voltaram
                await _atualizar_campaign_status(
                    profile_id, campaign_id, "cancelada",
                )
                logger.info(
                    "revert_completed",
                    application_id=application_id,
                    aplicados=app.itens_aplicados,
                    falhas=app.itens_falha,
                )
            else:
                # Todos falharam
                app.estado = "failed"
                app.erro = (
                    f"reversão falhou em todos os {app.itens_falha} itens. "
                    f"Verifique credenciais e tente de novo."
                )
                applications_repo.save(profile_slug, app)
                await _atualizar_campaign_status(profile_id, campaign_id, "falha")
                logger.error(
                    "revert_all_failed",
                    application_id=application_id,
                    total=app.itens_falha,
                )

    except Exception as e:
        logger.exception("revert_unexpected_error", application_id=application_id)
        await _finalizar_com_erro(
            applications_repo, profile_slug, application_id,
            f"erro inesperado na reversão: {e}",
        )
        await _atualizar_campaign_status(profile_id, campaign_id, "falha")
