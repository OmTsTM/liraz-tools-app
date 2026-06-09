"""Contrato do repositório de Profile.

O domínio NÃO conhece SQLAlchemy. Define só o que precisa via Protocol,
e a infraestrutura implementa. Isso permite trocar persistência (memória
pra testes, Postgres no futuro) sem mexer no domínio.
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from liraz_tools.domain.profiles.entity import Profile


class ProfileNotFoundError(Exception):
    """Perfil não encontrado por id ou slug."""


class ProfileAlreadyExistsError(Exception):
    """Conflito: já existe perfil com mesmo slug ou nome."""


class ProfileRepository(Protocol):
    """Persistência de Profile. Implementado em infrastructure/."""

    async def add(self, profile: Profile) -> Profile: ...

    async def get_by_id(self, profile_id: UUID) -> Profile: ...

    async def get_by_slug(self, slug: str) -> Profile: ...

    async def list_all(self, include_archived: bool = False) -> list[Profile]: ...

    async def update(self, profile: Profile) -> Profile: ...

    async def delete(self, profile_id: UUID) -> None:
        """Hard delete. Pra soft delete, chame archive() na entidade + update."""
        ...

    async def exists_by_slug(self, slug: str) -> bool: ...
