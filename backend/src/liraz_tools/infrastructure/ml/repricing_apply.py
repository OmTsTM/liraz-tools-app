"""Aplicação de reprecificação em massa — PUT /items/{id} em paralelo.

Usado pela feature "Reprecificar tudo" (operações da loja) que recalcula o
preço-base de N anúncios pro P passo3 (margem alvo Q2 do perfil) e aplica
direto, sem campanha. Diferente de `aplicar_apenas_inflacao` (que tem
semântica de "preparar pra entrar em campanha"), aqui o PUT é o objetivo
final — não há fase 2.

Estrutura espelha `aplicar_apenas_inflacao` (semáforo de writes + snapshot
em lote pra idempotência), mas o dataclass de resultado e os logs falam de
"aplicados" em vez de "inflados" pra refletir a semântica certa.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from liraz_tools.infrastructure.ml.campaign_skus_apply import (
    MAX_CONCORRENCIA_WRITES,
    _snapshot_precos,
)
from liraz_tools.infrastructure.ml.items_update import (
    MLItemUpdateError,
    atualizar_preco_item,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient


@dataclass
class AplicarRepricingResult:
    """Resultado da reprecificação em massa.

    `precos_anteriores_por_item` espelha o snapshot pré-PUT só pros itens
    que foram efetivamente APLICADOS (não pros `ja_no_preco` nem `erros`).
    Permite ao caller persistir num repositório de undo.
    """

    aplicados: list[str] = field(default_factory=list)
    ja_no_preco: list[str] = field(default_factory=list)
    erros: list[dict[str, Any]] = field(default_factory=list)
    precos_anteriores_por_item: dict[str, float] = field(default_factory=dict)


async def aplicar_repricing_em_massa(
    ml: MLClient,
    *,
    precos: dict[str, float],
    logger: Any = None,
    max_concorrencia: int = MAX_CONCORRENCIA_WRITES,
) -> AplicarRepricingResult:
    """Aplica PUT /items/{id} em massa pros preços novos do mapa `precos`.

    - Snapshot em lote dos preços atuais (multiget) detecta itens que JÁ estão
      no preço novo (idempotência) — caem em `ja_no_preco` sem re-aplicar.
    - PUTs rodam em paralelo, limitados por semáforo (default 6).
    - Falhas individuais não param o lote: cada erro é registrado em `erros`
      com `{item_id, operacao, erro}` e o caller decide o que fazer.

    Sem rollback: não há "depois" que possa falhar — quem chama essa função
    quis aplicar e ponto. Se algum item falhar, os outros que deram OK ficam
    com o preço novo aplicado.
    """
    res = AplicarRepricingResult()
    if not precos:
        return res

    item_ids = list(precos.keys())
    precos_atuais = await _snapshot_precos(ml, item_ids)

    sem = asyncio.Semaphore(max(1, max_concorrencia))

    async def _processar(item_id: str) -> tuple[str, str | None]:
        """Retorna (status, erro). status ∈ {"aplicado","ja_no_preco","erro"}."""
        novo_preco = precos.get(item_id)
        if novo_preco is None or novo_preco <= 0:
            return ("erro", f"preço inválido: {novo_preco}")

        preco_atual = precos_atuais.get(item_id)
        if preco_atual is not None and abs(preco_atual - novo_preco) < 0.01:
            return ("ja_no_preco", None)

        async with sem:
            try:
                await atualizar_preco_item(ml, item_id, novo_preco)
                if logger is not None:
                    logger.info(
                        "repricing_massa_ok",
                        item_id=item_id,
                        preco_anterior=preco_atual,
                        preco_novo=novo_preco,
                    )
                return ("aplicado", None)
            except MLItemUpdateError as e:
                if logger is not None:
                    logger.warning(
                        "repricing_massa_falhou",
                        item_id=item_id,
                        erro=str(e)[:200],
                    )
                return ("erro", str(e))

    outcomes = await asyncio.gather(
        *[_processar(iid) for iid in item_ids],
    )

    for item_id, (status, erro) in zip(item_ids, outcomes, strict=True):
        if status == "aplicado":
            res.aplicados.append(item_id)
            # Snapshot pré-PUT só faz sentido pros itens que de fato mudaram
            # de preço. Quem caiu em `ja_no_preco` não tem "antes" diferente
            # do "depois", e quem deu erro não tem PUT pra reverter.
            preco_ant = precos_atuais.get(item_id)
            if preco_ant is not None:
                res.precos_anteriores_por_item[item_id] = preco_ant
        elif status == "ja_no_preco":
            res.ja_no_preco.append(item_id)
        else:
            res.erros.append({
                "item_id": item_id, "operacao": "repricing", "erro": erro or "",
            })

    return res
