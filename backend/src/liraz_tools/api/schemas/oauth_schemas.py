"""DTOs HTTP do contexto OAuth."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SaveCredentialsRequest(BaseModel):
    """Body de POST /api/profiles/{id}/oauth/credentials."""

    client_id: str = Field(min_length=1, description="App ID da aplicação ML.")
    client_secret: str = Field(
        min_length=1, description="Secret Key da aplicação ML."
    )
    redirect_uri: str = Field(
        default="http://localhost:8000/api/auth/callback",
        description=(
            "Redirect URI cadastrado na aplicação ML. Precisa bater "
            "exatamente com o que está no painel dev do ML."
        ),
    )


class AuthorizationUrlResponse(BaseModel):
    """Resposta de GET /api/profiles/{id}/oauth/authorize-url."""

    url: str = Field(description="Abra essa URL no navegador pra autorizar.")


class ImportFromMCPRequest(BaseModel):
    """Body de POST /api/profiles/{id}/oauth/import-mcp."""

    tokens_db_path: str = Field(
        description=(
            "Caminho do tokens.db do MCP existente. "
            "Ex: C:\\\\Users\\\\Matteus\\\\projetos\\\\mcp-mercadolivre\\\\data\\\\tokens.db"
        )
    )
    client_id: str = Field(
        min_length=1,
        description="CLIENT_ID da aplicação ML usada pelo MCP que tem esse token.",
    )
    client_secret: str = Field(
        min_length=1,
        description="CLIENT_SECRET da mesma aplicação ML do MCP.",
    )
    redirect_uri: str = Field(
        description=(
            "Redirect URI da aplicação ML do MCP "
            "(precisa ser exatamente o que está cadastrado lá)."
        )
    )


class MLConnectionTestResponse(BaseModel):
    """Resposta de GET /api/profiles/{id}/ml-test."""

    id: int | None = None
    nickname: str | None = None
    email: str | None = None
    site_id: str | None = None
    country_id: str | None = None
    user_type: str | None = None
    registration_date: str | None = None
