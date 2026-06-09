"""Lógica de reprecificação portada do MCP (src/tools/reprecificacao.py).

Funções portadas:
- `_buscar_preco_para_margem_async` — busca binária pra achar preço que dê
  margem líquida alvo
- `_aplicar_regra_degrau` — regra de degrau R$ 79 pra anúncios sem frete
  grátis
- `_processar_um_item` — pipeline completo de 1 anúncio (taxas atuais →
  margem → FASE 1 → FASE 2)

Constantes idênticas ao MCP — qualquer mudança aqui muda o comportamento
da simulação. NÃO alterar sem alinhamento com a regra de negócio.

A regra de degrau R$ 79 vem da tarifa fixa do ML: produtos acima de R$ 79
não pagam tarifa fixa (R$ 6,75). Subir o preço de um produto que estava
abaixo de R$ 79 pra acima geraria uma "queda" de margem (porque o ML deixa
de cobrar tarifa fixa, mas a campanha aplica desconto sobre preço alto =
desconto exorbitante). Solução: trava em R$ 78,90 e aceita só se a margem
travada >= 25%.
"""
from __future__ import annotations

from typing import Any

from liraz_tools.core.logging import get_logger
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.pricing.calculator import (
    TETO_CUSTO_FIXO,
    calcular_taxas_anuncio,
)
from liraz_tools.infrastructure.pricing.costs_loader import buscar_custo
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache

logger = get_logger(__name__)


# ─── Constantes idênticas ao MCP ───────────────────────────────────────────

MARGEM_ALVO_AUMENTO = 0.50
"""Margem líquida buscada quando precisa subir preço (FASE 1)."""

LIMITE_MARGEM_AUMENTO = 0.30
"""Se margem atual >= 30%, mantém preço (não busca aumento)."""

MARGEM_ALVO_CAMPANHA = 0.20
"""Margem líquida que o deal_price da campanha precisa render (FASE 2)."""

MARGEM_MINIMA_TRAVADA = 0.25
"""Piso aceitável quando trava preço em R$ 78,90 (regra de degrau)."""

DEGRAU_TARIFA_FIXA = 79.0
"""Preço a partir do qual o ML deixa de cobrar tarifa fixa."""

PRECO_ALTERNATIVO_DEGRAU = 78.90
"""Preço travado quando alvo de 50% cruzaria o degrau R$ 79."""

PRECO_MAX_BUSCA = 50000.0
"""Limite superior da busca binária."""


# ─── Helpers ───────────────────────────────────────────────────────────────


def _margem_liquida(
    taxas: dict[str, Any], preco: float, aliquota_imposto: float,
) -> tuple[float, float]:
    """Retorna (valor_liquido_ml, margem_pct) já descontando imposto.

    Diferente do `valor_liquido` do calculator que NÃO desconta imposto.
    Aqui descontamos pra a busca binária convergir corretamente.
    """
    comissao = taxas.get("comissao_valor", 0) or 0
    tarifa_fixa = taxas.get("tarifa_fixa", 0) or 0
    frete = taxas.get("frete_vendedor", 0) or 0
    imposto = preco * aliquota_imposto
    liquido = preco - comissao - tarifa_fixa - frete - imposto
    margem = liquido / preco if preco > 0 else 0
    return round(liquido, 2), margem


def _custo_fixo_abaixo_79(tarifa_fixa: float, frete: float) -> float:
    """Custo fixo (R$) que o vendedor paga em itens < R$ 79 — sem frete grátis.

    Dois casos, a partir das taxas do anúncio NO PREÇO ATUAL:
    - item já está < R$ 79: `tarifa_fixa` já É o custo fixo → usa direto.
    - item está >= R$ 79 (frete grátis: frete>0, tarifa_fixa=0): abaixo de
      R$ 79 o ML troca o frete pelo custo fixo, que é o frete de tabela
      limitado ao teto (regra do custo fixo validada no painel).
    """
    if tarifa_fixa > 0:
        return tarifa_fixa
    return round(min(frete, TETO_CUSTO_FIXO), 2)


def margem_liquida_fixa(
    preco: float,
    *,
    comissao: float,
    tarifa_fixa: float,
    frete: float,
    custo: float,
    aliquota: float,
) -> float:
    """Margem líquida com taxas fixas, RESPEITANDO o degrau de R$ 79.

    Acima de R$ 79 o vendedor paga o frete (frete grátis) e não paga custo
    fixo. Abaixo de R$ 79 é o contrário: paga o custo fixo e não paga frete.
    Por isso o custo logístico muda conforme o preço cruza o degrau — não dá
    pra fixar só o que valia no preço atual.
        lucro = preco*(1-aliquota) - comissao - custo_logistico(preco) - custo
        margem = lucro / preco
    """
    if preco <= 0:
        return 0.0
    # Acima de R$ 79 o vendedor paga o frete (grátis); abaixo, paga o custo fixo.
    custo_log = (
        frete
        if preco >= DEGRAU_TARIFA_FIXA
        else _custo_fixo_abaixo_79(tarifa_fixa, frete)
    )
    lucro = preco * (1 - aliquota) - comissao - custo_log - custo
    return lucro / preco


def preco_ideal_para_margem(
    *,
    comissao: float,
    tarifa_fixa: float,
    frete: float,
    custo: float,
    aliquota: float,
    margem_alvo: float,
    teto_quebra: float,
    aplicar_quebra: bool = True,
) -> tuple[float | None, bool, float | None]:
    """Preço que atinge `margem_alvo` líquida — fórmula fechada (coluna V da
    planilha de referência LiraZ), substituindo a busca binária, mas tratando
    corretamente o degrau de R$ 79.

    Resolve em dois regimes:
        preco_acima  = (comissao + frete       + custo) / (1 - aliquota - margem)
        preco_abaixo = (comissao + custo_fixo   + custo) / (1 - aliquota - margem)
    onde `custo_fixo` é o que vale abaixo de R$ 79 (ver _custo_fixo_abaixo_79).

    - Se `preco_acima >= 79`, o regime com frete grátis se sustenta. Quebra de
      frete grátis (opcional): se ele cai na faixa [79, teto] e o preço sem
      frete fica < 79, prefere o de baixo (mais competitivo); o de cima vira
      fallback.
    - Se `preco_acima < 79`, o frete grátis NÃO se sustenta naquele preço — o
      vendedor não paga frete abaixo de 79, paga o custo fixo. Usa preco_abaixo
      (corrige o erro da planilha, que mantinha o frete e zerava a tarifa fixa).

    Retorna (preco_ideal, quebra_aplicada, preco_conservador_com_frete).
    Retorna (None, False, None) se a margem alvo é inviável (divisor <= 0).
    """
    divisor = 1.0 - aliquota - margem_alvo
    if divisor <= 0:
        return None, False, None
    custo_fixo_baixo = _custo_fixo_abaixo_79(tarifa_fixa, frete)
    preco_acima = round((comissao + frete + custo) / divisor, 2)
    preco_abaixo = round((comissao + custo_fixo_baixo + custo) / divisor, 2)

    if preco_acima >= DEGRAU_TARIFA_FIXA:
        if (
            aplicar_quebra
            and frete > 0
            and preco_acima <= teto_quebra
            and preco_abaixo < DEGRAU_TARIFA_FIXA
        ):
            return preco_abaixo, True, preco_acima
        return preco_acima, False, None
    return preco_abaixo, False, None


PRECO_LIMITE_ABAIXO_79 = 78.99
"""Preço-teto abaixo do degrau (planilha passo3 usa 78,99, não 78,90)."""


def _custo_fixo_below_79(
    list_cost: float | None, *, sem_teto: bool = False,
) -> float:
    """Custo fixo (R$) que o vendedor paga ABAIXO de R$ 79 = frete de tabela
    limitado ao teto. **Independe do preço atual** — derivado do `list_cost`
    cru (shipping_options). Teto como fallback quando o frete não pôde ser lido.

    `sem_teto=True`: quando `list_cost` vem do override do usuário
    (`tarifas_ml.xlsx`), ele já é a tarifa fixa REAL do painel ML (não o frete
    cru) — o teto teórico não deve limitar a fonte de verdade. Itens com
    tarifa fixa real > R$ 8,55 só são calculados certo passando essa flag.
    """
    if list_cost is None:
        return TETO_CUSTO_FIXO
    if sem_teto:
        return round(float(list_cost), 2)
    return round(min(list_cost, TETO_CUSTO_FIXO), 2)


def _frete_above_79(list_cost: float | None) -> float:
    """Frete (R$) que o VENDEDOR paga A PARTIR de R$ 79 = frete de tabela.

    Acima de R$ 79 o **frete grátis é OBRIGATÓRIO** no ML (programa "frete grátis
    a partir de R$ 79"), então o vendedor sempre paga o frete nesse regime —
    independe do flag `free_shipping` atual do anúncio (que reflete o preço de
    hoje, possivelmente < 79). Por isso NÃO condicionamos a `free_shipping`.
    Esta função só é chamada no regime ≥ 79.
    """
    if list_cost is None:
        return 0.0
    return round(list_cost, 2)


def preco_recomendado(
    *,
    custo: float,
    tarifa_pct: float,
    list_cost: float | None,
    aliquota: float,
    margem_alvo: float,
    margem_minima: float,
    sem_teto_custo_fixo: bool = False,
    list_cost_below_79: float | None = None,
    list_cost_above_79: float | None = None,
) -> float | None:
    """Preço final de venda (P) — escada **Filosofia B** da planilha passo3.

    A comissão é `tarifa_pct` (G%) que ESCALA com o preço. Os custos logísticos
    dos DOIS regimes do degrau R$ 79 são derivados do `list_cost` cru (frete de
    tabela, independente do preço atual) — corrige o bug de itens hoje ≥79 que
    ficavam sem a tarifa fixa ao avaliar o regime < 79:
      - H (custo fixo, vale < 79) = min(list_cost, teto)
      - I (frete, vale ≥ 79)      = list_cost se frete grátis, senão 0

    Quando o vendedor cadastrou tarifas reais no `tarifas_ml.xlsx`, o calculator
    expõe `list_cost_below_79` e `list_cost_above_79` separadamente (cada um
    com o valor REAL do painel ML pro respectivo regime). Quando passados, são
    usados em vez de derivar do `list_cost` cru — evita o erro de cruzar o
    degrau no cálculo e acabar usando o valor do regime errado.

    Escada: (1) `(custo+H)/(1-G-K2-Q2)` se <79 → usa; (2) senão 78,99 se margem
    lá ≥ R2; (3) senão `(custo+I)/(1-G-K2-Q2)` se ≥79; (4) senão
    `MAX(79,(custo+I)/(1-G-K2-R2))`. None se a margem alvo é inviável.
    """
    denom_alvo = 1.0 - tarifa_pct - aliquota - margem_alvo
    if denom_alvo <= 0:
        return None
    tarifa_fixa = (
        round(float(list_cost_below_79), 2)
        if list_cost_below_79 is not None
        else _custo_fixo_below_79(list_cost, sem_teto=sem_teto_custo_fixo)
    )
    frete = (
        round(float(list_cost_above_79), 2)
        if list_cost_above_79 is not None
        else _frete_above_79(list_cost)
    )
    p_fixa_alvo = (custo + tarifa_fixa) / denom_alvo
    if p_fixa_alvo < DEGRAU_TARIFA_FIXA:
        return round(p_fixa_alvo, 2)
    margem_no_limite = (
        PRECO_LIMITE_ABAIXO_79 * (1.0 - tarifa_pct - aliquota)
        - tarifa_fixa
        - custo
    ) / PRECO_LIMITE_ABAIXO_79
    if margem_no_limite >= margem_minima:
        return PRECO_LIMITE_ABAIXO_79
    p_frete_alvo = (custo + frete) / denom_alvo
    if p_frete_alvo >= DEGRAU_TARIFA_FIXA:
        return round(p_frete_alvo, 2)
    denom_min = 1.0 - tarifa_pct - aliquota - margem_minima
    p_frete_min = (
        (custo + frete) / denom_min if denom_min > 0 else DEGRAU_TARIFA_FIXA
    )
    return round(max(DEGRAU_TARIFA_FIXA, p_frete_min), 2)


def margem_liquida_pct(
    preco: float,
    *,
    custo: float,
    tarifa_pct: float,
    list_cost: float | None,
    aliquota: float,
    sem_teto_custo_fixo: bool = False,
    list_cost_below_79: float | None = None,
    list_cost_above_79: float | None = None,
) -> float:
    """Margem líquida real em `preco` — comissão como % (modelo planilha passo3).

    `(P - P*G - logístico(P) - P*K2 - custo) / P`. O custo logístico respeita o
    degrau: abaixo de R$ 79 = custo fixo (min(list_cost, teto)); a partir de
    R$ 79 = frete de tabela (frete grátis obrigatório acima de 79). Nunca os dois.

    `list_cost_below_79` / `list_cost_above_79`: overrides reais do painel ML
    (vindos do `tarifas_ml.xlsx`). Quando passados, são usados em vez de derivar
    do `list_cost` cru — necessário pra que o cálculo de margem em preços que
    NÃO são o preço atual respeite o regime correto (ex.: simular margem em
    um deal que cruza o degrau).
    """
    if preco <= 0:
        return 0.0
    if preco < DEGRAU_TARIFA_FIXA:
        fixo = (
            round(float(list_cost_below_79), 2)
            if list_cost_below_79 is not None
            else _custo_fixo_below_79(list_cost, sem_teto=sem_teto_custo_fixo)
        )
    else:
        fixo = (
            round(float(list_cost_above_79), 2)
            if list_cost_above_79 is not None
            else _frete_above_79(list_cost)
        )
    return (preco - preco * tarifa_pct - fixo - preco * aliquota - custo) / preco


def inflar_para_campanha(
    preco_final: float, *, pct_inflacao: float,
) -> tuple[float, float]:
    """Preço inflado p/ campanha (U) e desconto necessário (V) — planilha passo3.

    Publica `U = P*(1+T2)` no ML e dá `V = 1 - P/U` de desconto pra voltar a P
    (o cliente paga P; a campanha não come margem real).
    Retorna `(preco_inflado, desconto_fracao)`.
    """
    if preco_final <= 0 or pct_inflacao < 0:
        return preco_final, 0.0
    u = round(preco_final * (1.0 + pct_inflacao), 2)
    desconto = 1.0 - preco_final / u if u > 0 else 0.0
    return u, desconto


async def calcular_margem_para_preco(
    ml: MLClient,
    item_id: str,
    preco_simulado: float,
    custo: float,
    aliquota: float,
    cep: str,
    freight_cache: FreightCache,
    tarifas_override: dict[str, dict[str, float]] | None = None,
) -> tuple[float, float] | tuple[None, None]:
    """Calcula margem líquida resultante se o item for vendido a `preco_simulado`.

    Diferente de `buscar_preco_para_margem` que faz busca binária pra achar
    o preço dado uma margem alvo, aqui é o inverso: dado um preço (ex:
    min_discounted_price duma promoção ML), retorna a margem que sobra
    depois de comissão, tarifa fixa, frete, imposto e custo.

    Retorna `(margem_pct, liquido_brl)` ou `(None, None)` se falhou (ex:
    erro buscando taxas do ML).

    Usado por: detector de oportunidades de migração (Leva 5.9.4.B).
    """
    taxas = await calcular_taxas_anuncio(
        ml=ml,
        item_id=item_id,
        cep_destino=cep,
        freight_cache=freight_cache,
        preco_simulado=preco_simulado,
        tarifas_override=tarifas_override,
    )
    if "erro" in taxas:
        logger.warning(
            "calcular_margem_falhou",
            item_id=item_id,
            preco=preco_simulado,
            erro=taxas["erro"],
        )
        return None, None

    liq_ml, _ = _margem_liquida(taxas, preco_simulado, aliquota)
    liq_final = liq_ml - custo
    margem = liq_final / preco_simulado if preco_simulado > 0 else 0
    return margem, round(liq_final, 2)


def _extrair_taxas_para_planilha(taxas: dict[str, Any]) -> dict[str, Any]:
    """Reformata pro layout do XLSX (Clássico/Premium em colunas separadas)."""
    modalidade = taxas.get("modalidade", "")
    com_val = taxas.get("comissao_valor", 0) or 0
    return {
        "preco": taxas.get("preco", 0),
        "tarifa_classico": com_val if modalidade == "Clássico" else 0,
        "tarifa_premium": com_val if modalidade == "Premium" else 0,
        "percentual_tarifa": taxas.get("comissao_percentual", 0) or 0,
        "tarifa_fixa": taxas.get("tarifa_fixa", 0) or 0,
        "frete": taxas.get("frete_vendedor", 0) or 0,
    }


# ─── Busca binária ─────────────────────────────────────────────────────────


async def buscar_preco_para_margem(
    ml: MLClient,
    item_id: str,
    custo: float,
    margem_alvo: float,
    aliquota: float,
    cep: str,
    freight_cache: FreightCache,
    preco_min: float = 0.50,
    preco_max: float = PRECO_MAX_BUSCA,
    iteracoes_max: int = 25,
    tarifas_override: dict[str, dict[str, float]] | None = None,
) -> tuple[float | None, str | None]:
    """Busca binária pra achar preço onde margem líquida == margem_alvo.

    Retorna (preco_encontrado, erro). Se preco é None, erro descreve o motivo.
    Tolerância: 0.001 (0.1% de margem). Convergência típica: 15-20 iterações.
    """
    lo, hi = preco_min, preco_max
    melhor: float | None = None
    tolerancia = 0.001

    for _ in range(iteracoes_max):
        meio = round((lo + hi) / 2, 2)
        taxas = await calcular_taxas_anuncio(
            ml=ml,
            item_id=item_id,
            cep_destino=cep,
            freight_cache=freight_cache,
            preco_simulado=meio,
            tarifas_override=tarifas_override,
        )
        if "erro" in taxas:
            return None, taxas["erro"]

        liq_ml, _ = _margem_liquida(taxas, meio, aliquota)
        liq_final = liq_ml - custo
        margem = liq_final / meio if meio > 0 else 0

        if abs(margem - margem_alvo) < tolerancia:
            return meio, None

        if margem < margem_alvo:
            lo = meio
        else:
            hi = meio
            melhor = meio

    if melhor is not None:
        return melhor, None
    return None, f"não convergiu em {iteracoes_max} iter"


# ─── Regra do degrau R$ 79 ─────────────────────────────────────────────────


async def aplicar_regra_degrau(
    ml: MLClient,
    item_id: str,
    preco_candidato: float,
    tinha_frete_gratis: bool,
    custo: float,
    aliquota: float,
    cep: str,
    freight_cache: FreightCache,
    margem_atual_pct: float,
    limite_margem_aumento: float = LIMITE_MARGEM_AUMENTO,
    margem_minima_travada: float = MARGEM_MINIMA_TRAVADA,
    tarifas_override: dict[str, dict[str, float]] | None = None,
) -> tuple[float, str, str, dict[str, Any] | None]:
    """Aplica regra de degrau R$ 79.

    Lógica:
    - Com frete grátis: sobe livremente (custo do frete já está embutido)
    - Sem frete grátis + alvo NÃO cruza R$ 79: sobe normal
    - Sem frete grátis + alvo CRUZA R$ 79: trava em R$ 78,90
        - Margem travada >= `margem_minima_travada`: aceita
        - Margem travada < `margem_minima_travada`: exceção (revisar manualmente)

    Retorna (preco_final, fase1_acao, fase1_motivo, excecao_dict).
    Se excecao_dict não for None, item vai pra lista de exceções.
    """
    # Caso 1: frete grátis OU alvo abaixo do degrau
    if tinha_frete_gratis or preco_candidato <= DEGRAU_TARIFA_FIXA:
        motivo = (
            "frete grátis — sobe livremente"
            if tinha_frete_gratis
            else f"preço alvo R$ {preco_candidato:.2f} <= R$ 79"
        )
        return (
            preco_candidato,
            "preco_aumentado",
            (
                f"margem atual {margem_atual_pct * 100:.1f}% < "
                f"{limite_margem_aumento * 100:.0f}%; {motivo}"
            ),
            None,
        )

    # Caso 2: sem frete grátis E alvo cruzaria R$ 79 → testa travar em 78.90
    taxas_travado = await calcular_taxas_anuncio(
        ml=ml,
        item_id=item_id,
        cep_destino=cep,
        freight_cache=freight_cache,
        preco_simulado=PRECO_ALTERNATIVO_DEGRAU,
        tarifas_override=tarifas_override,
    )
    if "erro" in taxas_travado:
        return (
            preco_candidato,
            "preco_aumentado",
            (
                f"falha ao testar trava em R$ 78,90 — voltou ao alvo: "
                f"{taxas_travado['erro']}"
            ),
            None,
        )

    liq_ml_travado, _ = _margem_liquida(
        taxas_travado, PRECO_ALTERNATIVO_DEGRAU, aliquota,
    )
    liq_final_travado = liq_ml_travado - custo
    margem_travada = (
        liq_final_travado / PRECO_ALTERNATIVO_DEGRAU
        if PRECO_ALTERNATIVO_DEGRAU > 0
        else 0
    )

    if margem_travada >= margem_minima_travada:
        return (
            PRECO_ALTERNATIVO_DEGRAU,
            "preco_travado_abaixo_79",
            (
                f"margem atual {margem_atual_pct * 100:.1f}% < "
                f"{limite_margem_aumento * 100:.0f}%; "
                f"travou em R$ 78,90 (alvo R$ {preco_candidato:.2f} > R$ 79 "
                f"sem frete grátis); margem travada "
                f"{margem_travada * 100:.1f}% >= "
                f"{margem_minima_travada * 100:.0f}%"
            ),
            None,
        )

    # Margem travada < piso → exceção pra revisão manual
    return (
        preco_candidato,
        "preco_aumentado",
        "",
        {
            "tipo": "nao_alcanca_25pct_abaixo_79",
            "motivo": (
                f"travar preço em R$ 78,90 rende apenas "
                f"{margem_travada * 100:.1f}% líquido, abaixo do piso de "
                f"{margem_minima_travada * 100:.0f}%. Subir o preço pra "
                f"atingir {limite_margem_aumento * 100:.0f}%+ cruzaria R$ 79 "
                f"(alvo R$ {preco_candidato:.2f}) e geraria desconto "
                f"exorbitante. Revisar manualmente: ou aceitar margem baixa, "
                f"ou descontinuar."
            ),
        },
    )


# ─── Processamento de 1 item (pipeline completo) ───────────────────────────


async def processar_um_item(
    ml: MLClient,
    item_id: str,
    custos: dict[str, float],
    custos_manuais: dict[str, float] | None,
    aliquota: float,
    cep: str,
    freight_cache: FreightCache,
    margem_alvo_campanha: float = MARGEM_ALVO_CAMPANHA,
    margem_minima: float = 0.15,
    pct_inflacao: float = 0.20,
    promocoes_do_item: list[str] | None = None,
    tarifas_override: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Pipeline (modelo planilha passo3): taxas atuais → P (Q2) → campanha (U/V).

    - `margem_alvo_campanha` (Q2): margem do PREÇO FINAL DE VENDA P.
    - `margem_minima` (R2): piso inviolável (escada Filosofia B).
    - `pct_inflacao` (T2): publica U = P*(1+T2) e desconta de volta a P.

    Comissão é G% (escala com o preço); H/I (tarifa fixa/frete) seguem
    derivados da API. Retorna `tipo_resultado` = "simulacao" ou "excecao".
    """
    try:
        # 1) Taxas atuais (preço real do ML)
        atual = await calcular_taxas_anuncio(
            ml=ml, item_id=item_id, cep_destino=cep, freight_cache=freight_cache,
            tarifas_override=tarifas_override,
        )
        if "erro" in atual:
            return {
                "tipo_resultado": "excecao", "item_id": item_id,
                "tipo": "erro_taxas_atuais", "detalhe": atual["erro"],
            }

        sku = atual.get("sku")
        preco_atual = atual["preco"]

        # 2) Anúncios Grátis não entram em SELLER_CAMPAIGN
        if atual.get("modalidade_id") == "free":
            return {
                "tipo_resultado": "excecao", "item_id": item_id, "sku": sku,
                "titulo": atual.get("title"), "tipo": "modalidade_free",
                "motivo": "Anúncios Grátis não podem entrar em SELLER_CAMPAIGN",
            }

        # 3) Custo via cascata
        custo, fonte_custo = buscar_custo(sku, item_id, custos, custos_manuais)
        if custo is None or custo <= 0:
            return {
                "tipo_resultado": "excecao", "item_id": item_id, "sku": sku,
                "titulo": atual.get("title"), "tipo": "sem_custo_cadastrado",
                "motivo": (
                    "SKU sem custo válido na planilha nem em overrides manuais"
                ),
            }

        # G (tarifa %) escala com o preço; custo logístico vem do list_cost cru
        # (independente do preço atual) — avalia os dois regimes do degrau R$79.
        comissao_pct = (atual.get("comissao_percentual", 0.0) or 0.0) / 100.0
        list_cost = atual.get("list_cost")
        # Overrides reais do painel ML por regime (vindos do tarifas_ml.xlsx).
        # Quando presentes, cada ramo do degrau usa o valor REAL em vez de
        # derivar do list_cost cru — evita o erro de cruzar R$ 79 no cálculo
        # e usar o valor do regime errado. Idem flag `sem_teto_custo_fixo`:
        # tarifa fixa real do painel pode ser > R$ 8,55 (teto teórico).
        lc_below = atual.get("list_cost_below_79_override")
        lc_above = atual.get("list_cost_above_79_override")
        eh_override = atual.get("tarifa_fixa_fonte") == "override_xlsx"

        # 4) Margem atual (diagnóstico)
        margem_liq_atual = margem_liquida_pct(
            preco_atual, custo=custo, tarifa_pct=comissao_pct,
            list_cost=list_cost, aliquota=aliquota,
            sem_teto_custo_fixo=eh_override,
            list_cost_below_79=lc_below,
            list_cost_above_79=lc_above,
        )
        liq_final_atual = margem_liq_atual * preco_atual

        # 5) Preço final de venda P (Q2, Filosofia B + piso R2)
        preco_novo = preco_recomendado(
            custo=custo, tarifa_pct=comissao_pct, list_cost=list_cost,
            aliquota=aliquota,
            margem_alvo=margem_alvo_campanha, margem_minima=margem_minima,
            sem_teto_custo_fixo=eh_override,
            list_cost_below_79=lc_below,
            list_cost_above_79=lc_above,
        )
        if preco_novo is None:
            return {
                "tipo_resultado": "excecao", "item_id": item_id, "sku": sku,
                "titulo": atual.get("title"), "tipo": "margem_inviavel",
                "motivo": (
                    f"margem alvo {margem_alvo_campanha:.0%} inviável com "
                    f"alíquota {aliquota:.0%} + tarifa {comissao_pct:.0%}"
                ),
            }
        margem_p = margem_liquida_pct(
            preco_novo, custo=custo, tarifa_pct=comissao_pct,
            list_cost=list_cost, aliquota=aliquota,
            sem_teto_custo_fixo=eh_override,
            list_cost_below_79=lc_below,
            list_cost_above_79=lc_above,
        )
        if preco_novo > preco_atual:
            fase1_acao = "subir"
        elif preco_novo < preco_atual:
            fase1_acao = "baixar"
        else:
            fase1_acao = "mantido"
        fase1_motivo = (
            f"P pra margem {margem_alvo_campanha:.0%} (real {margem_p * 100:.1f}%)"
        )

        # 6) Campanha: publica U = P*(1+T2), desconta de volta a P
        preco_inflado, desconto_frac = inflar_para_campanha(
            preco_novo, pct_inflacao=pct_inflacao,
        )
        deal_price = preco_novo
        desconto_pct = desconto_frac * 100
        margem_inflado = margem_liquida_pct(
            preco_inflado, custo=custo, tarifa_pct=comissao_pct,
            list_cost=list_cost, aliquota=aliquota,
            sem_teto_custo_fixo=eh_override,
            list_cost_below_79=lc_below,
            list_cost_above_79=lc_above,
        )
        liq_final_deal = margem_p * preco_novo  # lucro R$ no preço final P (Y)

        # 7) Taxas detalhadas pra planilha (atual / no preço P / no inflado U)
        taxas_novo = await calcular_taxas_anuncio(
            ml=ml, item_id=item_id, cep_destino=cep,
            freight_cache=freight_cache, preco_simulado=preco_novo,
            tarifas_override=tarifas_override,
        )
        taxas_inflado = await calcular_taxas_anuncio(
            ml=ml, item_id=item_id, cep_destino=cep,
            freight_cache=freight_cache, preco_simulado=preco_inflado,
            tarifas_override=tarifas_override,
        )

        return {
            "tipo_resultado": "simulacao",
            "item_id": item_id,
            "sku": sku,
            "titulo": atual.get("title"),
            "modalidade": atual.get("modalidade"),
            "custo": custo,
            "fonte_custo": fonte_custo,
            "taxas_atual": _extrair_taxas_para_planilha(atual),
            "taxas_novo": _extrair_taxas_para_planilha(taxas_novo),
            "taxas_inflado": _extrair_taxas_para_planilha(taxas_inflado),
            "margem_atual_pct": round(margem_liq_atual * 100, 2),
            "liq_final_atual": round(liq_final_atual, 2),
            "fase1_acao": fase1_acao,
            "fase1_motivo": fase1_motivo,
            "preco_novo": round(preco_novo, 2),
            "preco_inflado": round(preco_inflado, 2),
            "deal_price": round(deal_price, 2),
            "desconto_pct": round(desconto_pct, 2),
            "margem_inflado_pct": round(margem_inflado * 100, 2),
            "liq_final_deal_projetado": round(liq_final_deal, 2),
            "margem_campanha_pct": round(margem_p * 100, 2),
            # Leva 5.8: info de campanhas em que o item já está
            "em_campanha": bool(promocoes_do_item),
            "nomes_campanha": list(promocoes_do_item or []),
        }

    except Exception as e:
        logger.exception("processar_um_item_erro", item_id=item_id)
        return {
            "tipo_resultado": "excecao",
            "item_id": item_id,
            "tipo": "erro_inesperado",
            "detalhe": str(e),
        }
