"""Imprime a resposta crua do GET /seller-promotions/items/{id} pra ver
se min_discounted_price/max_discounted_price vem nessa rota."""
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

PROFILE_ID = UUID("7de27552-bd0e-4f5a-a03a-d5206177f325")
MLBS_PROBLEM = ["MLB4690688283", "MLB4694655741", "MLB6797100086", "MLB4671291099"]


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None

        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async with MLClient(creds, tokens) as ml:
            for mlb in MLBS_PROBLEM:
                # Preço atual
                item = await ml.get(
                    f"/items/{mlb}",
                    params={"attributes": "id,price,available_quantity"},
                )
                preco_atual = item.get("price") if isinstance(item, dict) else None

                # Promoções vinculadas
                resp = await ml.get(
                    f"/seller-promotions/items/{mlb}",
                    params={"app_version": "v2"},
                )

                # Acha entrada da nossa campanha
                entradas = resp if isinstance(resp, list) else (
                    resp.get("results") or [resp]
                )
                ent = next(
                    (e for e in entradas if isinstance(e, dict)
                     and e.get("id") == "C-MLB4281804"),
                    None,
                )

                print(f"\n=== {mlb} ===")
                print(f"  preço atual no ML: R$ {preco_atual}")
                if ent:
                    print(f"  C-MLB4281804: {json.dumps(ent, indent=4, ensure_ascii=False)}")
                else:
                    print("  C-MLB4281804: NÃO ENCONTRADO entre as entradas")
                    print(f"  (entradas presentes: {[e.get('id') if isinstance(e, dict) else '?' for e in entradas]})")


if __name__ == "__main__":
    asyncio.run(main())
