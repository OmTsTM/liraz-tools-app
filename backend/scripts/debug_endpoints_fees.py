"""Sonda exploratória: tenta vários endpoints (públicos + internos) do ML
pra ver se algum devolve a TARIFA FIXA REAL e/ou o "Você recebe" do item.

Cada endpoint imprime: status, primeiros campos do response, ou erro 404/403.
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
MLB = "MLB6731404730"


async def _try(ml: MLClient, label: str, path: str, params: dict | None = None) -> None:
    """Imprime resumo do endpoint."""
    print(f"\n--- {label} ---")
    print(f"GET {path}{('?' + '&'.join(f'{k}={v}' for k, v in params.items())) if params else ''}")
    try:
        resp = await ml.get(path, params=params or {})
    except Exception as e:
        msg = str(e)[:200]
        # Detecta status nos primeiros chars
        if "404" in msg:
            print("  X 404 (nao existe)")
        elif "403" in msg:
            print("  X 403 (sem permissao)")
        elif "400" in msg:
            print(f"  X 400 (bad request): {msg}")
        else:
            print(f"  X ERRO: {msg}")
        return
    # Sumariza resposta
    out = json.dumps(resp, indent=2, ensure_ascii=False, default=str)
    if len(out) > 1500:
        out = out[:1500] + "\n  ... (truncado)"
    print(out)


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)
        user_id = profile.ml_user_id

        async with MLClient(creds, tokens) as ml:
            # 1) Variantes de listing_prices
            await _try(ml, "1a) listing_prices DEFAULT", "/sites/MLB/listing_prices", {
                "price": 31.79, "category_id": "MLB439453",
                "listing_type_id": "gold_special",
            })
            await _try(ml, "1b) listing_prices com use_listing_price=true",
                "/sites/MLB/listing_prices", {
                "price": 31.79, "category_id": "MLB439453",
                "listing_type_id": "gold_special",
                "use_listing_price": "true",
            })
            await _try(ml, "1c) listing_prices com fee_type=all",
                "/sites/MLB/listing_prices", {
                "price": 31.79, "category_id": "MLB439453",
                "listing_type_id": "gold_special",
                "fee_type": "all",
            })

            # 2) Endpoint por listing_type (sem categoria)
            await _try(ml, "2) /sites/MLB/listing_types/gold_special",
                "/sites/MLB/listing_types/gold_special")

            # 3) /items/{id}/* (variações)
            await _try(ml, "3a) items/{id}/sale_terms", f"/items/{MLB}/sale_terms")
            await _try(ml, "3b) items/{id}/financial_summary",
                f"/items/{MLB}/financial_summary")
            await _try(ml, "3c) items/{id}/sale_price", f"/items/{MLB}/sale_price")
            await _try(ml, "3d) items/{id}/fees", f"/items/{MLB}/fees")
            await _try(ml, "3e) items/{id}/seller_offers", f"/items/{MLB}/seller_offers")
            await _try(ml, "3f) items/{id}/marketplace", f"/items/{MLB}/marketplace")

            # 4) Endpoints do user
            await _try(ml, "4a) users/{u}/items/{id}/fees",
                f"/users/{user_id}/items/{MLB}/fees")
            await _try(ml, "4b) marketplace/sites/MLB/listing_prices",
                "/marketplace/sites/MLB/listing_prices", {
                "price": 31.79, "category_id": "MLB439453",
                "listing_type_id": "gold_special",
            })

            # 5) Caminho via ordem (histórico): pega uma ordem desse item pra ver fee_details
            await _try(ml, "5) orders/search por item (pega 1)",
                "/orders/search", {
                "seller": user_id, "item": MLB, "limit": 1,
                "sort": "date_desc",
            })

            # 6) /items_costs (não documentado mas vale tentar)
            await _try(ml, "6) sites/MLB/listing_prices/{id}",
                "/sites/MLB/listing_prices/gold_special")


if __name__ == "__main__":
    asyncio.run(main())
