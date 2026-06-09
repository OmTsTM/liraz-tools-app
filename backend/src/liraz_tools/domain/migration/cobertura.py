"""Watchdog de cobertura (Leva 5.9.4.C.2.A).

Garante o invariante: todo SKU da guarda-chuva está ATIVO em pelo menos
uma campanha ML (started). Sem cobertura = preço cheio = quebra de vendas.

Dois use cases:
1. VerificarCoberturaUseCase — só relata
2. CorrigirCoberturaUseCase — relata e tenta corrigir (dry_run ou real)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotion_items import (
    ItemAlreadyInCampaignError,
    MLPromotionError,
    adicionar_sku_em_campanha,
)
from liraz_tools.infrastructure.ml.promotions_lookup import _normalizar_status
from liraz_tools.infrastructure.pricing.costs_loader import (
    buscar_custo,
    carregar_custos,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.repricing_calculator import (
    buscar_preco_para_margem,
)
from liraz_tools.infrastructure.repositories.migracao_executada_repository import (
    MigracaoExecutadaRecord,
    MigracaoExecutadaRepository,
    agora_utc,
    novo_record_id,
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

# Status do ML que indicam "SKU está participando ativamente da promoção
# AGORA mesmo" (com desconto vivo). Pra cobertura, é o que conta.
STATUS_ATIVO_AGORA = {"started", "active"}


@dataclass
class CoberturaSKU:
    """Estado de cobertura de um SKU individual."""

    item_id: str
    sku: str | None = None
    titulo: str | None = None
    # Está started na campanha origem (guarda-chuva)?
    coberto_na_origem: bool = False
    # Está started em outra promoção (alternativa)?
    coberto_em_outras: list[str] = field(default_factory=list)
    """Lista de promotion_ids alternativos onde está started."""
    # Erros ao consultar
    erro_consulta: str | None = None

    @property
    def descoberto(self) -> bool:
        """True se não está em nenhuma promoção started."""
        return (
            not self.coberto_na_origem
            and not self.coberto_em_outras
            and self.erro_consulta is None
        )


@dataclass
class RelatorioCobertura:
    """Relatório completo da cobertura de uma campanha."""

    profile_id: UUID
    campaign_id: UUID
    ml_campaign_id: str
    total_skus: int
    coberto_na_origem: int
    coberto_em_outras_apenas: int  # cobertos só em outras, não na origem
    descobertos: int
    erros_consulta: int
    cobertura_skus: list[CoberturaSKU]

    @property
    def percentual_cobertura(self) -> float:
        """% de SKUs que têm pelo menos UMA promoção started."""
        if self.total_skus == 0:
            return 100.0
        cobertos = self.total_skus - self.descobertos - self.erros_consulta
        return round(cobertos / self.total_skus * 100, 1)


@dataclass
class ResultadoCorrecao:
    """Resumo da execução do corretor."""

    profile_id: UUID
    campaign_id: UUID
    dry_run: bool
    total_descobertos: int
    total_corrigidos: int
    total_falhas: int
    total_skip_circuit_breaker: int
    total_sem_custo_xlsx: int
    falhas: list[str]


# Circuit breaker — quantas falhas consecutivas pro mesmo (sku, promo) pra
# parar de tentar
CIRCUIT_BREAKER_MAX_FALHAS = 3


class VerificarCoberturaUseCase:
    """Verifica cobertura SEM agir (só relatório).

    Pra cada SKU da campanha origem, consulta GET /seller-promotions/items/{id}
    e classifica em: coberto-na-origem / coberto-em-outras / descoberto.
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
    ) -> RelatorioCobertura:
        campaign = await self._campaign_repo.get_by_id(profile_id, campaign_id)
        skus = campaign.skus_selecionados or []
        ml_campaign_id = campaign.ml_campaign_id or ""

        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            return RelatorioCobertura(
                profile_id=profile_id,
                campaign_id=campaign_id,
                ml_campaign_id=ml_campaign_id,
                total_skus=len(skus),
                coberto_na_origem=0,
                coberto_em_outras_apenas=0,
                descobertos=0,
                erros_consulta=len(skus),
                cobertura_skus=[],
            )

        if not skus:
            return RelatorioCobertura(
                profile_id=profile_id,
                campaign_id=campaign_id,
                ml_campaign_id=ml_campaign_id,
                total_skus=0,
                coberto_na_origem=0,
                coberto_em_outras_apenas=0,
                descobertos=0,
                erros_consulta=0,
                cobertura_skus=[],
            )

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: TokenSet) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        # Paraleliza consultas (max 8 simultâneas)
        sem = asyncio.Semaphore(8)
        cobertura_por_sku: list[CoberturaSKU] = []

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            async def consultar(item_id: str) -> CoberturaSKU:
                async with sem:
                    return await _consultar_cobertura_sku(
                        ml, item_id, ml_campaign_id,
                    )

            cobertura_por_sku = await asyncio.gather(
                *[consultar(s) for s in skus],
                return_exceptions=False,
            )

        # Contadores
        coberto_origem = sum(1 for c in cobertura_por_sku if c.coberto_na_origem)
        coberto_outras_apenas = sum(
            1 for c in cobertura_por_sku
            if not c.coberto_na_origem and c.coberto_em_outras
        )
        descobertos = sum(1 for c in cobertura_por_sku if c.descoberto)
        erros = sum(1 for c in cobertura_por_sku if c.erro_consulta)

        relatorio = RelatorioCobertura(
            profile_id=profile_id,
            campaign_id=campaign_id,
            ml_campaign_id=ml_campaign_id,
            total_skus=len(skus),
            coberto_na_origem=coberto_origem,
            coberto_em_outras_apenas=coberto_outras_apenas,
            descobertos=descobertos,
            erros_consulta=erros,
            cobertura_skus=cobertura_por_sku,
        )

        logger.info(
            "cobertura_verificada",
            campaign_id=str(campaign_id),
            total=len(skus),
            coberto_origem=coberto_origem,
            coberto_outras_apenas=coberto_outras_apenas,
            descobertos=descobertos,
            erros=erros,
            percentual=relatorio.percentual_cobertura,
        )
        return relatorio


async def _consultar_cobertura_sku(
    ml: MLClient, item_id: str, ml_campaign_id_origem: str,
) -> CoberturaSKU:
    """Consulta promoções de 1 item e classifica cobertura."""
    cob = CoberturaSKU(item_id=item_id)
    try:
        resp = await ml.get(
            f"/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
        )
    except Exception as e:
        cob.erro_consulta = str(e)
        return cob

    # ML retorna LISTA direta no app_version=v2, mas vamos aceitar dict tb
    if isinstance(resp, list):
        promocoes = resp
    elif isinstance(resp, dict):
        promocoes = resp.get("results", []) or []
    else:
        promocoes = []

    for promo in promocoes:
        if not isinstance(promo, dict):
            continue
        status_norm = _normalizar_status(promo.get("status"))
        if status_norm not in STATUS_ATIVO_AGORA:
            continue
        promo_id = promo.get("id") or ""
        if promo_id == ml_campaign_id_origem:
            cob.coberto_na_origem = True
        elif promo_id:
            cob.coberto_em_outras.append(promo_id)

    return cob


class CorrigirCoberturaUseCase:
    """Tenta re-adicionar SKUs descobertos na campanha guarda-chuva.

    Pra cada SKU descoberto:
    - Calcula `deal_price` que dá margem alvo (config.margem_alvo_campanha)
    - dry_run=True: só registra no histórico marcado
    - dry_run=False: POST no ML pra re-adicionar

    Circuit breaker: olha histórico recente — se já falhou 3x consecutivas
    pro mesmo (sku, promotion), pula. Evita loop infinito.
    """

    def __init__(
        self,
        verificador: VerificarCoberturaUseCase,
        profile_repo: SQLAlchemyProfileRepository,
        campaign_repo: CampaignRepository,
        creds_repo: PerProfileCredentialsRepository,
        historico_repo: MigracaoExecutadaRepository,
    ) -> None:
        self._verificador = verificador
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._creds_repo = creds_repo
        self._historico_repo = historico_repo

    async def execute(
        self, profile_id: UUID, campaign_id: UUID, *, dry_run: bool,
    ) -> ResultadoCorrecao:
        # 1) Verifica cobertura atual
        relatorio = await self._verificador.execute(profile_id, campaign_id)
        descobertos_skus = [
            c for c in relatorio.cobertura_skus if c.descoberto
        ]

        if not descobertos_skus:
            logger.info(
                "correcao_sem_descobertos",
                campaign_id=str(campaign_id),
            )
            return ResultadoCorrecao(
                profile_id=profile_id,
                campaign_id=campaign_id,
                dry_run=dry_run,
                total_descobertos=0,
                total_corrigidos=0,
                total_falhas=0,
                total_skip_circuit_breaker=0,
                total_sem_custo_xlsx=0,
                falhas=[],
            )

        campaign = await self._campaign_repo.get_by_id(profile_id, campaign_id)
        profile = await self._profile_repo.get_by_id(profile_id)
        ml_campaign_id = campaign.ml_campaign_id

        if not ml_campaign_id:
            raise ValueError(
                f"campanha {campaign_id} não tem ml_campaign_id — "
                "watchdog só funciona pra campanhas espelhadas do ML"
            )

        # Carrega histórico recente pra circuit breaker
        falhas_consecutivas = await _calcular_falhas_consecutivas(
            self._historico_repo, profile_id, ml_campaign_id,
        )

        # 2) Pra cada descoberto, tenta corrigir
        if not profile.config.custos_xlsx_path:
            logger.warning(
                "correcao_sem_custos_xlsx",
                profile_id=str(profile_id),
            )
            return ResultadoCorrecao(
                profile_id=profile_id,
                campaign_id=campaign_id,
                dry_run=dry_run,
                total_descobertos=len(descobertos_skus),
                total_corrigidos=0,
                total_falhas=0,
                total_skip_circuit_breaker=0,
                total_sem_custo_xlsx=len(descobertos_skus),
                falhas=[],
            )

        try:
            custos = carregar_custos(profile.config.custos_xlsx_path)
        except Exception as e:
            logger.warning(
                "correcao_falha_carregar_custos",
                error=str(e),
            )
            return ResultadoCorrecao(
                profile_id=profile_id,
                campaign_id=campaign_id,
                dry_run=dry_run,
                total_descobertos=len(descobertos_skus),
                total_corrigidos=0,
                total_falhas=len(descobertos_skus),
                total_skip_circuit_breaker=0,
                total_sem_custo_xlsx=0,
                falhas=[f"falha custos: {e}"],
            )

        freight_cache = FreightCache(profile.slug)
        cep = profile.config.cep_destino
        aliquota = profile.config.aliquota_imposto
        margem_alvo = profile.config.margem_alvo_campanha

        creds = self._creds_repo.get_app_credentials(profile.slug)
        # Invariante: correção de cobertura só roda pra perfil conectado.
        assert profile.ml_user_id is not None
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: TokenSet) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        corrigidos = 0
        falhas_count = 0
        skip_breaker = 0
        sem_custo = 0
        falhas_msgs: list[str] = []

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            for cob in descobertos_skus:
                item_id = cob.item_id

                # Circuit breaker check
                chave_falhas = (item_id, ml_campaign_id)
                if falhas_consecutivas.get(chave_falhas, 0) >= CIRCUIT_BREAKER_MAX_FALHAS:
                    logger.info(
                        "correcao_skip_circuit_breaker",
                        item_id=item_id,
                        promotion_id=ml_campaign_id,
                        falhas_consecutivas=falhas_consecutivas[chave_falhas],
                    )
                    skip_breaker += 1
                    continue

                # Buscar item info pra extrair SKU
                try:
                    item_info = await ml.get(f"/items/{item_id}")
                except Exception as e:
                    falhas_count += 1
                    falhas_msgs.append(f"{item_id}: fetch item falhou: {e}")
                    continue
                if not isinstance(item_info, dict):
                    falhas_count += 1
                    continue
                sku = _extrair_sku(item_info)

                # Resolver custo
                custo, _ = buscar_custo(sku, item_id, custos, None)
                if custo is None:
                    sem_custo += 1
                    await self._salvar_historico(
                        profile_id=profile_id, campaign_id=campaign_id,
                        ml_campaign_id=ml_campaign_id,
                        item_id=item_id, sku=sku,
                        deal_price=None, margem=None,
                        sucesso=False, dry_run=dry_run,
                        erro="sem_custo_xlsx",
                    )
                    continue

                # Calcular deal_price pra margem alvo
                preco_alvo, erro_busca = await buscar_preco_para_margem(
                    ml=ml,
                    item_id=item_id,
                    custo=custo,
                    margem_alvo=margem_alvo,
                    aliquota=aliquota,
                    cep=cep,
                    freight_cache=freight_cache,
                )
                if preco_alvo is None:
                    falhas_count += 1
                    msg = f"{item_id}: falha busca preço: {erro_busca}"
                    falhas_msgs.append(msg)
                    await self._salvar_historico(
                        profile_id=profile_id, campaign_id=campaign_id,
                        ml_campaign_id=ml_campaign_id,
                        item_id=item_id, sku=sku,
                        deal_price=None, margem=None,
                        sucesso=False, dry_run=dry_run,
                        erro=f"busca_preco: {erro_busca}",
                    )
                    continue

                # Executar (real ou dry_run)
                sucesso = False
                erro_detalhe: str | None = None
                if dry_run:
                    logger.info(
                        "correcao_dry_run",
                        item_id=item_id, sku=sku,
                        deal_price=preco_alvo,
                    )
                    sucesso = True
                else:
                    try:
                        await adicionar_sku_em_campanha(
                            ml,
                            item_id=item_id,
                            promotion_id=ml_campaign_id,
                            promotion_type="SELLER_CAMPAIGN",
                            deal_price=preco_alvo,
                        )
                        sucesso = True
                    except ItemAlreadyInCampaignError:
                        # Idempotência: já está lá → sucesso silencioso
                        sucesso = True
                    except MLPromotionError as e:
                        erro_detalhe = str(e)
                        falhas_msgs.append(f"{item_id}: {erro_detalhe}")

                await self._salvar_historico(
                    profile_id=profile_id, campaign_id=campaign_id,
                    ml_campaign_id=ml_campaign_id,
                    item_id=item_id, sku=sku,
                    deal_price=preco_alvo, margem=margem_alvo,
                    sucesso=sucesso, dry_run=dry_run,
                    erro=erro_detalhe,
                )

                if sucesso:
                    corrigidos += 1
                else:
                    falhas_count += 1

        logger.info(
            "correcao_concluida",
            campaign_id=str(campaign_id),
            dry_run=dry_run,
            descobertos=len(descobertos_skus),
            corrigidos=corrigidos,
            falhas=falhas_count,
            skip_breaker=skip_breaker,
            sem_custo=sem_custo,
        )

        return ResultadoCorrecao(
            profile_id=profile_id,
            campaign_id=campaign_id,
            dry_run=dry_run,
            total_descobertos=len(descobertos_skus),
            total_corrigidos=corrigidos,
            total_falhas=falhas_count,
            total_skip_circuit_breaker=skip_breaker,
            total_sem_custo_xlsx=sem_custo,
            falhas=falhas_msgs[:20],
        )

    async def _salvar_historico(
        self,
        *,
        profile_id: UUID,
        campaign_id: UUID,
        ml_campaign_id: str,
        item_id: str,
        sku: str | None,
        deal_price: float | None,
        margem: float | None,
        sucesso: bool,
        dry_run: bool,
        erro: str | None,
    ) -> None:
        await self._historico_repo.add(record=MigracaoExecutadaRecord(
            id=novo_record_id(),
            profile_id=profile_id,
            campanha_origem_id=campaign_id,
            campanha_destino_ml_id=ml_campaign_id,
            campanha_destino_ml_nome="(watchdog: re-add guarda-chuva)",
            campanha_destino_ml_tipo="SELLER_CAMPAIGN",
            item_id=item_id,
            sku=sku,
            operacao="watchdog_re_add",
            destino_status="started",
            deal_price=deal_price,
            margem_pct_prevista=margem,
            sucesso=sucesso,
            dry_run=dry_run,
            erro_detalhe=erro,
            timestamp=agora_utc(),
        ))


async def _calcular_falhas_consecutivas(
    historico_repo: MigracaoExecutadaRepository,
    profile_id: UUID,
    ml_campaign_id: str,
) -> dict[tuple[str, str], int]:
    """Olha histórico recente e conta falhas consecutivas pro mesmo (sku, promo).

    Pra cada (item_id, ml_campaign_id), conta quantas tentativas FALHARAM
    seguidas no histórico ordenado por timestamp DESC. Se a última foi
    sucesso, reseta. Resultado vira input do circuit breaker.

    Olha apenas últimas 200 entradas pra não custar caro.
    """
    registros = await historico_repo.list_by_profile(profile_id, limit=200)
    # Filtra só watchdog re-add nessa campanha
    relevantes = [
        r for r in registros
        if r.operacao == "watchdog_re_add"
        and r.campanha_destino_ml_id == ml_campaign_id
        and not r.dry_run  # dry_run não conta pro circuit breaker
    ]
    # Agrupa por item_id ordenado por timestamp DESC (já vem assim)
    contadores: dict[tuple[str, str], int] = {}
    vistos_sucesso: set[tuple[str, str]] = set()
    for r in relevantes:
        chave = (r.item_id, ml_campaign_id)
        if chave in vistos_sucesso:
            continue  # já viu um sucesso depois desse — não conta mais falhas
        if r.sucesso:
            vistos_sucesso.add(chave)
            continue
        contadores[chave] = contadores.get(chave, 0) + 1
    return contadores


def _extrair_sku(item: dict[str, Any]) -> str | None:
    """Extrai SKU (seller_custom_field → SELLER_SKU attribute)."""
    sku = item.get("seller_custom_field")
    if sku:
        return str(sku)
    for attr in item.get("attributes") or []:
        if attr.get("id") == "SELLER_SKU":
            val = attr.get("value_name") or attr.get("value_id")
            if val:
                return str(val)
    return None
