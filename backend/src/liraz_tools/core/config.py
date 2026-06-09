"""Settings da aplicação, lidas de variáveis de ambiente.

Usa pydantic-settings pra ter type safety nas configurações.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações globais do app.

    Carrega de variáveis de ambiente (LIRAZ_TOOLS_*) ou de arquivo .env
    no diretório de execução. Variáveis sensíveis (credenciais OAuth)
    NÃO vivem aqui — ficam criptografadas no perfil de cada loja.
    """

    model_config = SettingsConfigDict(
        env_prefix="LIRAZ_TOOLS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Servidor HTTP
    host: str = "127.0.0.1"
    port: int = 8000

    # CORS — permite o frontend dev (Vite na 5173) chamar a API
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:1420",  # tauri default, se um dia migrar
        ]
    )

    # OAuth ML
    ml_api_base: str = "https://api.mercadolibre.com"
    ml_auth_base: str = "https://auth.mercadolivre.com.br/authorization"
    ml_token_url: str = "https://api.mercadolibre.com/oauth/token"

    # Callback OAuth que o ML chama de volta
    # Importante: precisa bater com o Redirect URI cadastrado na app ML
    ml_redirect_path: str = "/api/auth/callback"

    # Ambiente
    environment: str = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Singleton de settings. Lazy load + cache."""
    return Settings()
