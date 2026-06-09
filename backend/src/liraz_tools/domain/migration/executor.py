"""Executor de migrações automáticas (Leva 5.9.4.C.1).

Pega oportunidades detectadas pela `DetectarOportunidadesMigracaoUseCase`,
aplica regra de preferência entre múltiplos destinos por SKU, e executa
via POST `/seller-promotions/items/{id}` no ML.

Modo dry_run: simula tudo, salva no histórico marcado como dry_run=True,
mas NÃO chama o ML.

Regra de preferência entre múltiplos destinos pro mesmo SKU
(do mais para o menos prioritário):
1. Campanha "especial" (nome bate padrão palíndromo data: 5.5, 6.6, 11.11)
2. Status started (em andamento) antes de pending (programada)
3. Margem % prevista mais alta
4. sold_quantity mais alto (tiebreaker)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.migration.detector_cache import get_detector_cache
from liraz_tools.domain.migration.use_cases import (
    DetectarOportunidadesMigracaoUseCase,
    OportunidadeMigracao,
)
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotion_items import (
    ItemAlreadyInCampaignError,
    MLPromotionError,
    adicionar_sku_em_campanha,
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

# Padrão pra identificar campanhas "especiais" pelo nome.
# Bate: 5.5, 6.6, 11.11, 12.12, 06.06, etc. Aceita até 2 dígitos.
_PADRAO_DATA_ESPECIAL = re.compile(r"\b(\d{1,2})\.\1\b")


def _eh_especial(nome: str | None) -> bool:
    """True se o nome da promoção bate padrão de data dupla (5.5, 6.6...)."""
    if not nome:
        return False
    return bool(_PADRAO_DATA_ESPECIAL.search(nome))


@dataclass
class ResultadoExecucao:
    """Resumo da execução de migrações pra um perfil/campanha."""

    profile_id: UUID
    dry_run: bool
    total_oportunidades: int
    total_skus_unicos: int
    total_migracoes_tentadas: int
    total_sucesso: int
    total_erros: int
    total_ja_estava: int
    erros: list[str]


def escolher_destino_canonico(
    oportunidades_do_sku: list[OportunidadeMigracao],
) -> OportunidadeMigracao:
    """Dado várias oportunidades pro mesmo SKU, escolhe a preferencial.

    Ordem (do mais para menos importante):
    1. Especial (nome bate padrão data dupla)
    2. Started antes de pending
    3. Margem % mais alta
    4. Sold quantity mais alto
    """
    assert oportunidades_do_sku, "lista não pode ser vazia"

    def chave(o: OportunidadeMigracao) -> tuple[Any, ...]:
        return (
            # Negativo pra ordem decrescente em sorted asc
            -int(_eh_especial(o.campanha_destino_ml_nome)),
            -int(o.campanha_destino_ml_status == "started"),
            -o.margem_pct_pos_migracao,
            -o.sold_quantity,
        )

    return sorted(oportunidades_do_sku, key=chave)[0]


class ExecutarMigracoesUseCase:
    """Executa migrações detectadas pra um perfil + campanha origem.

    Fluxo:
    1. Chama detector → lista oportunidades
    2. Agrupa por SKU, escolhe destino canônico pra cada
    3. Pra cada (SKU → destino):
       a. Se dry_run=True: log + salva histórico marcado como simulação
       b. Senão: POST no ML, salva histórico do resultado real
    4. Devolve ResultadoExecucao com contadores
    """

    def __init__(
        self,
        detector: DetectarOportunidadesMigracaoUseCase,
        profile_repo: SQLAlchemyProfileRepository,
        campaign_repo: CampaignRepository,
        creds_repo: PerProfileCredentialsRepository,
        historico_repo: MigracaoExecutadaRepository,
    ) -> None:
        self._detector = detector
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._creds_repo = creds_repo
        self._historico_repo = historico_repo

    async def execute(
        self,
        profile_id: UUID,
        campaign_origem_id: UUID,
        *,
        dry_run: bool,
        apenas_skus: set[str] | None = None,
    ) -> ResultadoExecucao:
        """Executa migrações.

        Args:
            apenas_skus: se fornecido, filtra oportunidades pra esses item_ids
                somente. Útil pra testes graduais ("migra só 1 SKU pra ganhar
                confiança antes de liberar todos").
        """
        # 1) Detecta oportunidades (passando apenas_skus pra reduzir trabalho)
        oportunidades = await self._detector.execute(
            profile_id, campaign_origem_id,
            incluir_programadas=True,
            apenas_skus=apenas_skus,
        )

        # Filtragem extra defensiva (caso detector não tenha respeitado)
        if apenas_skus:
            oportunidades = [
                o for o in oportunidades if o.item_id in apenas_skus
            ]
            logger.info(
                "execucao_filtrada_apenas_skus",
                qtd_filtradas=len(oportunidades),
                skus_solicitados=list(apenas_skus),
            )

        if not oportunidades:
            logger.info(
                "execucao_sem_oportunidades",
                profile_id=str(profile_id),
                campaign_id=str(campaign_origem_id),
            )
            return ResultadoExecucao(
                profile_id=profile_id,
                dry_run=dry_run,
                total_oportunidades=0,
                total_skus_unicos=0,
                total_migracoes_tentadas=0,
                total_sucesso=0,
                total_erros=0,
                total_ja_estava=0,
                erros=[],
            )

        # 2) Agrupa por SKU, escolhe destino canônico
        por_sku: dict[str, list[OportunidadeMigracao]] = {}
        for o in oportunidades:
            por_sku.setdefault(o.item_id, []).append(o)

        escolhas: list[OportunidadeMigracao] = [
            escolher_destino_canonico(opts) for opts in por_sku.values()
        ]
        logger.info(
            "execucao_iniciando",
            profile_id=str(profile_id),
            campaign_id=str(campaign_origem_id),
            dry_run=dry_run,
            total_oportunidades=len(oportunidades),
            total_skus_unicos=len(escolhas),
        )

        # 3) Resolve credenciais (precisa pra executar de verdade)
        profile = await self._profile_repo.get_by_id(profile_id)
        creds = self._creds_repo.get_app_credentials(profile.slug)
        # Invariante: execução de migração só roda pra perfil conectado.
        assert profile.ml_user_id is not None
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: TokenSet) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        # 4) Loop de execução
        total_sucesso = 0
        total_erros = 0
        total_ja_estava = 0
        erros: list[str] = []

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            for escolha in escolhas:
                resultado_ok = False
                erro_detalhe: str | None = None
                ja_estava = False

                if dry_run:
                    # Modo simulação — só registra
                    logger.info(
                        "execucao_dry_run_skip",
                        item_id=escolha.item_id,
                        sku=escolha.sku,
                        destino=escolha.campanha_destino_ml_id,
                        deal_price=escolha.deal_price_sugerido,
                        margem=escolha.margem_pct_pos_migracao,
                    )
                    resultado_ok = True
                else:
                    # Execução real — chama ML
                    try:
                        await adicionar_sku_em_campanha(
                            ml,
                            item_id=escolha.item_id,
                            promotion_id=escolha.campanha_destino_ml_id,
                            promotion_type=escolha.campanha_destino_ml_tipo,
                            deal_price=escolha.deal_price_sugerido,
                        )
                        resultado_ok = True
                    except ItemAlreadyInCampaignError as e:
                        # Idempotência: item já tá lá. Considera sucesso.
                        resultado_ok = True
                        ja_estava = True
                        logger.info(
                            "execucao_item_ja_na_campanha",
                            item_id=escolha.item_id,
                            destino=escolha.campanha_destino_ml_id,
                            detail=str(e),
                        )
                    except MLPromotionError as e:
                        erro_detalhe = str(e)
                        logger.warning(
                            "execucao_erro_ml",
                            item_id=escolha.item_id,
                            destino=escolha.campanha_destino_ml_id,
                            error=erro_detalhe,
                        )

                # 5) Salva histórico
                await self._historico_repo.add(record=MigracaoExecutadaRecord(
                    id=novo_record_id(),
                    profile_id=profile_id,
                    campanha_origem_id=campaign_origem_id,
                    campanha_destino_ml_id=escolha.campanha_destino_ml_id,
                    campanha_destino_ml_nome=escolha.campanha_destino_ml_nome,
                    campanha_destino_ml_tipo=escolha.campanha_destino_ml_tipo,
                    item_id=escolha.item_id,
                    sku=escolha.sku,
                    operacao="add",
                    destino_status=escolha.campanha_destino_ml_status,
                    deal_price=escolha.deal_price_sugerido,
                    margem_pct_prevista=escolha.margem_pct_pos_migracao,
                    sucesso=resultado_ok,
                    dry_run=dry_run,
                    erro_detalhe=erro_detalhe,
                    timestamp=agora_utc(),
                ))

                if resultado_ok:
                    if ja_estava:
                        total_ja_estava += 1
                    else:
                        total_sucesso += 1
                else:
                    total_erros += 1
                    if erro_detalhe:
                        erros.append(
                            f"{escolha.item_id} → {escolha.campanha_destino_ml_id}: {erro_detalhe}"
                        )

        logger.info(
            "execucao_concluida",
            profile_id=str(profile_id),
            campaign_id=str(campaign_origem_id),
            dry_run=dry_run,
            total_skus_unicos=len(escolhas),
            sucesso=total_sucesso,
            ja_estava=total_ja_estava,
            erros=total_erros,
        )

        # Invalida cache de detecção em memória — força próxima consulta
        # a re-detectar do zero (5.9.4 opt 2). Em execução real os SKUs
        # mudaram de campanha, então o cache anterior está stale.
        # Mesmo em dry_run invalidamos pra ser conservador.
        get_detector_cache().invalidar(profile_id, campaign_origem_id)

        return ResultadoExecucao(
            profile_id=profile_id,
            dry_run=dry_run,
            total_oportunidades=len(oportunidades),
            total_skus_unicos=len(escolhas),
            total_migracoes_tentadas=len(escolhas),
            total_sucesso=total_sucesso,
            total_erros=total_erros,
            total_ja_estava=total_ja_estava,
            erros=erros[:20],  # limita pra não vazar muito
        )
