"""Pipeline compartilhado de adição de SKUs a uma SELLER_CAMPAIGN.

Usado tanto pela criação completa de campanha ("Iniciar agora",
criar_ml_completo) quanto pela edição de campanha existente (sync-batch),
pra a lógica de dinheiro ficar em UM lugar só e os dois caminhos nunca
divergirem.

Faz, por item:
  1. Inflação (Fase 1) — opcional, via `inflar_precos`. O preço atual (pra
     reverter) é lido ANTES, em LOTE (multiget), não item a item.
  2. Adição à campanha com `deal_prices`. Em ERROR_CREDIBILITY, tenta de novo
     com `fallback_deal_prices` (deal conservador >= R$ 79 com frete).
  3. Descarte dos negados por ERROR_CREDIBILITY: reprecifica o anúncio pro
     preço de 20% de margem (o próprio deal_price) e deixa FORA da campanha.
     Inflados negados por OUTRO motivo voltam ao preço original.

Performance (P1+P2): os itens são processados em PARALELO (cada item é uma
task com inflar→adicionar→descarte em sequência *dentro* do item), com
concorrência limitada por um semáforo; e o snapshot de preço é feito em lote
via `GET /items?ids=...`. Antes era tudo sequencial (1 item por vez).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from liraz_tools.infrastructure.ml.items_update import (
    MLItemUpdateError,
    atualizar_preco_item,
)
from liraz_tools.infrastructure.ml.promotion_items import (
    ItemAlreadyInCampaignError,
    MLPromotionError,
    OfferLockedError,
    adicionar_sku_em_campanha,
)
from liraz_tools.infrastructure.ml.promotions_lookup import (
    buscar_limites_credibilidade,
)
from liraz_tools.infrastructure.pricing.repricing_calculator import (
    margem_liquida_pct,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

# Concorrência das operações de WRITE no ML (PUT preço / POST promoção).
# Conservador de propósito: writes em paralelo demais arriscam HTTP 429.
# O MLClient ainda tem o semáforo próprio (8) cobrindo todas as requests.
MAX_CONCORRENCIA_WRITES = 6

# Multiget do ML aceita até 20 ids por chamada.
_BATCH_MULTIGET = 20

# Piso de margem default pro CLAMP. Se clampar o dP pra dentro da faixa do ML
# faria a margem cair abaixo desse piso, o item é PULADO (não entra na campanha)
# em vez de sangrar margem. Configurável por chamada via param.
# 16,5% é o ponto entre R2 (15%, piso inviolável) e Q2 (20%, alvo) calibrado
# pra capturar itens "Padrão A" caros (~R$ 450) cuja margem cai pra ~16,9%
# após clamp pra ML_max (= 95% do preço atual). Piso 17% rejeitava esses por
# 0,1pp; 16,5% deixa entrar.
MARGEM_MINIMA_CLAMP_DEFAULT = 0.165


@dataclass
class DadosMargem:
    """Inputs pra recomputar margem líquida num deal arbitrário (modelo passo3).

    Fornecido por item pelo caller que quer ativar o CLAMP. Vem da sugestao
    (`SugerirDealPricesPorMargemUseCase` agora expõe os 4 componentes).
    """

    custo: float
    list_cost: float | None
    comissao_pct: float
    aliquota: float


@dataclass
class AplicarSkusResult:
    """Resultado granular da aplicação de SKUs (falhas parciais)."""

    adicionados: list[str] = field(default_factory=list)
    ja_estavam: list[str] = field(default_factory=list)
    inflados: list[str] = field(default_factory=list)
    revertidos: list[str] = field(default_factory=list)
    reprecificados_20pct: list[str] = field(default_factory=list)
    fallbacks_usados: list[str] = field(default_factory=list)
    # ── Novos campos do CLAMP (mai/2026) ──────────────────────────────
    # `clamped`: itens onde dP foi ajustado pra caber em [ML_min, ML_max]
    # `pulados_ausente`: itens que o ML não aceita como candidate da promoção
    # `pulados_por_margem`: itens onde o clamp violaria o piso de margem
    clamped: list[str] = field(default_factory=list)
    pulados_ausente: list[str] = field(default_factory=list)
    pulados_por_margem: list[str] = field(default_factory=list)
    # `pendentes_lock`: itens onde o ML retornou 423 LockedEntityException
    # mesmo após retries — o pipeline NÃO reverteu o preço-base inflado porque
    # o ML costuma adicionar o item assincronamente depois de destravar.
    # Caller deve consultar `/seller-promotions/items/{id}` algumas horas
    # depois pra confirmar se o item de fato entrou (e, se não entrou, decidir
    # se reverte preço manualmente).
    pendentes_lock: list[str] = field(default_factory=list)
    # `pulados_inflacao_falhou`: pediu inflação, PUT falhou → NÃO adicionamos
    # à campanha pra não deixar SKU com preço-base baixo + desconto (margem
    # despenca). Sem esse gate, o SKU acabava vendendo com margem quase zero.
    pulados_inflacao_falhou: list[str] = field(default_factory=list)
    erros: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _ItemOutcome:
    """Resultado de UM item — agregado depois, em ordem estável."""

    item_id: str
    inflado: bool = False
    adicionado: bool = False
    ja_estava: bool = False
    fallback_usado: bool = False
    reprecificado_20pct: bool = False
    revertido: bool = False
    clamped: bool = False
    pulado_ausente: bool = False
    pulado_por_margem: bool = False
    # Fase 1 (inflação) foi pedida e falhou — Fase 2 (adição) foi PULADA pra
    # evitar SKU na campanha com margem quebrada.
    pulado_inflacao_falhou: bool = False
    # 423 LockedEntity persistiu mesmo após retries — preço-base FICA inflado
    # (NÃO reverte) porque o ML costuma adicionar o item depois de destravar.
    pendente_lock: bool = False
    erros: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AplicarInflacaoResult:
    """Resultado da APENAS inflação (fluxo de 2 passos: inflar→confirmar).

    Diferente de `AplicarSkusResult` (que cobre inflação + adição + descarte),
    aqui só registra o que aconteceu no PUT de preço por item. Sem retry sem
    descarte — é a 1ª das duas etapas; a 2ª usa `aplicar_adicoes_skus_em_campanha`
    sem `inflar_precos`.
    """

    inflados: list[str] = field(default_factory=list)
    ja_no_preco: list[str] = field(default_factory=list)  # já estavam em U
    erros: list[dict[str, Any]] = field(default_factory=list)


async def _snapshot_precos(ml: MLClient, item_ids: list[str]) -> dict[str, float]:
    """Lê o preço atual de vários itens em LOTE (multiget) — pra reverter depois.

    Retorna {item_id -> preço}. Itens que falharem (404/erro) ficam de fora;
    o caller trata "sem snapshot" como "não pode inflar com segurança".
    """
    if not item_ids:
        return {}
    batches = [
        item_ids[i : i + _BATCH_MULTIGET]
        for i in range(0, len(item_ids), _BATCH_MULTIGET)
    ]

    async def _fetch(batch: list[str]) -> list[Any]:
        try:
            resp = await ml.get(
                "/items",
                params={"ids": ",".join(batch), "attributes": "id,price"},
                use_cache=False,
            )
        except Exception:
            return []
        return resp if isinstance(resp, list) else []

    precos: dict[str, float] = {}
    for batch_resp in await asyncio.gather(*[_fetch(b) for b in batches]):
        for entry in batch_resp:
            if not isinstance(entry, dict) or entry.get("code") != 200:
                continue
            body = entry.get("body")
            if not isinstance(body, dict):
                continue
            iid = body.get("id")
            preco = body.get("price")
            if iid and isinstance(preco, (int, float)) and preco > 0:
                precos[str(iid)] = float(preco)
    return precos


async def aplicar_adicoes_skus_em_campanha(
    ml: MLClient,
    *,
    ml_campaign_id: str,
    item_ids: list[str],
    deal_prices: dict[str, float],
    inflar_precos: dict[str, float],
    fallback_deal_prices: dict[str, float],
    dados_margem: dict[str, DadosMargem] | None = None,
    margem_minima_clamp: float = MARGEM_MINIMA_CLAMP_DEFAULT,
    logger: Any = None,
    max_concorrencia: int = MAX_CONCORRENCIA_WRITES,
) -> AplicarSkusResult:
    """Aplica inflação + adição + descarte pra `item_ids`, em paralelo. Não remove.

    Quando `dados_margem` é fornecido, ATIVA o CLAMP: lê a faixa de credibilidade
    do ML pros items em LOTE, ajusta cada `deal_price` pra caber em [ML_min, ML_max]
    e pula itens onde o clamp violaria o piso de margem (`margem_minima_clamp`,
    default 17%). Itens que o ML não aceita como candidate da promoção (ausentes
    da resposta) são pulados também. Resultado: muito menos descartes por
    ERROR_CREDIBILITY na fase de adição.

    Sem `dados_margem` (None), comportamento antigo: tenta com `deal_price` direto
    e cai no fallback/descarte se ML rejeitar.
    """
    res = AplicarSkusResult()
    if not item_ids:
        return res

    # CLAMP — fetch dos limites de credibilidade primeiro (1 call por item,
    # paralelos). Precisa vir antes do snapshot pra sabermos quais itens vão
    # precisar de PUT pra P (= preço 20% margem) e incluir no snapshot.
    limites_credibilidade: dict[str, dict[str, Any]] = {}
    if dados_margem is not None:
        limites_credibilidade = await buscar_limites_credibilidade(
            ml, promotion_id=ml_campaign_id, item_ids=item_ids,
        )

    # Pré-calcula o deal efetivo POR ITEM (clamp + gate de margem). Decisão
    # tomada FORA do loop paralelo pra evitar repetir importações por task.
    deals_efetivos: dict[str, float] = dict(deal_prices)  # copia base
    pulados_ausente: set[str] = set()
    pulados_por_margem: set[str] = set()
    clampados: set[str] = set()

    if dados_margem is not None:
        for iid in item_ids:
            dp_original = deal_prices.get(iid)
            if dp_original is None:
                continue  # sem deal_price — nada pra clampar

            dm = dados_margem.get(iid)
            if dm is None:
                continue  # caller não pediu clamp pra esse item

            limites = limites_credibilidade.get(iid)
            if limites is None:
                # ML não aceita esse item como candidate → não adianta nem tentar.
                pulados_ausente.add(iid)
                continue

            ml_min_raw = limites.get("min")
            ml_max_raw = limites.get("max")
            ml_sug_raw = limites.get("suggested")
            ml_min = float(ml_min_raw) if isinstance(ml_min_raw, (int, float)) else None
            ml_max = float(ml_max_raw) if isinstance(ml_max_raw, (int, float)) else None
            ml_sug = float(ml_sug_raw) if isinstance(ml_sug_raw, (int, float)) else None

            # Teto EFETIVO: o ML às vezes retorna `ml_max` baseado em desconto
            # mínimo teórico de 5%, mas a regra REAL pra FLEXIBLE_PERCENTAGE
            # parece ser 10% — refletida em `suggested_discounted_price`. Tomar
            # `min(ml_max - 0.01, suggested)` evita as duas armadilhas:
            # - Enviar ml_max exato = desconto 5% exato → MINIMUM_DISCOUNT_PERCENT
            # - Enviar acima de suggested = ERROR_CREDIBILITY (regra dos 10%)
            teto_efetivo: float | None = None
            if ml_max is not None:
                teto_efetivo = ml_max - 0.01
            if ml_sug is not None:
                teto_efetivo = ml_sug if teto_efetivo is None else min(teto_efetivo, ml_sug)

            novo_dp = dp_original
            if teto_efetivo is not None and dp_original > teto_efetivo:
                novo_dp = round(teto_efetivo, 2)
            elif ml_min is not None and dp_original < ml_min:
                # Simetricamente, adiciona 1 centavo no piso.
                novo_dp = round(ml_min + 0.01, 2)

            if novo_dp == dp_original:
                continue  # dP já está na faixa — nada a fazer

            # dP mudou — verifica se margem nova ainda respeita o piso.
            margem_nova = margem_liquida_pct(
                novo_dp, custo=dm.custo, tarifa_pct=dm.comissao_pct,
                list_cost=dm.list_cost, aliquota=dm.aliquota,
            )
            if margem_nova < margem_minima_clamp:
                pulados_por_margem.add(iid)
                if logger is not None:
                    logger.info(
                        "campaign_clamp_pulado_margem", item_id=iid,
                        dp_original=dp_original, dp_clampado=novo_dp,
                        margem_resultante_pct=round(margem_nova * 100, 2),
                        piso_pct=round(margem_minima_clamp * 100, 2),
                        ml_max=ml_max, ml_sug=ml_sug,
                    )
                continue

            deals_efetivos[iid] = novo_dp
            clampados.add(iid)
            if logger is not None:
                logger.info(
                    "campaign_clamp_aplicado", item_id=iid,
                    dp_original=dp_original, dp_clampado=novo_dp,
                    ml_min=ml_min, ml_max=ml_max,
                    margem_resultante_pct=round(margem_nova * 100, 2),
                )

    # Snapshot dos preços atuais (em lote). Inclui:
    # - `a_inflar`: pra reverter caso a adição falhe
    # - `pulados_por_margem`: pra decidir se precisamos PUT pra P (= preço 20%
    #   margem). Esses itens NÃO entram na campanha, mas o user quer que o
    #   preço deles fique em P (= deal_price calculado pelo passo3).
    a_inflar = [iid for iid in item_ids if iid in inflar_precos]
    a_snapshot = list(set(a_inflar) | pulados_por_margem)
    precos_originais = await _snapshot_precos(ml, a_snapshot)

    sem = asyncio.Semaphore(max(1, max_concorrencia))

    async def _processar_item(item_id: str) -> _ItemOutcome:
        out = _ItemOutcome(item_id=item_id)

        # Ausente: ML não aceita esse item como candidate — não toca em nada.
        if item_id in pulados_ausente:
            out.pulado_ausente = True
            return out

        # Pulado por margem: desistimos da campanha PROATIVAMENTE (clamp daria
        # margem abaixo do piso). Mas garante que o preço atual fique em P
        # (= deal_price calculado = preço de Q2=20% margem), via PUT se preço
        # atual diferir de P. Evita POST que falharia + manda preço pro alvo.
        if item_id in pulados_por_margem:
            out.pulado_por_margem = True
            preco_alvo = deal_prices.get(item_id)
            preco_atual = precos_originais.get(item_id)
            if (
                preco_alvo is not None and preco_alvo > 0
                and preco_atual is not None
                and abs(preco_atual - preco_alvo) > 0.01
            ):
                async with sem:
                    try:
                        await atualizar_preco_item(ml, item_id, preco_alvo)
                        out.reprecificado_20pct = True
                        if logger is not None:
                            logger.info(
                                "campaign_pulado_margem_reprecificado",
                                item_id=item_id, preco_anterior=preco_atual,
                                preco_alvo=preco_alvo,
                                ml_campaign_id=ml_campaign_id,
                            )
                    except MLItemUpdateError as e:
                        out.erros.append({
                            "item_id": item_id,
                            "operacao": "reprecificar_pulado",
                            "erro": (
                                f"pulado por margem; falhou ao ajustar pro preço "
                                f"de {margem_minima_clamp * 100:.1f}%+ margem "
                                f"{preco_alvo}: {e}"
                            ),
                        })
            return out

        async with sem:
            # ── Fase 1: inflação (se pedida e com snapshot pra reverter) ──
            novo_preco = inflar_precos.get(item_id)
            # Bug histórico: quando a inflação era pedida mas FALHAVA (preço
            # inválido, snapshot faltando, ML rejeitando PUT), a Fase 2 rodava
            # mesmo assim e o SKU acabava na campanha SEM o preço-base inflado
            # — resultado: desconto aplicado sobre preço-base baixo, margem
            # despenca. Fix: se pediu pra inflar E falhou por qualquer motivo,
            # PULA a Fase 2 pra não adicionar SKU quebrado na campanha.
            inflacao_pedida = novo_preco is not None
            inflacao_falhou = False
            if inflacao_pedida:
                if novo_preco is None or novo_preco <= 0:
                    out.erros.append({
                        "item_id": item_id, "operacao": "inflar",
                        "erro": f"preço inválido: {novo_preco}",
                    })
                    inflacao_falhou = True
                elif item_id not in precos_originais:
                    out.erros.append({
                        "item_id": item_id, "operacao": "inflar",
                        "erro": (
                            "não consegui ler o preço atual pra snapshot — não "
                            "inflei (evita ficar sem reversão)"
                        ),
                    })
                    inflacao_falhou = True
                else:
                    try:
                        await atualizar_preco_item(ml, item_id, novo_preco)
                        out.inflado = True
                    except MLItemUpdateError as e:
                        out.erros.append({
                            "item_id": item_id, "operacao": "inflar", "erro": str(e),
                        })
                        inflacao_falhou = True

            if inflacao_pedida and inflacao_falhou:
                out.pulado_inflacao_falhou = True
                if logger is not None:
                    logger.warning(
                        "campaign_add_pulado_inflacao_falhou",
                        item_id=item_id, ml_campaign_id=ml_campaign_id,
                        preco_alvo_inflacao=novo_preco,
                        preco_atual=precos_originais.get(item_id),
                        motivo=(
                            "inflar_precos pediu PUT mas falhou; adicionar sem "
                            "inflar deixaria o SKU na campanha com margem "
                            "quebrada (deal em cima do preço-base baixo)"
                        ),
                    )
                return out

            # ── Fase 2: adição (com retry de fallback em ERROR_CREDIBILITY) ──
            # Usa o deal EFETIVO (já clampado se aplicável; senão = original).
            deal_price = deals_efetivos.get(item_id)
            if item_id in clampados:
                out.clamped = True
            negado_credibility = False

            # Helper de retry pra OfferLockedError (HTTP 423 LockedEntityException).
            # O ML responde 423 imediatamente mas, depois de destravar
            # internamente (alguns segundos), processa a adição assincronamente
            # e o item entra na campanha com o `deal_price` enviado. Logo,
            # retentamos algumas vezes antes de desistir, e quando desistimos
            # NÃO revertemos o preço-base (porque o ML deve adicionar depois).
            async def _post_add(dp: float | None) -> tuple[str, str]:
                """Tenta POST add com retry específico pra 423 Locked.

                Retorna (status, erro_str), status ∈
                {"adicionado","ja_estava","lock_persistente"}. Outros
                MLPromotionError sobem como exception pro caller tratar.
                """
                tentativas = 3
                delay_s = 2.0
                ultimo_erro: str = ""
                for tentativa in range(1, tentativas + 1):
                    try:
                        await adicionar_sku_em_campanha(
                            ml, item_id=item_id, promotion_id=ml_campaign_id,
                            promotion_type="SELLER_CAMPAIGN", deal_price=dp,
                        )
                        return ("adicionado", "")
                    except ItemAlreadyInCampaignError:
                        return ("ja_estava", "")
                    except OfferLockedError as e_lock:
                        ultimo_erro = str(e_lock)
                        if logger is not None:
                            logger.warning(
                                "campaign_add_locked_retry",
                                item_id=item_id, tentativa=tentativa,
                                tentativas=tentativas,
                                ml_campaign_id=ml_campaign_id,
                            )
                        if tentativa < tentativas:
                            await asyncio.sleep(delay_s)
                            continue
                        return ("lock_persistente", ultimo_erro)
                return ("lock_persistente", ultimo_erro)

            try:
                status, _ = await _post_add(deal_price)
                if status == "adicionado":
                    out.adicionado = True
                elif status == "ja_estava":
                    out.ja_estava = True
                else:  # lock_persistente
                    out.pendente_lock = True
                    if logger is not None:
                        logger.warning(
                            "campaign_add_locked_pendente",
                            item_id=item_id, ml_campaign_id=ml_campaign_id,
                            preco_inflado_mantido=True,
                        )
            except MLPromotionError as e:
                fallback = fallback_deal_prices.get(item_id)
                erro_str = str(e)
                is_credibility = (
                    "ERROR_CREDIBILITY_DISCOUNTED_PRICE" in erro_str
                    or "DealPriceInvalidError" in type(e).__name__
                )
                if fallback is not None and is_credibility:
                    try:
                        status2, _ = await _post_add(fallback)
                        if status2 == "adicionado":
                            out.adicionado = True
                            out.fallback_usado = True
                        elif status2 == "ja_estava":
                            out.ja_estava = True
                        else:  # lock_persistente no fallback também
                            out.pendente_lock = True
                    except MLPromotionError as e2:
                        negado_credibility = True
                        out.erros.append({
                            "item_id": item_id, "operacao": "add",
                            "erro": (
                                f"primeiro tentei deal_price={deal_price} (quebra "
                                f"frete grátis), rejeitado; tentei fallback={fallback}"
                                f", também rejeitado: {e2}"
                            ),
                        })
                else:
                    if is_credibility:
                        negado_credibility = True
                    out.erros.append({
                        "item_id": item_id, "operacao": "add", "erro": erro_str,
                    })

            # ── Descarte: item não entrou na campanha ──
            # `pendente_lock` é exceção: o ML deve adicionar assincronamente
            # depois de destravar, então NÃO mexemos no preço (mantém inflado U).
            if not (out.adicionado or out.ja_estava or out.pendente_lock):
                # (a) negado por credibilidade → reprecifica pro preço de 20%
                #     de margem (desinfla); fica FORA da campanha.
                preco_20 = deal_prices.get(item_id)
                if negado_credibility and preco_20 is not None and preco_20 > 0:
                    try:
                        await atualizar_preco_item(ml, item_id, preco_20)
                        out.reprecificado_20pct = True
                        if logger is not None:
                            logger.info(
                                "campaign_descarte_reprecifica_20pct",
                                item_id=item_id, preco_20pct=preco_20,
                                ml_campaign_id=ml_campaign_id,
                            )
                    except MLItemUpdateError as e:
                        out.erros.append({
                            "item_id": item_id, "operacao": "reprecificar_20pct",
                            "erro": (
                                f"negado por credibilidade; falhou ao ajustar pro "
                                f"preço de 20% de margem {preco_20}: {e}"
                            ),
                        })
                # (b) inflado e não reprecificado (ex.: negado por OUTRO motivo)
                #     → volta ao preço original pra não deixar anúncio caro à toa.
                if out.inflado and not out.reprecificado_20pct:
                    preco_orig = precos_originais.get(item_id)
                    if preco_orig is not None:
                        try:
                            await atualizar_preco_item(ml, item_id, preco_orig)
                            out.revertido = True
                            if logger is not None:
                                logger.info(
                                    "campaign_auto_revert",
                                    item_id=item_id, preco_original=preco_orig,
                                    ml_campaign_id=ml_campaign_id,
                                )
                        except MLItemUpdateError as e:
                            out.erros.append({
                                "item_id": item_id, "operacao": "reverter",
                                "erro": (
                                    f"item não entrou na campanha e falhou ao "
                                    f"reverter pro preço original {preco_orig}: {e}"
                                ),
                            })
        return out

    # P1: processa todos os itens em paralelo (concorrência limitada pelo sem).
    outcomes = await asyncio.gather(*[_processar_item(iid) for iid in item_ids])

    # Agrega em ordem estável (ordem de item_ids).
    for out in outcomes:
        if out.inflado:
            res.inflados.append(out.item_id)
        if out.adicionado:
            res.adicionados.append(out.item_id)
        if out.ja_estava:
            res.ja_estavam.append(out.item_id)
        if out.fallback_usado:
            res.fallbacks_usados.append(out.item_id)
        if out.reprecificado_20pct:
            res.reprecificados_20pct.append(out.item_id)
        if out.revertido:
            res.revertidos.append(out.item_id)
        if out.clamped:
            res.clamped.append(out.item_id)
        if out.pulado_ausente:
            res.pulados_ausente.append(out.item_id)
        if out.pulado_por_margem:
            res.pulados_por_margem.append(out.item_id)
        if out.pulado_inflacao_falhou:
            res.pulados_inflacao_falhou.append(out.item_id)
        if out.pendente_lock:
            res.pendentes_lock.append(out.item_id)
        res.erros.extend(out.erros)

    return res


# ─── Fluxo 2-passos: apenas inflação (passo 1 do "Inflar Preços" + "Confirmar") ─

async def aplicar_apenas_inflacao(
    ml: MLClient,
    *,
    inflar_precos: dict[str, float],
    logger: Any = None,
    max_concorrencia: int = MAX_CONCORRENCIA_WRITES,
) -> AplicarInflacaoResult:
    """Aplica APENAS a inflação (PUT /items/{id}), sem POST de campanha.

    Usado quando o usuário quer ver a inflação acontecer ANTES de confirmar a
    adição à campanha (fluxo de 2 botões "Inflar Preços" → "Confirmar"). A
    confirmação chama `aplicar_adicoes_skus_em_campanha` com `inflar_precos={}`
    (= só fase 2: clamp + POST + retry + descarte).

    Não há descarte/reversão aqui: a inflação ainda não tem nada "depois" que
    possa falhar e exigir rollback. Se o PUT falhar, marca como erro e segue —
    o caller decide se retenta ou pula esse item na fase 2.

    Snapshot dos preços atuais é feito em lote pra detectar items que JÁ estão
    no preço inflado (ex.: clicou "Inflar" duas vezes). Esses caem em
    `ja_no_preco` em vez de re-aplicar o PUT.
    """
    res = AplicarInflacaoResult()
    if not inflar_precos:
        return res

    item_ids = list(inflar_precos.keys())
    precos_originais = await _snapshot_precos(ml, item_ids)

    sem = asyncio.Semaphore(max(1, max_concorrencia))

    async def _processar(item_id: str) -> _ItemOutcome:
        out = _ItemOutcome(item_id=item_id)
        novo_preco = inflar_precos.get(item_id)
        if novo_preco is None or novo_preco <= 0:
            out.erros.append({
                "item_id": item_id, "operacao": "inflar",
                "erro": f"preço inválido: {novo_preco}",
            })
            return out

        preco_atual = precos_originais.get(item_id)
        # Já está em U? Pula PUT (idempotência ao re-clicar "Inflar").
        if preco_atual is not None and abs(preco_atual - novo_preco) < 0.01:
            out.inflado = True  # do ponto de vista do estado: está inflado
            return out

        async with sem:
            try:
                await atualizar_preco_item(ml, item_id, novo_preco)
                out.inflado = True
                if logger is not None:
                    logger.info(
                        "campaign_inflar_only_ok", item_id=item_id,
                        preco_anterior=preco_atual, preco_novo=novo_preco,
                    )
            except MLItemUpdateError as e:
                out.erros.append({
                    "item_id": item_id, "operacao": "inflar", "erro": str(e),
                })
                if logger is not None:
                    logger.warning(
                        "campaign_inflar_only_falhou",
                        item_id=item_id, erro=str(e)[:200],
                    )
        return out

    outcomes = await asyncio.gather(*[_processar(iid) for iid in item_ids])

    for out in outcomes:
        # Detecta "já no preço": preço lido bate com novo_preco no snapshot
        preco_atual = precos_originais.get(out.item_id)
        novo_preco = inflar_precos.get(out.item_id)
        if (
            out.inflado and preco_atual is not None and novo_preco is not None
            and abs(preco_atual - novo_preco) < 0.01
        ):
            res.ja_no_preco.append(out.item_id)
        elif out.inflado:
            res.inflados.append(out.item_id)
        res.erros.extend(out.erros)

    return res
