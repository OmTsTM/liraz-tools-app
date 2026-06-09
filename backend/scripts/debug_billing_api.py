"""Testa direto se conseguimos chamar /billing/integration/periods com nosso
token OAuth atual. Tenta vários endpoints da família e mostra o que volta."""
from __future__ import annotations
import asyncio
import json
from datetime import date, timedelta
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


async def _try(ml: MLClient, label: str, path: str, params: dict | None = None) -> None:
    print(f"\n--- {label} ---")
    full = f"GET {path}"
    if params:
        full += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    print(full)
    try:
        resp = await ml.get(path, params=params or {})
        txt = json.dumps(resp, indent=2, ensure_ascii=False, default=str)
        if len(txt) > 2500:
            txt = txt[:2500] + "\n  ... (truncado)"
        print(txt)
    except Exception as e:
        msg = str(e)[:300]
        if "404" in msg:
            print(f"  X 404: {msg}")
        elif "403" in msg:
            print(f"  X 403 (sem permissao/scope): {msg}")
        elif "400" in msg:
            print(f"  X 400: {msg}")
        else:
            print(f"  X ERRO: {msg}")


async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        profile = await profile_repo.get_by_id(PROFILE_ID)
        assert profile.ml_user_id is not None
        creds_repo = PerProfileCredentialsRepository()
        creds = creds_repo.get_app_credentials(profile.slug)
        tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        # Mês passado (período mais provável de ter dados)
        hoje = date(2026, 5, 30)  # data conhecida (não usar Date.now em scripts)
        mes_passado = (hoje - timedelta(days=30)).replace(day=1)
        chave = mes_passado.strftime("%Y-%m-%d")

        async with MLClient(creds, tokens) as ml:
            # 1) Lista de periodos
            await _try(ml, "1) /billing/integration/periods (lista)",
                "/billing/integration/periods", {"group": "ML"})

            # 2) Summary do periodo do mes passado
            await _try(ml, f"2) summary periodo {chave}",
                f"/billing/integration/periods/key/{chave}/summary",
                {"group": "ML", "document_type": "BILL"})

            # 3) Details do periodo do mes passado (limit baixo)
            await _try(ml, f"3) details periodo {chave} ML (limit 2)",
                f"/billing/integration/periods/key/{chave}/group/ML/details",
                {"document_type": "BILL", "limit": 2})

            # 4) Documents
            await _try(ml, f"4) documents periodo {chave}",
                f"/billing/integration/periods/key/{chave}/documents",
                {"group": "ML"})


if __name__ == "__main__":
    asyncio.run(main())
