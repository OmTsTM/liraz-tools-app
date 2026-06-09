"""Repository de settings globais (key-value).

Usado pra coisas tipo "qual o perfil ativo atualmente" e preferências
da UI que não pertencem a nenhum perfil específico.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liraz_tools.infrastructure.db.models import SettingsModel

ACTIVE_PROFILE_KEY = "active_profile_id"


class SettingsRepository:
    """Acesso a settings globais."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> str | None:
        result = await self._session.execute(
            select(SettingsModel).where(SettingsModel.key == key)
        )
        model = result.scalar_one_or_none()
        return model.value if model else None

    async def set(self, key: str, value: str) -> None:
        result = await self._session.execute(
            select(SettingsModel).where(SettingsModel.key == key)
        )
        model = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if model is None:
            self._session.add(SettingsModel(key=key, value=value, updated_at=now))
        else:
            model.value = value
            model.updated_at = now
        await self._session.flush()

    async def delete(self, key: str) -> None:
        result = await self._session.execute(
            select(SettingsModel).where(SettingsModel.key == key)
        )
        model = result.scalar_one_or_none()
        if model is not None:
            await self._session.delete(model)
            await self._session.flush()
