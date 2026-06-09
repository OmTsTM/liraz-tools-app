"""Descobre quem é dono do item."""
from __future__ import annotations
import asyncio
from uuid import UUID
from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

MLB = "MLB6590364264"

async def main() -> None:
    async with session_scope() as session:
        profile_repo = SQLAlchemyProfileRepository(session)
        # Tenta com todos os perfis
        for pid_str in [
            "7de27552-bd0e-4f5a-a03a-d5206177f325",  # LiraZ
            "94c5477b-899e-4019-b651-4be75213e6fd",  # Toque Rico
            "722d19aa-983b-4a18-8cb9-70484520fcda",  # Namore
        ]:
            profile = await profile_repo.get_by_id(UUID(pid_str))
            print(f"\n--- Tentando com perfil {profile.name} (ml_user_id={profile.ml_user_id}) ---")
            if profile.ml_user_id is None:
                print("  perfil sem ml_user_id")
                continue
            creds_repo = PerProfileCredentialsRepository()
            try:
                creds = creds_repo.get_app_credentials(profile.slug)
                tokens = creds_repo.get_tokens(profile.slug, profile.ml_user_id)
            except Exception as e:
                print(f"  sem credenciais: {e}")
                continue
            async with MLClient(creds, tokens) as ml:
                try:
                    item = await ml.get(
                        f"/items/{MLB}",
                        params={"attributes": "id,seller_id,price,status,title"},
                    )
                    print(f"  {item}")
                except Exception as e:
                    print(f"  erro: {e}")


if __name__ == "__main__":
    asyncio.run(main())
