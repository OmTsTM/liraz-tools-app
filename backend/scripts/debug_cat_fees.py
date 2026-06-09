"""Sonda endpoints categoria-específicos pra ver se algum retorna a tarifa fixa."""
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
# 2 itens com tarifa fixa REAL distinta — categorias diferentes
ITENS = [
    ("MLB6731404730", "MLB439453", 31.79, "Clássico", 6.75, 11.0),
    ("MLB4592946051", "MLB186136", 42.35, "Clássico", 7.95, 11.5),
]


async def _try(ml: MLClient, label: str, path: str, params: dict | None = None) -> None:
    full = f"GET {path}"
    if params:
        full += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    print(f"\n--- {label} ---\n{full}")
    try:
        resp = await ml.get(path, params=params or {})
        txt = json.dumps(resp, indent=2, ensure_ascii=False, default=str)
        if len(txt) > 1500:
            txt = txt[:1500] + "\n  ... (truncado)"
        print(txt)
    except Exception as e:
        msg = str(e)[:200]
        if "404" in msg: print(f"  X 404")
        elif "403" in msg: print(f"  X 403")
        else: print(f"  X {msg}")


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async with MLClient(creds, tokens) as ml:
            for mlb, cat, preco, mod, tarifa_real, com in ITENS:
                print("\n" + "=" * 80)
                print(f"=== {mlb} | cat={cat} | preço=R${preco} | mod={mod}")
                print(f"=== TARIFA FIXA REAL = R$ {tarifa_real} | comissão = {com}%")
                print("=" * 80)

                # Endpoints categoria-específicos
                await _try(ml, "categories/{id}", f"/categories/{cat}")
                await _try(ml, "categories/{id}/attributes",
                    f"/categories/{cat}/attributes")
                await _try(ml, "categories/{id}/listing_prices",
                    f"/categories/{cat}/listing_prices",
                    {"price": preco, "listing_type_id": "gold_special"})
                await _try(ml, "categories/{id}/sale_fee",
                    f"/categories/{cat}/sale_fee",
                    {"price": preco, "listing_type_id": "gold_special"})
                await _try(ml, "categories/{id}/fees",
                    f"/categories/{cat}/fees",
                    {"price": preco, "listing_type_id": "gold_special"})
                # Sites com filtro de category
                await _try(ml, "sites/MLB/sale_fee",
                    "/sites/MLB/sale_fee",
                    {"price": preco, "category_id": cat,
                     "listing_type_id": "gold_special"})
                await _try(ml, "sites/MLB/categories/{id}/listing_prices",
                    f"/sites/MLB/categories/{cat}/listing_prices",
                    {"price": preco, "listing_type_id": "gold_special"})


if __name__ == "__main__":
    asyncio.run(main())
