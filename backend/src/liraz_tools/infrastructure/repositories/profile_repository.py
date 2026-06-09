"""Implementação SQLAlchemy do ProfileRepository.

Traduz entre entidade do domínio (Pydantic) e ProfileModel (SQLAlchemy).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.domain.profiles.entity import Profile, ProfileConfig, ProfileStatus
from liraz_tools.domain.profiles.repository import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
)
from liraz_tools.infrastructure.db.datetime_utils import ensure_aware_utc
from liraz_tools.infrastructure.db.models import ProfileModel


class SQLAlchemyProfileRepository:
    """Persiste Profile em SQLite via SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, profile: Profile) -> Profile:
        # Checa unicidade do slug
        existing = await self._session.execute(
            select(ProfileModel).where(ProfileModel.slug == profile.slug)
        )
        if existing.scalar_one_or_none() is not None:
            raise ProfileAlreadyExistsError(f"perfil com slug '{profile.slug}' já existe")

        model = self._to_model(profile)
        self._session.add(model)
        await self._session.flush()
        return profile

    async def get_by_id(self, profile_id: UUID) -> Profile:
        result = await self._session.execute(
            select(ProfileModel).where(ProfileModel.id == str(profile_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ProfileNotFoundError(f"perfil '{profile_id}' não encontrado")
        return self._to_entity(model)

    async def get_by_slug(self, slug: str) -> Profile:
        result = await self._session.execute(
            select(ProfileModel).where(ProfileModel.slug == slug)
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ProfileNotFoundError(f"perfil com slug '{slug}' não encontrado")
        return self._to_entity(model)

    async def list_all(self, include_archived: bool = False) -> list[Profile]:
        stmt = select(ProfileModel).order_by(ProfileModel.created_at.desc())
        if not include_archived:
            stmt = stmt.where(ProfileModel.status != ProfileStatus.ARCHIVED.value)

        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update(self, profile: Profile) -> Profile:
        result = await self._session.execute(
            select(ProfileModel).where(ProfileModel.id == str(profile.id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ProfileNotFoundError(f"perfil '{profile.id}' não existe pra update")

        profile.updated_at = datetime.now(UTC)
        self._apply_to_model(profile, model)
        await self._session.flush()
        return profile

    async def delete(self, profile_id: UUID) -> None:
        result = await self._session.execute(
            select(ProfileModel).where(ProfileModel.id == str(profile_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ProfileNotFoundError(f"perfil '{profile_id}' não encontrado")
        await self._session.delete(model)
        await self._session.flush()

    async def exists_by_slug(self, slug: str) -> bool:
        result = await self._session.execute(
            select(ProfileModel.id).where(ProfileModel.slug == slug).limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ─── Mappers ────────────────────────────────────────────────────────

    @staticmethod
    def _to_model(profile: Profile) -> ProfileModel:
        return ProfileModel(
            id=str(profile.id),
            name=profile.name,
            slug=profile.slug,
            status=profile.status.value,
            config_json=profile.config.model_dump_json(),
            ml_user_id=profile.ml_user_id,
            ml_nickname=profile.ml_nickname,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            archived_at=profile.archived_at,
        )

    @staticmethod
    def _apply_to_model(profile: Profile, model: ProfileModel) -> None:
        model.name = profile.name
        model.slug = profile.slug
        model.status = profile.status.value
        model.config_json = profile.config.model_dump_json()
        model.ml_user_id = profile.ml_user_id
        model.ml_nickname = profile.ml_nickname
        model.updated_at = profile.updated_at
        model.archived_at = profile.archived_at

    @staticmethod
    def _to_entity(model: ProfileModel) -> Profile:
        config_dict = json.loads(model.config_json)
        return Profile(
            id=UUID(model.id) if isinstance(model.id, str) else model.id,
            name=model.name,
            slug=model.slug,
            status=ProfileStatus(model.status),
            config=ProfileConfig(**config_dict),
            ml_user_id=model.ml_user_id,
            ml_nickname=model.ml_nickname,
            # created_at/updated_at são NOT NULL no schema — sempre presentes.
            created_at=cast("datetime", ensure_aware_utc(model.created_at)),
            updated_at=cast("datetime", ensure_aware_utc(model.updated_at)),
            archived_at=ensure_aware_utc(model.archived_at),
        )
