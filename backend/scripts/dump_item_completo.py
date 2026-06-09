"""Dump COMPLETO de um item — ML API + custos + promoções + sugestão passo3.

Uso:
  uv run --project backend python backend/scripts/dump_item_completo.py

Gera arquivo .txt no root do projeto com TODAS as informações que conseguimos
extrair sobre o item.
"""
from __future__ import annotations

import asyncio
import io
import json
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from liraz_tools.domain.skus.sugestao_use_case import (
    SugerirDealPricesPorMargemUseCase,
)
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.pricing.calculator import calcular_taxas_anuncio
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.repricing_calculator import (
    margem_liquida_pct,
)
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

PROFILE_ID = UUID("722d19aa-983b-4a18-8cb9-70484520fcda")  # Namore
MLB = "MLB6731404730"
OUT_PATH = Path(__file__).resolve().parents[2] / f"{MLB}_dump.txt"


def _fmt_json(data: object) -> str:
    """JSON indentado, ASCII-safe (Windows console é cp1252)."""
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


async def _try(ml: MLClient, label: str, path: str, params: dict | None = None) -> object:
    """GET com try/except — retorna data ou {'__error__': str}."""
    try:
        return await ml.get(path, params=params or {})
    except Exception as e:
        return {"__error__": str(e)[:500]}


async def main() -> None:  # noqa: PLR0915  (script de uma vez só)
    out = io.StringIO()
    p = lambda *a: print(*a, file=out)  # noqa: E731

    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None

        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)
        overrides_repo = CostOverridesRepository()

        from datetime import datetime
        p("=" * 80)
        p(f"DUMP COMPLETO — {MLB}")
        p(f"Loja: {profile.name} (ml_user_id={profile.ml_user_id})")
        p(f"Gerado em: {datetime.now().isoformat(timespec='seconds')}")
        p("=" * 80)

        async with MLClient(creds, tokens) as ml:
            # ── [1] Item completo (sem filtro de attributes) ────────────
            p("\n\n" + "─" * 80)
            p("[1] /items/{id}  — registro completo do anúncio")
            p("─" * 80)
            item = await _try(ml, "items", f"/items/{MLB}")
            p(_fmt_json(item))

            # ── [2] Descrição ───────────────────────────────────────────
            p("\n\n" + "─" * 80)
            p("[2] /items/{id}/description  — texto descritivo")
            p("─" * 80)
            desc = await _try(ml, "description", f"/items/{MLB}/description")
            p(_fmt_json(desc))

            # ── [3] Reviews ─────────────────────────────────────────────
            p("\n\n" + "─" * 80)
            p("[3] /reviews/item/{id}  — avaliações dos compradores")
            p("─" * 80)
            reviews = await _try(ml, "reviews", f"/reviews/item/{MLB}")
            # Trunca paging.reviews se for muito grande
            if isinstance(reviews, dict) and isinstance(reviews.get("reviews"), list):
                total = len(reviews["reviews"])
                if total > 10:
                    reviews["reviews"] = reviews["reviews"][:10]
                    reviews["__nota__"] = f"truncado pra 10 de {total} reviews"
            p(_fmt_json(reviews))

            # ── [4] Visitas ─────────────────────────────────────────────
            p("\n\n" + "─" * 80)
            p("[4] /items/{id}/visits  — métricas de visualização")
            p("─" * 80)
            visits = await _try(ml, "visits", f"/items/{MLB}/visits")
            p(_fmt_json(visits))

            visits_time = await _try(
                ml, "visits_time", f"/items/{MLB}/visits/time_window",
                params={"last": 30, "unit": "day"},
            )
            p("\n  /items/{id}/visits/time_window?last=30&unit=day:")
            p(_fmt_json(visits_time))

            # ── [5] Categoria do anúncio ────────────────────────────────
            p("\n\n" + "─" * 80)
            p("[5] /categories/{category_id}  — categoria do anúncio")
            p("─" * 80)
            cat_id = None
            if isinstance(item, dict):
                cat_id = item.get("category_id")
            if cat_id:
                cat = await _try(ml, "category", f"/categories/{cat_id}")
                p(_fmt_json(cat))
            else:
                p("(sem category_id)")

            # ── [6] Listing prices (fees teóricos) ──────────────────────
            p("\n\n" + "─" * 80)
            p("[6] /sites/MLB/listing_prices  — fees teóricos por modalidade")
            p("─" * 80)
            if isinstance(item, dict):
                lp_params = {
                    "price": item.get("price"),
                    "category_id": cat_id,
                    "listing_type_id": item.get("listing_type_id"),
                    "currency_id": "BRL",
                    "logistic_type": (
                        (item.get("shipping") or {}).get("logistic_type") or "cross_docking"
                    ),
                    "shipping_mode": (item.get("shipping") or {}).get("mode") or "me2",
                }
                lp = await _try(ml, "lp", "/sites/MLB/listing_prices", lp_params)
                p(_fmt_json(lp))

            # ── [7] Shipping options (frete) ────────────────────────────
            p("\n\n" + "─" * 80)
            p("[7] /items/{id}/shipping_options  — opções de frete")
            p("─" * 80)
            cep = profile.config.cep_destino or "01310100"
            so = await _try(
                ml, "shipping", f"/items/{MLB}/shipping_options",
                params={"zip_code": cep},
            )
            p(_fmt_json(so))

            # ── [8] Promoções vinculadas ────────────────────────────────
            p("\n\n" + "─" * 80)
            p(
                "[8] /seller-promotions/items/{id}  — promoções vinculadas "
                "(min/max/suggested por promo)"
            )
            p("─" * 80)
            promos = await _try(
                ml, "promos", f"/seller-promotions/items/{MLB}",
                params={"app_version": "v2"},
            )
            p(_fmt_json(promos))

            # ── [9] User do vendedor ────────────────────────────────────
            p("\n\n" + "─" * 80)
            p("[9] /users/{seller_id}  — info do vendedor (limitada)")
            p("─" * 80)
            seller = await _try(ml, "user", f"/users/{profile.ml_user_id}")
            # Truncar campos longos
            if isinstance(seller, dict):
                for k in ("seller_reputation",):
                    if k in seller:
                        seller[k] = "<truncado>"
            p(_fmt_json(seller))

            # ── [10] Taxas calculadas pelo app (passo3) ─────────────────
            p("\n\n" + "─" * 80)
            p("[10] calcular_taxas_anuncio  — saída do calculador interno")
            p("─" * 80)
            freight_cache = FreightCache(profile.slug)
            taxas = await calcular_taxas_anuncio(
                ml=ml, item_id=MLB, cep_destino=cep,
                freight_cache=freight_cache,
            )
            p(_fmt_json(taxas))

        # ── [11] Override de custo (xlsx do perfil) ─────────────────────
        p("\n\n" + "─" * 80)
        p("[11] CostOverridesRepository  — custo cadastrado no xlsx do perfil")
        p("─" * 80)
        ov = None
        with suppress(Exception):
            ov = overrides_repo.get(profile.id, MLB)
        p(f"override: {ov}")

        # ── [12] Sugestão passo3 ────────────────────────────────────────
        p("\n\n" + "─" * 80)
        p("[12] SugerirDealPricesPorMargemUseCase  — sugestão passo3 completa")
        p("─" * 80)
        uc = SugerirDealPricesPorMargemUseCase(profile_repo, creds_repo, overrides_repo)
        sugs = await uc.execute(
            PROFILE_ID, ml_campaign_id=None, item_ids=[MLB],
            margem_alvo=profile.config.margem_alvo_campanha,
        )
        if sugs:
            s = sugs[0]
            sug_dict = {
                "item_id": s.item_id, "sku": s.sku,
                "preco_atual": s.preco_atual,
                "margem_atual_pct": s.margem_atual_pct,
                "precisa_inflacao": s.precisa_inflacao,
                "preco_inflado_U": s.preco_inflado,
                "deal_price_P": s.deal_price,
                "desconto_pct": s.desconto_pct,
                "margem_real_pct_no_P": s.margem_real_pct,
                "min_aplicado": s.min_aplicado,
                "aviso_degrau": s.aviso_degrau,
                "erro": s.erro,
                "quebra_frete_gratis_aplicada": s.quebra_frete_gratis_aplicada,
                "fallback_deal_price": s.fallback_deal_price,
                "frete_a_confirmar": s.frete_a_confirmar,
                "custo_unit": s.custo_unit,
                "list_cost": s.list_cost,
                "comissao_pct": s.comissao_pct,
                "aliquota": s.aliquota,
            }
            p(_fmt_json(sug_dict))

            # ── [13] Margem em vários preços (sensibility) ───────────────
            p("\n\n" + "─" * 80)
            p("[13] Margem em vários preços (sensibility analysis)")
            p("─" * 80)
            if (
                s.custo_unit is not None and s.comissao_pct is not None
                and s.aliquota is not None
            ):
                precos = sorted(set([
                    s.preco_atual or 0, s.deal_price or 0, s.preco_inflado or 0,
                    20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 79.0, 80.0, 100.0,
                ]))
                p(f"  custo: R$ {s.custo_unit:.2f}")
                p(f"  list_cost (frete tabela): R$ {s.list_cost}")
                p(f"  comissão (G): {s.comissao_pct * 100:.2f}%")
                p(f"  alíquota (K2): {s.aliquota * 100:.2f}%\n")
                p(f"  {'preço':>8}  {'margem':>8}  obs")
                p(f"  {'-' * 8}  {'-' * 8}  ---")
                for x in precos:
                    if x <= 0:
                        continue
                    m = margem_liquida_pct(
                        x, custo=s.custo_unit, tarifa_pct=s.comissao_pct,
                        list_cost=s.list_cost, aliquota=s.aliquota,
                    )
                    obs = []
                    if x == s.preco_atual:
                        obs.append("preço ATUAL no ML")
                    if x == s.deal_price:
                        obs.append("deal_price (P) sugerido")
                    if x == s.preco_inflado:
                        obs.append("preço INFLADO (U)")
                    if x == 79.0:
                        obs.append("degrau ML (≥79 = frete obrigatório)")
                    obs_str = " / ".join(obs)
                    p(f"  R${x:>6.2f}  {m * 100:>6.2f}%  {obs_str}")

    OUT_PATH.write_text(out.getvalue(), encoding="utf-8")
    print(f"\nDump escrito em: {OUT_PATH}")
    print(f"Tamanho: {OUT_PATH.stat().st_size} bytes")


if __name__ == "__main__":
    asyncio.run(main())
