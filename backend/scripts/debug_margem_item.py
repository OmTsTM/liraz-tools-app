"""Mostra a quebra de margem em vários preços pra UM item específico,
ajudando a diagnosticar o que o app está fazendo."""
from __future__ import annotations

import asyncio
from uuid import UUID

from liraz_tools.domain.skus.sugestao_use_case import (
    SugerirDealPricesPorMargemUseCase,
)
from liraz_tools.infrastructure.db.database import session_scope
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
MLB = "MLB6731404730"  # CB3381-3, Ampulheta


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        creds_repo = PerProfileCredentialsRepository()
        overrides_repo = CostOverridesRepository()

        uc = SugerirDealPricesPorMargemUseCase(profile_repo, creds_repo, overrides_repo)
        sugs = await uc.execute(
            PROFILE_ID, ml_campaign_id=None,
            item_ids=[MLB], margem_alvo=0.20,
        )

    if not sugs:
        print("Sem sugestão")
        return
    s = sugs[0]
    print(f"\n=== {MLB} ({s.sku}) ===")
    print(f"  preço atual no ML: R$ {s.preco_atual}")
    print(f"  custo unitário:    R$ {s.custo_unit}")
    print(f"  list_cost (frete): R$ {s.list_cost}")
    print(f"  comissão (G):      {(s.comissao_pct or 0) * 100:.2f}%")
    print(f"  alíquota (K2):     {(s.aliquota or 0) * 100:.2f}%")
    print(f"  precisa inflar?    {s.precisa_inflacao}")
    print(f"  P (deal_price):    R$ {s.deal_price}")
    print(f"  U (preço inflado): R$ {s.preco_inflado}")
    print(f"  desconto %:        {s.desconto_pct}%")
    print(f"  margem ATUAL %:    {s.margem_atual_pct}% (no preço {s.preco_atual})")
    print(f"  margem REAL %:     {s.margem_real_pct}% (no deal_price {s.deal_price})")

    # Margens em cada preço-chave
    if (
        s.custo_unit is not None and s.comissao_pct is not None
        and s.aliquota is not None
    ):
        precos = [s.preco_atual, s.deal_price, s.preco_inflado]
        nomes = ["preço atual", "deal_price (P)", "inflado (U)"]
        print("\n  Margem em cada preço (modelo passo3 com G escalando):")
        for nome, p in zip(nomes, precos, strict=False):
            if p is None or p <= 0:
                continue
            m = margem_liquida_pct(
                p, custo=s.custo_unit, tarifa_pct=s.comissao_pct,
                list_cost=s.list_cost, aliquota=s.aliquota,
            )
            print(f"    {nome:<20} R$ {p:>7.2f} -> margem {m * 100:>6.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
