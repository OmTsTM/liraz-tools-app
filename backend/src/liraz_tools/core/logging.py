"""Logging estruturado em JSON via structlog.

Saída pra stdout (capturável por Docker/systemd) com timestamp ISO,
nível e contexto adicional. Em dev, sai com cores via console renderer.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from liraz_tools.core.config import get_settings


def _drop_color_message_key(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """Uvicorn adiciona uma chave 'color_message' que polui logs JSON. Remove."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging() -> None:
    """Configura logging do app. Chamar uma vez no startup."""
    settings = get_settings()
    is_dev = settings.environment == "development"

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _drop_color_message_key,
    ]

    processors: list[Processor]
    if is_dev:
        # Console colorido pra dev
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # JSON pra prod (parseável por agregadores)
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Encaminha logging stdlib pra structlog (uvicorn, fastapi, etc)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StdlibFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level.upper()))

    # Silencia ruído de bibliotecas
    for noisy in ("uvicorn.access", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _StdlibFormatter(logging.Formatter):
    """Formata logs do stdlib via structlog pra ficar consistente."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        return f"[{record.levelname:5}] {record.name}: {message}"


def get_logger(name: str | None = None) -> Any:
    """Atalho pra obter um logger nomeado."""
    return structlog.get_logger(name)
