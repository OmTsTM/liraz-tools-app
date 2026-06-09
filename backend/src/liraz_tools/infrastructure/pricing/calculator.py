"""Cálculo de taxas (comissão, tarifa fixa, frete) por anúncio.

Portado do `src/tools/pricing.py` do MCP, adaptado pra:
- Usar o MLClient async em vez do cliente síncrono
- Aceitar FreightCache injetado (por perfil)
- Ser usado em batch paralelo dentro de um único MLClient

LÓGICA IDÊNTICA AO MCP — mesmas constantes, mesma cascata de resolução:
1. Tenta a API /sites/MLB/listing_prices
2. Pra tarifa fixa: usa API se > 0, senão aplica regra drop_off observada
3. Pra frete: tenta shipping_options (com retry built-in no MLClient), em
   falha usa cache (TTL 7 dias)

A regra drop_off foi validada empiricamente contra o painel ML em maio/2026.
"""
from __future__ import annotations

import contextlib
from typing import Any, cast

from liraz_tools.core.logging import get_logger
from liraz_tools.infrastructure.ml.client import MLAPIError, MLClient
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache

logger = get_logger(__name__)


CEP_DESTINO_PADRAO = "01310100"  # MASP, Av. Paulista, SP capital


# ─── Custo fixo do vendedor (itens < R$ 79) ───────────────────────────────
# Validado no painel do ML (mai/2026, perfil LiraZ, 7 itens): o custo fixo é o
# frete de tabela (list_cost recommended) limitado ao TETO abaixo. Abaixo de
# R$ 12,50 o ML cobra 50% do preço.
#   preço 9,00  frete 19,99 → custo 4,50  (50% do preço)
#   preço 20,90 frete  6,55 → custo 6,55  (frete < teto)
#   preço 21,90 frete 43,99 → custo 8,55  (teto)
#   preço 78,90 frete  7,75 → custo 7,75  (frete < teto)
REGRA_DROP_OFF = {
    "limite_preco_baixo": 12.50,
    "percentual_preco_baixo": 0.50,
}
TETO_CUSTO_FIXO = 8.55
"""Teto do custo fixo pra itens entre R$ 12,50 e R$ 79 (painel ML, mai/2026)."""

# Acima de R$ 79 + free_shipping=True → ML divide o frete com o vendedor
# (vendedor paga uma parte). Abaixo de R$ 79, mesmo com free_shipping=True
# configurado, o ML retira automaticamente o subsídio: vendedor NÃO paga
# frete (cliente paga), mas o ML cobra tarifa fixa R$ 6,75. Essa regra
# vale tanto pro preço REAL do anúncio quanto pra preços SIMULADOS
# (busca binária de campanha, reprecificação, etc).
LIMITE_FRETE_GRATIS = 79.00


def _modalidade_pt(listing_type_id: str | None) -> str:
    return {
        "gold_special": "Clássico",
        "gold_pro": "Premium",
        "free": "Grátis",
        "gold_premium": "Premium (legado)",
        "gold": "Ouro",
        "silver": "Prata",
        "bronze": "Bronze",
    }.get(listing_type_id or "", listing_type_id or "Desconhecida")


def _extrair_sku(item: dict[str, Any]) -> str | None:
    """Extrai SKU do item: seller_custom_field → atributo SELLER_SKU → variações."""
    sku = item.get("seller_custom_field")
    if sku:
        return cast("str", sku)
    for attr in item.get("attributes") or []:
        if attr.get("id") == "SELLER_SKU":
            value = attr.get("value_name") or attr.get("value_id")
            if value:
                return cast("str", value)
    # Variações por cor/tamanho
    variations = item.get("variations") or []
    if variations:
        skus = []
        for var in variations:
            for attr in var.get("attributes") or []:
                if attr.get("id") == "SELLER_SKU":
                    val = attr.get("value_name")
                    if val:
                        skus.append(val)
        if skus:
            return " | ".join(skus)
    return None


def _opcao_recomendada(options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Opção que representa o custo logístico do vendedor em /shipping_options.

    Prioriza a opção marcada como `display == "recommended"` com list_cost > 0
    (é a que o ML destaca como envio padrão). Se nenhuma recommended tiver
    list_cost > 0, cai pra primeira opção com list_cost > 0 (comportamento
    legado), pra não perder o valor quando o ML não marca recommended.
    """
    rec = next(
        (
            o
            for o in options
            if o.get("display") == "recommended" and (o.get("list_cost") or 0) > 0
        ),
        None,
    )
    if rec is not None:
        return rec
    return next((o for o in options if (o.get("list_cost") or 0) > 0), None)


def _calcular_custo_fixo(
    preco: float,
    list_cost: float | None,
    fixed_fee_api: float,
    *,
    sem_teto: bool = False,
) -> tuple[float, str]:
    """Custo fixo que o VENDEDOR paga em itens < R$ 79 (comprador paga o frete).

    Validado no painel do ML (mai/2026, perfil LiraZ, 7 itens): o custo fixo é
    o frete de tabela (`list_cost` da opção recommended) LIMITADO a um teto de
    R$ 8,55. Itens leves pagam o próprio frete (ex.: R$ 6,55); itens volumosos
    batem no teto. Abaixo de R$ 12,50 vale 50% do preço.

    `sem_teto=True`: usado quando `list_cost` veio do override do usuário
    (`tarifas_ml.xlsx`) — esse valor JÁ É a tarifa fixa real do painel ML, não
    o frete cru. O teto teórico não deve limitar a fonte de verdade real (ex.:
    item com tarifa fixa real de R$ 8,95 — > R$ 8,55 — não pode ser truncado).

    Retorna (custo_fixo, fonte).
    """
    if preco < REGRA_DROP_OFF["limite_preco_baixo"]:  # < R$ 12,50
        return round(preco * REGRA_DROP_OFF["percentual_preco_baixo"], 2), "regra_preco_baixo"
    if list_cost is not None:
        if sem_teto:
            return round(float(list_cost), 2), "override_xlsx"
        return round(min(list_cost, TETO_CUSTO_FIXO), 2), "shipping_options_teto"
    # Sem /shipping_options (ex.: envio custom): usa fixed_fee da API se houver,
    # senão assume o teto como melhor estimativa.
    if fixed_fee_api > 0:
        return round(float(fixed_fee_api), 2), "api"
    return TETO_CUSTO_FIXO, "teto_fallback"


async def calcular_taxas_anuncio(
    ml: MLClient,
    item_id: str,
    cep_destino: str,
    freight_cache: FreightCache,
    preco_simulado: float | None = None,
    tarifas_override: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Calcula taxas reais de um anúncio na modalidade atual.

    Retorna comissão, tarifa fixa, frete e valor líquido. Tarifa fixa cai pra
    regra drop_off quando a API retorna 0 num produto < R$ 79. Frete usa
    retry built-in do MLClient, e em falha consulta cache (TTL 7 dias).

    Os campos `tarifa_fixa_fonte` e `frete_fonte` indicam a origem de cada valor.

    `preco_simulado` permite calcular as taxas como se o anúncio tivesse esse
    preço — usado pela busca binária da simulação de reprecificação. Não muda
    nada no ML, só recalcula localmente.

    `tarifas_override` é o map `{item_id: {custo_fixo, frete}}` carregado da
    aba 'TarifasML' do xlsx (alimentada pela extensão Chrome de captura do
    painel ML). Quando o item_id está no dict E o regime atual bate (custo_fixo
    quando vendedor paga tarifa, frete quando vendedor paga frete), o valor
    sobrescreve o cálculo teórico — porque o painel é fonte de verdade real.
    """
    cep = (cep_destino or CEP_DESTINO_PADRAO).replace("-", "").strip()

    # 1) Dados do item (com cache na sessão — não muda durante o relatório)
    try:
        item = await ml.get(f"/items/{item_id}", use_cache=True)
    except MLAPIError as e:
        return {"item_id": item_id, "erro": f"Falha ao buscar item: {e}"}

    # Quando o item está em SELLER_CAMPAIGN ativa, `price` pode vir já com
    # desconto aplicado e o preço-base verdadeiro vem em `original_price`.
    # Idem comentário em items_fetcher.py.
    preco_real = item.get("original_price") or item.get("price")
    # Se passou preco_simulado, usamos ele em vez do preço atual do ML
    preco = preco_simulado if preco_simulado is not None else preco_real
    category_id = item.get("category_id")
    listing_type_id = item.get("listing_type_id")
    title = item.get("title")
    status = item.get("status")
    sku = _extrair_sku(item)
    currency_id = item.get("currency_id") or "BRL"
    shipping_block = item.get("shipping") or {}
    free_shipping = shipping_block.get("free_shipping", False)
    shipping_mode = shipping_block.get("mode")
    logistic_type = shipping_block.get("logistic_type")

    if preco is None or category_id is None or listing_type_id is None:
        return {
            "item_id": item_id,
            "erro": "Anúncio sem preço, categoria ou modalidade definidos.",
            "sku": sku,
            "title": title,
        }

    # 2) listing_prices
    params: dict[str, Any] = {
        "price": preco,
        "category_id": category_id,
        "listing_type_id": listing_type_id,
        "currency_id": currency_id,
    }
    if logistic_type:
        params["logistic_type"] = logistic_type
    if shipping_mode:
        params["shipping_mode"] = shipping_mode

    try:
        data = await ml.get("/sites/MLB/listing_prices", params=params)
    except MLAPIError as e:
        return {
            "item_id": item_id,
            "sku": sku,
            "erro": f"Falha em listing_prices: {e}",
        }

    # Resposta vem como dict OU lista (depende do contexto)
    match: dict[str, Any] | None = None
    if isinstance(data, dict) and data.get("listing_type_id"):
        match = data
    elif isinstance(data, list):
        flat = [
            i for sub in data
            for i in (sub if isinstance(sub, list) else [sub])
        ]
        match = next(
            (i for i in flat if i.get("listing_type_id") == listing_type_id),
            None,
        )

    if not match:
        return {
            "item_id": item_id,
            "sku": sku,
            "erro": f"Modalidade {listing_type_id} indisponível",
        }

    sale_fee = match.get("sale_fee_details") or {}
    percentage_fee = sale_fee.get("percentage_fee", 0.0) or 0.0
    fixed_fee_api = sale_fee.get("fixed_fee", 0.0) or 0.0

    comissao_valor = round(preco * (percentage_fee / 100), 2)

    # 3) Frete de tabela (list_cost da opção recommended) via /shipping_options.
    #
    # Esse valor é o eixo do cálculo logístico: é o frete que o ML cobra. Quem
    # paga (e como entra na margem) depende do preço e do frete grátis — ver
    # passo 4. Buscamos pra todo item com logística ME; a API é flaky, então em
    # falha caímos no cache (TTL 7 dias).
    free_shipping_efetivo = free_shipping and preco >= LIMITE_FRETE_GRATIS
    list_cost: float | None = None
    metodo: str | None = None
    custo_fonte = "n/a"  # "shipping_options" | "cache" | "override_xlsx"

    # ── OTIMIZAÇÃO: se tem override do user pra esse item, PULA shipping_options
    # (economia de 1 call ML por item + foge do erro 424 flaky).
    # Deriva list_cost do override do REGIME ATUAL — pro regime oposto (cálculo
    # do degrau no passo3), o fallback é o teto teórico (`_custo_fixo_below_79`
    # com list_cost None retorna o teto).
    override_aplicado = False
    # Quando o override tem AMBOS os valores, expomos cada um por regime
    # (`list_cost_below_79_override`, `list_cost_above_79_override`) pra o
    # `preco_recomendado` calcular CADA RAMO com o número real do painel ML.
    # Sem isso, ao cruzar o degrau de R$ 79 no cálculo, o ramo oposto usava
    # o valor do regime atual e dava P/margem errados.
    list_cost_below_79_override: float | None = None
    list_cost_above_79_override: float | None = None
    if tarifas_override and item_id in tarifas_override:
        ovr = tarifas_override[item_id]
        cf_ovr = float(ovr.get("custo_fixo") or 0.0)
        fr_ovr = float(ovr.get("frete") or 0.0)
        if cf_ovr > 0 or fr_ovr > 0:
            # Deriva list_cost do regime atual do item (compat — vários callers
            # ainda usam só list_cost):
            # - vendedor paga frete (free_shipping_efetivo): list_cost = frete real
            # - vendedor paga tarifa (< R$79): list_cost = custo_fixo real
            if free_shipping_efetivo and fr_ovr > 0:
                list_cost = fr_ovr
            elif preco < LIMITE_FRETE_GRATIS and cf_ovr > 0:
                list_cost = cf_ovr
            # Expõe os 2 valores separados pra callers passo3 (preco_recomendado)
            # decidirem qual usar em cada ramo do degrau.
            if cf_ovr > 0:
                list_cost_below_79_override = cf_ovr
            if fr_ovr > 0:
                list_cost_above_79_override = fr_ovr
            custo_fonte = "override_xlsx"
            metodo = "override_xlsx"
            override_aplicado = True

    if logistic_type and not override_aplicado:
        try:
            ship_data = await ml.get(
                f"/items/{item_id}/shipping_options",
                params={"zip_code": cep},
                use_cache=True,
                max_retries=3,  # 1s, 2s, 4s backoff
                retry_on_status={424, 429, 500, 502, 503, 504},
            )
            options = ship_data.get("options") or []
            escolhida = _opcao_recomendada(options)
            if escolhida is not None:
                list_cost = round(float(escolhida["list_cost"]), 2)
                metodo = escolhida.get("name")
                custo_fonte = "shipping_options"
                # Salva no cache pra próximas falhas (best-effort)
                with contextlib.suppress(Exception):
                    freight_cache.save(item_id, list_cost, metodo, cep)
            else:
                # API respondeu mas sem list_cost > 0 — tenta cache
                cached = freight_cache.get(item_id)
                if cached and cached.valido:
                    list_cost = cached.list_cost
                    metodo = cached.metodo
                    custo_fonte = "cache"
        except MLAPIError:
            # API falhou após retries — tenta cache
            cached = freight_cache.get(item_id)
            if cached and cached.valido:
                list_cost = cached.list_cost
                metodo = cached.metodo
                custo_fonte = "cache"

    # 4) Distribui o custo logístico entre Frete e Tarifa Fixa (custo fixo).
    #
    #   a) frete grátis EFETIVO (free_shipping E preço >= R$ 79): o vendedor
    #      paga o frete → coluna Frete = list_cost; custo fixo = 0.
    #   b) preço < R$ 79 (comprador paga o frete): vendedor não paga frete, mas
    #      paga o CUSTO FIXO do ML (= frete limitado a R$ 8,55; ver
    #      _calcular_custo_fixo) → coluna Tarifa Fixa; Frete = 0.
    #   c) preço >= R$ 79 sem frete grátis: vendedor não paga frete nem custo
    #      fixo (acima de R$ 79 o ML não cobra custo fixo).
    frete_vendedor = 0.0
    frete_fonte = "n/a"
    frete_info: dict[str, Any] = {}

    if free_shipping_efetivo:
        if list_cost is not None:
            frete_vendedor = list_cost
            frete_fonte = custo_fonte
            frete_info = {
                "metodo": metodo,
                "list_cost": list_cost,
                "cep_destino": cep,
                "fonte": custo_fonte,
            }
        else:
            frete_fonte = "indisponivel"
        tarifa_fixa = 0.0
        tarifa_fonte = "zerada_frete_ativo"
    elif preco >= LIMITE_FRETE_GRATIS:
        # >= R$ 79 sem frete grátis: sem frete (comprador paga) e sem custo fixo
        tarifa_fixa = 0.0
        tarifa_fonte = "isento_acima_79"
    else:
        tarifa_fixa, tarifa_fonte = _calcular_custo_fixo(
            preco, list_cost, fixed_fee_api, sem_teto=override_aplicado,
        )

    # ── Override pela aba TarifasML do xlsx ─────────────────────────
    # Quando o vendedor cadastrou o valor REAL visto no painel ML (via
    # extensão de captura), prefere ele sobre o cálculo teórico — porque
    # o ML mudou o modelo em mar/2026 pra tarifa variável por peso/dims
    # e a API não expõe esse valor. O override só se aplica AO REGIME
    # ATUAL (custo_fixo se vendedor paga tarifa; frete se vendedor paga
    # frete). Se o passo3 mudar de regime, o cálculo teórico volta.
    if tarifas_override and item_id in tarifas_override:
        ovr = tarifas_override[item_id]
        cf_ovr = float(ovr.get("custo_fixo") or 0.0)
        fr_ovr = float(ovr.get("frete") or 0.0)
        if free_shipping_efetivo and fr_ovr > 0:
            frete_vendedor = fr_ovr
            frete_fonte = "override_xlsx"
        elif preco < LIMITE_FRETE_GRATIS and cf_ovr > 0:
            tarifa_fixa = cf_ovr
            tarifa_fonte = "override_xlsx"

    valor_liquido = round(preco - comissao_valor - tarifa_fixa - frete_vendedor, 2)

    resultado: dict[str, Any] = {
        "item_id": item_id,
        "sku": sku,
        "title": title,
        "status": status,
        "modalidade": _modalidade_pt(listing_type_id),
        "modalidade_id": listing_type_id,
        "preco": preco,
        "comissao_valor": comissao_valor,
        "comissao_percentual": percentage_fee,
        "tarifa_fixa": tarifa_fixa,
        "tarifa_fixa_fonte": tarifa_fonte,
        "free_shipping": free_shipping,
        "free_shipping_efetivo": free_shipping_efetivo,
        "frete_vendedor": frete_vendedor,
        "frete_fonte": frete_fonte,
        # Frete de tabela CRU (list_cost do shipping_options), independente do
        # preço/regime. Usado pelo cálculo do preço recomendado pra avaliar os
        # DOIS regimes do degrau R$ 79 (custo fixo abaixo / frete acima),
        # já que `tarifa_fixa`/`frete_vendedor` só refletem o regime atual.
        "list_cost": list_cost,
        # Quando o override (tarifas_ml.xlsx) tem ambos os valores, expomos
        # cada um separadamente pra o passo3 (preco_recomendado) calcular o
        # ramo de cima e o ramo de baixo com o número real do painel ML.
        # `None` quando não há override do regime correspondente.
        "list_cost_below_79_override": list_cost_below_79_override,
        "list_cost_above_79_override": list_cost_above_79_override,
        "valor_liquido": valor_liquido,
        "shipping_mode": shipping_mode,
        "logistic_type": logistic_type,
        "category_id": category_id,
    }

    if frete_info:
        resultado["frete_info"] = frete_info

    return resultado
