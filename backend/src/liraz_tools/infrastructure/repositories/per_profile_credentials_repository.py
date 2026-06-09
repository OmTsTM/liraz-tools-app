"""Repositório de credenciais OAuth por perfil.

Cada perfil tem 2 arquivos dentro de `<data>/profiles/<slug>/`:
- `app_credentials.enc` — CLIENT_ID/SECRET criptografados com Fernet
- `tokens.db` — SQLite no schema do MCP (tabela `tokens`), mas com
  access_token e refresh_token criptografados com Fernet

Vantagens dessa estrutura:
- Backup/portabilidade: copiar a pasta `profiles/toque-rico/` leva tudo
- Isolamento: deletar a pasta apaga só essa loja (não afeta outras)
- Compatibilidade visual com MCP: schema da tabela `tokens` é idêntico
  ao MCP, só os valores estão criptografados

Importante: pra abrir as credenciais ou tokens descriptografados, é
preciso ter acesso à `.master.key` que vive em <data>/.master.key.
Sem ela, os arquivos viram lixo (mas permanecem listáveis pra cleanup).
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from liraz_tools.core.logging import get_logger
from liraz_tools.core.paths import (
    get_profile_credentials_file,
    get_profile_dir,
    get_profile_tokens_db,
)
from liraz_tools.domain.oauth.entity import AppCredentials, TokenSet
from liraz_tools.infrastructure.security.crypto import Crypto, CryptoError, get_crypto

logger = get_logger(__name__)


class OAuthCredentialsNotFoundError(Exception):
    """Perfil não tem credenciais OAuth cadastradas."""


# ─── Schema do tokens.db (compatível com MCP) ──────────────────────────

_TOKENS_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    user_id INTEGER PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    scope TEXT NOT NULL,
    updated_at INTEGER NOT NULL
)
"""


@contextmanager
def _open_tokens_db(path: Path) -> Iterator[sqlite3.Connection]:
    """Abre conexão SQLite com tokens.db, criando tabela se necessário."""
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.execute(_TOKENS_DB_SCHEMA)
        yield conn
    finally:
        conn.close()


# ─── Repositório principal ────────────────────────────────────────────


class PerProfileCredentialsRepository:
    """Persiste credenciais OAuth em arquivos por perfil.

    Substitui o antigo OAuthCredentialsRepository que guardava tudo no
    liraz.db. Agora cada perfil tem seus arquivos isolados.
    """

    def __init__(self, crypto: Crypto | None = None) -> None:
        self._crypto = crypto or get_crypto()

    # ─── App credentials (CLIENT_ID / SECRET / redirect_uri) ──────────

    def upsert_app_credentials(
        self,
        profile_slug: str,
        credentials: AppCredentials,
        origin: str = "oauth_flow",
    ) -> None:
        """Cria ou atualiza CLIENT_ID/SECRET de um perfil.

        Salva como JSON criptografado em `app_credentials.enc`.
        """
        get_profile_dir(profile_slug)  # garante que pasta existe

        payload = {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "redirect_uri": credentials.redirect_uri,
            "origin": origin,
            "updated_at": int(time.time()),
        }
        encrypted = self._crypto.encrypt(json.dumps(payload))

        path = get_profile_credentials_file(profile_slug)
        path.write_text(encrypted, encoding="ascii")
        logger.debug("app_credentials_saved", profile_slug=profile_slug)

    def get_app_credentials(self, profile_slug: str) -> AppCredentials:
        """Lê CLIENT_ID/SECRET descriptografados."""
        path = get_profile_credentials_file(profile_slug)
        if not path.exists():
            raise OAuthCredentialsNotFoundError(
                f"perfil '{profile_slug}' não tem credenciais OAuth"
            )

        try:
            encrypted = path.read_text(encoding="ascii")
            payload = json.loads(self._crypto.decrypt(encrypted))
        except CryptoError as e:
            raise OAuthCredentialsNotFoundError(
                f"credenciais do perfil '{profile_slug}' estão corrompidas ou foram "
                f"criadas com outra chave-mestra: {e}"
            ) from e

        return AppCredentials(
            client_id=payload["client_id"],
            client_secret=payload["client_secret"],
            redirect_uri=payload["redirect_uri"],
        )

    def get_origin(self, profile_slug: str) -> str | None:
        """Retorna 'oauth_flow', 'mcp_import' ou None se não existe."""
        path = get_profile_credentials_file(profile_slug)
        if not path.exists():
            return None
        try:
            encrypted = path.read_text(encoding="ascii")
            payload = json.loads(self._crypto.decrypt(encrypted))
            return cast("str | None", payload.get("origin"))
        except (CryptoError, json.JSONDecodeError):
            return None

    def has_credentials(self, profile_slug: str) -> bool:
        """True se o perfil tem app_credentials.enc legível.

        Usado pra desarquivar de forma inteligente: se tem credenciais
        salvas, restaura como CONNECTED. Senão, força reconfiguração.
        """
        path = get_profile_credentials_file(profile_slug)
        if not path.exists():
            return False
        try:
            encrypted = path.read_text(encoding="ascii")
            self._crypto.decrypt(encrypted)  # valida que descriptografa OK
            return True
        except (CryptoError, OSError):
            return False

    # ─── Tokens (access_token / refresh_token) ────────────────────────

    def save_tokens(self, profile_slug: str, tokens: TokenSet) -> None:
        """Salva tokens criptografados no tokens.db do perfil.

        Schema da tabela é igual ao MCP — só os valores de access_token
        e refresh_token ficam criptografados.
        """
        access_enc = self._crypto.encrypt(tokens.access_token)
        refresh_enc = self._crypto.encrypt(tokens.refresh_token)
        expires_at_unix = int(tokens.expires_at.timestamp())
        now_unix = int(time.time())

        path = get_profile_tokens_db(profile_slug)
        with _open_tokens_db(path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tokens
                (user_id, access_token, refresh_token, expires_at, scope, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tokens.ml_user_id,
                    access_enc,
                    refresh_enc,
                    expires_at_unix,
                    tokens.scope,
                    now_unix,
                ),
            )
        logger.debug(
            "tokens_saved",
            profile_slug=profile_slug,
            ml_user_id=tokens.ml_user_id,
        )

    def get_tokens(self, profile_slug: str, ml_user_id: int) -> TokenSet:
        """Lê tokens descriptografados do tokens.db do perfil."""
        path = get_profile_tokens_db(profile_slug)
        if not path.exists():
            raise OAuthCredentialsNotFoundError(
                f"perfil '{profile_slug}' não tem tokens (não autorizado)"
            )

        with _open_tokens_db(path) as conn:
            row = conn.execute(
                """
                SELECT access_token, refresh_token, expires_at, scope
                FROM tokens
                WHERE user_id = ?
                """,
                (ml_user_id,),
            ).fetchone()

        if row is None:
            raise OAuthCredentialsNotFoundError(
                f"perfil '{profile_slug}' não tem tokens para user_id {ml_user_id}"
            )

        access_enc, refresh_enc, expires_at_unix, scope = row
        try:
            access_token = self._crypto.decrypt(access_enc)
            refresh_token = self._crypto.decrypt(refresh_enc)
        except CryptoError as e:
            raise OAuthCredentialsNotFoundError(
                f"tokens do perfil '{profile_slug}' estão corrompidos ou foram "
                f"criados com outra chave-mestra: {e}"
            ) from e

        return TokenSet(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=datetime.fromtimestamp(expires_at_unix, tz=UTC),
            scope=scope,
            ml_user_id=ml_user_id,
        )

    def clear_tokens(self, profile_slug: str) -> None:
        """Remove tokens.db (mas mantém app_credentials.enc)."""
        path = get_profile_tokens_db(profile_slug)
        if path.exists():
            path.unlink()
            logger.debug("tokens_cleared", profile_slug=profile_slug)

    # ─── Limpeza completa ─────────────────────────────────────────────

    def delete_all(self, profile_slug: str) -> None:
        """Apaga arquivos do perfil — credenciais e tokens.db.

        Mantém a pasta vazia (futuras criações reaproveitam).
        """
        for path in [
            get_profile_credentials_file(profile_slug),
            get_profile_tokens_db(profile_slug),
        ]:
            if path.exists():
                path.unlink()

        logger.info("profile_files_deleted", profile_slug=profile_slug)
