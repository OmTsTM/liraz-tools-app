"""Entidades de autenticação: User + ACL por loja.

Login do app (NÃO confundir com OAuth do ML, que é outra coisa em
`domain/oauth/`). Cada User tem e-mail/senha (hash) e pode ter:

- `is_admin=True` → enxerga TUDO; não precisa de linhas em
  `UserProfileAccess`.
- `is_admin=False` → enxerga só as lojas pra quais existe um
  `UserProfileAccess` apontando pra ele. O `role` daquela linha define
  o que ele pode fazer na loja.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ProfileRole(StrEnum):
    """Nível de acesso de um User a uma loja específica.

    Ordem de poder: admin > operator > viewer. Comparações de "ao menos
    role X" usam `nivel()`.
    """

    ADMIN = "admin"        # tudo (config, OAuth, deletar campanha)
    OPERATOR = "operator"  # opera (reprecificar, criar/editar campanha)
    VIEWER = "viewer"      # só leitura

    def nivel(self) -> int:
        """3=admin, 2=operator, 1=viewer. Permite `r.nivel() >= other.nivel()`."""
        return {"admin": 3, "operator": 2, "viewer": 1}[self.value]


class User(BaseModel):
    """User do app (entidade de domínio, sem campos sensíveis)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    email: EmailStr
    nome: str | None = None
    is_admin: bool = False
    is_active: bool = True
    created_at: datetime
    last_login_at: datetime | None = None


class UserWithHash(User):
    """User + password_hash. Usado SÓ na borda de auth (login flow).

    Nunca exponha ao cliente — `User` é o tipo seguro.
    """

    password_hash: str = Field(repr=False)


class UserProfileAccess(BaseModel):
    """Linha da tabela `user_profile_access`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    user_id: UUID
    profile_id: UUID
    role: ProfileRole
    created_at: datetime
