"""Criptografia simétrica de credenciais (Fernet = AES-128-CBC + HMAC-SHA256).

A chave-mestra é gerada na primeira execução e salva em
`%LOCALAPPDATA%\\LirazTools\\.master.key`. Quem tem essa chave consegue
ler todas as credenciais salvas — então depende das permissões NTFS do
sistema operacional pra proteger o arquivo.

Trade-offs aceitos:
- Não pedimos senha do usuário toda vez (UX vs segurança)
- Se a chave-mestra vazar, todos os refresh tokens vazam
- Se a chave-mestra for perdida, os tokens viram lixo e usuários precisam
  reautorizar do zero (não é catastrófico)

Para defesa em profundidade adicional, considerar no futuro:
- Migrar pra Windows Credential Manager (via 'keyring')
- Adicionar opção de senha-mestra
"""
from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from liraz_tools.core.logging import get_logger
from liraz_tools.core.paths import get_master_key_file

logger = get_logger(__name__)


class CryptoError(Exception):
    """Falha de criptografia/descriptografia."""


class Crypto:
    """Encapsula criptografia simétrica usando uma chave-mestra do disco.

    Use via singleton: `crypto = get_crypto()`.
    """

    def __init__(self, key_path: Path | None = None) -> None:
        self._key_path = key_path or get_master_key_file()
        self._fernet: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        """Inicializa Fernet, criando chave-mestra se necessário."""
        if self._fernet is not None:
            return self._fernet

        key = self._load_or_create_key()
        self._fernet = Fernet(key)
        return self._fernet

    def _load_or_create_key(self) -> bytes:
        """Lê chave do disco ou gera nova na 1ª execução.

        Em produção (Render etc.), o disco é efêmero — uma chave gerada no
        boot some no próximo deploy e todas as credenciais ML viram lixo.
        Por isso aceitamos override via env `LIRAZ_TOOLS_MASTER_KEY`: se
        setado, usamos esse valor (precisa ser uma chave Fernet válida,
        base64 urlsafe de 32 bytes — gere uma vez com
        `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
        e salve no painel de env vars do Render).
        """
        env_key = os.environ.get("LIRAZ_TOOLS_MASTER_KEY")
        if env_key:
            raw = env_key.strip().encode("ascii")
            try:
                Fernet(raw)  # valida formato
            except (InvalidToken, ValueError) as e:
                raise CryptoError(
                    "LIRAZ_TOOLS_MASTER_KEY tem formato inválido — gere com "
                    "Fernet.generate_key() e cole o valor exato"
                ) from e
            logger.info("master_key_loaded_from_env")
            return raw

        if self._key_path.exists():
            try:
                raw = self._key_path.read_bytes()
                # Fernet espera 44 bytes base64 (32 bytes binários encoded)
                Fernet(raw)  # valida formato
                logger.debug("master_key_loaded", path=str(self._key_path))
                return raw
            except (InvalidToken, ValueError) as e:
                raise CryptoError(
                    f"chave-mestra em {self._key_path} está corrompida: {e}. "
                    f"Delete o arquivo pra gerar uma nova (todas as credenciais "
                    f"salvas vão precisar ser reautorizadas)."
                ) from e

        # Gera nova chave
        new_key = Fernet.generate_key()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_bytes(new_key)

        # Permissões restritivas: 600 (owner read/write only) em Unix.
        # No Windows herda do parent (em %LOCALAPPDATA%, já privado) e pode
        # ignorar chmod — por isso suprimimos OSError.
        with contextlib.suppress(OSError):
            self._key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        logger.warning(
            "master_key_generated",
            path=str(self._key_path),
            note="primeira_execucao_chave_nova",
        )
        return new_key

    def encrypt(self, plaintext: str) -> str:
        """Encripta string em base64. Retorna token Fernet."""
        if not plaintext:
            raise CryptoError("não pode encriptar string vazia")

        try:
            token = self._get_fernet().encrypt(plaintext.encode("utf-8"))
            return token.decode("ascii")
        except Exception as e:
            raise CryptoError(f"falha ao encriptar: {e}") from e

    def decrypt(self, token: str) -> str:
        """Descripta token Fernet de volta pra string."""
        if not token:
            raise CryptoError("não pode descriptar token vazio")

        try:
            plaintext = self._get_fernet().decrypt(token.encode("ascii"))
            return plaintext.decode("utf-8")
        except InvalidToken as e:
            raise CryptoError(
                "token inválido — possivelmente foi criado com outra chave-mestra"
            ) from e
        except Exception as e:
            raise CryptoError(f"falha ao descriptar: {e}") from e


_crypto_instance: Crypto | None = None


def get_crypto() -> Crypto:
    """Singleton de Crypto pra reusar chave já carregada."""
    global _crypto_instance
    if _crypto_instance is None:
        _crypto_instance = Crypto()
    return _crypto_instance


def reset_crypto_singleton() -> None:
    """Pra testes. Não usar em produção."""
    global _crypto_instance
    _crypto_instance = None
