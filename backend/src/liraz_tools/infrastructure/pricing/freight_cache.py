"""Cache persistente do custo de frete por item.

Portado do MCP, adaptado pra arquitetura multi-perfil. Cada loja tem seu
próprio cache, vivendo na pasta do perfil pra isolamento total.

O endpoint /items/{id}/shipping_options do ML é flaky — frequentemente retorna
HTTP 424 (timeout interno), 429 (rate limit) ou 5xx. Esse cache armazena o
último list_cost obtido com sucesso, permitindo recuperar o valor mesmo quando
a API está temporariamente indisponível.

TTL de 7 dias: list_cost é função de peso/dimensão/tabela do ML, então muda
raramente. Refresh forçado semanal mantém os custos atualizados sem queimar
chamadas desnecessárias.

Arquivo: <data>/profiles/<slug>/freight_cache.db (tabela `frete_cache`).
Schema idêntico ao do MCP — pra futura compatibilidade ou debug com SQLite
browser.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from liraz_tools.core.paths import get_profile_dir

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 dias


_SCHEMA = """
CREATE TABLE IF NOT EXISTS frete_cache (
    item_id TEXT PRIMARY KEY,
    list_cost REAL NOT NULL,
    metodo TEXT,
    cep_destino TEXT,
    updated_at INTEGER NOT NULL
)
"""


@dataclass
class FreteCache:
    """Entrada de cache de frete (mesmos campos do MCP)."""
    item_id: str
    list_cost: float
    metodo: str | None
    cep_destino: str
    updated_at: int

    @property
    def idade_segundos(self) -> int:
        return int(time.time()) - self.updated_at

    @property
    def idade_dias(self) -> int:
        return self.idade_segundos // 86400

    @property
    def valido(self) -> bool:
        return self.idade_segundos < CACHE_TTL_SECONDS


def _get_db_path(profile_slug: str) -> Path:
    """Retorna o caminho do freight_cache.db de um perfil."""
    return get_profile_dir(profile_slug) / "freight_cache.db"


@contextmanager
def _open_db(profile_slug: str) -> Iterator[sqlite3.Connection]:
    """Abre conexão e garante schema (cria tabela na primeira vez)."""
    path = _get_db_path(profile_slug)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_SCHEMA)
        yield conn
    finally:
        conn.close()


class FreightCache:
    """Cache de fretes por perfil.

    Instanciar com o slug do perfil. Mesma API do MCP (save/get) pra
    facilitar a portabilidade da lógica de pricing.
    """

    def __init__(self, profile_slug: str) -> None:
        self._slug = profile_slug

    def save(
        self,
        item_id: str,
        list_cost: float,
        metodo: str | None,
        cep_destino: str,
    ) -> None:
        """Upsert do cache de um item."""
        with _open_db(self._slug) as conn:
            conn.execute(
                """
                INSERT INTO frete_cache (item_id, list_cost, metodo, cep_destino, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    list_cost=excluded.list_cost,
                    metodo=excluded.metodo,
                    cep_destino=excluded.cep_destino,
                    updated_at=excluded.updated_at
                """,
                (item_id, list_cost, metodo, cep_destino, int(time.time())),
            )
            conn.commit()

    def get(self, item_id: str) -> FreteCache | None:
        """Retorna entrada do cache ou None se nunca foi salva."""
        with _open_db(self._slug) as conn:
            cursor = conn.execute(
                "SELECT item_id, list_cost, metodo, cep_destino, updated_at "
                "FROM frete_cache WHERE item_id = ?",
                (item_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return FreteCache(*row)
