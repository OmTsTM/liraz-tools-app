"""Use cases do contexto OAuth.

Implementa as duas estratégias de conexão:
- OAuth tradicional: usuário autoriza no navegador
- Import MCP: lê refresh_token de outro projeto e valida

Persistência via PerProfileCredentialsRepository — cada perfil tem seus
arquivos em <data>/profiles/<slug>/ (tokens.db + app_credentials.enc).
"""
from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from liraz_tools.core.config import get_settings
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.entity import (
    AppCredentials,
    PendingAuthorization,
    TokenSet,
)
from liraz_tools.domain.profiles.entity import Profile
from liraz_tools.infrastructure.ml.client import (
    MLAuthError,
    MLAuthHelper,
    MLClient,
)
from liraz_tools.infrastructure.repositories.pending_auth_store import (
    PendingAuthorizationStore,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)

logger = get_logger(__name__)


class OAuthError(Exception):
    """Erro genérico no fluxo OAuth."""


class InvalidStateError(OAuthError):
    """State CSRF inválido ou expirado."""


class MCPImportError(OAuthError):
    """Falha ao importar credenciais de projeto MCP existente."""


# ─── Fluxo OAuth tradicional ───────────────────────────────────────────


class SaveAppCredentialsUseCase:
    """Salva CLIENT_ID/SECRET de um perfil. Pré-condição pro fluxo OAuth."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo

    async def execute(
        self,
        profile_id: UUID,
        credentials: AppCredentials,
    ) -> None:
        profile = await self._profile_repo.get_by_id(profile_id)
        self._creds_repo.upsert_app_credentials(profile.slug, credentials)
        logger.info(
            "app_credentials_saved",
            profile_id=str(profile_id),
            profile_slug=profile.slug,
        )


class GenerateAuthorizationUrlUseCase:
    """Gera URL de autorização do ML pra um perfil. Cria PendingAuthorization."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        pending_store: PendingAuthorizationStore,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._pending_store = pending_store

    async def execute(self, profile_id: UUID) -> str:
        profile = await self._profile_repo.get_by_id(profile_id)
        creds = self._creds_repo.get_app_credentials(profile.slug)

        state = secrets.token_urlsafe(32)
        pending = PendingAuthorization(
            profile_id=profile_id,
            state=state,
            redirect_uri=creds.redirect_uri,
        )
        await self._pending_store.add(pending)

        settings = get_settings()
        params = {
            "response_type": "code",
            "client_id": creds.client_id,
            "redirect_uri": creds.redirect_uri,
            "state": state,
        }
        url = f"{settings.ml_auth_base}?{urlencode(params)}"
        logger.info("authorization_url_generated", profile_id=str(profile_id))
        return url


class CompleteOAuthFlowUseCase:
    """Recebe o callback do ML, valida state, troca code por tokens, salva."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        pending_store: PendingAuthorizationStore,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._pending_store = pending_store

    async def execute(self, code: str, state: str) -> Profile:
        pending = await self._pending_store.pop_by_state(state)
        if pending is None:
            raise InvalidStateError(
                "state inválido ou expirado — recomece o fluxo de autorização"
            )

        profile = await self._profile_repo.get_by_id(pending.profile_id)
        creds = self._creds_repo.get_app_credentials(profile.slug)
        helper = MLAuthHelper()

        try:
            tokens = await helper.exchange_code_for_tokens(creds, code)
        except MLAuthError as e:
            logger.warning("oauth_exchange_failed", error=str(e))
            raise OAuthError(f"falha ao trocar code: {e}") from e

        # Persiste tokens no tokens.db do perfil
        self._creds_repo.save_tokens(profile.slug, tokens)

        # Atualiza Profile com dados do ML
        ml_nickname = await self._fetch_nickname(creds, tokens)
        profile.mark_connected(ml_user_id=tokens.ml_user_id, ml_nickname=ml_nickname)
        await self._profile_repo.update(profile)

        logger.info(
            "oauth_flow_completed",
            profile_id=str(profile.id),
            ml_user_id=tokens.ml_user_id,
            ml_nickname=ml_nickname,
        )
        return profile

    async def _fetch_nickname(self, creds: AppCredentials, tokens: TokenSet) -> str:
        try:
            async with MLClient(creds, tokens) as ml:
                me = await ml.get("/users/me")
            return str(me.get("nickname", ""))[:80]
        except Exception:
            return ""


# ─── Fluxo de importação de MCP existente ──────────────────────────────


class ImportFromMCPUseCase:
    """Importa refresh_token de um projeto MCP existente (tokens.db).

    Reusa a aplicação ML do MCP — não autoriza nova aplicação.
    Schema esperado do tokens.db de origem (igual ao MCP):
        CREATE TABLE tokens (
            user_id INTEGER PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            scope TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo

    async def execute(
        self,
        profile_id: UUID,
        tokens_db_path: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> Profile:
        profile = await self._profile_repo.get_by_id(profile_id)

        # 1. Lê refresh_token do tokens.db EXTERNO (do MCP, em texto puro)
        refresh_token, ml_user_id_from_db = self._read_refresh_token_from_mcp(
            tokens_db_path
        )

        # 2. Tenta refrescar pra validar que o token funciona
        credentials = AppCredentials(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
        helper = MLAuthHelper()

        try:
            new_tokens = await helper.refresh_tokens(
                credentials, refresh_token, ml_user_id_from_db
            )
        except MLAuthError as e:
            raise MCPImportError(
                f"refresh_token do MCP não funciona com esses CLIENT_ID/SECRET: {e}"
            ) from e

        # 3. Tudo certo, persiste credenciais e tokens no perfil
        self._creds_repo.upsert_app_credentials(
            profile.slug, credentials, origin="mcp_import"
        )
        self._creds_repo.save_tokens(profile.slug, new_tokens)

        # 4. Pega nickname e marca perfil como CONNECTED
        ml_nickname = await self._fetch_nickname(credentials, new_tokens)
        profile.mark_connected(
            ml_user_id=new_tokens.ml_user_id, ml_nickname=ml_nickname
        )
        await self._profile_repo.update(profile)

        logger.info(
            "mcp_import_completed",
            profile_id=str(profile.id),
            profile_slug=profile.slug,
            ml_user_id=new_tokens.ml_user_id,
            ml_nickname=ml_nickname,
        )
        return profile

    @staticmethod
    def _read_refresh_token_from_mcp(tokens_db_path: str) -> tuple[str, int]:
        """Lê refresh_token + user_id do tokens.db EXTERNO (MCP, texto puro)."""
        path = Path(tokens_db_path).expanduser()
        if not path.exists():
            raise MCPImportError(f"arquivo não encontrado: {path}")
        if not path.is_file():
            raise MCPImportError(f"caminho não é arquivo: {path}")

        try:
            conn = sqlite3.connect(str(path))
            try:
                cursor = conn.execute(
                    "SELECT user_id, refresh_token FROM tokens ORDER BY updated_at DESC LIMIT 1"
                )
                row = cursor.fetchone()
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise MCPImportError(f"falha ao abrir tokens.db: {e}") from e

        if row is None:
            raise MCPImportError(
                "tabela 'tokens' está vazia — o MCP nunca foi autorizado"
            )

        user_id, refresh_token = row
        if not refresh_token:
            raise MCPImportError("refresh_token está vazio no tokens.db")

        return str(refresh_token), int(user_id)

    async def _fetch_nickname(self, creds: AppCredentials, tokens: TokenSet) -> str:
        try:
            async with MLClient(creds, tokens) as ml:
                me = await ml.get("/users/me")
            return str(me.get("nickname", ""))[:80]
        except Exception:
            return ""


# ─── Operações pós-conexão ─────────────────────────────────────────────


class DisconnectUseCase:
    """Remove credenciais e tokens do perfil, marca como DISCONNECTED."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo

    async def execute(self, profile_id: UUID) -> Profile:
        profile = await self._profile_repo.get_by_id(profile_id)
        self._creds_repo.delete_all(profile.slug)

        profile.mark_disconnected()
        profile.ml_user_id = None
        profile.ml_nickname = None
        await self._profile_repo.update(profile)

        logger.info("profile_disconnected", profile_id=str(profile_id))
        return profile


class TestMLConnectionUseCase:
    """Faz GET /users/me pra validar que tokens funcionam."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo

    async def execute(self, profile_id: UUID) -> dict[str, Any]:
        profile = await self._profile_repo.get_by_id(profile_id)

        if profile.ml_user_id is None:
            raise OAuthError("perfil não está conectado ao ML")

        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        creds_repo = self._creds_repo
        slug = profile.slug

        async def save_refreshed(new_tokens: TokenSet) -> None:
            creds_repo.save_tokens(slug, new_tokens)

        async with MLClient(creds, tokens, on_tokens_refreshed=save_refreshed) as ml:
            me = await ml.get("/users/me")

        return {
            "id": me.get("id"),
            "nickname": me.get("nickname"),
            "email": me.get("email"),
            "site_id": me.get("site_id"),
            "country_id": me.get("country_id"),
            "user_type": me.get("user_type"),
            "registration_date": me.get("registration_date"),
        }
