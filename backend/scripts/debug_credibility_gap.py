"""Probe: mostra o GAP entre o deal_P do passo3 e a faixa aceita pelo ML
(min/max_discounted_price) pra itens que foram DESCARTADOS por
ERROR_CREDIBILITY_DISCOUNTED_PRICE.

Uso:
  uv run --project backend python backend/scripts/debug_credibility_gap.py

Imprime tabela:
  MLB | preco_atual | nosso_deal_P | ML_min | ML_max | ML_sugerido | diagnostico
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from liraz_tools.domain.skus.sugestao_use_case import (
    SugerirDealPricesPorMargemUseCase,
)
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotions_lookup import (
    buscar_limites_credibilidade,
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

# Loja LiraZ + campanha Maio Final v2 (a ativa onde os SKUs falharam).
PROFILE_ID = UUID("7de27552-bd0e-4f5a-a03a-d5206177f325")
ML_PROMO_ID = "C-MLB4281804"

# Sample de MLBs que apareceram como `campaign_descarte_reprecifica_20pct`
# no log (operacao das 22:21 de hoje). Mix de itens baratos e caros.
MLBS_DESCARTADOS = [
    "MLB6840887090",  # preco_20pct=20.76
    "MLB6832643894",  # preco_20pct=458.66 (caro)
    "MLB6832631660",  # preco_20pct=458.66 (caro)
    "MLB4706250101",  # preco_20pct=436.47
    "MLB4706148597",  # preco_20pct=458.66
    "MLB4690669565",  # preco_20pct=24.74
    "MLB4690688283",  # preco_20pct=18.0
    "MLB6796344422",  # preco_20pct=21.24
]


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None

        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        # 1) Computa nosso deal_P passo3 pra cada item
        sugestao_uc = SugerirDealPricesPorMargemUseCase(
            profile_repo, creds_repo, CostOverridesRepository(),
        )
        sugestoes = await sugestao_uc.execute(
            PROFILE_ID, ml_campaign_id=ML_PROMO_ID,
            item_ids=MLBS_DESCARTADOS, margem_alvo=0.20,
        )
        nosso_deal = {s.item_id: s for s in sugestoes}

        # 2) Lê limites de credibilidade pelo helper NOVO (per-item, paralelo).
        async with MLClient(creds, tokens) as ml:
            info_ml = await buscar_limites_credibilidade(
                ml, promotion_id=ML_PROMO_ID,
                promotion_type="SELLER_CAMPAIGN",
                item_ids=MLBS_DESCARTADOS,
            )

    # Importa pra simular o clamp aplicado pelo pipeline novo
    from liraz_tools.infrastructure.pricing.repricing_calculator import (
        margem_liquida_pct,
    )

    PISO_CLAMP = 0.165

    # 3) Tabela comparativa com clamp simulado (estratégia atual: min(max-0.01, sug))
    print(
        f"\n{'MLB':<16} {'preco_now':>9} {'nosso_dP':>9} "
        f"{'ML_max':>9} {'ML_sug':>9} {'teto_ef':>9} {'envia':>9} {'margem':>7}  decisao"
    )
    print("-" * 130)
    for mlb in MLBS_DESCARTADOS:
        s = nosso_deal.get(mlb)
        ml = info_ml.get(mlb, {})
        preco_now = s.preco_atual if s else None
        dp = s.deal_price if s else None
        m_min = ml.get("min")
        m_max = ml.get("max")
        m_sug = ml.get("suggested")

        def f(v: float | None) -> str:
            return f"{v:.2f}" if v is not None else "-"

        envia: float | None = None
        margem: float | None = None
        teto_ef: float | None = None
        decisao: str

        if s is None or s.erro is not None:
            decisao = f"ERRO: {s.erro if s else 'sem sugestao'}"
        elif s.frete_a_confirmar:
            decisao = "frete a confirmar — pulado"
        elif not ml:
            decisao = "AUSENTE — não tenta, deixa fora"
        elif dp is None:
            decisao = "sem deal"
        elif s.custo_unit is None or s.comissao_pct is None or s.aliquota is None:
            decisao = "sem dados de custo"
        else:
            # Teto efetivo: min(max-0.01, suggested)
            if m_max is not None:
                teto_ef = float(m_max) - 0.01
            if m_sug is not None:
                teto_ef = float(m_sug) if teto_ef is None else min(teto_ef, float(m_sug))

            novo_dp = dp
            if teto_ef is not None and dp > teto_ef:
                novo_dp = round(teto_ef, 2)
            elif m_min is not None and dp < float(m_min):
                novo_dp = round(float(m_min) + 0.01, 2)

            margem = margem_liquida_pct(
                novo_dp, custo=s.custo_unit, tarifa_pct=s.comissao_pct,
                list_cost=s.list_cost, aliquota=s.aliquota,
            )

            if novo_dp == dp:
                envia = dp
                decisao = f"OK direto — envia {dp:.2f}, margem {margem * 100:.1f}%"
            elif margem < PISO_CLAMP:
                decisao = (
                    f"DESISTE — clamp daria {margem * 100:.1f}% < piso "
                    f"{PISO_CLAMP * 100:.1f}%. PUT pro preço P={dp:.2f} (Q2=20%)"
                )
            else:
                envia = novo_dp
                decisao = f"CLAMP OK — envia {novo_dp:.2f}, margem {margem * 100:.1f}%"

        print(
            f"{mlb:<16} {f(preco_now):>9} {f(dp):>9} "
            f"{f(m_max):>9} {f(m_sug):>9} {f(teto_ef):>9} {f(envia):>9} "
            f"{(f'{margem*100:.1f}%' if margem is not None else '-'):>7}  "
            f"{decisao}"
        )


if __name__ == "__main__":
    asyncio.run(main())
