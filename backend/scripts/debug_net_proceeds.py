"""Testa /marketplace/items pra ver se devolve net_proceeds com breakdown REAL de fees."""
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
ITENS = ["MLB6731404730", "MLB4592946051"]


async def _try(ml, label, path, params=None):
    full = f"GET {path}"
    if params:
        full += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    print(f"\n--- {label} ---\n{full}")
    try:
        resp = await ml.get(path, params=params or {})
        txt = json.dumps(resp, indent=2, ensure_ascii=False, default=str)
        if len(txt) > 2000:
            txt = txt[:2000] + "\n  ... (truncado)"
        print(txt)
    except Exception as e:
        msg = str(e)[:250]
        print(f"  X {msg}")


async def main():
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)
        user_id = profile.ml_user_id

        async with MLClient(creds, tokens) as ml:
            for mlb in ITENS:
                print(f"\n{'=' * 80}\n=== {mlb}\n{'=' * 80}")
                await _try(ml, "1) /marketplace/items/{id}",
                    f"/marketplace/items/{mlb}")
                await _try(ml, "2) /marketplace/items?ids=",
                    "/marketplace/items", {"ids": mlb})
                await _try(ml, "3) /items/{id} com include=net_proceeds",
                    f"/items/{mlb}", {"include": "net_proceeds"})
                await _try(ml, "4) /items/{id}/net_proceeds",
                    f"/items/{mlb}/net_proceeds")
                await _try(ml, "5) /marketplace/items/{id}/prices",
                    f"/marketplace/items/{mlb}/prices")
                await _try(ml, "6) /users/{u}/marketplace/items",
                    f"/users/{user_id}/marketplace/items", {"ids": mlb})

asyncio.run(main())
