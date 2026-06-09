"""Entidades do contexto OAuth.

Separamos:
- AppCredentials: CLIENT_ID + CLIENT_SECRET da aplicação ML (do dev panel)
- TokenSet: access_token + refresh_token + expiração (resultado do OAuth)
- PendingAuthorization: state CSRF + redirect_uri durante fluxo OAuth
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class AppCredentials(BaseModel):
    """Credenciais da aplicação ML cadastrada no developer panel.

    São compartilhadas entre todos os usuários que conectam usando essa
    aplicação ML específica. NÃO são as credenciais da loja do usuário.
    """

    model_config = ConfigDict(frozen=True)

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)


class TokenSet(BaseModel):
    """Tokens OAuth obtidos após autorização."""

    model_config = ConfigDict(frozen=False)

    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    expires_at: datetime
    scope: str = "offline_access read write"
    ml_user_id: int

    def is_expired(self, safety_margin_seconds: int = 60) -> bool:
        """True se o token expirou (com margem de segurança).

        Margem evita usar token que vai expirar nos próximos N segundos —
        previne race condition em chamadas HTTP em batch.
        """
        now = datetime.now(UTC)
        margin = timedelta(seconds=safety_margin_seconds)
        return now + margin >= self.expires_at

    @classmethod
    def from_ml_response(
        cls, data: dict[str, Any], fallback_user_id: int | None = None,
    ) -> TokenSet:
        """Cria TokenSet a partir do JSON de resposta do /oauth/token do ML.

        Resposta típica:
            {
              "access_token": "APP_USR-...",
              "refresh_token": "TG-...",
              "token_type": "Bearer",
              "expires_in": 21600,
              "scope": "offline_access read write",
              "user_id": 123456789
            }
        """
        expires_in = int(data.get("expires_in", 21600))
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        user_id = data.get("user_id") or fallback_user_id
        if user_id is None:
            raise ValueError("resposta do ML não tem user_id nem fallback")

        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=expires_at,
            scope=data.get("scope", "offline_access read write"),
            ml_user_id=int(user_id),
        )


class PendingAuthorization(BaseModel):
    """Autorização em andamento — guarda state CSRF + perfil alvo.

    Quando o usuário clica "Conectar ML", criamos uma dessas e guardamos
    em memória (ou DB). Quando o ML chama o callback, validamos o state
    pra garantir que é a mesma requisição que iniciou.
    """

    model_config = ConfigDict(frozen=False)

    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    state: str = Field(min_length=20, description="CSRF token aleatório.")
    redirect_uri: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(minutes=15)
    )

    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at
