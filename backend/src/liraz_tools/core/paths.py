"""Resolução de paths do app (data dir, profiles dir, etc).

No Windows usa %LOCALAPPDATA%\\LirazTools\\.
No Linux/Mac usa ~/.local/share/liraz-tools/ pra dev.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "LirazTools"


def get_app_data_dir() -> Path:
    """Diretório raiz onde o app guarda dados persistentes.

    Windows: C:\\Users\\<user>\\AppData\\Local\\LirazTools\\
    Linux:   ~/.local/share/liraz-tools/
    Mac:     ~/Library/Application Support/LirazTools/

    O diretório é criado se não existir.
    """
    override = os.environ.get("LIRAZ_TOOLS_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        path = base / "liraz-tools"

    path.mkdir(parents=True, exist_ok=True)
    return path


def get_profiles_dir() -> Path:
    """Diretório onde cada perfil de loja é uma subpasta."""
    path = get_app_data_dir() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_profile_dir(profile_slug: str) -> Path:
    """Diretório de um perfil específico. Ex: <data>/profiles/toque-rico/

    Criado se não existir. Slug deve ser URL-safe (validado pelo domínio).
    """
    path = get_profiles_dir() / profile_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_profile_tokens_db(profile_slug: str) -> Path:
    """Caminho do tokens.db de um perfil.

    Schema compatível com o MCP (tabela 'tokens'), mas access_token e
    refresh_token ficam criptografados com Fernet.
    """
    return get_profile_dir(profile_slug) / "tokens.db"


def get_profile_credentials_file(profile_slug: str) -> Path:
    """Caminho do arquivo com CLIENT_ID/SECRET criptografados de um perfil."""
    return get_profile_dir(profile_slug) / "app_credentials.enc"


def get_shared_dir() -> Path:
    """Diretório de arquivos compartilhados entre perfis (custos.xlsx, etc)."""
    path = get_app_data_dir() / "_shared"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_settings_file() -> Path:
    """Arquivo JSON com settings globais (último perfil ativo, tema, etc)."""
    return get_app_data_dir() / "settings.json"


def get_master_key_file() -> Path:
    """Arquivo com chave-mestra usada pra criptografar credenciais.

    Gerada na primeira execução. Se perdida, todas as credenciais salvas
    ficam ilegíveis e os usuários precisam reautorizar.
    """
    return get_app_data_dir() / ".master.key"
