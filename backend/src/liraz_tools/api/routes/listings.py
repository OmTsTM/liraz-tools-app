"""Endpoints de listings (relatório de taxas/margem).

Rotas:
  GET  /api/profiles/{id}/listings/with-fees       — JSON pra tabela na UI
  GET  /api/profiles/{id}/listings/with-fees/xlsx  — download do XLSX
  POST /api/profiles/{id}/listings/with-fees/refresh — invalida cache 15min
  GET  /api/profiles/{id}/skus-com-promocoes       — lista pra criar campanha (Leva 5.12)
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from liraz_tools.api.deps import CredsRepo, ProfileRepo
from liraz_tools.api.schemas.pricing_schemas import FeeReportResponse
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.domain.pricing.use_cases import (
    CustosXLSXNotConfiguredError,
    ExportFeeReportXLSXUseCase,
    GenerateFeeReportUseCase,
    PricingError,
)
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.domain.skus.use_cases import (
    ListarSkusComPromocoesUseCase,
)
from liraz_tools.infrastructure.repositories.pricing_cache import (
    FeeReportCache,
    get_fee_report_cache,
)

router = APIRouter(prefix="/api/profiles", tags=["listings"])
logger = get_logger(__name__)


PricingCache = Annotated[FeeReportCache, Depends(get_fee_report_cache)]


@router.get(
    "/{profile_id}/listings/with-fees",
    response_model=FeeReportResponse,
)
async def get_listings_with_fees(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    cache: PricingCache,
) -> FeeReportResponse:
    """Retorna lista de anúncios ativos com taxas, custos, margens.

    Cacheado por 15min. Pra forçar refresh, chame o endpoint /refresh antes.
    """
    use_case = GenerateFeeReportUseCase(profile_repo, creds_repo, cache)
    try:
        report = await use_case.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except CustosXLSXNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except PricingError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
    return FeeReportResponse.from_domain(report)


@router.post(
    "/{profile_id}/listings/with-fees/refresh",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def refresh_fee_report(
    profile_id: UUID,
    cache: PricingCache,
) -> None:
    """Invalida o cache do relatório. Próxima chamada de /with-fees regera."""
    cache.invalidate(profile_id)


@router.get("/{profile_id}/listings/with-fees/xlsx")
async def download_fee_report_xlsx(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    cache: PricingCache,
) -> Response:
    """Baixa o relatório como XLSX (layout idêntico ao MCP)."""
    generate = GenerateFeeReportUseCase(profile_repo, creds_repo, cache)
    export = ExportFeeReportXLSXUseCase(generate)
    try:
        xlsx_bytes, filename = await export.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except CustosXLSXNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except PricingError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e

    return Response(
        content=xlsx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ─── Schemas pro endpoint /skus-com-promocoes (Leva 5.12) ──────────────


class PromoNoItemResponse(BaseModel):
    promotion_id: str
    nome: str | None
    tipo: str
    status: str
    start_date: str | None
    finish_date: str | None


class SkuComPromocoesResponse(BaseModel):
    item_id: str
    sku: str | None
    titulo: str | None
    preco: float | None
    promocoes: list[PromoNoItemResponse]


class SkusComPromocoesListResponse(BaseModel):
    total: int
    items_em_promocao: int
    results: list[SkuComPromocoesResponse]


@router.get(
    "/{profile_id}/skus-com-promocoes",
    response_model=SkusComPromocoesListResponse,
)
async def listar_skus_com_promocoes(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    incluir_programadas: Annotated[bool, Query()] = True,
) -> SkusComPromocoesListResponse:
    """Lista anúncios ativos do vendedor + promoções em que cada um participa.

    Usado pela tela de criação de campanha (Leva 5.12) — substitui a
    necessidade de simulação como fonte de SKUs disponíveis.

    Parâmetros:
      - `incluir_programadas` (default true): inclui promoções em status
        `pending` (futuras). User criando campanha quer ver conflitos com
        promoções que vão começar em breve, não só as ativas agora.

    Performance: ~25-30 chamadas ML em paralelo. Loja com 200 anúncios e 5
    promoções leva tipicamente 8-15 segundos. Sem cache server-side — a UI
    deve cachear no React Query (5min é razoável).
    """
    try:
        await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    use_case = ListarSkusComPromocoesUseCase(profile_repo, creds_repo)
    skus = await use_case.execute(
        profile_id, incluir_programadas=incluir_programadas,
    )

    items_em_promocao = sum(1 for s in skus if s.promocoes)
    return SkusComPromocoesListResponse(
        total=len(skus),
        items_em_promocao=items_em_promocao,
        results=[
            SkuComPromocoesResponse(
                item_id=s.item_id,
                sku=s.sku,
                titulo=s.titulo,
                preco=s.preco,
                promocoes=[
                    PromoNoItemResponse(
                        promotion_id=p.promotion_id,
                        nome=p.nome,
                        tipo=p.tipo,
                        status=p.status,
                        start_date=p.start_date,
                        finish_date=p.finish_date,
                    )
                    for p in s.promocoes
                ],
            )
            for s in skus
        ],
    )


# ─── Debug: inspecionar tarifa fixa de um item ponta-a-ponta ───────────
# Endpoint de diagnóstico (não usado pela UI) pra investigar divergências
# entre o que o app calcula e o que o ML cobra. Pra cada item:
#  1. Lê /items/{id} cru (modalidade, categoria, logística, preço, MLB legado/novo)
#  2. Chama /sites/MLB/listing_prices com mesmos params do calculator.py
#  3. Opcionalmente repete com price simulado pra ver tarifa em outro patamar
#  4. Retorna tudo lado a lado pra comparar com painel ML / planilha
@router.post("/{profile_id}/debug-tarifa-fixa/{item_id}")
async def debug_tarifa_fixa(
    profile_id: UUID,
    item_id: str,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    preco_simulado: Annotated[
        float | None,
        Query(
            description="Preço simulado (ex: deal_price da campanha). "
            "Se omitido, usa o preço atual do anúncio.",
            gt=0,
        ),
    ] = None,
) -> dict[str, Any]:
    """Inspeção ponta-a-ponta da tarifa fixa pra um item.

    Útil pra diagnosticar:
      - Por que o app cobrou R$ X enquanto sua planilha esperava Y
      - Diferenças entre items MLB4 (legado) e MLB6 (novo)
      - Comportamento da `fixed_fee` quando o preço varia (degrau R$ 79)
      - Quando o app cai no fallback `REGRA_DROP_OFF` vs usa a API direto

    Retorna tudo cru:
      - item: campos relevantes do /items/{id}
      - listing_prices_real: GET /sites/MLB/listing_prices com preço atual
      - listing_prices_simulado: idem com preco_simulado (se passado)
      - calculator_resultado: o que o app calcula hoje com calcular_taxas_anuncio
      - diagnosticos: análise textual (degrau, free_shipping, fallback usado)
    """
    from liraz_tools.infrastructure.ml.client import MLClient
    from liraz_tools.infrastructure.pricing.calculator import (
        LIMITE_FRETE_GRATIS,
        REGRA_DROP_OFF,
        TETO_CUSTO_FIXO,
        calcular_taxas_anuncio,
    )
    from liraz_tools.infrastructure.pricing.freight_cache import FreightCache

    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(404, detail=str(e)) from e

    if profile.status.value != "connected" or profile.ml_user_id is None:
        raise HTTPException(400, detail="loja não conectada")

    creds = creds_repo.get_app_credentials(profile.slug)
    tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile.slug, new_tokens)

    out: dict[str, Any] = {
        "item_id": item_id,
        "profile_slug": profile.slug,
        "params": {
            "preco_simulado": preco_simulado,
        },
    }

    async with MLClient(
        creds, tokens, on_tokens_refreshed=save_refreshed,
    ) as ml:
        # 1) Item cru
        try:
            item_raw = await ml.get(f"/items/{item_id}")
        except Exception as e:
            return {**out, "erro": f"falha em GET /items/{item_id}: {e}"}

        shipping = item_raw.get("shipping") or {}
        item_resumo = {
            "id": item_raw.get("id"),
            "title": item_raw.get("title"),
            "category_id": item_raw.get("category_id"),
            "listing_type_id": item_raw.get("listing_type_id"),
            "currency_id": item_raw.get("currency_id"),
            "price": item_raw.get("price"),
            "original_price": item_raw.get("original_price"),
            "base_price": item_raw.get("base_price"),
            "available_quantity": item_raw.get("available_quantity"),
            "status": item_raw.get("status"),
            "permalink": item_raw.get("permalink"),
            "tags": item_raw.get("tags"),
            "shipping": {
                "mode": shipping.get("mode"),
                "free_shipping": shipping.get("free_shipping"),
                "logistic_type": shipping.get("logistic_type"),
                "tags": shipping.get("tags"),
            },
            # Heurística MLB legado (4xxx) vs novo (6xxx+)
            "mlb_familia": (
                "legado_4x" if item_id.startswith("MLB4")
                else "novo_6x" if item_id.startswith("MLB6")
                else "outro"
            ),
        }
        out["item"] = item_resumo

        preco_real = item_raw.get("original_price") or item_raw.get("price")
        category_id = item_raw.get("category_id")
        listing_type_id = item_raw.get("listing_type_id")
        currency_id = item_raw.get("currency_id", "BRL")
        logistic_type = shipping.get("logistic_type")
        shipping_mode = shipping.get("mode")

        # 2) listing_prices com preço atual
        params_real = {
            "price": preco_real,
            "category_id": category_id,
            "listing_type_id": listing_type_id,
            "currency_id": currency_id,
        }
        if logistic_type:
            params_real["logistic_type"] = logistic_type
        if shipping_mode:
            params_real["shipping_mode"] = shipping_mode

        try:
            lp_real = await ml.get("/sites/MLB/listing_prices", params=params_real)
            out["listing_prices_real"] = {
                "params": params_real,
                "raw": lp_real,
            }
        except Exception as e:
            out["listing_prices_real"] = {"params": params_real, "erro": str(e)}

        # 3) listing_prices com preço simulado (opcional)
        if preco_simulado is not None and preco_simulado != preco_real:
            params_sim = {**params_real, "price": preco_simulado}
            try:
                lp_sim = await ml.get(
                    "/sites/MLB/listing_prices", params=params_sim,
                )
                out["listing_prices_simulado"] = {
                    "params": params_sim,
                    "raw": lp_sim,
                }
            except Exception as e:
                out["listing_prices_simulado"] = {
                    "params": params_sim, "erro": str(e),
                }

        # 4) O que o calculator_resultado do app produziria HOJE
        freight_cache = FreightCache(profile.slug)
        cep = profile.config.cep_destino
        preco_calc = preco_simulado if preco_simulado else None
        try:
            calc = await calcular_taxas_anuncio(
                ml=ml, item_id=item_id, cep_destino=cep,
                freight_cache=freight_cache, preco_simulado=preco_calc,
            )
            out["calculator_resultado"] = calc
        except Exception as e:
            out["calculator_resultado"] = {"erro": str(e)}

    # 5) Diagnósticos textuais (análise pós-coleta)
    diagnosticos: list[str] = []
    preco_efetivo = preco_simulado or preco_real

    if preco_efetivo is not None:
        if preco_efetivo < REGRA_DROP_OFF["limite_preco_baixo"]:
            diagnosticos.append(
                f"Preço R$ {preco_efetivo:.2f} < R$ "
                f"{REGRA_DROP_OFF['limite_preco_baixo']} — "
                f"custo fixo = 50% do preço."
            )
        elif preco_efetivo < LIMITE_FRETE_GRATIS:
            diagnosticos.append(
                f"Preço R$ {preco_efetivo:.2f} < R$ {LIMITE_FRETE_GRATIS} — custo fixo "
                f"(coluna Tarifa Fixa) = min(frete recommended, teto R$ {TETO_CUSTO_FIXO})."
            )
        else:
            diagnosticos.append(
                f"Preço R$ {preco_efetivo:.2f} ≥ R$ {LIMITE_FRETE_GRATIS} — sem custo fixo."
            )

        if shipping.get("free_shipping"):
            if preco_efetivo >= LIMITE_FRETE_GRATIS:
                diagnosticos.append(
                    "Anúncio com `free_shipping=true` E preço acima do "
                    f"degrau R$ {LIMITE_FRETE_GRATIS} — vendedor PAGA frete."
                )
            else:
                diagnosticos.append(
                    "Anúncio com `free_shipping=true` MAS preço abaixo do "
                    f"degrau R$ {LIMITE_FRETE_GRATIS} — ML retira subsídio, "
                    "vendedor NÃO paga frete (free_shipping_efetivo=false)."
                )

    # Compara fixed_fee da API vs fallback que o app aplicaria
    lp_real_data = out.get("listing_prices_real", {}).get("raw")
    if lp_real_data is not None:
        match = None
        if isinstance(lp_real_data, dict):
            match = lp_real_data if lp_real_data.get("listing_type_id") else None
        elif isinstance(lp_real_data, list):
            flat = [
                i for sub in lp_real_data
                for i in (sub if isinstance(sub, list) else [sub])
            ]
            match = next(
                (i for i in flat if i.get("listing_type_id") == listing_type_id),
                None,
            )
        if match:
            sale_fee = match.get("sale_fee_details") or {}
            fixed_fee_api = sale_fee.get("fixed_fee") or 0
            if fixed_fee_api > 0:
                diagnosticos.append(
                    f"API retornou fixed_fee=R$ {fixed_fee_api:.2f} — "
                    "app USA esse valor (não cai no fallback)."
                )
            else:
                diagnosticos.append(
                    "API retornou fixed_fee=0 — custo fixo vem do frete de "
                    f"tabela limitado ao teto de R$ {TETO_CUSTO_FIXO}."
                )

    out["diagnosticos"] = diagnosticos
    return out
