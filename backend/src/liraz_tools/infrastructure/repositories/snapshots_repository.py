"""Persistência de snapshots de simulação por perfil.

Cada simulação vira um JSON em `profiles/<slug>/snapshots/sim_<timestamp>_<uid>.json`
com o estado completo da simulação (idempotente — pode ser lido a qualquer
momento pra reconstruir o estado).

Estados possíveis:
  - "running"      → simulação em andamento (use case ainda processando)
  - "completed"    → terminou com sucesso (mesmo com algumas exceções)
  - "failed"       → erro fatal (não deve ser comum)
  - "interrupted"  → backend reiniciou no meio; pode ser retomado

Schema dos campos é compatível com o MCP — o snapshot pode ser inspecionado
pelo MCP usando `listar_snapshots()` se você quiser cross-check.

Operações atômicas via tmp + rename pra não corromper em crash no meio do
write (importante porque o checkpoint roda a cada 20 itens em batch).
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from liraz_tools.core.logging import get_logger
from liraz_tools.core.paths import get_profile_dir

logger = get_logger(__name__)


def _get_snapshots_dir(profile_slug: str) -> Path:
    """Retorna a pasta de snapshots do perfil, criando se necessário."""
    path = get_profile_dir(profile_slug) / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


class SnapshotsRepository:
    """Gerencia snapshots de simulação no filesystem.

    Cada simulação tem um arquivo `sim_<timestamp>_<uid>.json` único. O
    `simulation_id` é o stem do arquivo (sem extensão), garantindo
    correspondência 1:1 entre arquivo e ID.
    """

    def list_all(self, profile_slug: str) -> list[dict[str, Any]]:
        """Lista todos os snapshots do perfil, ordem mais recente → mais antigo.

        Retorna metadados leves (não os arrays inteiros) pra UI listar rápido.
        """
        snapshots_dir = _get_snapshots_dir(profile_slug)
        results: list[dict[str, Any]] = []

        for path in sorted(snapshots_dir.glob("sim_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "simulation_id": data.get("simulation_id", path.stem),
                    "criado_em": data.get("criado_em"),
                    "estado": data.get("estado", "unknown"),
                    "total_ativos_analisados": data.get(
                        "total_ativos_analisados", 0
                    ),
                    "total_simulados": data.get("total_simulados", 0),
                    "total_excecoes": data.get("total_excecoes", 0),
                    "total_processados": data.get("total_processados", 0),
                    "retomado_de_checkpoint": data.get(
                        "retomado_de_checkpoint", False
                    ),
                })
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "snapshot_list_skip",
                    path=str(path),
                    error=str(e),
                )
                continue

        return results

    def get(
        self, profile_slug: str, simulation_id: str,
    ) -> dict[str, Any] | None:
        """Carrega o snapshot completo (com listas inteiras)."""
        path = _get_snapshots_dir(profile_slug) / f"{simulation_id}.json"
        if not path.exists():
            return None

        try:
            return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "snapshot_load_failed",
                profile_slug=profile_slug,
                simulation_id=simulation_id,
                error=str(e),
            )
            return None

    def save(
        self, profile_slug: str, simulation_id: str, data: dict[str, Any],
    ) -> None:
        """Persiste o snapshot atomicamente (write tmp + rename).

        Atualiza `atualizado_em` automaticamente pra refletir o último write.
        """
        path = _get_snapshots_dir(profile_slug) / f"{simulation_id}.json"
        tmp = path.with_suffix(".json.tmp")

        data["atualizado_em"] = datetime.now(UTC).isoformat()

        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)  # atômico no mesmo filesystem

    def delete(self, profile_slug: str, simulation_id: str) -> bool:
        """Apaga o snapshot. Retorna True se existia."""
        path = _get_snapshots_dir(profile_slug) / f"{simulation_id}.json"
        if path.exists():
            path.unlink()
            logger.info(
                "snapshot_deleted",
                profile_slug=profile_slug,
                simulation_id=simulation_id,
            )
            return True
        return False

    def list_interrupted(self, profile_slug: str) -> list[str]:
        """Retorna IDs de simulações com estado=running ao reiniciar o backend.

        Chamado no startup pra detectar simulações órfãs (backend caiu no meio).
        Essas precisam ser marcadas como `interrupted` pra serem retomadas.
        """
        ids = []
        for snap in self.list_all(profile_slug):
            if snap["estado"] == "running":
                ids.append(snap["simulation_id"])
        return ids

    @staticmethod
    def new_simulation_id() -> str:
        """Gera um ID único pra nova simulação. Formato: sim_<timestamp>_<uid>."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sufixo de 4 dígitos via time.time_ns pra evitar colisão se
        # disparar 2 simulações no mesmo segundo
        uid = f"{(time.time_ns() % 10000):04d}"
        return f"sim_{ts}_{uid}"
