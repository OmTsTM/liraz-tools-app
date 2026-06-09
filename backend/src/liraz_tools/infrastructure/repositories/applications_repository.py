"""Repository de aplicações (Application) e rollbacks.

Persistência em JSON sob `profiles/<slug>/applications/<id>.json` e
`profiles/<slug>/rollbacks/<id>.json`, mesmo padrão dos snapshots de
simulação.

Writes são atômicos (write em .tmp + rename) pra não corromper se cair
no meio. Não usa locks — uma única aplicação por vez por design (UI
bloqueia o botão Play enquanto status=executando).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from liraz_tools.core.paths import get_app_data_dir
from liraz_tools.domain.applications.entity import (
    Application,
    Rollback,
    RollbackItem,
)


class ApplicationNotFoundError(Exception):
    pass


def _applications_dir(slug: str) -> Path:
    d = get_app_data_dir() / "profiles" / slug / "applications"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rollbacks_dir(slug: str) -> Path:
    d = get_app_data_dir() / "profiles" / slug / "rollbacks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomicamente — escreve em .tmp, fsync, rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


class ApplicationsRepository:
    """CRUD de Application por perfil."""

    def get(self, slug: str, application_id: str) -> Application | None:
        path = _applications_dir(slug) / f"{application_id}.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return Application.model_validate(data)

    def save(self, slug: str, app: Application) -> None:
        path = _applications_dir(slug) / f"{app.application_id}.json"
        _atomic_write(path, app.model_dump(mode="json"))

    def list_by_campaign(self, slug: str, campaign_id: str) -> list[Application]:
        """Lista todas aplicações de uma campanha, mais recentes primeiro."""
        results: list[Application] = []
        for path in _applications_dir(slug).glob("*.json"):
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            if str(data.get("campaign_id")) == campaign_id:
                results.append(Application.model_validate(data))
        results.sort(key=lambda a: a.criado_em, reverse=True)
        return results


class RollbacksRepository:
    """CRUD de Rollback por perfil. Append-only durante Fase 1."""

    def get(self, slug: str, rollback_id: str) -> Rollback | None:
        path = _rollbacks_dir(slug) / f"{rollback_id}.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return Rollback.model_validate(data)

    def save(self, slug: str, rb: Rollback) -> None:
        path = _rollbacks_dir(slug) / f"{rb.rollback_id}.json"
        _atomic_write(path, rb.model_dump(mode="json"))

    def append_item(self, slug: str, rollback_id: str, item: RollbackItem) -> None:
        """Adiciona um item ao rollback e re-grava o arquivo.

        Custo: read+write inteiro a cada item. Pra 200 itens isso são
        200 writes de arquivo de ~50KB max — perfeitamente OK.

        Razão de fazer assim em vez de manter em memória até o final:
        se a aplicação cair no meio (server crash, queda de internet),
        ainda tem registro completo do que foi aplicado e pode reverter.
        """
        rb = self.get(slug, rollback_id)
        if rb is None:
            raise ApplicationNotFoundError(
                f"rollback {rollback_id} não encontrado no perfil {slug}"
            )
        rb.itens.append(item)
        self.save(slug, rb)
