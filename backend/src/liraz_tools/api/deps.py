"""Dependências do FastAPI (Depends)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.core.auth.session import verificar_token
from liraz_tools.core.config import get_settings
from liraz_tools.domain.auth.entity import ProfileRole, User
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
from liraz_tools.infrastructure.repositories.user_repository import (
    SQLAlchemyUserProfileAccessRepository,
    SQLAlchemyUserRepository,
    UserNotFoundError,
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


def get_user_repository(session: DbSession) -> SQLAlchemyUserRepository:
    return SQLAlchemyUserRepository(session)


def get_user_profile_access_repository(
    session: DbSession,
) -> SQLAlchemyUserProfileAccessRepository:
    return SQLAlchemyUserProfileAccessRepository(session)


ProfileRepo = Annotated[SQLAlchemyProfileRepository, Depends(get_profile_repository)]
SettingsRepo = Annotated[SettingsRepository, Depends(get_settings_repository)]
CredsRepo = Annotated[
    PerProfileCredentialsRepository,
    Depends(get_per_profile_credentials_repository),
]
PendingStore = Annotated[PendingAuthorizationStore, Depends(get_pending_store)]
UserRepo = Annotated[SQLAlchemyUserRepository, Depends(get_user_repository)]
AccessRepo = Annotated[
    SQLAlchemyUserProfileAccessRepository,
    Depends(get_user_profile_access_repository),
]


async def get_current_user_optional(
    request: Request,
    user_repo: UserRepo,
) -> User | None:
    """Lê cookie de sessão pelo nome configurado e devolve User. None se
    não logado, expirado, ou user inativo."""
    cookie_name = get_settings().auth_cookie_name
    token = request.cookies.get(cookie_name)
    if not token:
        return None
    user_id = verificar_token(token)
    if user_id is None:
        return None
    try:
        user = await user_repo.get_by_id(user_id)
    except UserNotFoundError:
        return None
    if not user.is_active:
        return None
    return user


async def get_current_user(
    request: Request,
    user_repo: UserRepo,
) -> User:
    """Exige cookie de sessão válido. 401 se não logado."""
    user = await get_current_user_optional(request=request, user_repo=user_repo)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="não autenticado",
        )
    return user


async def get_admin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Exige usuário admin. 403 se logado mas não-admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="acesso restrito a administradores",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserOptional = Annotated[User | None, Depends(get_current_user_optional)]
AdminUser = Annotated[User, Depends(get_admin_user)]


async def require_profile_access_role(
    profile_id_str: str,
    user: User,
    access_repo: SQLAlchemyUserProfileAccessRepository,
    *,
    minimum: ProfileRole = ProfileRole.VIEWER,
) -> ProfileRole:
    """Valida que `user` tem `role >= minimum` em `profile_id_str`.

    Admin do sistema bypassa — devolve ADMIN. Para usuários comuns, busca
    linha em `user_profile_access` e compara níveis. Levanta 403 se sem
    acesso, 404 se UUID inválido. Usar dentro de endpoints que recebem
    `profile_id` no path/body — não dá pra virar `Depends` direto porque
    o nome do param varia.
    """
    if user.is_admin:
        return ProfileRole.ADMIN
    try:
        profile_uuid = UUID(profile_id_str)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="profile_id inválido") from e
    role = await access_repo.get_role(user_id=user.id, profile_id=profile_uuid)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sem acesso a esta loja",
        )
    if role.nivel() < minimum.nivel():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requer role mínimo '{minimum.value}', você tem '{role.value}'",
        )
    return role
