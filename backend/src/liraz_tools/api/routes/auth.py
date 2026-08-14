"""Endpoints de autenticação do app (login email/senha).

Fluxo:
  POST /api/auth/login   {email, password} → seta cookie httpOnly, devolve user
  POST /api/auth/logout  → limpa cookie
  GET  /api/auth/me      → devolve user logado (401 se não)
  POST /api/auth/change-password → troca senha do user logado

NÃO confundir com `/api/auth/callback` (router `oauth_callback`) — esse
é o redirect do OAuth do ML, fluxo totalmente separado.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from liraz_tools.api.deps import (
    AccessRepo,
    AdminUser,
    CurrentUser,
    UserRepo,
)
from liraz_tools.core.auth.password import hash_password, verify_password
from liraz_tools.core.auth.session import emitir_token
from liraz_tools.core.config import get_settings
from liraz_tools.domain.auth.entity import ProfileRole
from liraz_tools.infrastructure.repositories.user_repository import (
    UserAlreadyExistsError,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================
# Schemas
# ============================================================


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: UUID
    email: str
    nome: str | None
    is_admin: bool
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    nome: str | None = None
    is_admin: bool = False


class ProfileAccessRequest(BaseModel):
    profile_id: UUID
    role: ProfileRole


class ProfileAccessResponse(BaseModel):
    id: UUID
    user_id: UUID
    profile_id: UUID
    role: ProfileRole
    created_at: datetime


# ============================================================
# Helpers
# ============================================================


def _set_session_cookie(response: Response, user_id: UUID) -> None:
    s = get_settings()
    token = emitir_token(user_id)
    response.set_cookie(
        key=s.auth_cookie_name,
        value=token,
        max_age=s.auth_session_ttl_hours * 3600,
        httponly=True,
        secure=s.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        key=s.auth_cookie_name,
        path="/",
        httponly=True,
        secure=s.auth_cookie_secure,
        samesite="lax",
    )


def _user_to_response(user: object) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)


# ============================================================
# Endpoints
# ============================================================


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    response: Response,
    user_repo: UserRepo,
) -> UserResponse:
    """Valida senha, seta cookie, devolve user."""
    user_with_hash = await user_repo.get_with_hash_by_email(body.email)
    # Mensagem genérica pra não vazar se foi e-mail ou senha errada.
    if user_with_hash is None or not verify_password(
        body.password, user_with_hash.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="e-mail ou senha inválidos",
        )
    if not user_with_hash.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="usuário desativado",
        )

    await user_repo.touch_last_login(user_with_hash.id)
    _set_session_cookie(response, user_with_hash.id)
    return _user_to_response(user_with_hash)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Limpa cookie. Idempotente — sem-cookie também devolve 204."""
    _clear_session_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    """Devolve user logado. 401 se não autenticado."""
    return _user_to_response(user)


@router.get("/my-access", response_model=list[ProfileAccessResponse])
async def my_access(
    user: CurrentUser,
    access_repo: AccessRepo,
) -> list[ProfileAccessResponse]:
    """ACLs do user logado (qual role em quais lojas).

    Admin global (`is_admin=True`) retorna lista vazia — admin enxerga TUDO
    via bypass do servidor, não precisa de linhas em `user_profile_access`.
    Frontend deve checar `is_admin` em `/me` antes — admin = role "admin"
    em qualquer loja.
    """
    if user.is_admin:
        return []
    rows = await access_repo.list_for_user(user.id)
    return [ProfileAccessResponse.model_validate(r, from_attributes=True) for r in rows]


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    user_repo: UserRepo,
) -> None:
    """Usuário troca a própria senha (precisa da senha atual)."""
    user_with_hash = await user_repo.get_with_hash_by_email(user.email)
    if user_with_hash is None or not verify_password(
        body.current_password, user_with_hash.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="senha atual incorreta",
        )
    await user_repo.update_password_hash(user.id, hash_password(body.new_password))


# ============================================================
# Gestão de usuários (admin only)
# ============================================================


@router.get("/users", response_model=list[UserResponse])
async def list_users(_admin: AdminUser, user_repo: UserRepo) -> list[UserResponse]:
    """Lista todos os usuários (só admin)."""
    users = await user_repo.list_all()
    return [_user_to_response(u) for u in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    _admin: AdminUser,
    user_repo: UserRepo,
) -> UserResponse:
    """Cria usuário novo (só admin)."""
    try:
        user = await user_repo.add(
            email=body.email,
            password_hash=hash_password(body.password),
            nome=body.nome,
            is_admin=body.is_admin,
        )
    except UserAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e),
        ) from e
    return _user_to_response(user)


@router.post("/users/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: UUID,
    admin: AdminUser,
    user_repo: UserRepo,
) -> None:
    """Desativa usuário (mantém histórico). Não pode desativar a si mesmo."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=400, detail="você não pode desativar a si mesmo",
        )
    await user_repo.set_active(user_id, is_active=False)


@router.post("/users/{user_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate_user(
    user_id: UUID,
    _admin: AdminUser,
    user_repo: UserRepo,
) -> None:
    """Reativa usuário."""
    await user_repo.set_active(user_id, is_active=True)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    admin: AdminUser,
    user_repo: UserRepo,
) -> None:
    """Apaga usuário + todos os user_profile_access dele. Não-reversível.
    Não pode apagar a si mesmo."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=400, detail="você não pode apagar a si mesmo",
        )
    await user_repo.delete(user_id)


# ============================================================
# ACL granular: acessos por loja (admin only)
# ============================================================


@router.get(
    "/users/{user_id}/profile-access",
    response_model=list[ProfileAccessResponse],
)
async def list_profile_access(
    user_id: UUID,
    _admin: AdminUser,
    access_repo: AccessRepo,
) -> list[ProfileAccessResponse]:
    """Lista as lojas que o user tem acesso + role em cada uma.

    Admin do sistema (`is_admin=True`) tem acesso TOTAL e a lista vai vir
    vazia mesmo assim — checar `is_admin` antes de chamar este endpoint
    se quiser distinguir "sem acesso" de "admin global".
    """
    rows = await access_repo.list_for_user(user_id)
    return [ProfileAccessResponse.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/users/{user_id}/profile-access",
    response_model=ProfileAccessResponse,
)
async def grant_profile_access(
    user_id: UUID,
    body: ProfileAccessRequest,
    _admin: AdminUser,
    access_repo: AccessRepo,
) -> ProfileAccessResponse:
    """Concede (ou atualiza) acesso do user a uma loja. Idempotente:
    chamar de novo com role diferente troca o role da linha existente."""
    row = await access_repo.grant(
        user_id=user_id,
        profile_id=body.profile_id,
        role=body.role,
    )
    return ProfileAccessResponse.model_validate(row, from_attributes=True)


@router.delete(
    "/users/{user_id}/profile-access/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_profile_access(
    user_id: UUID,
    profile_id: UUID,
    _admin: AdminUser,
    access_repo: AccessRepo,
) -> None:
    """Remove acesso do user à loja. Idempotente."""
    await access_repo.revoke(user_id=user_id, profile_id=profile_id)


@router.get(
    "/profiles/{profile_id}/access",
    response_model=list[ProfileAccessResponse],
)
async def list_users_with_access(
    profile_id: UUID,
    _admin: AdminUser,
    access_repo: AccessRepo,
) -> list[ProfileAccessResponse]:
    """Lista usuários que têm acesso a uma loja específica + role de cada."""
    rows = await access_repo.list_for_profile(profile_id)
    return [ProfileAccessResponse.model_validate(r, from_attributes=True) for r in rows]
