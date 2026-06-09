"""Sonda os endpoints do ML que podem retornar a tarifa fixa real do item.

Compara:
- /sites/MLB/listing_prices?price=... (o que usamos hoje — pega fixed_fee)
- /items/{id}/listing_fees (alternativa por item)
- Outros sinais do /items/{id}
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

PROFILE_ID = UUID("722d19aa-983b-4a18-8cb9-70484520fcda")  # Namore
MLB = "MLB4592946051"  # SKU 2x5209-23-clone — tarifa fixa REAL = R$ 7,95


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async with MLClient(creds, tokens) as ml:
            # 1) Item: pega category_id, listing_type_id, price, logistic_type, etc.
            item = await ml.get(f"/items/{MLB}")
            cat = item.get("category_id")
            lst = item.get("listing_type_id")
            price = item.get("price")
            fs = item.get("shipping", {}).get("free_shipping", False)
            mode = item.get("shipping", {}).get("mode")
            logistic = item.get("shipping", {}).get("logistic_type")
            print(f"\n=== {MLB} ===")
            print(f"  preço: {price}, categoria: {cat}, modalidade: {lst}")
            print(f"  envio: free_shipping={fs}, mode={mode}, logistic_type={logistic}")

            # 2) /sites/MLB/listing_prices — o que o app usa hoje pra fees
            print("\n--- /sites/MLB/listing_prices ---")
            try:
                lp = await ml.get(
                    "/sites/MLB/listing_prices",
                    params={
                        "price": price,
                        "category_id": cat,
                        "listing_type_id": lst,
                        "currency_id": "BRL",
                        "logistic_type": logistic or "cross_docking",
                        "shipping_mode": mode or "me2",
                    },
                )
                if isinstance(lp, list):
                    lp_match = next(
                        (e for e in lp if isinstance(e, dict)
                         and e.get("listing_type_id") == lst), lp[0] if lp else None,
                    )
                else:
                    lp_match = lp
                print(json.dumps(lp_match, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"erro: {e}")

            # 3) /items/{id}/listing_fees (alternativa específica)
            print("\n--- /items/{id}/listing_fees ---")
            try:
                lf = await ml.get(f"/items/{MLB}/listing_fees")
                print(json.dumps(lf, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"NAO EXISTE ou erro: {e}")

            # 4) /users/{id}/items/{item_id}/listing_fees (variação)
            print("\n--- /users/{u}/items/{id}/listing_fees ---")
            try:
                uf = await ml.get(
                    f"/users/{profile.ml_user_id}/items/{MLB}/listing_fees",
                )
                print(json.dumps(uf, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"NAO EXISTE ou erro: {e}")

            # 5) /items/{id}/shipping_options
            print("\n--- /items/{id}/shipping_options ---")
            try:
                so = await ml.get(
                    f"/items/{MLB}/shipping_options",
                    params={"zip_code": "01310100"},
                )
                opts = (so or {}).get("options") if isinstance(so, dict) else None
                if opts:
                    for o in opts[:5]:
                        print(json.dumps({
                            "name": o.get("name"),
                            "display": o.get("display"),
                            "cost": o.get("cost"),
                            "list_cost": o.get("list_cost"),
                            "base_cost": o.get("base_cost"),
                            "discount": o.get("discount"),
                        }, indent=2, ensure_ascii=False))
                else:
                    print(json.dumps(so, indent=2, ensure_ascii=False)[:500])
            except Exception as e:
                print(f"erro: {e}")


if __name__ == "__main__":
    asyncio.run(main())
