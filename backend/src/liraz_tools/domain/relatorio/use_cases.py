"""Use case principal: gerar o relatório diário de uma loja num dia.

Orquestra: carrega perfil + tokens → coleta orders → carrega custos.xlsx →
busca histórico (ontem + últimos 7d) pra comparativos → monta → persiste KPIs.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.relatorio.calculador import montar_relatorio
from liraz_tools.domain.relatorio.categorias import resolver_nomes_de_categorias
from liraz_tools.domain.relatorio.coletor import coletar_linhas_do_dia
from liraz_tools.domain.relatorio.custos_logisticos import (
    calcular_custos_logisticos_por_sku,
)
from liraz_tools.domain.relatorio.entity import RelatorioDiario
from liraz_tools.domain.relatorio.envio import calcular_metricas_envio
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.pricing.costs_loader import (
    carregar_custos,
    carregar_tarifas_ml,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache

if TYPE_CHECKING:
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )
    from liraz_tools.infrastructure.repositories.profile_repository import (
        SQLAlchemyProfileRepository,
    )
    from liraz_tools.infrastructure.repositories.relatorio_diario_repository import (
        RelatorioDiarioRepository,
    )

logger = get_logger(__name__)


class ProfileNaoConectadoError(Exception):
    """Perfil não tem tokens válidos ou não tem ml_user_id."""


class SemCustosXlsxError(Exception):
    """Perfil sem `custos_xlsx_path` configurado — sem custo o cálculo de
    lucro fica vazio. Erro explícito pra UI mandar configurar antes."""


class GerarRelatorioDiarioUseCase:
    """Compõe o RelatorioDiario do dia. Persiste o snapshot de KPIs."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        relatorio_repo: RelatorioDiarioRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._relatorio_repo = relatorio_repo

    async def execute(
        self, profile_id: UUID, *, dia: date,
    ) -> RelatorioDiario:
        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            raise ProfileNaoConectadoError(
                f"loja '{profile.name}' não está conectada ao ML",
            )
        if not profile.config.custos_xlsx_path:
            raise SemCustosXlsxError(
                f"loja '{profile.name}' sem custos.xlsx — configure antes",
            )

        custos_map = carregar_custos(profile.config.custos_xlsx_path)
        tarifas_overrides = carregar_tarifas_ml(profile.config.tarifas_ml_xlsx_path)
        freight_cache = FreightCache(profile.slug)

        # Histórico pra comparativos
        ontem = await self._relatorio_repo.buscar(profile_id, dia - timedelta(days=1))
        historico_7d = await self._relatorio_repo.listar_ultimos_n_dias(
            profile_id, ate=dia - timedelta(days=1), n=7,
        )
        # Mesmo dia da semana passada (dia - 7) — suaviza sazonalidade semanal
        mesma_dow = await self._relatorio_repo.buscar(
            profile_id, dia - timedelta(days=7),
        )
        # Histórico desde o dia 1 do mês até dia-1 pra acumulado + projeção.
        # Reusa o mesmo método: `listar_ultimos_n_dias` com n = dia atual - 1.
        n_desde_inicio_mes = max(1, dia.day - 1)
        historico_mes = await self._relatorio_repo.listar_ultimos_n_dias(
            profile_id, ate=dia - timedelta(days=1), n=n_desde_inicio_mes,
        )

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            linhas = await coletar_linhas_do_dia(
                ml, seller_id=profile.ml_user_id, dia_brt=dia,
            )

            # Calcula custo logístico (frete + tarifa fixa) por SKU distinto
            # do dia. `sale_fee` é só comissão; sem isso o lucro fica inflado
            # pros SKUs ≥ R$ 79 com frete grátis (vendedor paga ~R$ 17-25).
            skus_para_calcular: dict[str, float] = {}
            for lin in linhas:
                if lin.status != "paid":
                    continue
                # Pra SKU vendido por preços diferentes (raro), usa o último.
                skus_para_calcular[lin.item_id] = lin.unit_price
            custos_logisticos = await calcular_custos_logisticos_por_sku(
                ml,
                skus_vendidos=skus_para_calcular,
                cep=profile.config.cep_destino,
                freight_cache=freight_cache,
                tarifas_overrides=tarifas_overrides,
            )

            # SLA de envio: 1 chamada /shipments/{id} por order pago.
            orders_envio: dict[int, tuple[int, str]] = {}
            for lin in linhas:
                if lin.status != "paid" or lin.shipping_id is None:
                    continue
                orders_envio[lin.order_id] = (lin.shipping_id, lin.timestamp_iso)
            metricas_envio = await calcular_metricas_envio(
                ml, orders_data=orders_envio,
            )

            # Resolve nomes legíveis das categorias ML (`MLB186136 → "Tapetes"`).
            category_ids = {
                lin.category_id
                for lin in linhas
                if lin.status == "paid" and lin.category_id
            }
            nomes_categorias = await resolver_nomes_de_categorias(
                ml, category_ids,
            )

        logger.info(
            "relatorio_diario_coletado",
            profile_id=str(profile_id),
            dia=dia.isoformat(),
            total_linhas=len(linhas),
            pedidos_distintos=len({lin.order_id for lin in linhas}),
            skus_com_frete_calculado=len(custos_logisticos),
        )

        rel = montar_relatorio(
            profile_id=profile_id,
            profile_name=profile.name,
            dia=dia,
            linhas=linhas,
            custos_por_sku=custos_map,
            custos_logisticos_por_item=custos_logisticos,
            ontem=ontem,
            historico_7d=historico_7d,
            mesma_dow=mesma_dow,
            historico_mes=historico_mes,
            metricas_envio=metricas_envio,
            nomes_categorias=nomes_categorias,
        )

        # Persiste snapshot pra alimentar comparativos de outros dias
        await self._relatorio_repo.upsert(
            profile_id,
            dia=dia,
            receita_bruta=rel.kpis.receita_bruta,
            lucro_liquido=rel.kpis.lucro_liquido,
            total_pedidos=rel.kpis.total_pedidos,
            total_unidades=rel.kpis.total_unidades,
        )

        logger.info(
            "relatorio_diario_gerado",
            profile_id=str(profile_id),
            dia=dia.isoformat(),
            receita=rel.kpis.receita_bruta,
            lucro=rel.kpis.lucro_liquido,
            pedidos=rel.kpis.total_pedidos,
            sugestoes=len(rel.sugestoes),
        )

        return rel
