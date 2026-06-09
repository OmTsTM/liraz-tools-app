"""Verifica se /orders/search e /orders/{id} retornam fee_details com tarifa fixa."""
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


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async with MLClient(creds, tokens) as ml:
            # 1) Procura ordens de items BARATOS (<R$ 79) pra ver tarifa fixa
            print("=== Busca: 30 últimas ordens, filtrar baratos (<R$79) ===\n")
            search = await ml.get(
                "/orders/search",
                params={"seller": profile.ml_user_id, "limit": 30, "sort": "date_desc"},
            )
            results = search.get("results") or []
            baratos = []
            for o in results:
                items = o.get("order_items") or []
                if items and float(items[0].get("unit_price") or 0) < 79:
                    baratos.append(o)
            print(f"Encontradas {len(baratos)} ordens de items baratos das ultimas 30.\n")

            # Pega 1 ordem barata e mostra TUDO + chama /orders/{id}/billing_info
            for o in baratos[:2]:
                oid = o.get("id")
                item = (o.get("order_items") or [{}])[0]
                preco = float(item.get("unit_price") or 0)
                comissao = float(item.get("sale_fee") or 0)
                total = float(o.get("total_amount") or 0)
                paid = float(o.get("paid_amount") or 0)
                lst = item.get("listing_type_id")
                print(f"--- Ordem {oid} ---")
                print(f"  item: {item.get('item', {}).get('id')} "
                      f"({item.get('item', {}).get('title', '')[:50]}...)")
                print(f"  listing_type_id: {lst}")
                print(f"  unit_price: R$ {preco}")
                print(f"  sale_fee: R$ {comissao}  (= {comissao/preco*100:.2f}% do preço)")
                print(f"  total_amount: R$ {total}")
                print(f"  paid_amount: R$ {paid}")
                if comissao > 0:
                    # Suposição: 11% pra gold_special clássico
                    pct_estimada = 11.0 if lst == "gold_special" else 16.5
                    comissao_pura = preco * pct_estimada / 100
                    tarifa_inferida = comissao - comissao_pura
                    print(f"  Se comissao_% = {pct_estimada}%: "
                          f"comissao pura = R$ {comissao_pura:.2f}, "
                          f"diferenca = R$ {tarifa_inferida:.2f}  "
                          f"<- TARIFA FIXA inferida")

                # /billing_info pode trazer fees detalhados
                print(f"\n  GET /orders/{oid}/billing_info:")
                try:
                    bi = await ml.get(f"/orders/{oid}/billing_info")
                    txt = json.dumps(bi, indent=2, ensure_ascii=False, default=str)
                    if len(txt) > 1200:
                        txt = txt[:1200] + "\n  ... (truncado)"
                    print(f"  {txt}")
                except Exception as e:
                    print(f"  X erro: {str(e)[:200]}")

                # /shipments/{id} (pra ver custo de envio)
                ship_id = (o.get("shipping") or {}).get("id")
                if ship_id:
                    print(f"\n  GET /shipments/{ship_id}/costs:")
                    try:
                        sc = await ml.get(f"/shipments/{ship_id}/costs")
                        txt = json.dumps(sc, indent=2, ensure_ascii=False, default=str)
                        if len(txt) > 1200:
                            txt = txt[:1200] + "\n  ... (truncado)"
                        print(f"  {txt}")
                    except Exception as e:
                        print(f"  X erro: {str(e)[:200]}")
                print()


if __name__ == "__main__":
    asyncio.run(main())
