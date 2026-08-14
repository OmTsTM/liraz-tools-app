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

    # Auth (login email/senha + ACL granular).
    # `auth_required`: quando True, todos os /api/* (exceto health, login,
    # callback OAuth) exigem cookie de sessão válido. Default False enquanto
    # a UI de login não está pronta — vira True quando subir pra Render.
    # `auth_session_secret`: segredo HMAC do cookie de sessão. EM PRODUCAO
    # precisa ser setado por env var (LIRAZ_TOOLS_AUTH_SESSION_SECRET). O
    # default só serve pra dev local; trocar invalida sessoes existentes.
    # `auth_session_ttl_hours`: validade do cookie. 12h é confortável pra
    # dia de trabalho sem fazer relogin no meio.
    # `auth_cookie_secure`: marca o cookie como Secure (só envia em HTTPS).
    # Em dev local (HTTP) precisa ficar False; em Render (HTTPS) True.
    auth_required: bool = False
    auth_session_secret: str = "dev-only-change-me-in-production"
    auth_session_ttl_hours: int = 12
    auth_cookie_secure: bool = False
    auth_cookie_name: str = "liraz_session"

    # HTTP Basic Auth global (gating de borda — antes do app de login). Se
    # `basic_auth_user` e `basic_auth_password` estiverem setados, TODO o
    # tráfego HTTP precisa do `Authorization: Basic ...` correto (modal do
    # navegador). Útil pra esconder o app pré-login do mundo no Render —
    # nem o login fica aberto pra força bruta. Em dev local fica vazio.
    basic_auth_user: str = ""
    basic_auth_password: str = ""

    # SPA estática (frontend buildado). Em produção, o backend serve o
    # `frontend/dist/` empacotado — paths que não começam com `/api` ou
    # `/health` viram `index.html` (SPA routing). Em dev, deixar vazio:
    # o Vite continua servindo.
    spa_dist_dir: str = ""


@lru_cache
def get_settings() -> Settings:
    """Singleton de settings. Lazy load + cache."""
    return Settings()
