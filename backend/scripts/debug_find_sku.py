"""Procura o item correto pelo SKU."""
from __future__ import annotations
import asyncio
from uuid import UUID

from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.items_fetcher import _extrair_sku
from liraz_tools.infrastructure.ml.promotions_lookup import (
    _listar_todos_items_ativos_do_vendedor,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

PROFILE_ID = UUID("722d19aa-983b-4a18-8cb9-70484520fcda")
SKU_PROCURADO = "2x5209-23-clone"

ATRIBUTOS = "id,seller_custom_field,attributes,variations,price,title,listing_type_id,shipping"


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async with MLClient(creds, tokens) as ml:
            print("Listando itens ativos da Namore...")
            ativos = await _listar_todos_items_ativos_do_vendedor(ml, profile.ml_user_id)
            print(f"  {len(ativos)} ativos")

            # Multiget em batches de 20
            achados = []
            for i in range(0, len(ativos), 20):
                batch = ativos[i:i + 20]
                resp = await ml.get("/items", params={
                    "ids": ",".join(batch), "attributes": ATRIBUTOS,
                })
                if not isinstance(resp, list):
                    continue
                for e in resp:
                    if e.get("code") != 200:
                        continue
                    body = e.get("body") or {}
                    sku = _extrair_sku(body)
                    if sku and SKU_PROCURADO in sku:
                        achados.append(body)

            print(f"\nItens com '{SKU_PROCURADO}' no SKU: {len(achados)}")
            for body in achados:
                print(f"\n  MLB: {body.get('id')}")
                print(f"  SKU: {_extrair_sku(body)}")
                print(f"  Título: {body.get('title')}")
                print(f"  Preço: R$ {body.get('price')}")
                print(f"  Modalidade: {body.get('listing_type_id')}")
                shipping = body.get('shipping') or {}
                print(f"  Free shipping: {shipping.get('free_shipping')}")
                print(f"  Logistic: {shipping.get('logistic_type')}, mode: {shipping.get('mode')}")


if __name__ == "__main__":
    asyncio.run(main())
