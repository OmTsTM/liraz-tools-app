"""Testa /sites/MLB/listing_prices com billable_weight pra ver se retorna fixed_fee correto.

Hipótese (descoberta na doc do ML): a tarifa fixa depende de billable_weight +
logistic_type, não só de listing_type_id. Vamos validar com 2 itens reais.
"""
from __future__ import annotations
import asyncio
import json
from uuid import UUID

from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

PROFILE_ID = UUID("722d19aa-983b-4a18-8cb9-70484520fcda")

# (MLB, tarifa_fixa_REAL_painel)
ITENS = [
    ("MLB6731404730", 6.75),  # CB3381-3 — Ampulheta
    ("MLB4592946051", 7.95),  # 2x5209-23-clone — Tapete
]


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async with MLClient(creds, tokens) as ml:
            for mlb, tarifa_real in ITENS:
                print(f"\n{'=' * 80}")
                print(f"=== {mlb}  | tarifa REAL = R$ {tarifa_real}")
                print("=" * 80)

                # 1) Pega dimensions e listing_type_id do item
                item = await ml.get(f"/items/{mlb}")
                cat = item.get("category_id")
                lst = item.get("listing_type_id")
                price = item.get("price")
                shipping = item.get("shipping") or {}
                dims = shipping.get("dimensions")  # "AxLxC,P" ou null
                free = shipping.get("free_shipping")
                logistic = shipping.get("logistic_type")
                mode = shipping.get("mode")
                print(f"  preço={price}  categoria={cat}  modalidade={lst}")
                print(f"  dimensions: {dims}")
                print(f"  free_shipping={free}  logistic={logistic}  mode={mode}")

                # 2) Pega billable_weight de shipping_options
                so = await ml.get(
                    f"/items/{mlb}/shipping_options",
                    params={"zip_code": "01310100"},
                )
                opts = (so or {}).get("options") if isinstance(so, dict) else None
                billable = None
                if opts:
                    for o in opts:
                        bw = o.get("billable_weight") or o.get("weight")
                        if bw:
                            billable = bw
                            break
                print(f"  billable_weight (shipping_options): {billable}")

                # 3) Tenta variantes de listing_prices
                base_params = {
                    "price": price, "category_id": cat,
                    "listing_type_id": lst, "currency_id": "BRL",
                }

                variantes = [
                    ("default (sem peso)", base_params),
                    ("+ logistic+mode", {
                        **base_params,
                        "logistic_type": logistic or "drop_off",
                        "shipping_mode": mode or "me2",
                    }),
                    ("+ billable_weight=500", {
                        **base_params,
                        "logistic_type": logistic or "drop_off",
                        "shipping_mode": mode or "me2",
                        "billable_weight": 500,
                    }),
                    ("+ billable_weight=1000", {
                        **base_params,
                        "logistic_type": logistic or "drop_off",
                        "shipping_mode": mode or "me2",
                        "billable_weight": 1000,
                    }),
                ]
                if billable:
                    variantes.append((f"+ billable_weight={billable} (real)", {
                        **base_params,
                        "logistic_type": logistic or "drop_off",
                        "shipping_mode": mode or "me2",
                        "billable_weight": billable,
                    }))

                for label, params in variantes:
                    print(f"\n  > {label}")
                    try:
                        resp = await ml.get(
                            "/sites/MLB/listing_prices", params=params,
                        )
                        if isinstance(resp, list):
                            resp = next(
                                (e for e in resp if isinstance(e, dict)
                                 and e.get("listing_type_id") == lst), resp[0],
                            )
                        sf = (resp or {}).get("sale_fee_details") or {}
                        lf = (resp or {}).get("listing_fee_details") or {}
                        print(
                            f"    sale_fee_amount={resp.get('sale_fee_amount')}  "
                            f"sale_fee_details.fixed_fee={sf.get('fixed_fee')}  "
                            f"listing_fee.fixed_fee={lf.get('fixed_fee')}"
                        )
                    except Exception as e:
                        print(f"    X {str(e)[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
