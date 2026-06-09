"""Persistência de overrides de custo por perfil.

Cada perfil tem um arquivo `profiles/<slug>/cost_overrides.json` que mapeia
SKU ou MLB → valor de custo manual. Esse mapa é injetado em
`GenerateFeeReportUseCase` como `custos_manuais`, ou seja, reusa a cascata
que JÁ EXISTE em `buscar_custo`:

  0) override manual por MLB    ← este arquivo
  1) override manual por SKU    ← este arquivo
  2) match exato no XLSX
  3) case-insensitive
  4) prefixo

Schema do JSON é simples:
{
  "version": 1,
  "overrides": {
    "SKU-123": 12.50,
    "MLB1234567890": 99.99
  },
  "updated_at": 1716239421
}

A chave pode ser SKU (string normal) OU MLB (formato "MLB" + dígitos). O
endpoint aceita ambos e o cliente passa qualquer um — o `buscar_custo`
resolve corretamente.

Não criptografamos: são dados de negócio, não credenciais. Se o usuário
quiser proteger, fica nas permissões NTFS do %LOCALAPPDATA% — mesmo nível
do tokens.db, que já está protegido.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from liraz_tools.core.logging import get_logger
from liraz_tools.core.paths import get_profile_dir

logger = get_logger(__name__)


SCHEMA_VERSION = 1


def _get_overrides_file(profile_slug: str) -> Path:
    return get_profile_dir(profile_slug) / "cost_overrides.json"


class CostOverridesRepository:
    """Lê e escreve overrides de custo no JSON do perfil.

    Operações em todo o mapa (load_all) — operações granulares (set/remove)
    fazem load + mutate + save porque o arquivo é pequeno (uma linha por
    override). Pra 1000+ overrides não é gargalo: cada save é <10ms.
    """

    def load_all(self, profile_slug: str) -> dict[str, float]:
        """Retorna o mapa completo {sku_ou_mlb: valor} do perfil."""
        path = _get_overrides_file(profile_slug)
        if not path.exists():
            return {}

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "cost_overrides_load_failed",
                profile_slug=profile_slug,
                error=str(e),
            )
            return {}

        raw = data.get("overrides") or {}
        # Filtra entries inválidas defensivamente
        clean: dict[str, float] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, (int, float)):
                clean[key] = float(value)
        return clean

    def set_one(
        self, profile_slug: str, key: str, value: float,
    ) -> dict[str, float]:
        """Define um override (cria ou substitui). Retorna o mapa completo após."""
        overrides = self.load_all(profile_slug)
        overrides[key.strip()] = float(value)
        self._persist(profile_slug, overrides)
        logger.info(
            "cost_override_set",
            profile_slug=profile_slug,
            key=key,
            value=value,
        )
        return overrides

    def remove_one(self, profile_slug: str, key: str) -> dict[str, float]:
        """Remove um override. No-op se não existia. Retorna mapa atualizado."""
        overrides = self.load_all(profile_slug)
        if key in overrides:
            del overrides[key]
            self._persist(profile_slug, overrides)
            logger.info("cost_override_removed", profile_slug=profile_slug, key=key)
        return overrides

    def clear_all(self, profile_slug: str) -> None:
        """Apaga TODOS os overrides do perfil. Reset total."""
        path = _get_overrides_file(profile_slug)
        if path.exists():
            path.unlink()
            logger.info("cost_overrides_cleared", profile_slug=profile_slug)

    def _persist(self, profile_slug: str, overrides: dict[str, float]) -> None:
        """Escreve atomicamente: write em tmp + rename pra não corromper em crash."""
        path = _get_overrides_file(profile_slug)
        tmp = path.with_suffix(".json.tmp")

        payload = {
            "version": SCHEMA_VERSION,
            "overrides": overrides,
            "updated_at": int(time.time()),
        }

        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)  # rename atômico no mesmo filesystem
