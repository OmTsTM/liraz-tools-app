"""Dependências do FastAPI (Depends)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.infrastructure.db.database import session_scope
from liraz_tools.infrastructure.repositories.pending_auth_store import (
    PendingAuthorizationStore,
    get_pending_authorization_store,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)
from liraz_tools.infrastructure.repositories.settings_repository import (
    SettingsRepository,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Sessão de banco com escopo da request."""
    async with session_scope() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_profile_repository(session: DbSession) -> SQLAlchemyProfileRepository:
    return SQLAlchemyProfileRepository(session)


def get_settings_repository(session: DbSession) -> SettingsRepository:
    return SettingsRepository(session)


def get_per_profile_credentials_repository() -> PerProfileCredentialsRepository:
    """Repositório de credenciais por perfil (arquivos no disco).

    Sem sessão de DB — só lê/escreve arquivos.
    """
    return PerProfileCredentialsRepository()


def get_pending_store() -> PendingAuthorizationStore:
    return get_pending_authorization_store()


ProfileRepo = Annotated[SQLAlchemyProfileRepository, Depends(get_profile_repository)]
SettingsRepo = Annotated[SettingsRepository, Depends(get_settings_repository)]
CredsRepo = Annotated[
    PerProfileCredentialsRepository,
    Depends(get_per_profile_credentials_repository),
]
PendingStore = Annotated[PendingAuthorizationStore, Depends(get_pending_store)]
