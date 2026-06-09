"""Use cases do contexto Pricing.

GenerateFeeReportUseCase orquestra:
1. Lista todos os anúncios ativos (paginação automática via API ML)
2. Roda calcular_taxas_anuncio em paralelo (semaphore 8 simultâneos)
3. Carrega custos.xlsx do perfil + join SKU → custo (com cascata)
4. Aplica alíquota de imposto e calcula lucro líquido
5. Retorna FeeReport pronto pra JSON ou pra XLSX renderer

Cache em memória 15min — segunda chamada na mesma sessão é instantânea.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.domain.pricing.entity import FeeReport, ListingFees, MatchType
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.pricing.calculator import calcular_taxas_anuncio
from liraz_tools.infrastructure.pricing.costs_loader import (
    CustosXLSXError,
    buscar_custo,
    carregar_custos,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.xlsx_renderer import render_fee_report_xlsx
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.pricing_cache import FeeReportCache
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

logger = get_logger(__name__)


class PricingError(Exception):
    """Erro genérico ao gerar relatório."""


class CustosXLSXNotConfiguredError(PricingError):
    """Perfil não tem custos_xlsx_path configurado."""


class GenerateFeeReportUseCase:
    """Gera FeeReport completo de um perfil.

    Estratégia:
    - Cache hit (15min): retorna direto
    - Cache miss: lista anúncios → paralelo → join custos → calcula lucro
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        cache: FeeReportCache,
        overrides_repo: CostOverridesRepository | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._cache = cache
        # overrides_repo é opcional pra retrocompatibilidade — se não passar,
        # usa o repo default sem overrides aplicados
        self._overrides_repo = overrides_repo or CostOverridesRepository()

    async def execute(
        self,
        profile_id: UUID,
        force_refresh: bool = False,
    ) -> FeeReport:
        # Cache hit
        if not force_refresh:
            cached = self._cache.get(profile_id)
            if cached is not None:
                logger.info(
                    "fee_report_cache_hit",
                    profile_id=str(profile_id),
                    age_seconds=int(
                        (FeeReport.now_utc() - cached.generated_at).total_seconds()
                    ),
                )
                return cached

        profile = await self._profile_repo.get_by_id(profile_id)

        if not profile.config.custos_xlsx_path:
            raise CustosXLSXNotConfiguredError(
                "Caminho da planilha de custos não configurado neste perfil. "
                "Configure em Dashboard → Configuração → Planilha de custos."
            )

        # 1) Carrega custos
        try:
            custos_map = carregar_custos(profile.config.custos_xlsx_path)
        except CustosXLSXError as e:
            raise PricingError(str(e)) from e

        # 1b) Carrega planilha de tarifas reais (separada, alimentada pela
        # extensão Chrome). Path opcional — vazio quando ausente.
        from liraz_tools.infrastructure.pricing.costs_loader import carregar_tarifas_ml
        tarifas_overrides = carregar_tarifas_ml(profile.config.tarifas_ml_xlsx_path)

        logger.info(
            "custos_loaded",
            profile_id=str(profile_id),
            skus=len(custos_map),
            tarifas_overrides=len(tarifas_overrides),
        )

        # 2) Pega credenciais e tokens, abre MLClient
        creds = self._creds_repo.get_app_credentials(profile.slug)
        if profile.ml_user_id is None:
            raise PricingError("perfil não está conectado ao ML")
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        creds_repo = self._creds_repo
        slug = profile.slug

        async def save_refreshed(new_tokens: TokenSet) -> None:
            creds_repo.save_tokens(slug, new_tokens)

        # 3) Cache de frete do perfil
        freight_cache = FreightCache(profile.slug)

        # 4) Roda tudo dentro de um MLClient (reusa connection pool + cache de sessão)
        async with MLClient(
            credentials=creds,
            tokens=tokens,
            on_tokens_refreshed=save_refreshed,
            max_concurrent=8,
        ) as ml:
            # 4a) Lista todos os anúncios ativos (paginação)
            item_ids = await self._listar_todos_ativos(ml, profile.ml_user_id)
            logger.info(
                "active_listings_fetched",
                profile_id=str(profile_id),
                total=len(item_ids),
            )

            # 4b) Paralelo: calcula taxas pra cada anúncio
            tasks = [
                calcular_taxas_anuncio(
                    ml=ml,
                    item_id=iid,
                    cep_destino=profile.config.cep_destino,
                    freight_cache=freight_cache,
                    tarifas_override=tarifas_overrides,
                )
                for iid in item_ids
            ]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 5) Carrega overrides manuais do perfil (cost_overrides.json)
        overrides = self._overrides_repo.load_all(profile.slug)
        logger.info(
            "cost_overrides_applied",
            profile_id=str(profile_id),
            count=len(overrides),
        )

        # 6) Aplica join + cálculo de lucro + diagnóstico
        listings, matches, anuncios_sem_custo, anuncios_com_fallback = (
            self._aplicar_custos_e_lucro(
                raw_results=raw_results,
                item_ids=item_ids,
                custos_map=custos_map,
                aliquota=profile.config.aliquota_imposto,
                custos_manuais=overrides,
            )
        )

        report = FeeReport(
            profile_id=str(profile_id),
            profile_slug=profile.slug,
            generated_at=FeeReport.now_utc(),
            cep_destino=profile.config.cep_destino,
            aliquota_imposto=profile.config.aliquota_imposto,
            custos_xlsx_path=profile.config.custos_xlsx_path,
            listings=listings,
            total_anuncios=len(listings),
            skus_no_custos_xlsx=len(custos_map),
            matches=matches,
            anuncios_sem_custo=anuncios_sem_custo,
            anuncios_com_fallback=anuncios_com_fallback,
        )

        # 6) Cacheia
        self._cache.set(profile_id, report)
        logger.info(
            "fee_report_generated",
            profile_id=str(profile_id),
            total=len(listings),
            sem_custo=len(anuncios_sem_custo),
            com_fallback=len(anuncios_com_fallback),
        )

        return report

    async def _listar_todos_ativos(
        self, ml: MLClient, user_id: int
    ) -> list[str]:
        """Pagina /users/{id}/items/search até esgotar, status=active."""
        item_ids: list[str] = []
        offset = 0
        limit = 50

        while True:
            data = await ml.get(
                f"/users/{user_id}/items/search",
                params={"status": "active", "limit": limit, "offset": offset},
            )
            results = data.get("results") or []
            item_ids.extend(results)

            total = data.get("paging", {}).get("total", 0)
            if offset + limit >= total or not results:
                break
            offset += limit

        return item_ids

    def _aplicar_custos_e_lucro(
        self,
        raw_results: list[Any],
        item_ids: list[str],
        custos_map: dict[str, float],
        aliquota: float,
        custos_manuais: dict[str, float] | None = None,
    ) -> tuple[list[ListingFees], dict[str, int], list[dict[str, Any]], list[dict[str, Any]]]:
        """Faz join SKU→custo, calcula lucro líquido e monta diagnóstico.

        `custos_manuais` é o mapa de overrides do cost_overrides.json — passa
        com prioridade máxima (acima do XLSX) via cascata do buscar_custo.
        """
        listings: list[ListingFees] = []
        matches: dict[str, int] = {
            "manual": 0,
            "exato": 0,
            "case_insensitive": 0,
            "prefixo": 0,
            "prefixo_divergente": 0,
            "nao_encontrado": 0,
        }
        anuncios_sem_custo: list[dict[str, Any]] = []
        anuncios_com_fallback: list[dict[str, Any]] = []

        for iid, r in zip(item_ids, raw_results, strict=False):
            # Exceção não capturada pelo calculator (raro)
            if isinstance(r, BaseException):
                listings.append(
                    ListingFees(
                        item_id=iid,
                        sku=None,
                        title=None,
                        status=None,
                        modalidade="?",
                        modalidade_id=None,
                        preco=0.0,
                        comissao_valor=0.0,
                        comissao_percentual=0.0,
                        tarifa_fixa=0.0,
                        tarifa_fixa_fonte="api",
                        frete_vendedor=0.0,
                        frete_fonte="n/a",
                        free_shipping=False,
                        valor_liquido=0.0,
                        custo_produto=None,
                        custo_fonte="nao_encontrado",
                        custo_fonte_detalhe=None,
                        lucro_bruto=None,
                        imposto_valor=None,
                        lucro_liquido=None,
                        margem_liquida_percentual=None,
                        erro=str(r),
                    )
                )
                continue

            # Erro estruturado vindo do calculator
            if "erro" in r:
                listings.append(
                    ListingFees(
                        item_id=r.get("item_id", iid),
                        sku=r.get("sku"),
                        title=r.get("title"),
                        status=None,
                        modalidade="?",
                        modalidade_id=None,
                        preco=0.0,
                        comissao_valor=0.0,
                        comissao_percentual=0.0,
                        tarifa_fixa=0.0,
                        tarifa_fixa_fonte="api",
                        frete_vendedor=0.0,
                        frete_fonte="n/a",
                        free_shipping=False,
                        valor_liquido=0.0,
                        custo_produto=None,
                        custo_fonte="nao_encontrado",
                        custo_fonte_detalhe=None,
                        lucro_bruto=None,
                        imposto_valor=None,
                        lucro_liquido=None,
                        margem_liquida_percentual=None,
                        erro=r["erro"],
                    )
                )
                continue

            # Cálculo OK — faz join de custo
            sku = r.get("sku")
            custo, fonte = buscar_custo(sku, r["item_id"], custos_map, custos_manuais)

            # Categoriza fonte pro diagnóstico
            custo_fonte: MatchType
            if fonte == "manual":
                custo_fonte = "manual"
            elif fonte == "exato":
                custo_fonte = "exato"
            elif fonte.startswith("case_insensitive"):
                custo_fonte = "case_insensitive"
            elif fonte.startswith("prefixo_divergente"):
                custo_fonte = "prefixo_divergente"
            elif fonte.startswith("prefixo"):
                custo_fonte = "prefixo"
            else:
                custo_fonte = "nao_encontrado"

            matches[custo_fonte] += 1

            if custo_fonte in ("case_insensitive", "prefixo", "prefixo_divergente"):
                anuncios_com_fallback.append(
                    {"item_id": r["item_id"], "sku": sku, "fonte": fonte}
                )
            elif custo_fonte == "nao_encontrado":
                anuncios_sem_custo.append(
                    {
                        "item_id": r["item_id"],
                        "sku": sku,
                        "titulo": r.get("title"),
                    }
                )

            # Cálculo de lucro (só se tem custo)
            lucro_bruto: float | None = None
            imposto_valor: float | None = None
            lucro_liquido: float | None = None
            margem_pct: float | None = None

            if custo is not None:
                valor_final = r["valor_liquido"]  # já vem descontado de taxas
                lucro_bruto = round(valor_final - custo, 2)
                imposto_valor = round(r["preco"] * aliquota, 2)
                lucro_liquido = round(lucro_bruto - imposto_valor, 2)
                margem_pct = round(lucro_liquido / r["preco"], 4) if r["preco"] else 0.0

            listings.append(
                ListingFees(
                    item_id=r["item_id"],
                    sku=sku,
                    title=r.get("title"),
                    status=r.get("status"),
                    modalidade=r.get("modalidade", "?"),
                    modalidade_id=r.get("modalidade_id"),
                    preco=r["preco"],
                    comissao_valor=r["comissao_valor"],
                    comissao_percentual=r["comissao_percentual"],
                    tarifa_fixa=r["tarifa_fixa"],
                    tarifa_fixa_fonte=r["tarifa_fixa_fonte"],
                    # O calculator já divide o list_cost (recommended) entre
                    # tarifa_fixa e frete_vendedor conforme o frete grátis
                    # efetivo (free_shipping E preço >= R$ 79). Exatamente um
                    # dos dois carrega o valor; o outro fica zerado.
                    frete_vendedor=r["frete_vendedor"],
                    frete_fonte=r["frete_fonte"],
                    free_shipping=r["free_shipping"],
                    valor_liquido=r["valor_liquido"],
                    custo_produto=custo,
                    custo_fonte=custo_fonte,
                    custo_fonte_detalhe=fonte if fonte != custo_fonte else None,
                    lucro_bruto=lucro_bruto,
                    imposto_valor=imposto_valor,
                    lucro_liquido=lucro_liquido,
                    margem_liquida_percentual=margem_pct,
                    erro=None,
                )
            )

        return listings, matches, anuncios_sem_custo, anuncios_com_fallback


class ExportFeeReportXLSXUseCase:
    """Pega um FeeReport (cache ou geração nova) e renderiza XLSX bytes.

    Layout idêntico ao MCP — alíquota editável em R1, fórmulas em I/K/M/N/O.
    """

    def __init__(
        self,
        generate_use_case: GenerateFeeReportUseCase,
        overrides_repo: CostOverridesRepository | None = None,
    ) -> None:
        self._generate = generate_use_case
        self._overrides_repo = overrides_repo or CostOverridesRepository()

    async def execute(self, profile_id: UUID) -> tuple[bytes, str]:
        """Returns: (xlsx_bytes, suggested_filename)."""
        report = await self._generate.execute(profile_id)

        # Reconstrói formato esperado pelo renderer (lista de dicts MCP-style)
        resultados_mcp_style = [
            self._listing_to_mcp_dict(listing) for listing in report.listings
        ]
        custos_map = carregar_custos(report.custos_xlsx_path)

        # Overrides ficam no mesmo nível de prioridade no XLSX que na UI
        overrides = self._overrides_repo.load_all(report.profile_slug)

        xlsx_bytes, _ = render_fee_report_xlsx(
            resultados=resultados_mcp_style,
            custos=custos_map,
            aliquota=report.aliquota_imposto,
            custos_manuais=overrides,
        )

        ts = report.generated_at.strftime("%Y%m%d_%H%M%S")
        filename = f"taxas_lucro_{report.profile_slug}_{ts}.xlsx"
        return xlsx_bytes, filename

    @staticmethod
    def _listing_to_mcp_dict(listing: ListingFees) -> dict[str, Any]:
        """Converte ListingFees pro formato dict que o renderer espera."""
        if listing.erro:
            return {"item_id": listing.item_id, "erro": listing.erro}
        return {
            "item_id": listing.item_id,
            "sku": listing.sku,
            "title": listing.title,
            "status": listing.status,
            "modalidade": listing.modalidade,
            "preco": listing.preco,
            "comissao_valor": listing.comissao_valor,
            "comissao_percentual": listing.comissao_percentual,
            "tarifa_fixa": listing.tarifa_fixa,
            "frete_vendedor": listing.frete_vendedor,
        }


# ─── Use cases pra editar overrides direto da UI ─────────────────────────────


def _recalc_listing_with_cost(
    listing: ListingFees, new_custo: float | None, aliquota: float,
) -> ListingFees:
    """Recalcula lucro_bruto/imposto/lucro_liquido/margem de uma linha.

    Usado tanto pelo SetCostOverride (passa o novo valor) quanto pelo
    RemoveCostOverride (passa o valor original do XLSX ou None).

    Não muda preço, comissão, tarifa fixa ou frete — esses vêm da API ML e
    não dependem do custo.
    """
    if new_custo is None or listing.preco <= 0:
        return listing.model_copy(update={
            "custo_produto": None,
            "lucro_bruto": None,
            "imposto_valor": None,
            "lucro_liquido": None,
            "margem_liquida_percentual": None,
        })

    lucro_bruto = round(listing.valor_liquido - new_custo, 2)
    imposto = round(listing.preco * aliquota, 2)
    lucro_liquido = round(lucro_bruto - imposto, 2)
    margem = round(lucro_liquido / listing.preco, 4)

    return listing.model_copy(update={
        "custo_produto": new_custo,
        "lucro_bruto": lucro_bruto,
        "imposto_valor": imposto,
        "lucro_liquido": lucro_liquido,
        "margem_liquida_percentual": margem,
        "custo_fonte": "manual",
        "custo_fonte_detalhe": "override manual",
    })


class SetCostOverrideUseCase:
    """Define um override manual de custo pra uma chave (SKU ou MLB).

    Persiste no cost_overrides.json e ATUALIZA o report em cache aplicando
    o novo custo + recálculo de lucro/margem da linha afetada.

    Isso evita ter que regenerar todo o report (que custa 30-60s) só pra
    refletir uma edição local. O cache fica consistente até expirar (15min).
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        cache: FeeReportCache,
        overrides_repo: CostOverridesRepository | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._cache = cache
        self._overrides_repo = overrides_repo or CostOverridesRepository()

    async def execute(
        self, profile_id: UUID, key: str, value: float,
    ) -> ListingFees | None:
        """Salva override e retorna a linha atualizada (ou None se não bateu)."""
        if value < 0:
            raise PricingError(f"custo deve ser >= 0, recebido {value}")

        profile = await self._profile_repo.get_by_id(profile_id)
        self._overrides_repo.set_one(profile.slug, key, value)

        # Atualiza cache em memória — encontra a linha pela chave (SKU ou MLB)
        cached = self._cache.get(profile_id)
        if cached is None:
            return None  # sem cache, próxima geração já pega o override

        aliquota = profile.config.aliquota_imposto
        updated_listing: ListingFees | None = None
        new_listings: list[ListingFees] = []
        for listing in cached.listings:
            matches_key = (
                listing.item_id == key
                or (listing.sku is not None and listing.sku == key)
            )
            if matches_key:
                updated_listing = _recalc_listing_with_cost(
                    listing, value, aliquota
                )
                new_listings.append(updated_listing)
            else:
                new_listings.append(listing)

        # Recalcula contagem de matches do diagnóstico (a linha virou "manual")
        updated_report = cached.model_copy(update={
            "listings": new_listings,
            "matches": _recount_matches(new_listings),
            "anuncios_sem_custo": [
                {"item_id": it.item_id, "sku": it.sku, "titulo": it.title}
                for it in new_listings
                if it.custo_fonte == "nao_encontrado" and not it.erro
            ],
            "anuncios_com_fallback": [
                {
                    "item_id": it.item_id,
                    "sku": it.sku,
                    "fonte": it.custo_fonte_detalhe or it.custo_fonte,
                }
                for it in new_listings
                if it.custo_fonte in ("case_insensitive", "prefixo", "prefixo_divergente")
            ],
        })
        self._cache.set(profile_id, updated_report)

        return updated_listing


class RemoveCostOverrideUseCase:
    """Remove um override e recarrega a linha com o custo original do XLSX."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        cache: FeeReportCache,
        overrides_repo: CostOverridesRepository | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._cache = cache
        self._overrides_repo = overrides_repo or CostOverridesRepository()

    async def execute(self, profile_id: UUID, key: str) -> ListingFees | None:
        profile = await self._profile_repo.get_by_id(profile_id)
        self._overrides_repo.remove_one(profile.slug, key)

        cached = self._cache.get(profile_id)
        if cached is None:
            return None

        # Recarrega custos do XLSX pra obter o valor original (sem override)
        try:
            custos_map = carregar_custos(cached.custos_xlsx_path)
        except CustosXLSXError:
            custos_map = {}

        # Carrega overrides RESTANTES (já sem essa key, foi removida)
        overrides_restantes = self._overrides_repo.load_all(profile.slug)

        aliquota = profile.config.aliquota_imposto
        updated_listing: ListingFees | None = None
        new_listings: list[ListingFees] = []

        for listing in cached.listings:
            matches_key = (
                listing.item_id == key
                or (listing.sku is not None and listing.sku == key)
            )
            if matches_key:
                # Resolve o custo "natural" pela cascata (sem override pra essa key)
                custo, fonte = buscar_custo(
                    listing.sku, listing.item_id, custos_map, overrides_restantes,
                )
                recalced = _recalc_listing_with_cost(listing, custo, aliquota)
                # Override fonte na recalced (fonte natural, não 'manual')
                recalced = recalced.model_copy(update={
                    "custo_fonte": _categorize_fonte(fonte),
                    "custo_fonte_detalhe": fonte if "_" in fonte or ":" in fonte else None,
                })
                updated_listing = recalced
                new_listings.append(recalced)
            else:
                new_listings.append(listing)

        updated_report = cached.model_copy(update={
            "listings": new_listings,
            "matches": _recount_matches(new_listings),
            "anuncios_sem_custo": [
                {"item_id": it.item_id, "sku": it.sku, "titulo": it.title}
                for it in new_listings
                if it.custo_fonte == "nao_encontrado" and not it.erro
            ],
            "anuncios_com_fallback": [
                {
                    "item_id": it.item_id,
                    "sku": it.sku,
                    "fonte": it.custo_fonte_detalhe or it.custo_fonte,
                }
                for it in new_listings
                if it.custo_fonte in ("case_insensitive", "prefixo", "prefixo_divergente")
            ],
        })
        self._cache.set(profile_id, updated_report)

        return updated_listing


def _categorize_fonte(fonte: str) -> MatchType:
    """Mesma categorização que GenerateFeeReportUseCase usa."""
    if fonte == "manual":
        return "manual"
    if fonte == "exato":
        return "exato"
    if fonte.startswith("case_insensitive"):
        return "case_insensitive"
    if fonte.startswith("prefixo_divergente"):
        return "prefixo_divergente"
    if fonte.startswith("prefixo"):
        return "prefixo"
    return "nao_encontrado"


def _recount_matches(listings: list[ListingFees]) -> dict[str, int]:
    """Recalcula a contagem de matches por tipo após edições."""
    counts: dict[str, int] = {
        "manual": 0,
        "exato": 0,
        "case_insensitive": 0,
        "prefixo": 0,
        "prefixo_divergente": 0,
        "nao_encontrado": 0,
    }
    for listing in listings:
        if listing.erro:
            continue
        counts[listing.custo_fonte] = counts.get(listing.custo_fonte, 0) + 1
    return counts
