"""SQLAlchemy repos: User + UserProfileAccess.

Cuida das CRUDs básicas. As regras de negócio (quem pode criar quem,
quando dropar acessos órfãos) ficam nos use cases.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.domain.auth.entity import (
    ProfileRole,
    User,
    UserProfileAccess,
    UserWithHash,
)
from liraz_tools.infrastructure.db.datetime_utils import ensure_aware_utc
from liraz_tools.infrastructure.db.models import (
    UserModel,
    UserProfileAccessModel,
)


class UserAlreadyExistsError(Exception):
    """E-mail já cadastrado."""


class UserNotFoundError(Exception):
    """User não encontrado."""


def _email_norm(email: str) -> str:
    return email.strip().lower()


class SQLAlchemyUserRepository:
    """Persiste Users."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, email: str, password_hash: str, nome: str | None,
                  is_admin: bool) -> User:
        email_n = _email_norm(email)
        existing = await self._session.execute(
            select(UserModel).where(UserModel.email == email_n),
        )
        if existing.scalar_one_or_none() is not None:
            raise UserAlreadyExistsError(f"e-mail '{email_n}' já cadastrado")

        # SQLite/aiosqlite não aceita UUID nativo — cast pra str (igual ProfileRepo)
        new_id = uuid4()
        model = UserModel(
            id=cast("UUID", str(new_id)),
            email=email_n,
            password_hash=password_hash,
            nome=nome,
            is_admin=is_admin,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def get_by_id(self, user_id: UUID) -> User:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == str(user_id)),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise UserNotFoundError(f"user '{user_id}' não encontrado")
        return self._to_entity(model)

    async def get_with_hash_by_email(self, email: str) -> UserWithHash | None:
        """Pra fluxo de login: traz hash junto. Retorna None se não existir
        (em vez de erro — login não vaza se foi e-mail ou senha errada)."""
        result = await self._session.execute(
            select(UserModel).where(UserModel.email == _email_norm(email)),
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        base = self._to_entity(model)
        return UserWithHash(
            **base.model_dump(),
            password_hash=model.password_hash,
        )

    async def list_all(self) -> list[User]:
        result = await self._session.execute(
            select(UserModel).order_by(UserModel.created_at.desc()),
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == str(user_id)),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise UserNotFoundError(f"user '{user_id}' não encontrado")
        model.password_hash = password_hash
        await self._session.flush()

    async def touch_last_login(self, user_id: UUID) -> None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == str(user_id)),
        )
        model = result.scalar_one_or_none()
        if model is None:
            return
        model.last_login_at = datetime.now(UTC)
        await self._session.flush()

    async def set_active(self, user_id: UUID, *, is_active: bool) -> None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == str(user_id)),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise UserNotFoundError(f"user '{user_id}' não encontrado")
        model.is_active = is_active
        await self._session.flush()

    async def delete(self, user_id: UUID) -> None:
        # Acessos por loja saem junto (cascade manual)
        await self._session.execute(
            delete(UserProfileAccessModel).where(
                UserProfileAccessModel.user_id == str(user_id),
            ),
        )
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == str(user_id)),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise UserNotFoundError(f"user '{user_id}' não encontrado")
        await self._session.delete(model)
        await self._session.flush()

    def _to_entity(self, m: UserModel) -> User:
        return User(
            id=UUID(str(m.id)),
            email=m.email,
            nome=m.nome,
            is_admin=m.is_admin,
            is_active=m.is_active,
            created_at=ensure_aware_utc(m.created_at) or m.created_at,
            last_login_at=(
                ensure_aware_utc(m.last_login_at) if m.last_login_at else None
            ),
        )


class SQLAlchemyUserProfileAccessRepository:
    """Persiste linhas da ACL (user x loja -> role)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def grant(
        self, *, user_id: UUID, profile_id: UUID, role: ProfileRole,
    ) -> UserProfileAccess:
        """Idempotente: se já existe linha (user_id, profile_id), só atualiza role."""
        result = await self._session.execute(
            select(UserProfileAccessModel).where(
                UserProfileAccessModel.user_id == str(user_id),
                UserProfileAccessModel.profile_id == str(profile_id),
            ),
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.role = role.value
            await self._session.flush()
            return self._to_entity(existing)

        new_id = uuid4()
        model = UserProfileAccessModel(
            id=cast("UUID", str(new_id)),
            user_id=cast("UUID", str(user_id)),
            profile_id=cast("UUID", str(profile_id)),
            role=role.value,
            created_at=datetime.now(UTC),
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def revoke(self, *, user_id: UUID, profile_id: UUID) -> None:
        await self._session.execute(
            delete(UserProfileAccessModel).where(
                UserProfileAccessModel.user_id == str(user_id),
                UserProfileAccessModel.profile_id == str(profile_id),
            ),
        )
        await self._session.flush()

    async def get_role(
        self, *, user_id: UUID, profile_id: UUID,
    ) -> ProfileRole | None:
        result = await self._session.execute(
            select(UserProfileAccessModel.role).where(
                UserProfileAccessModel.user_id == str(user_id),
                UserProfileAccessModel.profile_id == str(profile_id),
            ),
        )
        row = result.scalar_one_or_none()
        return ProfileRole(row) if row else None

    async def list_for_user(self, user_id: UUID) -> list[UserProfileAccess]:
        result = await self._session.execute(
            select(UserProfileAccessModel).where(
                UserProfileAccessModel.user_id == str(user_id),
            ),
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_for_profile(self, profile_id: UUID) -> list[UserProfileAccess]:
        result = await self._session.execute(
            select(UserProfileAccessModel).where(
                UserProfileAccessModel.profile_id == str(profile_id),
            ),
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    def _to_entity(self, m: UserProfileAccessModel) -> UserProfileAccess:
        return UserProfileAccess(
            id=UUID(str(m.id)),
            user_id=UUID(str(m.user_id)),
            profile_id=UUID(str(m.profile_id)),
            role=ProfileRole(m.role),
            created_at=ensure_aware_utc(m.created_at) or m.created_at,
        )
