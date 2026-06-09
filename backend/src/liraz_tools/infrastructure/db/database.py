"""Configuração do banco de dados (SQLite assíncrono via aiosqlite).

O banco fica em <data_dir>/liraz.db e guarda metadados de perfis,
histórico, snapshots, etc.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from liraz_tools.core.paths import get_app_data_dir


class Base(DeclarativeBase):
    """Base declarativa de todos os models SQLAlchemy."""


def get_database_url() -> str:
    """URL do SQLite, no diretório de dados do app."""
    db_path = get_app_data_dir() / "liraz.db"
    return f"sqlite+aiosqlite:///{db_path}"


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Singleton do engine. Lazy init."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_database_url(),
            echo=False,
            future=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Singleton da factory de sessions."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager que abre sessão, commita ou faz rollback automático."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all_tables() -> None:
    """Cria todas as tabelas. Chamar no startup (uma vez).

    Também roda migrations leves (ALTER TABLE) pra colunas adicionadas
    em versões novas. SQLAlchemy `create_all` só cria tabelas
    inexistentes — não adiciona colunas em tabelas que já existem.
    """
    # Importa models pra registrar metadata

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_migrations)


def _run_migrations(sync_conn: Connection) -> None:
    """Migrations idempotentes pra schema deltas que `create_all` não cobre.

    Cada bloco adiciona uma coluna SE ela ainda não existir. Rodar
    múltiplas vezes é seguro (idempotente).

    Adicionar entradas aqui quando criar coluna nova em model existente.
    Ordem por data de adição.
    """
    # ── Leva 5.3.1: campanha ganha hora_disparo (default 09:00) ──
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="hora_disparo",
        # SQLite não aceita DEFAULT com função; usa literal '09:00:00'
        ddl="TIME NOT NULL DEFAULT '09:00:00'",
    )

    # ── Leva 5.3.2: campanha ganha hora_fim opcional ──
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="hora_fim",
        ddl="TIME",  # nullable, sem default
    )

    # ── Leva 5.7: seleção parcial de SKUs pra entrar na campanha ──
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="skus_selecionados_json",
        ddl="TEXT",  # nullable, sem default — None = todos
    )

    # ── Leva 5.7.1: soft delete automático após 15 dias ──
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="archived_at",
        ddl="DATETIME",  # nullable
    )

    # ── Leva 5.9.2: origem (local | ml) ──
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="origem",
        ddl="TEXT NOT NULL DEFAULT 'local'",
    )

    # ── Leva 5.9.4.B: faixa de margem por campanha ──
    # NULL = herda do perfil (config.margem_min/max_migracao)
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="margem_min_migracao",
        ddl="REAL",
    )
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="margem_max_migracao",
        ddl="REAL",
    )
    # Leva 5.11 — flag de renovação automática
    _add_column_if_missing(
        sync_conn,
        table="campaigns",
        column="renovada",
        ddl="BOOLEAN NOT NULL DEFAULT 0",
    )


def _add_column_if_missing(
    sync_conn: Connection, *, table: str, column: str, ddl: str,
) -> None:
    """Roda ALTER TABLE ADD COLUMN se a coluna não existir.

    Usa PRAGMA table_info pra inspeção — específico do SQLite, que é o
    único banco que o app usa.
    """
    # Se a tabela não existe ainda (banco novo), pula — create_all já criou
    # com a coluna correta no schema atual
    tables = sync_conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:n",
        {"n": table},
    ).fetchall()
    if not tables:
        return

    cols = sync_conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    nomes = {c[1] for c in cols}  # PRAGMA retorna (cid, name, type, ...)
    if column in nomes:
        return

    sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


async def dispose_engine() -> None:
    """Fecha conexões. Chamar no shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
