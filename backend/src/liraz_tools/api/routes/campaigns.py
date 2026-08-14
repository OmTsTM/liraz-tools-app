"""Endpoints de campanhas.

Rotas (Leva 5.3 — CRUD + transições de status sem execução no ML):
  POST   /api/profiles/{id}/campaigns                — criar (rascunho)
  GET    /api/profiles/{id}/campaigns                — listar
  GET    /api/profiles/{id}/campaigns/{cid}          — detalhe
  PATCH  /api/profiles/{id}/campaigns/{cid}          — editar campos
  DELETE /api/profiles/{id}/campaigns/{cid}          — apagar
  POST   /api/profiles/{id}/campaigns/{cid}/schedule   — rascunho → agendada
  POST   /api/profiles/{id}/campaigns/{cid}/unschedule — agendada → rascunho
  POST   /api/profiles/{id}/campaigns/{cid}/cancel     — qualquer ativo → cancelada

Levas futuras vão adicionar:
  POST   /api/profiles/{id}/campaigns/{cid}/start    — dispara execução (Leva 5.4)
  POST   /api/profiles/{id}/campaigns/{cid}/revert   — reverter via rollback (Leva 5.6)
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from liraz_tools.api.deps import (
    CredsRepo,
    DbSession,
    ProfileRepo,
    require_operator_in_profile,
)
from liraz_tools.api.schemas.campaign_schemas import (
    CampaignItemEligible,
    CampaignItemInfo,
    CampaignResponse,
    CreateCampaignRequest,
    ImportMLCampaignRequest,
    UpdateCampaignRequest,
    UpdateCampaignSkusRequest,
)
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.criar_ml_completo_use_case import (
    CriarSellerCampaignCompletoUseCase,
)
from liraz_tools.domain.campaigns.criar_ml_use_case import (
    CriarMLCampaignError,
    CriarSellerCampaignNoMLUseCase,
    NomeJaExisteLocalError,
)
from liraz_tools.domain.campaigns.entity import Campaign
from liraz_tools.domain.campaigns.margens_use_cases import (
    CampaignNaoNoMLError,
    EditarMargemSkusCampanhaUseCase,
    ListarMargensCampanhaUseCase,
)
from liraz_tools.domain.campaigns.ml_import_use_case import (
    ImportMLCampaignUseCase,
    MLCampaignNotFoundError,
    MLImportError,
)
from liraz_tools.domain.campaigns.ml_merge_use_case import (
    ListCampaignsWithMLMergeUseCase,
)
from liraz_tools.domain.campaigns.skus_use_cases import (
    HidrarItemsUseCase,
    ListSkusElegiveisUseCase,
    UpdateCampaignSkusUseCase,
)
from liraz_tools.domain.campaigns.use_cases import (
    CampaignError,
    CampaignNameTakenError,
    CampaignNotEditableError,
    CancelCampaignUseCase,
    CreateCampaignUseCase,
    DeleteCampaignUseCase,
    GetCampaignUseCase,
    InvalidDateError,
    ReactivateCampaignUseCase,
    ScheduleCampaignUseCase,
    SimulationNotReadyError,
    UnscheduleCampaignUseCase,
    UpdateCampaignUseCase,
)
from liraz_tools.domain.migration.cobertura import (
    CorrigirCoberturaUseCase,
    VerificarCoberturaUseCase,
)
from liraz_tools.domain.migration.executor import ExecutarMigracoesUseCase
from liraz_tools.domain.migration.use_cases import (
    DetectarOportunidadesMigracaoUseCase,
)
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.infrastructure.background.migration_scheduler import (
    _processar_perfil,
    _ultima_execucao_por_perfil,
    scheduler_habilitado,
)
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotion_items import (
    DealPriceInvalidError,
    ItemAlreadyInCampaignError,
    ItemNotEligibleError,
    ItemNotInCampaignError,
    MLPromotionError,
    MLServerError,
    adicionar_sku_em_campanha,
    remover_sku_de_campanha,
)
from liraz_tools.infrastructure.ml.promotions_create import (
    CampaignNameConflictError,
    InvalidDatesError,
    StartDateTooFarError,
    criar_seller_campaign,
)
from liraz_tools.infrastructure.ml.promotions_lookup import (
    PAGE_SIZE,
    _normalizar_status,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignNotFoundError,
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.campaigns_list_cache import (
    CampaignsListCache,
    get_campaigns_list_cache,
)
from liraz_tools.infrastructure.repositories.descarte_migracao_cache_repository import (
    DescarteMigracaoCacheRepository,
)
from liraz_tools.infrastructure.repositories.migracao_executada_repository import (
    MigracaoExecutadaRepository,
)
from liraz_tools.infrastructure.repositories.pricing_cache import (
    FeeReportCache,
    get_fee_report_cache,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
        CostOverridesRepository,
    )

router = APIRouter(
    prefix="/api/profiles",
    tags=["campaigns"],
    # Campanhas (CRUD + sync ML) é fluxo de operador — viewer não vê.
    dependencies=[Depends(require_operator_in_profile)],
)
logger = get_logger(__name__)


# ─── Dependency injection ───────────────────────────────────────────────────


def _get_campaign_repo(session: DbSession) -> CampaignRepository:
    return CampaignRepository(session)


def _get_snapshots_repo() -> SnapshotsRepository:
    return SnapshotsRepository()


CampaignRepoDep = Annotated[CampaignRepository, Depends(_get_campaign_repo)]
SnapshotsRepoDep = Annotated[SnapshotsRepository, Depends(_get_snapshots_repo)]
PricingCacheDep = Annotated[FeeReportCache, Depends(get_fee_report_cache)]
CampaignsListCacheDep = Annotated[CampaignsListCache, Depends(get_campaigns_list_cache)]


# ─── Helpers ────────────────────────────────────────────────────────────────


def _to_response(campaign: Campaign) -> CampaignResponse:
    """Converte entity → response (preenche campos derivados)."""
    return CampaignResponse(
        id=campaign.id,
        profile_id=campaign.profile_id,
        nome=campaign.nome,
        simulacao_id=campaign.simulacao_id,
        data_inicio=campaign.data_inicio,
        data_fim=campaign.data_fim,
        hora_disparo=campaign.hora_disparo,
        hora_fim=campaign.hora_fim,
        skus_selecionados=campaign.skus_selecionados,
        status=campaign.status,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        ml_campaign_id=campaign.ml_campaign_id,
        aplicacao_id=campaign.aplicacao_id,
        rollback_id=campaign.rollback_id,
        erro=campaign.erro,
        archived_at=campaign.archived_at,
        duracao_dias=campaign.duracao_dias,
        is_editable=campaign.is_editable,
        origem=campaign.origem,
    )


def _raise_404(e: Exception) -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


def _raise_400(e: Exception) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


# ─── Endpoints ──────────────────────────────────────────────────────────────


@router.post(
    "/{profile_id}/campaigns",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign(
    profile_id: UUID,
    body: CreateCampaignRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    snapshots_repo: SnapshotsRepoDep,
) -> CampaignResponse:
    """Cria nova campanha em status `rascunho`. simulacao_id é opcional."""
    use_case = CreateCampaignUseCase(profile_repo, campaign_repo, snapshots_repo)
    try:
        result = await use_case.execute(
            profile_id=profile_id,
            nome=body.nome,
            data_inicio=body.data_inicio,
            data_fim=body.data_fim,
            hora_disparo=body.hora_disparo,
            hora_fim=body.hora_fim,
            simulacao_id=body.simulacao_id,
            skus_selecionados=body.skus_selecionados,
            forcar_nome=body.forcar_nome,
        )
    except ProfileNotFoundError as e:
        _raise_404(e)
    except CampaignNameTakenError as e:
        # 409 Conflict — frontend usa `sugestao` pra oferecer próximo nome livre
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(e), "sugestao": e.sugestao, "nome_tentado": e.nome},
        ) from e
    except (SimulationNotReadyError, InvalidDateError) as e:
        _raise_400(e)
    except (CampaignError, ValueError) as e:
        _raise_400(e)
    return _to_response(result)


@router.get(
    "/{profile_id}/campaigns",
    response_model=list[CampaignResponse],
)
async def list_campaigns(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    cache: CampaignsListCacheDep,
    include_archived: bool = False,
) -> list[CampaignResponse]:
    """Lista campanhas locais + SELLER_CAMPAIGN do ML (Leva 5.9.1).

    Campanhas locais sempre aparecem. Campanhas SELLER_CAMPAIGN do ML que
    NÃO foram criadas pelo app (sem `ml_campaign_id` vinculado a uma
    local) aparecem com `origem="ml"` — read-only no frontend.

    Em caso de falha ao consultar ML (token, rate limit, etc), retorna só
    as locais — o app nunca quebra por causa disso.

    `include_archived=true` inclui campanhas auto-arquivadas (15 dias após
    data_fim). Default false — esconde as antigas. Não afeta campanhas
    do ML (elas não passam pelo nosso scheduler de soft-delete).
    """
    # Valida que perfil existe (lança 404 se não)
    try:
        await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        _raise_404(e)

    # Cache curto do merge ML (ver CampaignsListCache): evita re-sincronizar
    # tudo com o ML em rajadas de refetch. Invalidado por versão a cada escrita.
    cached = cache.get(profile_id, include_archived)
    if cached is not None:
        return [CampaignResponse.model_validate(d) for d in cached]

    use_case = ListCampaignsWithMLMergeUseCase(
        profile_repo, campaign_repo, creds_repo,
    )
    campaigns_dicts = await use_case.execute(
        profile_id, include_archived=include_archived,
    )
    cache.set(profile_id, include_archived, campaigns_dicts)
    # Validamos via Pydantic pra garantir contrato — mesmo formato anterior
    # + campo novo "origem"
    return [CampaignResponse.model_validate(d) for d in campaigns_dicts]


@router.post(
    "/{profile_id}/campaigns/import-from-ml",
    response_model=CampaignResponse,
)
async def import_ml_campaign(
    profile_id: UUID,
    body: ImportMLCampaignRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> CampaignResponse:
    """Importa (espelha localmente) uma SELLER_CAMPAIGN do ML (Leva 5.9.2).

    Idempotente: se já existe campanha local com mesmo `ml_promotion_id`,
    retorna a existente em vez de criar duplicata.

    Não retorna 201 porque a operação é frequentemente "lookup" (já existe).
    """
    try:
        await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        _raise_404(e)

    use_case = ImportMLCampaignUseCase(profile_repo, campaign_repo, creds_repo)
    try:
        result = await use_case.execute(
            profile_id, body.ml_promotion_id, full_scan=body.full_scan,
        )
    except MLCampaignNotFoundError as e:
        _raise_404(e)
    except MLImportError as e:
        _raise_400(e)
    return _to_response(result)


@router.post("/{profile_id}/campaigns/{ml_promotion_id}/force-resync")
async def force_resync_ml_campaign(
    profile_id: UUID,
    ml_promotion_id: str,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """**DEBUG**: força resync ignorando cache. POST (não GET) porque tem efeito
    colateral — evita disparo via `<img>`/link cross-site (CSRF drive-by).

    Diferente do `import-from-ml`: retorna estado ANTES e DEPOIS pra
    confirmar que a escrita aconteceu. Útil pra debugar quando o auto-sync
    falsamente reporta "já estava sincronizado".
    """
    existing_antes = await campaign_repo.find_by_ml_id(profile_id, ml_promotion_id)
    qtd_antes = (
        len(existing_antes.skus_selecionados or []) if existing_antes else 0
    )

    use_case = ImportMLCampaignUseCase(profile_repo, campaign_repo, creds_repo)
    try:
        # force-resync = varredura completa (é o ponto do endpoint de debug).
        resultado = await use_case.execute(
            profile_id, ml_promotion_id, full_scan=True,
        )
    except MLCampaignNotFoundError as e:
        _raise_404(e)
    except MLImportError as e:
        _raise_400(e)

    qtd_depois = len(resultado.skus_selecionados or [])

    # Re-busca direto do banco pra confirmar persistência
    existing_apos = await campaign_repo.find_by_ml_id(profile_id, ml_promotion_id)
    qtd_no_banco = (
        len(existing_apos.skus_selecionados or []) if existing_apos else 0
    )

    return {
        "campaign_id_local": str(resultado.id),
        "ml_campaign_id": ml_promotion_id,
        "status": resultado.status,
        "qtd_skus_antes_do_sync": qtd_antes,
        "qtd_skus_retornado_pelo_use_case": qtd_depois,
        "qtd_skus_no_banco_apos_commit": qtd_no_banco,
        "consistencia_ok": qtd_depois == qtd_no_banco,
        "primeiros_5_skus": (resultado.skus_selecionados or [])[:5],
    }


# ============================================================================
# Leva 5.9.4.A — Edição manual de SKUs em campanha ML (POST/DELETE)
# ============================================================================


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/skus/{item_id}/add-to-ml",
)
async def add_sku_to_ml_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    item_id: str,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    deal_price: Annotated[float | None, Query()] = None,
    top_deal_price: Annotated[float | None, Query()] = None,
) -> dict[str, Any]:
    """Adiciona um SKU a uma campanha ML (sincroniza ML + banco local).

    A campanha local precisa ter `origem='ml'` e `ml_campaign_id` definido.
    Após sucesso no ML, atualiza `skus_selecionados` localmente.

    Pra SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE, o ML ignora `deal_price` (usa
    a regra de desconto da campanha). Pra DEAL/PRICE_DISCOUNT é obrigatório.

    Retorna a resposta crua do ML + estado atualizado da campanha local.
    """
    # 1) Carrega campanha local
    try:
        campaign = await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    if not campaign.ml_campaign_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Esse endpoint só opera em campanhas que estão no ML "
                "(`ml_campaign_id` setado). Pra campanhas locais ainda não "
                "disparadas, edite `skus_selecionados` via PATCH "
                "/campaigns/{id}/skus."
            ),
        )

    # 2) Resolve credenciais
    profile = await profile_repo.get_by_id(profile_id)
    if profile.status.value != "connected" or profile.ml_user_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"loja '{profile.name}' não está conectada ao ML",
        )

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

    async def save_refreshed(new_tokens: Any) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    # 3) Chama ML — pode falhar com várias exceções tipadas
    async with MLClient(
        creds, tokens, on_tokens_refreshed=save_refreshed,
    ) as ml:
        try:
            resp_ml = await adicionar_sku_em_campanha(
                ml,
                item_id=item_id,
                promotion_id=campaign.ml_campaign_id,
                # SELLER_CAMPAIGN por enquanto. Quando suportarmos outros
                # tipos (DEAL, PRICE_DISCOUNT) na 5.9.4.B, vir da campanha.
                promotion_type="SELLER_CAMPAIGN",
                deal_price=deal_price,
                top_deal_price=top_deal_price,
            )
        except ItemAlreadyInCampaignError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except DealPriceInvalidError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except ItemNotEligibleError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except MLServerError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except MLPromotionError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # 4) Atualiza local: adiciona ao skus_selecionados se ainda não estiver
    skus_atuais = list(campaign.skus_selecionados or [])
    if item_id not in skus_atuais:
        skus_atuais.append(item_id)
        atualizada = campaign.model_copy(
            update={"skus_selecionados": skus_atuais},
        )
        await campaign_repo.update(atualizada)
        logger.info(
            "campaign_sku_added_local",
            campaign_id=str(campaign_id),
            item_id=item_id,
            total_skus=len(skus_atuais),
        )

    return {
        "ok": True,
        "item_id": item_id,
        "resposta_ml": resp_ml,
        "total_skus_apos": len(skus_atuais),
    }


@router.delete(
    "/{profile_id}/campaigns/{campaign_id}/skus/{item_id}/remove-from-ml",
)
async def remove_sku_from_ml_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    item_id: str,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    offer_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Remove um SKU duma campanha ML (sincroniza ML + banco local).

    Análogo a add-to-ml. Se o item já não estava no ML, devolve 200 com
    `ja_nao_estava_no_ml=true` e mesmo assim limpa o local (idempotência).
    """
    try:
        campaign = await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    if not campaign.ml_campaign_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Esse endpoint só opera em campanhas que estão no ML "
                "(`ml_campaign_id` setado)."
            ),
        )

    profile = await profile_repo.get_by_id(profile_id)
    if profile.status.value != "connected" or profile.ml_user_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"loja '{profile.name}' não está conectada ao ML",
        )

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

    async def save_refreshed(new_tokens: Any) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    ja_nao_estava = False
    async with MLClient(
        creds, tokens, on_tokens_refreshed=save_refreshed,
    ) as ml:
        try:
            await remover_sku_de_campanha(
                ml,
                item_id=item_id,
                promotion_id=campaign.ml_campaign_id,
                promotion_type="SELLER_CAMPAIGN",
                offer_id=offer_id,
            )
        except ItemNotInCampaignError:
            # Já não estava no ML — OK, continuamos pra limpar o local
            ja_nao_estava = True
        except MLServerError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except MLPromotionError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # Atualiza local — remove se estava
    skus_atuais = list(campaign.skus_selecionados or [])
    if item_id in skus_atuais:
        skus_atuais.remove(item_id)
        atualizada = campaign.model_copy(
            update={"skus_selecionados": skus_atuais if skus_atuais else None},
        )
        await campaign_repo.update(atualizada)
        logger.info(
            "campaign_sku_removed_local",
            campaign_id=str(campaign_id),
            item_id=item_id,
            total_skus=len(skus_atuais),
        )

    return {
        "ok": True,
        "item_id": item_id,
        "ja_nao_estava_no_ml": ja_nao_estava,
        "total_skus_apos": len(skus_atuais),
    }


# ============================================================================
# Leva 5.9.4.B — Detecção de oportunidades de migração
# ============================================================================


@router.get(
    "/{profile_id}/campaigns/{campaign_id}/oportunidades-migracao",
)
async def listar_oportunidades_migracao(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    db_session: DbSession,
    incluir_programadas: Annotated[bool, Query()] = True,
    ignorar_historico: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Detecta SKUs duma campanha guarda-chuva (origem='local') que podem
    migrar pra campanhas ML ajustáveis (DEAL/PRICE_DISCOUNT) mantendo
    margem líquida dentro da faixa [margem_min, margem_max] definida na
    campanha (ou perfil).

    Parâmetros:
      - `incluir_programadas` (default true): inclui campanhas ML em
        status `pending`. Pra essas, `pode_migrar_agora` vem False
        (são só preview de oportunidades futuras).
      - `ignorar_historico` (default false): se True, ignora o filtro de
        migrações já executadas com sucesso nos últimos 7 dias. Use quando
        você removeu o item manualmente no painel ML e quer re-detectar
        imediatamente sem esperar a janela.

    Retorna lista ordenada por (margem_pct DESC, sold_quantity DESC).
    """
    try:
        await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    use_case = DetectarOportunidadesMigracaoUseCase(
        profile_repo, campaign_repo, creds_repo,
        descarte_cache_repo=DescarteMigracaoCacheRepository(db_session),
        historico_migracoes_repo=MigracaoExecutadaRepository(db_session),
    )
    oportunidades = await use_case.execute(
        profile_id, campaign_id,
        incluir_programadas=incluir_programadas,
        ignorar_historico=ignorar_historico,
    )

    # Serializa pra JSON
    return {
        "campaign_origem_id": str(campaign_id),
        "total_oportunidades": len(oportunidades),
        "incluir_programadas": incluir_programadas,
        "oportunidades": [
            {
                "item_id": o.item_id,
                "sku": o.sku,
                "titulo": o.titulo,
                "sold_quantity": o.sold_quantity,
                "campanha_destino_ml_id": o.campanha_destino_ml_id,
                "campanha_destino_ml_nome": o.campanha_destino_ml_nome,
                "campanha_destino_ml_tipo": o.campanha_destino_ml_tipo,
                "campanha_destino_ml_status": o.campanha_destino_ml_status,
                "deal_price_sugerido": o.deal_price_sugerido,
                "preco_atual": o.preco_atual,
                "margem_pct_pos_migracao": o.margem_pct_pos_migracao,
                "margem_brl_pos_migracao": o.margem_brl_pos_migracao,
                "margem_pct_atual": o.margem_pct_atual,
                "pode_migrar_agora": o.pode_migrar_agora,
            }
            for o in oportunidades
        ],
    }


# ============================================================================
# Leva 5.9.4.C.1 — Executor de migrações + histórico
# ============================================================================


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/executar-migracoes",
)
async def executar_migracoes(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    db_session: DbSession,
    dry_run: Annotated[bool, Query()] = True,
    apenas_skus: Annotated[
        str | None,
        Query(
            description=(
                "Lista de MLBs separados por vírgula. Se fornecido, executa só"
                " esses SKUs. Útil pra testar gradualmente."
                " Ex: MLB6590366814,MLB6590366812"
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Executa migrações automáticas pra uma campanha guarda-chuva.

    Fluxo:
    1. Roda detector → lista oportunidades pra todos os SKUs da guarda-chuva
    2. Pra cada SKU, escolhe destino canônico (regra de preferência:
       especial > started > maior margem > sold_quantity)
    3. Se `dry_run=true` (default): só simula, marca histórico como dry_run
    4. Se `dry_run=false`: chama POST no ML pra cada item, salva resultado

    Use `apenas_skus` (vírgula-separado) pra rodar só pra alguns SKUs.
    Recomendado: rode com 1 SKU primeiro em dry_run=false pra ganhar
    confiança antes de liberar todos.

    O retorno traz resumo (contadores) + lista limitada de erros.
    Pra ver detalhes de cada operação, use `GET /migracao/historico`.
    """
    try:
        await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    skus_filtrados = None
    if apenas_skus:
        skus_filtrados = {
            s.strip() for s in apenas_skus.split(",") if s.strip()
        }

    detector = DetectarOportunidadesMigracaoUseCase(
        profile_repo, campaign_repo, creds_repo,
        descarte_cache_repo=DescarteMigracaoCacheRepository(db_session),
        historico_migracoes_repo=MigracaoExecutadaRepository(db_session),
    )
    historico_repo = MigracaoExecutadaRepository(db_session)
    executor = ExecutarMigracoesUseCase(
        detector=detector,
        profile_repo=profile_repo,
        campaign_repo=campaign_repo,
        creds_repo=creds_repo,
        historico_repo=historico_repo,
    )
    resultado = await executor.execute(
        profile_id, campaign_id,
        dry_run=dry_run,
        apenas_skus=skus_filtrados,
    )

    return {
        "profile_id": str(resultado.profile_id),
        "campaign_origem_id": str(campaign_id),
        "dry_run": resultado.dry_run,
        "total_oportunidades": resultado.total_oportunidades,
        "total_skus_unicos": resultado.total_skus_unicos,
        "total_migracoes_tentadas": resultado.total_migracoes_tentadas,
        "total_sucesso": resultado.total_sucesso,
        "total_ja_estava_no_ml": resultado.total_ja_estava,
        "total_erros": resultado.total_erros,
        "primeiros_erros": resultado.erros,
    }


@router.get("/{profile_id}/migracao/historico")
async def listar_historico_migracoes(
    profile_id: UUID,
    db_session: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Retorna histórico de migrações executadas pro perfil, mais recentes primeiro.

    Inclui execuções reais E dry_run (marcadas com `dry_run=true`).
    """
    historico_repo = MigracaoExecutadaRepository(db_session)
    registros = await historico_repo.list_by_profile(
        profile_id, limit=limit, offset=offset,
    )
    return {
        "profile_id": str(profile_id),
        "total_retornado": len(registros),
        "limit": limit,
        "offset": offset,
        "registros": [
            {
                "id": str(r.id),
                "timestamp": r.timestamp.isoformat(),
                "operacao": r.operacao,
                "item_id": r.item_id,
                "sku": r.sku,
                "campanha_origem_id": (
                    str(r.campanha_origem_id)
                    if r.campanha_origem_id else None
                ),
                "campanha_destino_ml_id": r.campanha_destino_ml_id,
                "campanha_destino_ml_nome": r.campanha_destino_ml_nome,
                "campanha_destino_ml_tipo": r.campanha_destino_ml_tipo,
                "destino_status": r.destino_status,
                "deal_price": r.deal_price,
                "margem_pct_prevista": r.margem_pct_prevista,
                "sucesso": r.sucesso,
                "dry_run": r.dry_run,
                "erro_detalhe": r.erro_detalhe,
            }
            for r in registros
        ],
    }


# ============================================================================
# Leva 5.9.4.C.2.A — Watchdog de cobertura
# ============================================================================


@router.get("/{profile_id}/campaigns/{campaign_id}/cobertura")
async def verificar_cobertura(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """Verifica cobertura de SKUs da campanha guarda-chuva.

    Pra cada SKU, consulta no ML quais promoções estão `started` agora.
    Classifica em 3 grupos:
    - **Coberto na origem**: started na guarda-chuva (caso desejado)
    - **Coberto em outras**: started em outra promoção (não na origem)
    - **Descoberto**: NÃO está started em nenhuma promoção (risco!)

    O endpoint só RELATA, não corrige. Pra corrigir, use
    `POST /cobertura/corrigir`.
    """
    try:
        await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    use_case = VerificarCoberturaUseCase(
        profile_repo, campaign_repo, creds_repo,
    )
    relatorio = await use_case.execute(profile_id, campaign_id)

    return {
        "profile_id": str(relatorio.profile_id),
        "campaign_id": str(relatorio.campaign_id),
        "ml_campaign_id": relatorio.ml_campaign_id,
        "total_skus": relatorio.total_skus,
        "coberto_na_origem": relatorio.coberto_na_origem,
        "coberto_em_outras_apenas": relatorio.coberto_em_outras_apenas,
        "descobertos": relatorio.descobertos,
        "erros_consulta": relatorio.erros_consulta,
        "percentual_cobertura": relatorio.percentual_cobertura,
        "descobertos_detalhe": [
            {
                "item_id": c.item_id,
                "sku": c.sku,
                "titulo": c.titulo,
            }
            for c in relatorio.cobertura_skus
            if c.descoberto
        ],
        "coberto_em_outras_detalhe": [
            {
                "item_id": c.item_id,
                "promocoes_ativas": c.coberto_em_outras,
            }
            for c in relatorio.cobertura_skus
            if not c.coberto_na_origem and c.coberto_em_outras
        ],
        "erros_detalhe": [
            {"item_id": c.item_id, "erro": c.erro_consulta}
            for c in relatorio.cobertura_skus
            if c.erro_consulta
        ][:10],  # limita pra não vazar muito
    }


@router.post("/{profile_id}/campaigns/{campaign_id}/cobertura/corrigir")
async def corrigir_cobertura(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    db_session: DbSession,
    dry_run: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    """Tenta re-adicionar SKUs descobertos na campanha guarda-chuva.

    Pra cada SKU descoberto, calcula `deal_price` que dá a margem alvo
    configurada (`profile.config.margem_alvo_campanha`, default 20%) e
    tenta adicionar na campanha ML.

    Modo `dry_run=true` (default): só simula e registra no histórico.

    Circuit breaker: se um SKU já falhou 3+ vezes consecutivas (em
    execuções reais anteriores) pra mesma campanha, pula pra evitar
    loop infinito.

    Histórico salvo com `operacao=watchdog_re_add` pra distinguir de
    migrações regulares.
    """
    try:
        await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    verificador = VerificarCoberturaUseCase(
        profile_repo, campaign_repo, creds_repo,
    )
    historico_repo = MigracaoExecutadaRepository(db_session)
    corretor = CorrigirCoberturaUseCase(
        verificador=verificador,
        profile_repo=profile_repo,
        campaign_repo=campaign_repo,
        creds_repo=creds_repo,
        historico_repo=historico_repo,
    )

    try:
        resultado = await corretor.execute(
            profile_id, campaign_id, dry_run=dry_run,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {
        "profile_id": str(resultado.profile_id),
        "campaign_id": str(resultado.campaign_id),
        "dry_run": resultado.dry_run,
        "total_descobertos": resultado.total_descobertos,
        "total_corrigidos": resultado.total_corrigidos,
        "total_falhas": resultado.total_falhas,
        "total_skip_circuit_breaker": resultado.total_skip_circuit_breaker,
        "total_sem_custo_xlsx": resultado.total_sem_custo_xlsx,
        "primeiras_falhas": resultado.falhas,
    }


# ============================================================================
# Leva 5.10 (Fase 1) — Criar SELLER_CAMPAIGN no ML (endpoint isolado de teste)
# ============================================================================


@router.post("/{profile_id}/campaigns/criar-ml-direto", status_code=201)
async def criar_campaign_ml_direto(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    nome: Annotated[str, Query(description="Nome da campanha")],
    data_inicio: Annotated[str, Query(description="YYYY-MM-DD")],
    data_fim: Annotated[str, Query(description="YYYY-MM-DD")],
) -> dict[str, Any]:
    """Cria uma SELLER_CAMPAIGN diretamente no ML e espelha localmente.

    Após criar, a campanha aparece na lista com origem='ml' e ml_campaign_id
    preenchido. SKUs ficam vazios — devem ser adicionados separadamente
    (via aba SKUs ou endpoints de migração).

    Validações antes de chamar ML:
    - `data_fim > data_inicio`
    - `data_inicio >= hoje`
    - `data_inicio` no máximo 60 dias no futuro (limite do ML)
    - Nome único no perfil

    Erros possíveis:
    - 400 com nome duplicado local
    - 400 com data inválida
    - 400 com nome conflitando no ML
    - 400 com data > 60 dias no futuro
    - 500 se ML indisponível
    """
    try:
        di = date.fromisoformat(data_inicio)
        df = date.fromisoformat(data_fim)
    except ValueError as e:
        raise HTTPException(400, detail=f"Data inválida: {e}") from e

    use_case = CriarSellerCampaignNoMLUseCase(
        profile_repo, campaign_repo, creds_repo,
    )
    try:
        result = await use_case.execute(
            profile_id, nome=nome, data_inicio=di, data_fim=df,
        )
    except NomeJaExisteLocalError as e:
        raise HTTPException(400, detail=str(e)) from e
    except (
        StartDateTooFarError, CampaignNameConflictError, InvalidDatesError,
    ) as e:
        raise HTTPException(400, detail=str(e)) from e
    except CriarMLCampaignError as e:
        raise HTTPException(400, detail=str(e)) from e
    except MLPromotionError as e:
        raise HTTPException(502, detail=f"ML rejeitou: {e}") from e

    return {
        "id": str(result.campaign.id),
        "profile_id": str(result.campaign.profile_id),
        "nome": result.campaign.nome,
        "data_inicio": result.campaign.data_inicio.isoformat(),
        "data_fim": result.campaign.data_fim.isoformat(),
        "ml_campaign_id": result.ml_campaign_id,
        "ml_status": result.ml_status,
        "status_local": result.campaign.status,
        "origem": result.campaign.origem,
    }


# ============================================================================
# Leva 5.12 — Criar SELLER_CAMPAIGN no ML JÁ COM os SKUs (UI nova de campanha)
# ============================================================================


class CriarCampanhaCompletaRequest(BaseModel):
    """Body do endpoint /criar-ml-completo (Leva 5.12)."""

    nome: str
    data_inicio: str  # YYYY-MM-DD
    data_fim: str     # YYYY-MM-DD
    skus_selecionados: list[str] = []
    # deal_prices: item_id → preço final pós-desconto. Obrigatório pra
    # SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE (ML rejeita sem o preço).
    deal_prices: dict[str, float] = {}
    # Fase 1 — item_id → novo preço de listagem a aplicar ANTES de adicionar
    # (inflação pra atingir a margem alvo). Snapshot + auto-revert embutidos.
    inflar_precos: dict[str, float] = {}
    # item_id → deal conservador (>=79 com frete) pra retry em ERROR_CREDIBILITY.
    fallback_deal_prices: dict[str, float] = {}


@router.post("/{profile_id}/campaigns/criar-ml-completo", status_code=201)
async def criar_campaign_ml_completo(
    profile_id: UUID,
    body: CriarCampanhaCompletaRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """Cria uma SELLER_CAMPAIGN no ML JÁ COM os SKUs adicionados (Leva 5.12).

    Diferente do `criar-ml-direto` (que cria campanha vazia), este endpoint
    orquestra criação + add de cada SKU em sequência. Usado pelo fluxo
    novo de "Iniciar agora" na UI de criar campanha.

    Política de falhas:
    - Falha em criar a campanha → 400/502, nada criado
    - Falha em adicionar SKU X → continua com os outros. Resposta inclui
      `skus_adicionados`, `skus_ja_estavam` e `erros[]` pra UI mostrar
      o resultado granular sem matar todo o fluxo.

    Pra SELLER_CAMPAIGN (tipo padrão criado), o `deal_price` é ignorado —
    ML aplica a regra de desconto da campanha. Cliente não precisa passar.
    """
    try:
        di = date.fromisoformat(body.data_inicio)
        df = date.fromisoformat(body.data_fim)
    except ValueError as e:
        raise HTTPException(400, detail=f"Data inválida: {e}") from e

    use_case = CriarSellerCampaignCompletoUseCase(
        profile_repo, campaign_repo, creds_repo,
    )
    try:
        result = await use_case.execute(
            profile_id,
            nome=body.nome, data_inicio=di, data_fim=df,
            skus_selecionados=body.skus_selecionados,
            deal_prices=body.deal_prices,
            inflar_precos=body.inflar_precos,
            fallback_deal_prices=body.fallback_deal_prices,
        )
    except NomeJaExisteLocalError as e:
        raise HTTPException(400, detail=str(e)) from e
    except (
        StartDateTooFarError, CampaignNameConflictError, InvalidDatesError,
    ) as e:
        raise HTTPException(400, detail=str(e)) from e
    except CriarMLCampaignError as e:
        raise HTTPException(400, detail=str(e)) from e
    except MLPromotionError as e:
        raise HTTPException(502, detail=f"ML rejeitou: {e}") from e

    return {
        "id": str(result.campaign.id),
        "profile_id": str(result.campaign.profile_id),
        "nome": result.campaign.nome,
        "data_inicio": result.campaign.data_inicio.isoformat(),
        "data_fim": result.campaign.data_fim.isoformat(),
        "ml_campaign_id": result.ml_campaign_id,
        "ml_status": result.ml_status,
        "status_local": result.campaign.status,
        "origem": result.campaign.origem,
        "skus_adicionados": result.skus_adicionados,
        "skus_ja_estavam": result.skus_ja_estavam,
        "inflados": result.inflados,
        "revertidos": result.revertidos,
        "reprecificados_20pct": result.reprecificados_20pct,
        "fallbacks_usados": result.fallbacks_usados,
        "pendentes_lock": result.pendentes_lock,
        "erros": [
            {"item_id": e.item_id, "erro": e.erro}
            for e in result.erros
        ],
    }


# ============================================================================
# Leva 5.12 follow-up — Batch sync de SKUs em campanha existente
# ============================================================================


class SyncSkusBatchRequest(BaseModel):
    """Body do endpoint /skus/sync-batch (Leva 5.12 follow-up).

    Operações aplicadas em ordem:
    1. `inflar_precos`: aplica PUT /items/{id} pra cada par {item_id, preco}.
       Usado na Fase 1 de reprecificação ANTES de adicionar à campanha,
       garantindo que o preço-base esteja na margem alvo do perfil.
    2. `adicionar`: entra na campanha (com deal_prices). Pra cada item,
       precisa de `deal_prices[item_id]` se a campanha é FLEXIBLE_PERCENTAGE.
    3. `remover`: sai da campanha.

    Falhas em qualquer etapa não abortam as outras — política de falhas
    parciais com resposta granular.
    """

    adicionar: list[str] = []
    remover: list[str] = []
    deal_prices: dict[str, float] = {}
    inflar_precos: dict[str, float] = {}  # rev8: Fase 1 antes da campanha
    # Leva 5.13 — mapa item_id → deal_price conservador (>=79 com frete),
    # usado pra retry automático quando o ML rejeita o deal agressivo
    # (<79 sem frete) com ERROR_CREDIBILITY_DISCOUNTED_PRICE. Só preenchido
    # pra itens onde a UI ativou a quebra de frete grátis.
    fallback_deal_prices: dict[str, float] = {}


@router.post("/{profile_id}/campaigns/{campaign_id}/skus/sync-batch")
async def sync_skus_batch(
    profile_id: UUID,
    campaign_id: UUID,
    body: SyncSkusBatchRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """Aplica adições E remoções de SKUs em uma campanha existente.

    Pra campanha `origem='ml'`: chama POST/DELETE no ML em sequência, depois
    atualiza skus_selecionados localmente.

    Pra campanha `origem='local'`: só edita skus_selecionados — campanhas
    locais não têm representação no ML ainda.

    Política de falhas (igual ao criar-ml-completo): falha em item X não
    aborta os outros. Resposta tem detalhe granular.
    """
    try:
        campaign = await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    # Sanitize: tira duplicados, descarta strings vazias
    adicionar = [s for s in dict.fromkeys(body.adicionar) if s]
    remover = [s for s in dict.fromkeys(body.remover) if s]

    if not adicionar and not remover and not body.inflar_precos:
        return {
            "ok": True,
            "operacoes": 0,
            "adicionados": [],
            "removidos": [],
            "ja_estavam": [],
            "nao_estavam": [],
            "inflados": [],
            "revertidos": [],
            "reprecificados_20pct": [],
            "pendentes_lock": [],
            "fallbacks_usados": [],
            "erros": [],
        }

    adicionados: list[str] = []
    removidos: list[str] = []
    ja_estavam: list[str] = []
    nao_estavam: list[str] = []
    inflados: list[str] = []
    revertidos: list[str] = []
    reprecificados_20pct: list[str] = []
    erros: list[dict[str, Any]] = []

    if campaign.ml_campaign_id:
        # ── Caminho ML: chama POST/DELETE pra cada item ─────────────
        # Vale pra qualquer campanha que esteja no ML — incluindo campanhas
        # `origem='local'` que já foram disparadas (StartCampaignPasso3UseCase
        # cria no ML mas mantém `origem='local'`).
        if not campaign.ml_campaign_id:
            raise HTTPException(
                status_code=400,
                detail="campanha origem='ml' sem ml_campaign_id (estado inconsistente)",
            )

        profile = await profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"loja '{profile.name}' não está conectada ao ML",
            )

        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            creds_repo.save_tokens(profile.slug, new_tokens)

        from liraz_tools.domain.skus.sugestao_use_case import (
            SugerirDealPricesPorMargemUseCase,
        )
        from liraz_tools.infrastructure.ml.campaign_skus_apply import (
            DadosMargem,
            aplicar_adicoes_skus_em_campanha,
        )
        from liraz_tools.infrastructure.ml.promotion_items import (
            ItemNotInCampaignError,
            remover_sku_de_campanha,
        )

        # ── CLAMP: recomputa sugestao server-side pros itens a adicionar pra
        #    obter os componentes de custo (custo, list_cost, comissão, K2).
        #    Sem isso o `aplicar_adicoes_skus_em_campanha` não tem como
        #    recalcular margem em dP arbitrário e o clamp fica desligado.
        #    Custo: ~1-2 ML calls por item (com cache de freight no disco). Vale
        #    o trade-off — em troca, ~70% dos descartes ERROR_CREDIBILITY somem.
        dados_margem_map: dict[str, DadosMargem] = {}
        if adicionar:
            sugestao_uc = SugerirDealPricesPorMargemUseCase(
                profile_repo, creds_repo, _get_overrides_repo_inline(),
            )
            sugs = await sugestao_uc.execute(
                profile_id, ml_campaign_id=campaign.ml_campaign_id,
                item_ids=adicionar,
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
            # ── Inflação + adição + descarte (pipeline compartilhado com o
            #    fluxo de criar campanha): infla com snapshot, adiciona com
            #    retry de fallback, e descarta os negados — reprecifica pro
            #    preço de 20% de margem (credibility) ou reverte ao original.
            #    Com `dados_margem`, o clamp pela faixa do ML é aplicado antes
            #    da adição (piso de margem default = 17%).
            res_add = await aplicar_adicoes_skus_em_campanha(
                ml,
                ml_campaign_id=campaign.ml_campaign_id,
                item_ids=adicionar,
                deal_prices=body.deal_prices,
                inflar_precos=body.inflar_precos,
                fallback_deal_prices=body.fallback_deal_prices,
                dados_margem=dados_margem_map if dados_margem_map else None,
                logger=logger,
            )
            adicionados = res_add.adicionados
            ja_estavam = res_add.ja_estavam
            inflados = res_add.inflados
            revertidos = res_add.revertidos
            reprecificados_20pct = res_add.reprecificados_20pct
            fallbacks_usados = res_add.fallbacks_usados
            clamped = res_add.clamped
            pulados_ausente = res_add.pulados_ausente
            pulados_por_margem = res_add.pulados_por_margem
            pendentes_lock = res_add.pendentes_lock
            erros.extend(res_add.erros)

            # Remoções
            for item_id in remover:
                try:
                    await remover_sku_de_campanha(
                        ml,
                        item_id=item_id,
                        promotion_id=campaign.ml_campaign_id,
                        promotion_type="SELLER_CAMPAIGN",
                    )
                    removidos.append(item_id)
                except ItemNotInCampaignError:
                    nao_estavam.append(item_id)
                except MLPromotionError as e:
                    erros.append({
                        "item_id": item_id, "operacao": "remove", "erro": str(e),
                    })

    # ── Atualiza local (aplica tudo que aconteceu de fato no ML, ou
    #    tudo que foi pedido se for campanha local sem ml_campaign_id) ─
    skus_atuais = set(campaign.skus_selecionados or [])
    if campaign.ml_campaign_id:
        # Só reflete o que realmente aconteceu no ML
        skus_atuais.update(adicionados)
        skus_atuais.update(ja_estavam)  # já estavam = continuam
        # `pendentes_lock`: ML deve adicionar depois de destravar — mantemos
        # na lista local pra não sumir do painel e o user poder monitorar.
        skus_atuais.update(locals().get("pendentes_lock", []))
        skus_atuais.difference_update(removidos)
    else:
        # Campanha local — confia 100% no pedido
        skus_atuais.update(adicionar)
        adicionados = list(adicionar)
        skus_atuais.difference_update(remover)
        removidos = list(remover)

    atualizada = campaign.model_copy(
        update={"skus_selecionados": sorted(skus_atuais)},
    )
    await campaign_repo.update(atualizada)

    logger.info(
        "sync_skus_batch_concluido",
        profile_id=str(profile_id),
        campaign_id=str(campaign_id),
        origem=campaign.origem,
        pedidos_add=len(adicionar),
        pedidos_remove=len(remover),
        pedidos_inflar=len(body.inflar_precos),
        adicionados=len(adicionados),
        removidos=len(removidos),
        inflados=len(inflados),
        revertidos=len(revertidos),
        reprecificados_20pct=len(reprecificados_20pct),
        pendentes_lock=len(locals().get("pendentes_lock", [])),
        clamped=len(locals().get("clamped", [])),
        pulados_ausente=len(locals().get("pulados_ausente", [])),
        pulados_por_margem=len(locals().get("pulados_por_margem", [])),
        ja_estavam=len(ja_estavam),
        nao_estavam=len(nao_estavam),
        erros=len(erros),
    )

    return {
        "ok": True,
        "operacoes": len(adicionar) + len(remover) + len(body.inflar_precos),
        "total_skus_apos": len(skus_atuais),
        "adicionados": adicionados,
        "removidos": removidos,
        "inflados": inflados,
        "revertidos": revertidos,
        "reprecificados_20pct": reprecificados_20pct,
        "pendentes_lock": locals().get("pendentes_lock", []),
        "fallbacks_usados": locals().get("fallbacks_usados", []),
        "clamped": locals().get("clamped", []),
        "pulados_ausente": locals().get("pulados_ausente", []),
        "pulados_por_margem": locals().get("pulados_por_margem", []),
        "ja_estavam": ja_estavam,
        "nao_estavam": nao_estavam,
        "erros": erros,
    }


# ============================================================================
# Fluxo "2 botões": Inflar Preços → Confirmar (mai/2026)
# ============================================================================
#
# O endpoint `/skus/sync-batch` faz tudo num call (infla + adiciona + descarte).
# Pra dar visibilidade ao usuário, separamos a 1ª etapa num endpoint dedicado:
# `/skus/inflar-precos` só faz PUT /items pra subir os preços-base. Depois o
# frontend habilita o botão "Confirmar" que chama `/skus/sync-batch` com
# `inflar_precos={}` (= só fase 2: clamp + POST + retry + descarte).


class InflarPrecosRequest(BaseModel):
    """Body do POST /skus/inflar-precos — só a fase 1 do fluxo 2-passos."""

    inflar_precos: dict[str, float] = {}


@router.post("/{profile_id}/campaigns/{campaign_id}/skus/inflar-precos")
async def inflar_precos_only(
    profile_id: UUID,
    campaign_id: UUID,
    body: InflarPrecosRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """1ª etapa do fluxo 2-passos: aplica APENAS a inflação (PUT /items).

    Não toca em campanha (não chama POST /seller-promotions/items). O frontend
    libera o botão "Confirmar" só depois desse call terminar — aí a 2ª etapa
    chama `/skus/sync-batch` com `inflar_precos={}` pra rodar só a fase de
    adição + clamp + retry + descarte.
    """
    try:
        campaign = await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    if not campaign.ml_campaign_id:
        raise HTTPException(
            status_code=400,
            detail="inflar-precos só faz sentido em campanha que já está no "
            "ML (`ml_campaign_id` setado). Pra campanhas locais ainda não "
            "disparadas, a inflação acontece no momento do disparo.",
        )

    if not body.inflar_precos:
        return {"ok": True, "inflados": [], "ja_no_preco": [], "erros": []}

    profile = await profile_repo.get_by_id(profile_id)
    if profile.status.value != "connected" or profile.ml_user_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"loja '{profile.name}' não está conectada ao ML",
        )

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

    async def save_refreshed(new_tokens: Any) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    from liraz_tools.infrastructure.ml.campaign_skus_apply import (
        aplicar_apenas_inflacao,
    )

    async with MLClient(creds, tokens, on_tokens_refreshed=save_refreshed) as ml:
        res = await aplicar_apenas_inflacao(
            ml, inflar_precos=body.inflar_precos, logger=logger,
        )

    logger.info(
        "campaign_inflar_only_concluido",
        profile_id=str(profile_id), campaign_id=str(campaign_id),
        pedidos=len(body.inflar_precos),
        inflados=len(res.inflados),
        ja_no_preco=len(res.ja_no_preco),
        erros=len(res.erros),
    )

    return {
        "ok": True,
        "inflados": res.inflados,
        "ja_no_preco": res.ja_no_preco,
        "erros": res.erros,
    }


# ============================================================================
# Leva 5.12 rev4 — Sugestão de deal_prices baseada em margem-alvo
# ============================================================================


class SugestaoDealPricesRequest(BaseModel):
    """Body: item_ids selecionados + margem-alvo (fração 0-1).

    Ex: margem_alvo=0.20 = 20% de margem líquida desejada após custo,
    imposto, comissão ML e frete.
    """

    item_ids: list[str]
    margem_alvo: float  # 0-1


def _get_overrides_repo_inline() -> CostOverridesRepository:
    from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
        CostOverridesRepository,
    )
    return CostOverridesRepository()


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/skus/sugestao-deal-prices",
)
async def sugerir_deal_prices(
    profile_id: UUID,
    campaign_id: UUID,
    body: SugestaoDealPricesRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """Calcula deal_price ideal por SKU pra atingir margem-alvo.

    Reusa `buscar_preco_para_margem` da infra de pricing (busca binária).
    Respeita `MINIMUM_DISCOUNT_PERCENT` da campanha — se o desconto
    calculado fica abaixo, ajusta pro min e a margem real fica acima do alvo.

    Itens sem custo cadastrado (planilha+overrides) retornam com erro
    mas os outros continuam.

    Pré-requisito: campanha precisa ter `origem='ml'` com `ml_campaign_id`.
    """
    from liraz_tools.domain.skus.sugestao_use_case import (
        SugerirDealPricesPorMargemUseCase,
    )

    if not 0.0 <= body.margem_alvo <= 0.95:
        raise HTTPException(
            400, detail="margem_alvo deve estar entre 0 e 0.95 (0% a 95%)",
        )

    try:
        campaign = await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    if not campaign.ml_campaign_id:
        raise HTTPException(
            400,
            detail=(
                "sugestão de deal_price só faz sentido pra campanhas que "
                "já estão no ML (`ml_campaign_id` definido)"
            ),
        )

    overrides_repo = _get_overrides_repo_inline()
    use_case = SugerirDealPricesPorMargemUseCase(
        profile_repo, creds_repo, overrides_repo,
    )
    sugestoes = await use_case.execute(
        profile_id,
        ml_campaign_id=campaign.ml_campaign_id,
        item_ids=body.item_ids,
        margem_alvo=body.margem_alvo,
    )

    return {
        "ml_campaign_id": campaign.ml_campaign_id,
        "margem_alvo_pct": round(body.margem_alvo * 100, 2),
        "results": [
            {
                "item_id": s.item_id,
                "sku": s.sku,
                "preco_atual": s.preco_atual,
                "margem_atual_pct": s.margem_atual_pct,
                "precisa_inflacao": s.precisa_inflacao,
                "preco_inflado": s.preco_inflado,
                "deal_price": s.deal_price,
                "desconto_pct": s.desconto_pct,
                "margem_real_pct": s.margem_real_pct,
                "min_aplicado": s.min_aplicado,
                "aviso_degrau": s.aviso_degrau,
                "quebra_frete_gratis_aplicada": s.quebra_frete_gratis_aplicada,
                "fallback_deal_price": s.fallback_deal_price,
                "frete_a_confirmar": s.frete_a_confirmar,
                "erro": s.erro,
            }
            for s in sugestoes
        ],
    }


# ─── Preview de sugestão SEM campanha criada (rev11) ────────────────────
# Versão do endpoint acima pra o fluxo "criar nova campanha" — onde a
# campanha ainda não existe pra ter um id. Assume min% conservador de 5%
# (default do ML pra SELLER_CAMPAIGN). Quando o user efetivamente cria a
# campanha e adiciona SKUs, o min% real é consultado e o cálculo é refeito.
@router.post("/{profile_id}/skus/sugestao-deal-prices-preview")
async def sugerir_deal_prices_preview(
    profile_id: UUID,
    body: SugestaoDealPricesRequest,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """Preview de sugestão de deal_price sem campanha existente.

    Igual ao endpoint normal `sugestao-deal-prices`, mas:
    - Não exige campaign_id (modo create do CampaignForm)
    - Não consulta min/max da campanha — assume min=5% conservador
    - Resto idêntico (Fase 1 inflação opcional + Fase 2 deal_price)

    O cliente pode usar esse endpoint pra mostrar a cadeia
    `preco_atual → inflado → deal_price` antes da campanha ser
    criada de fato. Quando criar, o backend recalcula com o min%
    real da campanha — pode diferir se a campanha tiver min ≠ 5%.
    """
    from liraz_tools.domain.skus.sugestao_use_case import (
        SugerirDealPricesPorMargemUseCase,
    )

    if not 0.0 <= body.margem_alvo <= 0.95:
        raise HTTPException(
            400, detail="margem_alvo deve estar entre 0 e 0.95 (0% a 95%)",
        )

    overrides_repo = _get_overrides_repo_inline()
    use_case = SugerirDealPricesPorMargemUseCase(
        profile_repo, creds_repo, overrides_repo,
    )
    sugestoes = await use_case.execute(
        profile_id,
        ml_campaign_id=None,  # modo preview
        item_ids=body.item_ids,
        margem_alvo=body.margem_alvo,
    )

    return {
        "ml_campaign_id": None,
        "margem_alvo_pct": round(body.margem_alvo * 100, 2),
        "preview": True,
        "results": [
            {
                "item_id": s.item_id,
                "sku": s.sku,
                "preco_atual": s.preco_atual,
                "margem_atual_pct": s.margem_atual_pct,
                "precisa_inflacao": s.precisa_inflacao,
                "preco_inflado": s.preco_inflado,
                "deal_price": s.deal_price,
                "desconto_pct": s.desconto_pct,
                "margem_real_pct": s.margem_real_pct,
                "min_aplicado": s.min_aplicado,
                "aviso_degrau": s.aviso_degrau,
                "quebra_frete_gratis_aplicada": s.quebra_frete_gratis_aplicada,
                "fallback_deal_price": s.fallback_deal_price,
                "frete_a_confirmar": s.frete_a_confirmar,
                "erro": s.erro,
            }
            for s in sugestoes
        ],
    }


@router.post("/{profile_id}/debug-criar-seller-campaign")
async def debug_criar_seller_campaign(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    nome: Annotated[str, Query(description="Nome da campanha")],
    data_inicio: Annotated[str, Query(description="Data início YYYY-MM-DD")],
    data_fim: Annotated[str, Query(description="Data fim YYYY-MM-DD")],
) -> dict[str, Any]:
    """Endpoint ISOLADO de teste pra Leva 5.10 fase 1.

    Cria uma SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE no ML, sem adicionar SKUs.
    Útil pra validar que `POST /seller-promotions/promotions` funciona antes
    de integrarmos no fluxo de campanhas locais.

    ATENÇÃO: cria de verdade no ML. Pra apagar, use o painel ML manualmente
    (ou implementaremos DELETE de campanha como Fase 1.5 se necessário).
    """
    try:
        di = date.fromisoformat(data_inicio)
        df = date.fromisoformat(data_fim)
    except ValueError as e:
        raise HTTPException(400, detail=f"Data inválida: {e}") from e

    profile = await profile_repo.get_by_id(profile_id)
    if profile.status.value != "connected" or profile.ml_user_id is None:
        raise HTTPException(400, detail="loja não conectada")

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    async with MLClient(
        creds, tokens, on_tokens_refreshed=save_refreshed,
    ) as ml:
        try:
            resp = await criar_seller_campaign(
                ml, nome=nome, start_date=di, finish_date=df,
            )
        except (
            StartDateTooFarError, CampaignNameConflictError, InvalidDatesError,
        ) as e:
            raise HTTPException(400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(500, detail=f"Erro inesperado: {e}") from e

    return {
        "criado_no_ml": True,
        "ml_response": resp,
        "obs": (
            "Campanha criada no ML mas NÃO espelhada localmente. Pra criar "
            "no fluxo normal, use a Fase 2 (vem na próxima entrega)."
        ),
    }


# ============================================================================
# Leva 5.9.4.C.3 — Migration scheduler (status + trigger manual)
# ============================================================================


@router.get("/{profile_id}/migracao/scheduler-state")
async def status_migration_scheduler(profile_id: UUID) -> dict[str, Any]:
    """Retorna o estado do scheduler de migrações pra este perfil.

    Mostra:
    - Se o scheduler GLOBAL está habilitado (env var)
    - Quando rodou pela última vez nesse perfil (in-memory, reseta no
      restart do uvicorn)
    """

    ultima = _ultima_execucao_por_perfil.get(profile_id)
    return {
        "profile_id": str(profile_id),
        "scheduler_globally_enabled": scheduler_habilitado(),
        "ultima_execucao_iso": ultima.isoformat() if ultima else None,
    }


@router.post("/{profile_id}/migracao/scheduler-trigger")
async def trigger_migration_scheduler_now(profile_id: UUID) -> dict[str, Any]:
    """Dispara IMEDIATAMENTE 1 ciclo do scheduler de migrações para um
    perfil específico, ignorando o intervalo configurado.

    Útil pra testar o scheduler sem esperar X horas. Respeita o
    `profile.config.migracao_dry_run` normalmente — não é um modo
    "executar de verdade", é só uma forma de adiantar o tick.
    """
    try:
        await _processar_perfil(profile_id)
        # Registra timestamp de última execução (igual o tick automático faz),
        # pra que o GET /scheduler-state mostre o disparo manual.
        _ultima_execucao_por_perfil[profile_id] = datetime.now(UTC)
    except Exception as e:
        raise HTTPException(500, detail=f"falha: {e}") from e

    return {
        "profile_id": str(profile_id),
        "status": "ciclo_disparado",
        "obs": (
            "Veja /migracao/historico pra ver o que aconteceu. Operações "
            "respeitam config.migracao_dry_run do perfil."
        ),
    }


@router.post("/{profile_id}/debug-legados/{item_id}")
async def debug_anuncio_legado(
    profile_id: UUID,
    item_id: str,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """Endpoint de debug pra anúncios legados (MLB4xxx).

    Retorna:
    - quantos anúncios ativos a loja tem total (via `/users/{id}/items/search`)
    - quantos são MLB4xxx vs MLB6xxx
    - se o `item_id` específico aparece nessa listagem
    - resultado bruto de `GET /seller-promotions/items/{item_id}` pra ele
      (mostra todas as promoções do item, se ML expõe)

    Uso: confirmar se MLB4xxx aparece na listagem de anúncios e se o ML
    expõe via API as promoções associadas a anúncios legados.
    """
    profile = await profile_repo.get_by_id(profile_id)
    if profile.status.value != "connected" or profile.ml_user_id is None:
        return {"erro": "loja não conectada"}

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    async with MLClient(
        creds, tokens, on_tokens_refreshed=save_refreshed,
    ) as ml:
        # 1) Lista todos anúncios ativos
        ativos: list[str] = []
        offset = 0
        while True:
            try:
                resp = await ml.get(
                    f"/users/{profile.ml_user_id}/items/search",
                    params={"status": "active", "limit": 50, "offset": offset},
                )
            except Exception as e:
                return {
                    "erro_listagem": str(e),
                    "offset_falha": offset,
                    "ativos_ate_falha": len(ativos),
                }
            if not isinstance(resp, dict):
                break
            results = resp.get("results", [])
            if not isinstance(results, list) or not results:
                break
            ativos.extend(str(r) for r in results if r)
            paging = resp.get("paging", {})
            total = paging.get("total", 0) if isinstance(paging, dict) else 0
            offset += 50
            if offset >= total:
                break

        mlb4 = [a for a in ativos if a.startswith("MLB4")]
        mlb6 = [a for a in ativos if a.startswith("MLB6")]

        # 2) Tenta listar TODAS as listagens (sem filtro de status)
        # pra confirmar que MLB4 não está só "inactive"
        ativos_sem_filtro: list[str] = []
        offset = 0
        while True:
            try:
                resp = await ml.get(
                    f"/users/{profile.ml_user_id}/items/search",
                    params={"limit": 50, "offset": offset},
                )
            except Exception:
                break
            if not isinstance(resp, dict):
                break
            results = resp.get("results", [])
            if not isinstance(results, list) or not results:
                break
            ativos_sem_filtro.extend(str(r) for r in results if r)
            paging = resp.get("paging", {})
            total = paging.get("total", 0) if isinstance(paging, dict) else 0
            offset += 50
            if offset >= total or offset > 500:  # safety
                break

        mlb4_total = [a for a in ativos_sem_filtro if a.startswith("MLB4")]

        # 3) Consulta o item específico
        consulta_item_promo: dict[str, Any] = {}
        try:
            consulta_item_promo = await ml.get(
                f"/seller-promotions/items/{item_id}",
                params={"app_version": "v2"},
            ) or {}
        except Exception as e:
            consulta_item_promo = {"erro": str(e)}

        # 4) Consulta /items/{id} pra ver o status real
        item_detalhe: dict[str, Any] = {}
        try:
            raw = await ml.get(f"/items/{item_id}")
            if isinstance(raw, dict):
                item_detalhe = {
                    "id": raw.get("id"),
                    "title": raw.get("title"),
                    "status": raw.get("status"),
                    "sub_status": raw.get("sub_status"),
                    "price": raw.get("price"),
                    "listing_type_id": raw.get("listing_type_id"),
                    "domain_id": raw.get("domain_id"),
                }
        except Exception as e:
            item_detalhe = {"erro": str(e)}

        return {
            "item_id_consultado": item_id,
            "loja_ml_user_id": profile.ml_user_id,
            "anuncios_status_active": {
                "total": len(ativos),
                "mlb4xxx": len(mlb4),
                "mlb6xxx": len(mlb6),
                "amostra_mlb4xxx": mlb4[:10],
            },
            "anuncios_sem_filtro_status": {
                "total": len(ativos_sem_filtro),
                "mlb4xxx_total": len(mlb4_total),
                "amostra_mlb4xxx": mlb4_total[:10],
            },
            "item_consultado_aparece_em_active": item_id in ativos,
            "item_consultado_aparece_em_qualquer_status": (
                item_id in ativos_sem_filtro
            ),
            "item_detalhe_do_ml": item_detalhe,
            "promocoes_do_item_seller_promotions_items": consulta_item_promo,
        }


@router.post("/{profile_id}/campaigns/{ml_promotion_id}/debug-ml-items")
async def debug_ml_items(
    profile_id: UUID,
    ml_promotion_id: str,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
) -> dict[str, Any]:
    """**Endpoint de DEBUG** — não usado pelo frontend normal.

    Retorna info crua do ML pra uma promoção: distribuição de status,
    samples completos de items por status (pra ver a estrutura exata),
    total de linhas, etc. Útil pra investigar por que items legados
    (MLB4xxx) não aparecem no app.

    Como usar: abra no navegador
      http://localhost:8000/api/profiles/{profile_id}/campaigns/{ml_promotion_id}/debug-ml-items

    Onde `ml_promotion_id` é o id da campanha no ML (ex: C-MLB4216507).

    Tenta MÚLTIPLOS endpoints do ML e compara os resultados:
      1. `/seller-promotions/promotions/{id}/items` (atual, padrão)
      2. `/marketplace/seller-promotions/promotions/{id}/items?user_id=X`
      3. `/marketplace/seller-promotions/promotions/{id}?user_id=X`

    Útil pra investigar se anúncios legados (MLB4xxx) estão em endpoint
    diferente. O `/marketplace/` é a versão "internal" que o painel web usa.
    """
    profile = await profile_repo.get_by_id(profile_id)
    if profile.status.value != "connected" or profile.ml_user_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"loja '{profile.name}' não está conectada ao ML",
        )

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)
    user_id = profile.ml_user_id

    async def save_refreshed(new_tokens: Any) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    async def _coletar_de_endpoint(
        ml: Any, url: str, params: dict[str, Any],
    ) -> dict[str, Any]:
        """Pagina um endpoint, devolve métricas + samples."""
        distribuicao: dict[str, int] = {}
        samples: dict[str, list[dict[str, Any]]] = {}
        total_linhas = 0
        ids_unicos: set[str] = set()
        samples_mlb4xxx: list[dict[str, Any]] = []
        samples_mlb6xxx: list[dict[str, Any]] = []
        offset = 0
        erro_msg = None
        while True:
            try:
                req_params = dict(params)
                req_params["limit"] = PAGE_SIZE
                req_params["offset"] = offset
                resp = await ml.get(url, params=req_params)
            except Exception as e:
                erro_msg = f"falha em offset={offset}: {e}"
                break
            if not isinstance(resp, dict):
                break
            page = resp.get("results") or resp.get("items") or []
            if not isinstance(page, list) or not page:
                break
            for it in page:
                if not isinstance(it, dict):
                    continue
                total_linhas += 1
                status_norm = _normalizar_status(it.get("status"))
                key = status_norm or "<sem_status>"
                distribuicao[key] = distribuicao.get(key, 0) + 1
                if len(samples.get(key, [])) < 2:
                    samples.setdefault(key, []).append(it)
                item_id = str(it.get("id") or it.get("item_id") or "")
                if item_id:
                    ids_unicos.add(item_id)
                if item_id.startswith("MLB4") and len(samples_mlb4xxx) < 3:
                    samples_mlb4xxx.append(it)
                elif item_id.startswith("MLB6") and len(samples_mlb6xxx) < 3:
                    samples_mlb6xxx.append(it)
            paging = resp.get("paging", {})
            total = paging.get("total", 0) if isinstance(paging, dict) else 0
            if total == 0:
                break
            offset += PAGE_SIZE
            if offset >= total:
                break
        return {
            "total_linhas": total_linhas,
            "ids_unicos": len(ids_unicos),
            "ids_mlb4xxx": sum(1 for i in ids_unicos if i.startswith("MLB4")),
            "ids_mlb6xxx": sum(1 for i in ids_unicos if i.startswith("MLB6")),
            "distribuicao_status": distribuicao,
            "samples_por_status": samples,
            "samples_mlb4xxx": samples_mlb4xxx,
            "samples_mlb6xxx": samples_mlb6xxx,
            "erro": erro_msg,
        }

    resultado = {}
    async with MLClient(
        creds, tokens, on_tokens_refreshed=save_refreshed,
    ) as ml:
        # Endpoint 1: o que usamos hoje
        resultado["endpoint_1_seller_promotions_items"] = await _coletar_de_endpoint(
            ml,
            f"/seller-promotions/promotions/{ml_promotion_id}/items",
            {"promotion_type": "SELLER_CAMPAIGN", "app_version": "v2"},
        )
        # Endpoint 2: variante "marketplace" com /items
        resultado["endpoint_2_marketplace_promotions_items"] = await _coletar_de_endpoint(
            ml,
            f"/marketplace/seller-promotions/promotions/{ml_promotion_id}/items",
            {
                "promotion_type": "SELLER_CAMPAIGN",
                "user_id": user_id,
                "app_version": "v2",
            },
        )
        # Endpoint 3: variante "marketplace" sem /items
        resultado["endpoint_3_marketplace_promotions"] = await _coletar_de_endpoint(
            ml,
            f"/marketplace/seller-promotions/promotions/{ml_promotion_id}",
            {
                "promotion_type": "SELLER_CAMPAIGN",
                "user_id": user_id,
                "app_version": "v2",
            },
        )

    # União dos ids únicos pra ver se algum endpoint traz items que outro não
    return {
        "promotion_id": ml_promotion_id,
        "ml_user_id": user_id,
        "comparacao_endpoints": {
            nome: {
                "total_linhas": dados["total_linhas"],
                "ids_unicos": dados["ids_unicos"],
                "ids_mlb4xxx": dados["ids_mlb4xxx"],
                "ids_mlb6xxx": dados["ids_mlb6xxx"],
                "erro": dados["erro"],
            }
            for nome, dados in resultado.items()
        },
        "detalhes_por_endpoint": resultado,
    }


@router.get(
    "/{profile_id}/campaigns/skus-eligible",
    response_model=list[CampaignItemEligible],
)
async def list_skus_eligible(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    cache: PricingCacheDep,
) -> list[CampaignItemEligible]:
    """Lista TODOS items do catálogo com flag de elegibilidade (Leva 5.9.3).

    Item é "elegível pra ser adicionado em campanha" se NÃO está participando
    de outra campanha agora. Retorna todos com a flag — frontend filtra.

    Se cache do fee_report está vazio, retorna []. User deve abrir o
    relatório de margens primeiro pra hidratar o cache.
    """
    try:
        await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        _raise_404(e)

    use_case = ListSkusElegiveisUseCase(profile_repo, creds_repo, cache)
    items = await use_case.execute(profile_id)
    return [CampaignItemEligible.model_validate(it) for it in items]


@router.get(
    "/{profile_id}/campaigns/{campaign_id}/skus-info",
    response_model=list[CampaignItemInfo],
)
async def get_campaign_skus_info(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
    cache: PricingCacheDep,
) -> list[CampaignItemInfo]:
    """Retorna info enriquecida (sku, titulo, preço) dos SKUs da campanha.

    Estratégia em camadas: lê do cache do fee_report primeiro (rápido),
    pra items missing consulta o ML diretamente via multiget. Frontend
    nunca depende do user ter aberto o relatório de margens.
    """
    try:
        campaign = await campaign_repo.get_by_id(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)

    skus = campaign.skus_selecionados or []
    use_case = HidrarItemsUseCase(cache, profile_repo, creds_repo)
    items = await use_case.execute(profile_id, skus)
    return [CampaignItemInfo.model_validate(it) for it in items]


# ─── Editor de margens dentro da campanha (mai/2026) ─────────────────────


class MargemItemResponse(BaseModel):
    item_id: str
    sku: str | None = None
    titulo: str | None = None
    preco_base: float | None = None
    deal_price: float | None = None
    desconto_pct: float | None = None
    margem_pct: float | None = None
    ml_min: float | None = None
    ml_max: float | None = None
    custo: float | None = None
    comissao_pct: float | None = None
    tarifa_fixa: float | None = None
    frete: float | None = None
    aliquota: float | None = None
    erro: str | None = None


class EditarMargemRequest(BaseModel):
    item_ids: list[str] = Field(default_factory=list)
    margem_alvo: float = Field(
        description="Fração 0-1 (ex.: 0.20 = 20%)",
        ge=0.01, le=0.80,
    )


class EditarMargemItemResponse(BaseModel):
    item_id: str
    sku: str | None = None
    deal_anterior: float | None = None
    deal_novo: float | None = None
    margem_anterior_pct: float | None = None
    margem_nova_pct: float | None = None
    status: str
    motivo: str | None = None


class EditarMargemResponse(BaseModel):
    results: list[EditarMargemItemResponse]


@router.get(
    "/{profile_id}/campaigns/{campaign_id}/skus/margens",
    response_model=list[MargemItemResponse],
)
async def listar_margens_campanha(
    profile_id: UUID,
    campaign_id: UUID,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> list[MargemItemResponse]:
    """Pra cada SKU da campanha, retorna deal/preço-base/desconto/margem real
    e os limites `ml_min`/`ml_max` do ML.

    Pesado (1-3 chamadas ML por item) — só carrega quando o usuário abre o
    painel de margens da campanha.
    """
    use_case = ListarMargensCampanhaUseCase(
        profile_repo=profile_repo, campaign_repo=campaign_repo,
        creds_repo=creds_repo,
    )
    try:
        results = await use_case.execute(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)
    except CampaignNaoNoMLError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return [
        MargemItemResponse(
            item_id=r.item_id, sku=r.sku, titulo=r.titulo,
            preco_base=r.preco_base, deal_price=r.deal_price,
            desconto_pct=r.desconto_pct, margem_pct=r.margem_pct,
            ml_min=r.ml_min, ml_max=r.ml_max,
            custo=r.custo, comissao_pct=r.comissao_pct,
            tarifa_fixa=r.tarifa_fixa, frete=r.frete, aliquota=r.aliquota,
            erro=r.erro,
        )
        for r in results
    ]


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/skus/editar-margem",
    response_model=EditarMargemResponse,
)
async def editar_margem_skus_campanha(
    profile_id: UUID,
    campaign_id: UUID,
    body: EditarMargemRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    creds_repo: CredsRepo,
) -> EditarMargemResponse:
    """Pra cada SKU, calcula novo deal_price pra atingir `margem_alvo` e
    aplica via POST /seller-promotions/items.

    Pula itens cujo deal calculado ultrapassa `ml_max` (decisão de design:
    não infla preço-base automaticamente). Itens com deal abaixo do
    `ml_min` são ajustados pro `ml_min` (margem fica acima do alvo).
    """
    use_case = EditarMargemSkusCampanhaUseCase(
        profile_repo=profile_repo, campaign_repo=campaign_repo,
        creds_repo=creds_repo,
    )
    try:
        results = await use_case.execute(
            profile_id, campaign_id,
            item_ids=body.item_ids, margem_alvo=body.margem_alvo,
        )
    except CampaignNotFoundError as e:
        _raise_404(e)
    except CampaignNaoNoMLError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return EditarMargemResponse(
        results=[
            EditarMargemItemResponse(
                item_id=r.item_id, sku=r.sku,
                deal_anterior=r.deal_anterior, deal_novo=r.deal_novo,
                margem_anterior_pct=r.margem_anterior_pct,
                margem_nova_pct=r.margem_nova_pct,
                status=r.status, motivo=r.motivo,
            )
            for r in results
        ]
    )


@router.patch(
    "/{profile_id}/campaigns/{campaign_id}/skus",
    response_model=CampaignResponse,
)
async def update_campaign_skus(
    profile_id: UUID,
    campaign_id: UUID,
    body: UpdateCampaignSkusRequest,
    campaign_repo: CampaignRepoDep,
) -> CampaignResponse:
    """Atualiza apenas `skus_selecionados` da campanha (Leva 5.9.3).

    Mais permissivo que o PATCH geral — bloqueia origem=ml até 5.9.4
    (que vai propagar mudanças ao ML).
    """
    use_case = UpdateCampaignSkusUseCase(campaign_repo)
    try:
        result = await use_case.execute(
            profile_id, campaign_id, body.skus_selecionados,
        )
    except CampaignNotFoundError as e:
        _raise_404(e)
    except CampaignNotEditableError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e),
        ) from e
    return _to_response(result)


@router.get(
    "/{profile_id}/campaigns/{campaign_id}",
    response_model=CampaignResponse,
)
async def get_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    campaign_repo: CampaignRepoDep,
) -> CampaignResponse:
    use_case = GetCampaignUseCase(campaign_repo)
    try:
        result = await use_case.execute(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)
    return _to_response(result)


@router.patch(
    "/{profile_id}/campaigns/{campaign_id}",
    response_model=CampaignResponse,
)
async def update_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    body: UpdateCampaignRequest,
    profile_repo: ProfileRepo,
    campaign_repo: CampaignRepoDep,
    snapshots_repo: SnapshotsRepoDep,
) -> CampaignResponse:
    """Atualiza campos editáveis. Pra desassociar a simulação, passe null."""
    use_case = UpdateCampaignUseCase(profile_repo, campaign_repo, snapshots_repo)

    # Distingue "campo omitido" de "campo enviado como null" pra simulacao_id,
    # hora_fim e skus_selecionados (todos aceitam null explícito).
    body_dict = body.model_dump(exclude_unset=True)
    sim_id_param: str | None | object = body_dict.get("simulacao_id", ...)
    hora_fim_param: object = body_dict.get("hora_fim", ...)
    skus_param: object = body_dict.get("skus_selecionados", ...)

    try:
        result = await use_case.execute(
            profile_id=profile_id,
            campaign_id=campaign_id,
            nome=body.nome,
            data_inicio=body.data_inicio,
            data_fim=body.data_fim,
            hora_disparo=body.hora_disparo,
            hora_fim=hora_fim_param,
            simulacao_id=sim_id_param,
            skus_selecionados=skus_param,
            forcar_nome=body.forcar_nome,
        )
    except CampaignNotFoundError as e:
        _raise_404(e)
    except CampaignNotEditableError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e),
        ) from e
    except CampaignNameTakenError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(e), "sugestao": e.sugestao, "nome_tentado": e.nome},
        ) from e
    except (SimulationNotReadyError, InvalidDateError) as e:
        _raise_400(e)
    except (CampaignError, ValueError) as e:
        _raise_400(e)
    return _to_response(result)


@router.delete(
    "/{profile_id}/campaigns/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    campaign_repo: CampaignRepoDep,
) -> None:
    use_case = DeleteCampaignUseCase(campaign_repo)
    try:
        await use_case.execute(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)
    except CampaignNotEditableError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e),
        ) from e


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/schedule",
    response_model=CampaignResponse,
)
async def schedule_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    campaign_repo: CampaignRepoDep,
) -> CampaignResponse:
    """Promove rascunho → agendada. Requer simulacao_id definido."""
    use_case = ScheduleCampaignUseCase(campaign_repo)
    try:
        result = await use_case.execute(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)
    except (CampaignNotEditableError, CampaignError) as e:
        _raise_400(e)
    return _to_response(result)


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/unschedule",
    response_model=CampaignResponse,
)
async def unschedule_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    campaign_repo: CampaignRepoDep,
) -> CampaignResponse:
    """Despromove agendada → rascunho."""
    use_case = UnscheduleCampaignUseCase(campaign_repo)
    try:
        result = await use_case.execute(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)
    except CampaignNotEditableError as e:
        _raise_400(e)
    return _to_response(result)


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/cancel",
    response_model=CampaignResponse,
)
async def cancel_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    campaign_repo: CampaignRepoDep,
) -> CampaignResponse:
    """Cancela uma campanha. Leva 5.3: só rascunho/agendada."""
    use_case = CancelCampaignUseCase(campaign_repo)
    try:
        result = await use_case.execute(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)
    except CampaignNotEditableError as e:
        _raise_400(e)
    return _to_response(result)


@router.post(
    "/{profile_id}/campaigns/{campaign_id}/reactivate",
    response_model=CampaignResponse,
)
async def reactivate_campaign(
    profile_id: UUID,
    campaign_id: UUID,
    campaign_repo: CampaignRepoDep,
) -> CampaignResponse:
    """Reativa campanha cancelada. Vira agendada se tem simulacao_id, senão rascunho.

    Exige que `data_fim` ainda esteja no futuro. Senão recomenda criar nova.
    """
    use_case = ReactivateCampaignUseCase(campaign_repo)
    try:
        result = await use_case.execute(profile_id, campaign_id)
    except CampaignNotFoundError as e:
        _raise_404(e)
    except (CampaignNotEditableError, InvalidDateError) as e:
        _raise_400(e)
    return _to_response(result)
