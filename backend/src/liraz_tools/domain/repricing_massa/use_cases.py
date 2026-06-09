"""Use cases da feature 'Reprecificar tudo' (operações da loja).

Pra lojas que não podem criar campanha (sem `seller-promotions` no perfil
ML do vendedor), expõe o mesmo cálculo passo3 do P (margem alvo Q2 do
perfil) — só que aplica direto no preço-base via PUT /items/{id}, sem
campanha.

Dois passos pra dar visibilidade ao usuário:
  1. **Simular** — `SimulateRepricingMassaUseCase` recebe `item_ids` e
     devolve, por item: preço atual, P recomendado, margem prevista,
     custo, frete, e o erro caso o item não dê pra reprecificar (sem
     custo, modalidade incompatível, preço inválido).
  2. **Aplicar** — `ApplyRepricingMassaUseCase` recebe `precos`
     (`item_id → novo_preco` que o usuário confirmou na tela) e roda
     PUT em massa via `aplicar_repricing_em_massa` (semáforo de writes
     + idempotência por snapshot de preço).

A separação garante que o usuário SEMPRE veja a tabela antes do PUT —
nunca há um caminho "aplica direto sem simular".
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.items_fetcher import fetch_items_basic_info
from liraz_tools.infrastructure.ml.repricing_apply import (
    AplicarRepricingResult,
    aplicar_repricing_em_massa,
)
from liraz_tools.infrastructure.pricing.calculator import calcular_taxas_anuncio
from liraz_tools.infrastructure.pricing.costs_loader import (
    buscar_custo,
    carregar_custos,
    carregar_tarifas_ml,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.repricing_calculator import (
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
    from liraz_tools.infrastructure.repositories.repricing_snapshot_repository import (
        RepricingSnapshotRepository,
    )

logger = get_logger(__name__)


@dataclass
class RepricingMassaItem:
    """Resultado da simulação de UM item.

    `preco_novo` é None quando o item tem erro (sem custo, modalidade free,
    margem inviável). Nesse caso a UI mostra o motivo em `erro` e desabilita
    o checkbox de aplicar.
    """

    item_id: str
    sku: str | None
    titulo: str | None
    preco_atual: float | None
    preco_novo: float | None
    margem_atual_pct: float | None
    margem_nova_pct: float | None
    custo: float | None
    fonte_custo: str | None
    erro: str | None


class RepricingMassaError(Exception):
    """Erro de pré-condição: loja desconectada, custos não configurados, etc."""


class SimulateRepricingMassaUseCase:
    """Pra cada item_id, calcula o P passo3 + a margem prevista nesse P."""

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
        item_ids: list[str],
    ) -> list[RepricingMassaItem]:
        if not item_ids:
            return []

        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            raise RepricingMassaError("loja não conectada ao ML")
        if not profile.config.custos_xlsx_path:
            raise RepricingMassaError(
                "planilha de custos não configurada no perfil",
            )

        try:
            custos_map = carregar_custos(profile.config.custos_xlsx_path)
        except Exception as e:
            raise RepricingMassaError(f"erro lendo custos.xlsx: {e}") from e

        tarifas_overrides = carregar_tarifas_ml(
            profile.config.tarifas_ml_xlsx_path,
        )
        overrides = self._overrides_repo.load_all(profile.slug)
        aliquota = profile.config.aliquota_imposto
        cep = profile.config.cep_destino
        margem_alvo = profile.config.margem_alvo_campanha  # Q2
        margem_minima = profile.config.margem_minima       # R2
        freight_cache = FreightCache(profile.slug)

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed, max_concurrent=8,
        ) as ml:
            info = await fetch_items_basic_info(ml, item_ids)

            async def _processar(item_id: str) -> RepricingMassaItem:
                info_item = info.get(item_id, {})
                sku = info_item.get("sku")
                titulo = info_item.get("titulo")
                preco_raw = info_item.get("preco")
                try:
                    preco_atual = (
                        float(preco_raw) if preco_raw is not None else None
                    )
                except (TypeError, ValueError):
                    preco_atual = None

                if not preco_atual or preco_atual <= 0:
                    return RepricingMassaItem(
                        item_id=item_id, sku=sku, titulo=titulo,
                        preco_atual=preco_atual, preco_novo=None,
                        margem_atual_pct=None, margem_nova_pct=None,
                        custo=None, fonte_custo=None,
                        erro="item sem preço no ML",
                    )

                custo, fonte = buscar_custo(sku, item_id, custos_map, overrides)
                if custo is None or custo <= 0:
                    valor = "ausente" if custo is None else f"R$ {custo:.2f}"
                    return RepricingMassaItem(
                        item_id=item_id, sku=sku, titulo=titulo,
                        preco_atual=preco_atual, preco_novo=None,
                        margem_atual_pct=None, margem_nova_pct=None,
                        custo=custo, fonte_custo=fonte,
                        erro=(
                            f"sem custo válido ({valor}, fonte={fonte}) — "
                            "corrija no custos.xlsx"
                        ),
                    )

                try:
                    taxas = await calcular_taxas_anuncio(
                        ml=ml, item_id=item_id, cep_destino=cep,
                        freight_cache=freight_cache,
                        tarifas_override=tarifas_overrides,
                    )
                except Exception as e:
                    return RepricingMassaItem(
                        item_id=item_id, sku=sku, titulo=titulo,
                        preco_atual=preco_atual, preco_novo=None,
                        margem_atual_pct=None, margem_nova_pct=None,
                        custo=custo, fonte_custo=fonte,
                        erro=f"falha ao consultar taxas no ML: {e}",
                    )
                if "erro" in taxas:
                    return RepricingMassaItem(
                        item_id=item_id, sku=sku, titulo=titulo,
                        preco_atual=preco_atual, preco_novo=None,
                        margem_atual_pct=None, margem_nova_pct=None,
                        custo=custo, fonte_custo=fonte,
                        erro=f"taxas indisponíveis: {taxas['erro']}",
                    )

                comissao_pct = (
                    taxas.get("comissao_percentual", 0.0) or 0.0
                ) / 100.0
                list_cost = taxas.get("list_cost")
                # Quando o list_cost vem do override do usuário (tarifas_ml.xlsx),
                # ele JÁ É a tarifa fixa real do painel ML — o teto teórico de
                # R$ 8,55 não deve limitar (itens com tarifa real > 8,55 seriam
                # truncados, dando margem inflada e preço sugerido errado).
                eh_override = taxas.get("tarifa_fixa_fonte") == "override_xlsx"
                # Overrides por regime (custo fixo <79 / frete ≥79) pra o passo3
                # avaliar cada ramo do degrau com o valor REAL — sem isso, ao
                # cruzar R$ 79 no cálculo, o ramo oposto usaria o valor do
                # regime errado.
                lc_below = taxas.get("list_cost_below_79_override")
                lc_above = taxas.get("list_cost_above_79_override")

                margem_atual = margem_liquida_pct(
                    preco_atual, custo=custo, tarifa_pct=comissao_pct,
                    list_cost=list_cost, aliquota=aliquota,
                    sem_teto_custo_fixo=eh_override,
                    list_cost_below_79=lc_below,
                    list_cost_above_79=lc_above,
                )

                preco_novo = preco_recomendado(
                    custo=custo, tarifa_pct=comissao_pct,
                    list_cost=list_cost, aliquota=aliquota,
                    margem_alvo=margem_alvo, margem_minima=margem_minima,
                    sem_teto_custo_fixo=eh_override,
                    list_cost_below_79=lc_below,
                    list_cost_above_79=lc_above,
                )
                if preco_novo is None:
                    return RepricingMassaItem(
                        item_id=item_id, sku=sku, titulo=titulo,
                        preco_atual=preco_atual, preco_novo=None,
                        margem_atual_pct=round(margem_atual * 100, 2),
                        margem_nova_pct=None,
                        custo=custo, fonte_custo=fonte,
                        erro=(
                            f"margem alvo {margem_alvo:.0%} inviável "
                            f"(alíquota {aliquota:.0%} + tarifa "
                            f"{comissao_pct:.0%})"
                        ),
                    )

                margem_nova = margem_liquida_pct(
                    preco_novo, custo=custo, tarifa_pct=comissao_pct,
                    list_cost=list_cost, aliquota=aliquota,
                    sem_teto_custo_fixo=eh_override,
                    list_cost_below_79=lc_below,
                    list_cost_above_79=lc_above,
                )

                return RepricingMassaItem(
                    item_id=item_id, sku=sku, titulo=titulo,
                    preco_atual=round(preco_atual, 2),
                    preco_novo=round(preco_novo, 2),
                    margem_atual_pct=round(margem_atual * 100, 2),
                    margem_nova_pct=round(margem_nova * 100, 2),
                    custo=round(custo, 2),
                    fonte_custo=fonte,
                    erro=None,
                )

            return list(
                await asyncio.gather(*[_processar(iid) for iid in item_ids]),
            )


class ApplyRepricingMassaUseCase:
    """Aplica PUT /items/{id} em massa pros preços confirmados pelo usuário.

    Persiste snapshot no `RepricingSnapshotRepository` pra cada PUT
    bem-sucedido, sob uma `session_id` única gerada nessa execução.
    Permite reverter via `RevertRepricingMassaUseCase`.
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        snapshot_repo: RepricingSnapshotRepository | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        # Opcional: caller pode passar None se não quer persistir. Em prod
        # vamos sempre passar pra ter undo.
        self._snapshot_repo = snapshot_repo

    async def execute(
        self,
        profile_id: UUID,
        *,
        precos: dict[str, float],
    ) -> tuple[AplicarRepricingResult, UUID | None]:
        """Executa o repricing e devolve (result, session_id).

        `session_id` é o UUID que agrupa essa execução no DB de snapshots;
        None quando `snapshot_repo=None` (= modo legado sem persistência) ou
        quando nenhum item foi aplicado de fato.
        """
        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            raise RepricingMassaError("loja não conectada ao ML")
        if not precos:
            return AplicarRepricingResult(), None

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed, max_concurrent=8,
        ) as ml:
            result = await aplicar_repricing_em_massa(
                ml, precos=precos, logger=logger,
            )

        session_id: UUID | None = None
        if self._snapshot_repo is not None and result.precos_anteriores_por_item:
            from liraz_tools.infrastructure.repositories.repricing_snapshot_repository import (
                SnapshotItem,
            )
            session_id = self._snapshot_repo.nova_sessao()
            snapshots = [
                SnapshotItem(
                    item_id=iid,
                    preco_anterior=result.precos_anteriores_por_item[iid],
                    preco_novo=precos[iid],
                )
                for iid in result.aplicados
                if iid in result.precos_anteriores_por_item and iid in precos
            ]
            await self._snapshot_repo.gravar_em_lote(
                profile_id, session_id, snapshots,
            )

        logger.info(
            "repricing_massa_done",
            profile_slug=profile.slug,
            session_id=str(session_id) if session_id else None,
            aplicados=len(result.aplicados),
            ja_no_preco=len(result.ja_no_preco),
            erros=len(result.erros),
        )
        return result, session_id


class RevertRepricingMassaUseCase:
    """Reverte uma sessão de repricing: PUT pros `preco_anterior` originais.

    Idempotente: chamar de novo numa sessão já revertida não bagunça (volta
    a setar os preços anteriores, mas o `aplicar_repricing_em_massa` detecta
    `ja_no_preco` quando o preço atual já bate).
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        snapshot_repo: RepricingSnapshotRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._snapshot_repo = snapshot_repo

    async def execute(
        self, profile_id: UUID, session_id: UUID,
    ) -> AplicarRepricingResult:
        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            raise RepricingMassaError("loja não conectada ao ML")

        items = await self._snapshot_repo.buscar_itens_da_sessao(
            profile_id, session_id,
        )
        if not items:
            raise RepricingMassaError(
                f"sessão {session_id} não encontrada pra este perfil",
            )

        precos_undo = {it.item_id: it.preco_anterior for it in items}

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed, max_concurrent=8,
        ) as ml:
            result = await aplicar_repricing_em_massa(
                ml, precos=precos_undo, logger=logger,
            )

        await self._snapshot_repo.marcar_revertido(profile_id, session_id)

        logger.info(
            "repricing_massa_revert_done",
            profile_slug=profile.slug,
            session_id=str(session_id),
            revertidos=len(result.aplicados),
            ja_no_preco=len(result.ja_no_preco),
            erros=len(result.erros),
        )
        return result
