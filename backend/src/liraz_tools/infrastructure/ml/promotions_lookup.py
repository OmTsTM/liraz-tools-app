"""Mapa de items que ESTÃO RODANDO em promoções no ML, AGORA.

Usado pela simulação de reprecificação (Leva 5.8/5.8.1) pra adicionar colunas
"Em campanha?" e "Nome da campanha" no relatório.

**Critério de "agora"** (Leva 5.8.1):
- Promoção precisa estar em status `started` — pendentes (futuras) e
  finalizadas (passadas) são ignoradas.
- Item dentro da promoção precisa estar em `started` ou `active` —
  candidates (apenas convidados) e programmed (aprovados mas não rodando)
  NÃO contam como "participando agora".

Decisão pensada: o vendedor quer saber, no momento de simular, quais SKUs
estão de fato com desconto vivo no ML. Listar candidatos infla a coluna
com promoções que o vendedor nem aceitou.

Estratégia:
1. `GET /seller-promotions/users/{user_id}` — lista todas as promoções
   do vendedor. Filtra status != started.
2. Pra cada promoção started, `GET /seller-promotions/promotions/{id}/items`
   pra pegar items. Filtra status != started/active.
3. Monta dict `{item_id -> list[nome_promocao]}`.

Custo: 1 + N requests onde N = número de promoções `started`. Pra loja com
3-5 campanhas rodando, são ~4-6 requests totais — independente de quantos
items a loja tem.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from liraz_tools.infrastructure.ml.client import MLClient

logger = structlog.get_logger(__name__)

# Status de PROMOÇÃO considerada "rodando agora"
PROMOCAO_RODANDO = {"started"}

# Filtro EXCLUSION-based pra robustez. O ML retorna vários esquemas de status
# pra items dentro duma promoção, dependendo da versão do anúncio:
#   - Anúncios novos (MLB6xxx): {"status": "started"}
#   - Anúncios legados (MLB4xxx): às vezes {"status": "active"}, às vezes
#     {"status": {"id": "ACTIVE"}}, e podem aparecer em MAIÚSCULA
# Em vez de listar tudo que aceitamos (frágil), listamos só o que excluímos.
#
# Excluídos sempre:
#   - 'candidate': só sugerido pelo ML, seller não aceitou → não está NA campanha
#   - 'finished': já terminou
#   - 'inactive': inativo
ITEM_FORA_DA_CAMPANHA = {"candidate", "finished", "inactive"}

# Items com desconto vivo no ANÚNCIO neste segundo (uso: relatório de margens).
# Aqui sim listamos restritivo — quero ter certeza que o preço atual está
# afetado pelo desconto.
ITEM_DESCONTO_VIVO = {"started", "active"}

# Tipos de promoção do ML que aceitam `deal_price` customizado pelo seller
# no body do POST /seller-promotions/items/{ITEM_ID}. Apenas esses tipos
# são candidatos a destino de "migração inteligente" (Leva 5.9.4.B), porque
# pra eles podemos calcular o deal_price que respeita a margem alvo.
#
# Excluídos (preço fixo ou definido pelo ML):
#   - DOD (Deal of the Day) — desconto-relâmpago, preço definido
#   - LIGHTNING — flash deal, preço definido
#   - MARKETPLACE_CAMPAIGN — co-funded, ML define
#   - SMART — automated co-funded
#   - VOLUME — desconto por quantidade, regra fixa
#   - PRE_NEGOTIATED — negociado individualmente, offer_id fixo
#   - SELLER_CAMPAIGN — apesar de aceitar deal_price no body, na prática
#     o ML aplica FLEXIBLE_PERCENTAGE e ignora. NOSSAS campanhas são esse
#     tipo (a guarda-chuva), então faz sentido excluir como destino.
TIPOS_AJUSTAVEIS = {"DEAL", "PRICE_DISCOUNT"}


def _normalizar_status(raw: Any) -> str:
    """Normaliza o campo status do ML pra string lowercase.

    O ML retorna status em formatos inconsistentes:
    - String direta: "started", "active", "ACTIVE"
    - Objeto: {"id": "started"}, {"id": "ACTIVE"}
    Pra robustez, extraímos a string canônica e baixamos case.
    """
    if raw is None:
        return ""
    if isinstance(raw, dict):
        raw = raw.get("id") or raw.get("status") or ""
    return str(raw).strip().lower()

# Limite por página do endpoint /items (ML permite até 50)
PAGE_SIZE = 50


async def listar_promocoes_do_vendedor(
    ml: MLClient,
    user_id: int,
    *,
    incluir_programadas: bool = False,
) -> list[dict[str, Any]]:
    """Lista promoções do vendedor.

    Endpoint: GET /seller-promotions/users/{user_id}

    Retorna lista de dicts com `id`, `type`, `status`, `name` (quando
    aplicável — SELLER_CAMPAIGN tem nome, outros tipos podem não ter),
    `start_date`, `finish_date`, `sub_type`.

    Por default filtra mantendo SÓ promoções em status `started` — ou seja,
    rodando AGORA. Se `incluir_programadas=True`, inclui também as `pending`
    (programadas mas ainda não começaram) — útil pra detector de migração
    poder preview de oportunidades futuras.
    Vazio é resultado válido (vendedor sem nenhuma campanha rodando).
    """
    try:
        resp = await ml.get(
            f"/seller-promotions/users/{user_id}",
            params={"app_version": "v2"},
        )
    except Exception as e:
        logger.warning(
            "promocoes_list_failed",
            user_id=user_id,
            error=str(e),
        )
        # Não é fatal — segue sem informação de promoções (colunas viram "—")
        return []

    if not isinstance(resp, dict):
        return []
    results = resp.get("results", [])
    if not isinstance(results, list):
        return []

    status_aceitos = set(PROMOCAO_RODANDO)
    if incluir_programadas:
        status_aceitos.add("pending")

    filtradas = [
        p for p in results
        if isinstance(p, dict) and p.get("status") in status_aceitos
    ]
    logger.info(
        "promocoes_listed",
        user_id=user_id,
        total=len(results),
        filtradas=len(filtradas),
        incluir_programadas=incluir_programadas,
    )
    return filtradas


async def listar_items_da_promocao(
    ml: MLClient,
    promotion_id: str,
    promotion_type: str,
    *,
    apenas_desconto_vivo: bool = True,
) -> list[str]:
    """Lista item_ids que participam de uma promoção específica.

    Pagina até 50 por chamada. Faz dedup interno (variações do mesmo
    anúncio aparecem como linhas separadas mas viram 1 item_id).

    Parâmetros:
        apenas_desconto_vivo: se True (default), filtra só items com
            desconto vivo no anúncio (started/active). Uso típico:
            relatório de margens — quero saber se o preço atual já
            está com desconto. Se False, aceita TUDO menos os
            explicitamente fora (candidate, finished, inactive). Uso
            típico: visualizar SKUs duma campanha — quero ver tudo
            que o seller comprometeu, mesmo anúncios legados que
            usam status diferentes.

    Tipos suportados: SELLER_CAMPAIGN, DEAL, PRICE_DISCOUNT, MARKETPLACE_CAMPAIGN,
    DOD, LIGHTNING, VOLUME, PRE_NEGOTIATED, SMART.

    Implementação robusta:
    - Normaliza status (aceita dict ou string, qualquer case) pra string lowercase
    - Em `apenas_desconto_vivo=False`: filtro exclusion-based — aceita tudo
      exceto candidate/finished/inactive. Mais resiliente a status novos do ML
      e a anúncios legados (MLB4xxx) que usam status diferente.
    - Em `apenas_desconto_vivo=True`: só started/active.

    **Dedup**: o ML retorna a mesma `id` múltiplas vezes quando o anúncio tem
    variações (cor, tamanho, etc) — cada variação vira uma linha. Como nossa
    unidade de trabalho é o anúncio (item_id), deduplicamos pra ter contagem
    consistente com o painel do ML, que conta por anúncio.
    """
    items_ordered: list[str] = []
    seen: set[str] = set()
    duplicatas = 0
    total_aceitos = 0
    distribuicao_total: dict[str, int] = {}
    distribuicao_aceitos: dict[str, int] = {}
    # Pra debug: até 2 samples completos por status único encontrado
    samples_por_status: dict[str, list[dict[str, Any]]] = {}
    samples_limite = 2
    offset = 0
    while True:
        try:
            resp = await ml.get(
                f"/seller-promotions/promotions/{promotion_id}/items",
                params={
                    "promotion_type": promotion_type,
                    "app_version": "v2",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
        except Exception as e:
            logger.warning(
                "promocao_items_failed",
                promotion_id=promotion_id,
                promotion_type=promotion_type,
                offset=offset,
                error=str(e),
            )
            break

        if not isinstance(resp, dict):
            break
        page = resp.get("results", [])
        if not isinstance(page, list) or not page:
            break

        for it in page:
            if not isinstance(it, dict):
                continue
            item_id = it.get("id")
            status = _normalizar_status(it.get("status"))
            key = status or "<sem_status>"
            distribuicao_total[key] = distribuicao_total.get(key, 0) + 1

            # Sample completo do item — útil pra debug de status desconhecidos
            if len(samples_por_status.get(key, [])) < samples_limite:
                samples_por_status.setdefault(key, []).append(it)

            if not item_id:
                continue

            # Decide se aceita pelo modo
            if apenas_desconto_vivo:
                aceita = status in ITEM_DESCONTO_VIVO
            else:
                # Exclusion-based: aceita tudo exceto explicitamente fora
                aceita = bool(status) and status not in ITEM_FORA_DA_CAMPANHA

            if not aceita:
                continue

            distribuicao_aceitos[key] = distribuicao_aceitos.get(key, 0) + 1
            total_aceitos += 1
            sid = str(item_id)
            if sid in seen:
                duplicatas += 1
                continue
            seen.add(sid)
            items_ordered.append(sid)

        paging = resp.get("paging", {})
        total = paging.get("total", 0) if isinstance(paging, dict) else 0
        offset += PAGE_SIZE
        if offset >= total:
            break

    logger.info(
        "promocao_items_listed",
        promotion_id=promotion_id,
        modo="apenas_desconto_vivo" if apenas_desconto_vivo else "associados_campanha",
        anuncios_unicos=len(items_ordered),
        linhas_aceitas=total_aceitos,
        duplicatas=duplicatas,
        distribuicao_total=distribuicao_total,
        distribuicao_aceitos=distribuicao_aceitos,
        samples_por_status=samples_por_status,
    )
    return items_ordered


async def montar_mapa_items_em_promocao(
    ml: MLClient, user_id: int,
) -> dict[str, list[str]]:
    """Monta dict {item_id -> [nomes_de_promocoes_em_andamento]}.

    Resposta inclui APENAS items efetivamente participando AGORA — passou
    os dois filtros: promoção em `started` E item em `started`/`active`.

    Custo: 1 request pra listar promoções (filtradas pra rodando) + 1 por
    promoção rodando pra listar items. Pra loja com 3 campanhas rodando,
    são ~4 requests totais — independente de quantos items a loja tem.

    Em caso de falha em uma promoção específica, ela é pulada (log) mas as
    outras continuam. Vazio se a loja não tem nenhuma promoção rodando.

    O "nome" usado é:
    - `name` da promoção (SELLER_CAMPAIGN tem)
    - Senão, fallback pra `{type}` (ex: "DEAL", "LIGHTNING")
    """
    mapa: dict[str, list[str]] = {}

    promocoes = await listar_promocoes_do_vendedor(ml, user_id)
    if not promocoes:
        return mapa

    for promo in promocoes:
        promo_id = promo.get("id")
        promo_type = promo.get("type")
        promo_name = promo.get("name") or promo_type or "(sem nome)"

        if not promo_id or not promo_type:
            continue

        item_ids = await listar_items_da_promocao(ml, promo_id, promo_type)
        for item_id in item_ids:
            if item_id not in mapa:
                mapa[item_id] = []
            # Evita duplicatas (mesma promoção listada 2x improvável, mas defesa)
            if promo_name not in mapa[item_id]:
                mapa[item_id].append(promo_name)

    logger.info(
        "mapa_promocoes_built",
        user_id=user_id,
        promocoes=len(promocoes),
        items_com_promocao=len(mapa),
    )
    return mapa


async def _item_esta_na_promocao(
    ml: MLClient,
    item_id: str,
    promotion_id: str,
    *,
    apenas_desconto_vivo: bool,
) -> tuple[str, bool, str | None]:
    """Consulta as promoções de UM item e diz se ele está na promoção alvo.

    Retorna `(item_id, aceita, status_key)`. `status_key=None` quando o item não
    aparece na promoção alvo (ou a request falhou) — não entra na distribuição de
    status. Compartilhado pela varredura completa (todos os anúncios) e pela
    direcionada (só os SKUs já conhecidos da campanha).
    """
    try:
        # max_retries=3: com concorrência maior, um 429/5xx transitório não pode
        # derrubar o item silenciosamente (cairia fora da campanha). O backoff do
        # MLClient (1s,2s,4s) reabsorve rajadas de rate-limit.
        resp = await ml.get(
            f"/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
            max_retries=3,
        )
    except Exception as e:
        logger.debug("consulta_promo_item_falhou", item_id=item_id, error=str(e))
        return item_id, False, None

    # O endpoint /seller-promotions/items/{id}?app_version=v2 retorna uma LISTA
    # direta no top-level (cada elemento = uma promoção), não um dict com chave
    # "results". Aceita os dois formatos pra robustez (versões antigas davam dict).
    if isinstance(resp, list):
        results = resp
    elif isinstance(resp, dict):
        results = resp.get("results", [])
        if not isinstance(results, list):
            return item_id, False, None
    else:
        return item_id, False, None

    for promo in results:
        if not isinstance(promo, dict):
            continue
        if promo.get("id") != promotion_id:
            continue
        status_norm = _normalizar_status(promo.get("status"))
        if apenas_desconto_vivo:
            aceita = status_norm in ITEM_DESCONTO_VIVO
        else:
            aceita = bool(status_norm) and status_norm not in ITEM_FORA_DA_CAMPANHA
        return item_id, aceita, status_norm or "<sem_status>"
    return item_id, False, None


def _agregar_items_em_promo(
    resultados: list[tuple[str, bool, str | None]],
) -> tuple[list[str], dict[str, int]]:
    """Agrega `(item_id, aceita, status_key)` em `(items_aceitos, distribuicao)`."""
    items_na_promo: list[str] = []
    distribuicao_status: dict[str, int] = {}
    for item_id, aceita, status_key in resultados:
        if status_key is not None:
            distribuicao_status[status_key] = distribuicao_status.get(status_key, 0) + 1
        if aceita:
            items_na_promo.append(item_id)
    return items_na_promo, distribuicao_status


async def listar_items_do_vendedor_em_promocao(
    ml: MLClient,
    user_id: int,
    promotion_id: str,
    *,
    apenas_desconto_vivo: bool = False,
) -> tuple[list[str], dict[str, int]]:
    """**Caminho reverso**: lista TODOS anúncios do vendedor e descobre
    quais estão participando da promoção.

    Necessário porque o endpoint direto
    `GET /seller-promotions/promotions/{id}/items` NÃO retorna anúncios
    legados (MLB4xxx) — só MLB6xxx. Já o `GET /users/{id}/items/search`
    retorna TODOS os anúncios do vendedor, e o
    `GET /seller-promotions/items/{item_id}` retorna todas as promoções
    de um item específico (funciona pra MLB4 e MLB6).

    O custo é alto (N+1 chamadas, onde N = anúncios ativos do vendedor),
    mas é a única forma de capturar os legados ADICIONADOS fora do app. Pra
    só CONFERIR os SKUs já conhecidos, use `confirmar_items_na_promocao`.

    Retorna `(items_em_promo, distribuicao_status)`. Status normalizados
    como em `_normalizar_status`.
    """
    user_items = await _listar_todos_items_ativos_do_vendedor(ml, user_id)
    logger.info(
        "listar_via_reverso_user_items",
        user_id=user_id,
        total_items_ativos=len(user_items),
    )

    # Paraleliza as N consultas por item. A concorrência real é limitada pelo
    # semáforo do MLClient — antes este loop era sequencial (1 item por vez),
    # o que fazia uma loja com ~500 anúncios levar ~2,5min.
    resultados = await asyncio.gather(
        *(
            _item_esta_na_promocao(
                ml, item_id, promotion_id, apenas_desconto_vivo=apenas_desconto_vivo,
            )
            for item_id in user_items
        )
    )
    items_na_promo, distribuicao_status = _agregar_items_em_promo(resultados)

    logger.info(
        "listar_via_reverso_concluido",
        promotion_id=promotion_id,
        items_na_promo=len(items_na_promo),
        distribuicao_status=distribuicao_status,
    )
    return items_na_promo, distribuicao_status


async def confirmar_items_na_promocao(
    ml: MLClient,
    promotion_id: str,
    candidatos: list[str],
    *,
    apenas_desconto_vivo: bool = False,
) -> tuple[list[str], dict[str, int]]:
    """**Caminho direcionado**: dado um conjunto de items que JÁ sabemos estar
    na campanha (os `skus_selecionados` locais), confirma quais continuam nela.

    Consulta só `len(candidatos)` itens (não o catálogo inteiro) — ~5x mais barato
    que a varredura reversa numa loja típica. **Não descobre** items NOVOS
    adicionados direto no painel do ML (pra isso, varredura completa). Pega
    REMOÇÕES (item que saiu da promoção deixa de ser aceito) e mudança de status.

    Retorna `(items_confirmados, distribuicao_status)`.
    """
    if not candidatos:
        return [], {}
    resultados = await asyncio.gather(
        *(
            _item_esta_na_promocao(
                ml, item_id, promotion_id, apenas_desconto_vivo=apenas_desconto_vivo,
            )
            for item_id in candidatos
        )
    )
    confirmados, distribuicao_status = _agregar_items_em_promo(resultados)
    logger.info(
        "confirmar_items_direcionado_concluido",
        promotion_id=promotion_id,
        candidatos=len(candidatos),
        confirmados=len(confirmados),
        distribuicao_status=distribuicao_status,
    )
    return confirmados, distribuicao_status


async def _listar_todos_items_ativos_do_vendedor(
    ml: MLClient, user_id: int,
) -> list[str]:
    """Lista TODOS os item_ids ativos do vendedor via /users/{id}/items/search.

    Inclui MLB4xxx (legados) e MLB6xxx (novos). Pagina até o fim.
    """
    items: list[str] = []
    offset = 0
    while True:
        try:
            resp = await ml.get(
                f"/users/{user_id}/items/search",
                params={
                    "status": "active",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
        except Exception as e:
            logger.warning(
                "user_items_search_failed",
                user_id=user_id,
                offset=offset,
                error=str(e),
            )
            break
        if not isinstance(resp, dict):
            break
        results = resp.get("results", [])
        if not isinstance(results, list):
            break
        # Os "results" são strings (item_ids) — não objetos
        items.extend(str(r) for r in results if r)

        paging = resp.get("paging", {})
        total = paging.get("total", 0) if isinstance(paging, dict) else 0
        offset += PAGE_SIZE
        if offset >= total or not results:
            break

    return items


async def listar_items_da_promocao_completo(
    ml: MLClient,
    user_id: int,
    promotion_id: str,
    promotion_type: str,
    *,
    apenas_desconto_vivo: bool = False,
) -> list[str]:
    """Lista items duma promoção combinando dois caminhos pra cobrir
    MLB4xxx (legados) E MLB6xxx (novos).

    - **Direto** (`/seller-promotions/promotions/{id}/items`) pega
      principalmente MLB6xxx. Rápido.
    - **Reverso** (loop por todos os anúncios + consulta por item) pega
      MLB4xxx que o direto perde. Lento.

    Resultado é a união dos dois conjuntos (deduplicada).
    """
    items_direto = await listar_items_da_promocao(
        ml, promotion_id, promotion_type,
        apenas_desconto_vivo=apenas_desconto_vivo,
    )

    items_reverso, _ = await listar_items_do_vendedor_em_promocao(
        ml, user_id, promotion_id,
        apenas_desconto_vivo=apenas_desconto_vivo,
    )

    # União, mantendo ordem (direto primeiro pelos MLB6, depois MLB4 do reverso)
    visto: set[str] = set()
    combinado: list[str] = []
    for iid in [*items_direto, *items_reverso]:
        if iid not in visto:
            visto.add(iid)
            combinado.append(iid)

    logger.info(
        "listar_completo_concluido",
        promotion_id=promotion_id,
        via_direto=len(items_direto),
        via_reverso=len(items_reverso),
        total_combinado=len(combinado),
        ganho_pelo_reverso=len(combinado) - len(items_direto),
    )
    return combinado


async def listar_items_da_promocao_direcionado(
    ml: MLClient,
    promotion_id: str,
    promotion_type: str,
    candidatos: list[str],
    *,
    apenas_desconto_vivo: bool = False,
) -> list[str]:
    """Versão DIRECIONADA de `listar_items_da_promocao_completo`: não varre o
    catálogo inteiro. Combina:

    - **Direto** (`/seller-promotions/promotions/{id}/items`, 1 chamada barata):
      pega MLB6xxx que entraram na campanha (inclusive adicionados no painel ML).
    - **Confirmação dos conhecidos** (`confirmar_items_na_promocao`): confere os
      `candidatos` (SKUs locais), pegando REMOÇÕES e legados MLB4xxx que o direto
      perde.

    União deduplicada. Custo ~ `1 + len(candidatos)` chamadas, vs `1 + N anúncios`
    da loja na versão completa. **Não descobre** adições legadas (MLB4xxx) feitas
    fora do app — só a varredura completa pega isso.
    """
    items_direto = await listar_items_da_promocao(
        ml, promotion_id, promotion_type,
        apenas_desconto_vivo=apenas_desconto_vivo,
    )
    items_conhecidos, _ = await confirmar_items_na_promocao(
        ml, promotion_id, candidatos,
        apenas_desconto_vivo=apenas_desconto_vivo,
    )

    visto: set[str] = set()
    combinado: list[str] = []
    for iid in [*items_direto, *items_conhecidos]:
        if iid not in visto:
            visto.add(iid)
            combinado.append(iid)

    logger.info(
        "listar_direcionado_concluido",
        promotion_id=promotion_id,
        via_direto=len(items_direto),
        via_conhecidos=len(items_conhecidos),
        total_combinado=len(combinado),
    )
    return combinado


# Status do ML que mapeamos pro status interno do app
# (entidade Campaign aceita: rascunho, agendada, executando, ativa, finalizada,
#  cancelada, falha)
ML_STATUS_TO_LOCAL = {
    "started": "ativa",       # rodando agora
    "pending": "agendada",    # aprovada, vai começar
    "finished": "finalizada", # já terminou
}


async def listar_seller_campaigns_completas(
    ml: MLClient, user_id: int,
) -> list[dict[str, Any]]:
    """Lista TODAS SELLER_CAMPAIGN do vendedor (qualquer status) com
    detalhes pra exibir no grid de campanhas do app.

    Diferente de `listar_promocoes_do_vendedor` que filtra por status,
    esta função traz tudo pra dar visibilidade completa. O caller que
    filtra status ou aplica regras de exibição.

    SÓ traz tipo SELLER_CAMPAIGN — outras (DEAL, DOD, LIGHTNING, etc) são
    criadas pelo ML, vendedor não controla, viraria ruído no grid.

    Retorna dicts com keys padronizadas pro app:
    - `ml_promotion_id`: id da promoção no ML (ex: C-MLB12345)
    - `name`: nome da campanha
    - `ml_status`: status original do ML (started/pending/finished)
    - `start_date_iso`, `finish_date_iso`: datas (string ISO)
    - `sub_type`: ex: FLEXIBLE_PERCENTAGE
    """
    try:
        resp = await ml.get(
            f"/seller-promotions/users/{user_id}",
            params={"app_version": "v2"},
        )
    except Exception as e:
        logger.warning(
            "ml_campaigns_list_failed",
            user_id=user_id,
            error=str(e),
        )
        return []

    if not isinstance(resp, dict):
        return []
    results = resp.get("results", [])
    if not isinstance(results, list):
        return []

    campaigns: list[dict[str, Any]] = []
    for p in results:
        if not isinstance(p, dict):
            continue
        if p.get("type") != "SELLER_CAMPAIGN":
            continue
        campaigns.append({
            "ml_promotion_id": str(p.get("id", "")),
            "name": p.get("name") or "(sem nome)",
            "ml_status": p.get("status") or "",
            "start_date_iso": p.get("start_date") or "",
            "finish_date_iso": p.get("finish_date") or "",
            "sub_type": p.get("sub_type") or "",
        })

    logger.info(
        "ml_campaigns_listed",
        user_id=user_id,
        total=len(campaigns),
    )
    return campaigns


async def buscar_limites_credibilidade(
    ml: MLClient,
    *,
    promotion_id: str,
    promotion_type: str = "SELLER_CAMPAIGN",  # noqa: ARG001  (compat)
    item_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Lê a faixa de credibilidade do ML pros items DA promoção (candidate+started).

    Estratégia: faz `GET /seller-promotions/items/{id}?app_version=v2` em PARALELO
    pra cada item pedido — esse endpoint devolve TODAS as promoções vinculadas ao
    item, com `min_discounted_price`/`max_discounted_price`/`suggested_discounted_price`
    por promoção. Filtramos a entrada da `promotion_id` pedida.

    Por que NÃO usar `GET /seller-promotions/promotions/{id}/items` (mais óbvio)?
    Aquele endpoint tem cap implícito (~50 candidates), então itens além desse
    corte virariam "ausente" indevidamente. O per-item dá a verdade completa
    (~232 calls em vez de 5 — vai paralelo no MLClient, ~20-30s pro lote).

    Retorna `{item_id: {min, max, suggested, original, status}}` SÓ pros items
    que o ML reconhece como vinculados à promoção. Itens AUSENTES (não aparecem
    em nenhuma entrada da resposta) NÃO entram no dict — significa que o ML não
    aceita esse item nessa promoção (regra de elegibilidade, condição, etc.).
    """
    if not item_ids:
        return {}

    async def _fetch_um(item_id: str) -> tuple[str, dict[str, Any] | None]:
        """Busca promoções do item; retorna (item_id, dados_da_promo_pedida ou None)."""
        try:
            resp = await ml.get(
                f"/seller-promotions/items/{item_id}",
                params={"app_version": "v2"},
                max_retries=2,
            )
        except Exception:
            # 404 (item não existe ou sem promoções), 4xx aleatórios, etc.
            # Trata como ausente — clamp pula o item sem queimar PUT/POST.
            return item_id, None

        # Resposta pode vir como list direto OU dict com "results" (depende da versão).
        if isinstance(resp, list):
            entradas = resp
        elif isinstance(resp, dict):
            entradas_raw = resp.get("results")
            entradas = entradas_raw if isinstance(entradas_raw, list) else [resp]
        else:
            return item_id, None

        # Acha a entrada da promoção pedida. O ML às vezes devolve N entradas
        # da mesma promoção (variações) — pega a primeira válida com min/max.
        encontrada: dict[str, Any] | None = None
        # Fallback de limites: pra itens já `started` em SELLER_CAMPAIGN o ML
        # não devolve min/max. Mas a entrada PRICE_DISCOUNT (candidate, sempre
        # presente) tem os limites válidos pra esse item — uso como fallback
        # quando a entrada principal está sem min/max.
        min_fallback: float | None = None
        max_fallback: float | None = None
        for e in entradas:
            if not isinstance(e, dict):
                continue
            if e.get("id") == promotion_id and encontrada is None:
                encontrada = e
            elif e.get("type") == "PRICE_DISCOUNT":
                if min_fallback is None:
                    min_fallback = e.get("min_discounted_price")
                if max_fallback is None:
                    max_fallback = e.get("max_discounted_price")
        if encontrada is not None:
            return item_id, {
                "min": (
                    encontrada.get("min_discounted_price") or min_fallback
                ),
                "max": (
                    encontrada.get("max_discounted_price") or max_fallback
                ),
                "suggested": encontrada.get("suggested_discounted_price"),
                "original": encontrada.get("original_price"),
                # `price` = deal_price ativo na campanha. None pra entradas
                # `candidate` (não ativada) ou se ML não devolver. Necessário
                # pra UI calcular margem real no preço que o cliente paga.
                "price": encontrada.get("price"),
                "status": str(encontrada.get("status") or "").lower(),
            }
        return item_id, None

    # Paralelo com a concorrência do MLClient (semáforo de 12 cobre todas as
    # requests). 232 chamadas em ~20-30s.
    resultados = await asyncio.gather(*[_fetch_um(iid) for iid in item_ids])

    resultado: dict[str, dict[str, Any]] = {
        iid: dados for iid, dados in resultados if dados is not None
    }

    logger.info(
        "limites_credibilidade_lidos",
        promotion_id=promotion_id,
        pedidos=len(item_ids),
        encontrados=len(resultado),
        ausentes=len(item_ids) - len(resultado),
    )
    return resultado

