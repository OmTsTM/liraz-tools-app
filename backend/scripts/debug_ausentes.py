"""Investiga por que tantos itens viram `ausente` no clamp.

Pra uma amostra de items que TENTAMOS adicionar mas o ML não devolveu como
candidate (status=ausente), consulta `GET /seller-promotions/items/{id}?app_version=v2`
pra ver:
- Em quais promoções esse item está AGORA (qualquer status)
- O motivo da inelegibilidade pra C-MLB4281804 (se houver razão estruturada)

Usa a lista atual de SKUs da campanha LiraZ Maio Final v2 (origem ML).
"""
from __future__ import annotations

import asyncio
import random
from uuid import UUID

from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotions_lookup import (
    buscar_limites_credibilidade,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

PROFILE_ID = UUID("7de27552-bd0e-4f5a-a03a-d5206177f325")
CAMPAIGN_ID = UUID("0cf27d81-f5b2-4ba4-99d3-e5a77cbca308")
ML_PROMO_ID = "C-MLB4281804"
AMOSTRA = 8  # quantos ausentes investigar a fundo


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None

        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        camp_repo = CampaignRepository(session)
        camp = await camp_repo.get_by_id(PROFILE_ID, CAMPAIGN_ID)
        skus_no_campaign_local = set(camp.skus_selecionados or [])

        # Pega TODOS os anúncios ativos do vendedor pra cruzar.
        async with MLClient(creds, tokens) as ml:
            items_ativos: list[str] = []
            offset = 0
            while True:
                resp = await ml.get(
                    f"/users/{profile.ml_user_id}/items/search",
                    params={"status": "active", "limit": 100, "offset": offset},
                )
                results = resp.get("results") or []
                if not results:
                    break
                items_ativos.extend(str(x) for x in results if x)
                paging = resp.get("paging") or {}
                total = paging.get("total", 0)
                offset += 100
                if offset >= total:
                    break

            # Limites pra todos
            print(f"\nVendedor tem {len(items_ativos)} anúncios ativos.")
            print(f"Já na campanha local: {len(skus_no_campaign_local)}")

            limites = await buscar_limites_credibilidade(
                ml, promotion_id=ML_PROMO_ID,
                promotion_type="SELLER_CAMPAIGN", item_ids=items_ativos,
            )
            print(f"ML retorna {len(limites)} como candidate/started da promo.")

            # Ausentes do clamp = ativos & não-na-camp-local & não-em-limites
            nao_na_camp = set(items_ativos) - skus_no_campaign_local
            ausentes = nao_na_camp - set(limites.keys())
            print(f"\nAtivos NAO na campanha: {len(nao_na_camp)}")
            print(f"Desses, AUSENTES (ML nao devolve como candidate): {len(ausentes)}")

            # Amostra de N ausentes pra investigar (random pra evitar viés)
            sample = random.sample(sorted(ausentes), min(AMOSTRA, len(ausentes)))
            print(f"\n=== Investigando amostra de {len(sample)} ausentes ===")
            for mlb in sample:
                try:
                    info = await ml.get(
                        f"/seller-promotions/items/{mlb}",
                        params={"app_version": "v2"},
                    )
                except Exception as e:
                    print(f"\n{mlb}: ERRO ao consultar — {e}")
                    continue

                # Resposta pode vir como list direto OU dict com "results"
                if isinstance(info, list):
                    results = info
                elif isinstance(info, dict):
                    results = info.get("results") or [info]
                else:
                    results = []
                if not results:
                    print(f"\n{mlb}: NENHUMA promoção (resposta vazia)")
                    continue

                print(f"\n{mlb}: aparece em {len(results)} promoção(ões):")
                for p in results:
                    if not isinstance(p, dict):
                        continue
                    pid = p.get("id") or p.get("promotion_id") or "?"
                    ptype = p.get("type") or p.get("promotion_type") or "?"
                    status = p.get("status") or "?"
                    sub_type = p.get("sub_type") or p.get("subtype") or "?"
                    print(f"  - {pid} ({ptype}/{sub_type}) status={status}")


if __name__ == "__main__":
    asyncio.run(main())
