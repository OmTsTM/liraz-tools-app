"""Detecção de oportunidades de migração SKU → campanha ML (Leva 5.9.4.B).

Pra cada SKU duma campanha guarda-chuva nossa (origem='local'), avalia se há
campanhas no ML rodando agora (ou programadas) onde o SKU é `candidate` e:

1. A campanha permite ajuste de `deal_price` (tipos em TIPOS_AJUSTAVEIS)
2. Existe um preço dentro do range `[min_discounted_price, max_discounted_price]`
   do ML onde a margem líquida resultante cai dentro da faixa
   `[margem_min, margem_max]` da campanha guarda-chuva

SKUs que se encaixam viram oportunidades de migração. Quem não, fica na
guarda-chuva.

Quando a campanha ML tem limite (categoria, quantidade), ranqueia por
`(margem_pct DESC, sold_quantity DESC)` e pega top N.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.migration.detector_cache import get_detector_cache
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotions_lookup import (
    PAGE_SIZE,
    TIPOS_AJUSTAVEIS,
    _normalizar_status,
    listar_promocoes_do_vendedor,
)
from liraz_tools.infrastructure.pricing.costs_loader import (
    buscar_custo,
    carregar_custos,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.repricing_calculator import (
    buscar_preco_para_margem,
    calcular_margem_para_preco,
)
from liraz_tools.infrastructure.repositories.descarte_migracao_cache_repository import (
    DescarteCacheEntry,
)

if TYPE_CHECKING:
    from liraz_tools.infrastructure.repositories.campaign_repository import (
        CampaignRepository,
    )
    from liraz_tools.infrastructure.repositories.descarte_migracao_cache_repository import (
        DescarteMigracaoCacheRepository,
    )
    from liraz_tools.infrastructure.repositories.migracao_executada_repository import (
        MigracaoExecutadaRepository,
    )
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )
    from liraz_tools.infrastructure.repositories.profile_repository import (
        SQLAlchemyProfileRepository,
    )


logger = get_logger(__name__)


@dataclass
class OportunidadeMigracao:
    """Uma oportunidade de migrar um SKU pra campanha ML.

    Atributos:
        item_id: MLB do anúncio (ex: MLB6694476520)
        sku: SKU interno (ex: CB5037-12) — pode ser None se anúncio não tem
        titulo: título do anúncio
        sold_quantity: vendas acumuladas no ML (proxy de demanda)

        campanha_origem_id: UUID local da guarda-chuva atual
        campanha_destino_ml_id: promotion_id do ML (ex: P-MLB1806019)
        campanha_destino_ml_nome: nome amigável (quando disponível)
        campanha_destino_ml_tipo: DEAL, PRICE_DISCOUNT, etc
        campanha_destino_ml_status: started ou pending

        deal_price_sugerido: preço promocional ideal (margem mais alta
            dentro do range que ainda fica dentro da faixa aceitável)
        preco_atual: preço sem desconto no ML
        margem_pct_pos_migracao: margem líquida % se migrar
        margem_brl_pos_migracao: lucro R$ por venda se migrar
        margem_pct_atual: margem líquida % na guarda-chuva agora

        pode_migrar_agora: True só se campanha destino está started
            (pra programmed, é só preview)
    """

    item_id: str
    sku: str | None
    titulo: str | None
    sold_quantity: int

    campanha_origem_id: UUID
    campanha_destino_ml_id: str
    campanha_destino_ml_nome: str | None
    campanha_destino_ml_tipo: str
    campanha_destino_ml_status: str

    deal_price_sugerido: float
    preco_atual: float
    margem_pct_pos_migracao: float
    margem_brl_pos_migracao: float
    margem_pct_atual: float

    pode_migrar_agora: bool


class DetectarOportunidadesMigracaoUseCase:
    """Detecta SKUs duma campanha guarda-chuva que valem a pena migrar pra ML.

    Algoritmo (alto nível):
    1. Lista promoções do ML do vendedor (started + opcionalmente pending)
    2. Filtra só tipos ajustáveis (TIPOS_AJUSTAVEIS)
    3. Pra cada promoção, lista items participantes/candidate
    4. Cruza com SKUs da campanha guarda-chuva: pra cada SKU que aparece
       como candidate em alguma promoção ajustável:
       a. Calcula deal_price ideal usando busca binária pelo topo da faixa
          de margem (margem_max) — se cair dentro do range ML, ótimo
       b. Se margem_max não consegue (preço ficaria acima do max permitido),
          tenta margem_min — se cair dentro, registra oportunidade no
          melhor preço possível
       c. Se ainda assim não cabe, SKU não tem oportunidade nessa promoção
    5. Pra cada (SKU → promoção destino), gera um OportunidadeMigracao
    6. Quando há limite de quantidade, ranqueia por (margem%, sold_qty)
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        campaign_repo: CampaignRepository,
        creds_repo: PerProfileCredentialsRepository,
        *,
        descarte_cache_repo: DescarteMigracaoCacheRepository | None = None,
        historico_migracoes_repo: MigracaoExecutadaRepository | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._creds_repo = creds_repo
        # Opcional — quando passado, salva descartes e pula avaliações
        # repetidas em runs subsequentes (opt 4)
        self._descarte_cache_repo = descarte_cache_repo
        # Opcional — quando passado, filtra oportunidades cujos pares
        # (item_id, destino_ml_id) já tenham sido migrados com sucesso
        # nos últimos N dias (default 7). Evita sugestões fantasmas após
        # execução bem-sucedida (Leva 5.9.4 — fix bug duplicação).
        self._historico_repo = historico_migracoes_repo

    async def execute(
        self,
        profile_id: UUID,
        campaign_origem_id: UUID,
        *,
        incluir_programadas: bool = True,
        apenas_skus: set[str] | None = None,
        usar_cache: bool = True,
        ignorar_historico: bool = False,
        ttl_historico_dias: int = 7,
    ) -> list[OportunidadeMigracao]:
        """Detecta oportunidades de migração.

        Args:
            apenas_skus: se fornecido, AVALIA SÓ esses item_ids da guarda-chuva.
                Aceita IDs MLB diretos. Reduz drasticamente o tempo quando
                você quer rodar 1-N SKUs específicos. (opt 1)
            usar_cache: se True, consulta cache em memória de detecções
                recentes antes de re-executar (TTL 10min). (opt 2)
            ignorar_historico: se True, desativa o filtro por histórico de
                migrações. Útil quando o user removeu o item manualmente no
                painel ML e quer re-detectar imediatamente sem esperar TTL.
            ttl_historico_dias: janela do histórico que conta como "já
                migrado" (default 7 dias). Pares fora desse prazo voltam
                a ser sugeridos normalmente.
        """
        # ─── opt 2: cache hit check ─────────────────────────────────────
        cache = get_detector_cache() if usar_cache else None

        # 1) Carrega campanha guarda-chuva e perfil
        campaign = await self._campaign_repo.get_by_id(
            profile_id, campaign_origem_id,
        )
        # Aceitamos tanto campanhas locais (criadas no app) quanto espelhadas
        # do ML (origem='ml') como ponto de partida. O que define se um SKU é
        # candidato a migrar é a PROMOÇÃO DESTINO ser de tipo ajustável, não
        # o tipo da campanha origem.

        skus_origem = campaign.skus_selecionados or []
        if not skus_origem:
            logger.info(
                "deteccao_skip_sem_skus",
                campaign_id=str(campaign_origem_id),
            )
            return []

        # opt 1: filtra antes de qualquer trabalho pesado
        if apenas_skus:
            skus_origem = [s for s in skus_origem if s in apenas_skus]
            if not skus_origem:
                logger.info(
                    "deteccao_skip_apenas_skus_vazio_intersecao",
                    campaign_id=str(campaign_origem_id),
                    apenas_skus=list(apenas_skus)[:10],
                )
                return []

        skus_origem_frozen = frozenset(skus_origem)

        # opt 2: cache lookup
        if cache is not None:
            cached = cache.get(profile_id, campaign_origem_id, skus_origem_frozen)
            if cached is not None:
                return cached

        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            logger.info(
                "deteccao_skip_loja_desconectada",
                profile_id=str(profile_id),
            )
            return []

        # 2) Resolve faixa de margem (campanha sobrescreve perfil, perfil default)
        margem_min = (
            campaign.margem_min_migracao
            if campaign.margem_min_migracao is not None
            else profile.config.margem_min_migracao
        )
        margem_max = (
            campaign.margem_max_migracao
            if campaign.margem_max_migracao is not None
            else profile.config.margem_max_migracao
        )

        # 3) Prepara dependências de pricing
        if not profile.config.custos_xlsx_path:
            logger.info(
                "deteccao_skip_sem_custos_xlsx",
                profile_id=str(profile_id),
            )
            return []
        try:
            custos = carregar_custos(profile.config.custos_xlsx_path)
        except Exception as e:
            logger.warning(
                "deteccao_falha_carregar_custos",
                profile_id=str(profile_id),
                error=str(e),
            )
            return []
        freight_cache = FreightCache(profile.slug)
        cep = profile.config.cep_destino
        aliquota = profile.config.aliquota_imposto

        # 4) Loop por promoção ML — coleta candidatas
        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: TokenSet) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        oportunidades: list[OportunidadeMigracao] = []
        # opt 4: descartes pra invalidar/pular avaliações repetidas
        descartes_para_salvar: list[DescarteCacheEntry] = []
        descartes_cacheados: set[tuple[str, str]] = set()
        if self._descarte_cache_repo is not None:
            descartes_cacheados = await self._descarte_cache_repo.listar_validos(
                profile_id,
            )
            if descartes_cacheados:
                logger.info(
                    "deteccao_descartes_cache_carregado",
                    profile_id=str(profile_id),
                    qtd_descartes=len(descartes_cacheados),
                )

        # Fix bug duplicação: carrega pares (item_id, destino_ml_id) já
        # migrados com sucesso nos últimos `ttl_historico_dias`. Esses
        # NÃO devem aparecer como sugestão de novo — o ML às vezes retorna
        # o item como `candidate` mesmo após o POST de adição (eventual
        # consistency, variations parcialmente aceitas, etc).
        pares_ja_migrados: set[tuple[str, str]] = set()
        if self._historico_repo is not None and not ignorar_historico:
            pares_ja_migrados = await self._historico_repo.listar_pares_migrados_recentemente(
                profile_id,
                ttl_dias=ttl_historico_dias,
            )
            if pares_ja_migrados:
                logger.info(
                    "deteccao_historico_carregado",
                    profile_id=str(profile_id),
                    qtd_pares_ja_migrados=len(pares_ja_migrados),
                    ttl_dias=ttl_historico_dias,
                )

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            promocoes = await listar_promocoes_do_vendedor(
                ml, profile.ml_user_id,
                incluir_programadas=incluir_programadas,
            )
            promocoes_ajustaveis = [
                p for p in promocoes if p.get("type") in TIPOS_AJUSTAVEIS
            ]
            logger.info(
                "deteccao_promocoes_filtradas",
                profile_id=str(profile_id),
                total=len(promocoes),
                ajustaveis=len(promocoes_ajustaveis),
            )

            skus_origem_set = set(skus_origem)

            # opt 3: paraleliza chamadas ML, limitado por semáforo pra não
            # estourar rate limit do ML
            sem = asyncio.Semaphore(8)

            async def avaliar_com_sem(
                item_data: dict[str, Any],
                promo: dict[str, Any],
            ) -> tuple[OportunidadeMigracao | None, str | None]:
                """Wrapper com semáforo. Retorna (oport, motivo_descarte)."""
                async with sem:
                    promo_id_local = promo["id"]
                    promo_type_local = promo["type"]
                    item_id_local = item_data["item_id"]
                    # opt 4: pula se já está no cache de descartes
                    if (item_id_local, promo_id_local) in descartes_cacheados:
                        return None, "cache_descarte"
                    oport = await _avaliar_sku_em_promocao(
                        ml=ml,
                        item_id=item_id_local,
                        item_data=item_data,
                        custos=custos,
                        aliquota=aliquota,
                        cep=cep,
                        freight_cache=freight_cache,
                        margem_min=margem_min,
                        margem_max=margem_max,
                        promo_id=promo_id_local,
                        promo_type=promo_type_local,
                        promo_nome=promo.get("name"),
                        promo_status=str(promo.get("status") or ""),
                        campanha_origem_id=campaign_origem_id,
                    )
                    return oport, None if oport else "avaliado_negativo"

            for promo in promocoes_ajustaveis:
                promo_id = promo.get("id")
                promo_type = promo.get("type")
                if not promo_id or not promo_type:
                    continue

                # Lista items candidate dessa promoção
                items_candidate = await _listar_candidates_da_promocao(
                    ml, promo_id, promo_type,
                )
                # Intersecta com SKUs da guarda-chuva (já filtrado se apenas_skus)
                items_relevantes = [
                    it for it in items_candidate
                    if it.get("item_id") in skus_origem_set
                ]

                # Filtro extra: pula pares já migrados recentemente (fix bug
                # de sugestão fantasma). NÃO conta como descarte porque pode
                # voltar a valer depois do TTL — não queremos poluir o cache.
                if pares_ja_migrados:
                    antes = len(items_relevantes)
                    items_relevantes = [
                        it for it in items_relevantes
                        if (it["item_id"], promo_id) not in pares_ja_migrados
                    ]
                    bloqueados = antes - len(items_relevantes)
                    if bloqueados > 0:
                        logger.info(
                            "deteccao_filtrados_por_historico",
                            promotion_id=promo_id,
                            qtd_bloqueados=bloqueados,
                            promo_nome=promo.get("name"),
                        )

                logger.info(
                    "deteccao_promocao_processando",
                    promotion_id=promo_id,
                    promotion_type=promo_type,
                    promo_status=promo.get("status"),
                    promo_nome=promo.get("name"),
                    items_candidate_total=len(items_candidate),
                    skus_origem_total=len(skus_origem_set),
                    skus_relevantes=len(items_relevantes),
                )

                if not items_relevantes:
                    continue

                # opt 3: avalia todos em paralelo (max 8 simultâneos)
                resultados = await asyncio.gather(
                    *[avaliar_com_sem(it, promo) for it in items_relevantes],
                    return_exceptions=True,
                )

                candidatos_da_promo: list[OportunidadeMigracao] = []
                for it, res in zip(items_relevantes, resultados, strict=False):
                    if isinstance(res, BaseException):
                        logger.warning(
                            "deteccao_avaliacao_falhou",
                            item_id=it.get("item_id"),
                            error=str(res),
                        )
                        continue
                    oport, motivo = res
                    if oport is not None:
                        candidatos_da_promo.append(oport)
                    elif motivo and motivo != "cache_descarte":
                        # Marca pra salvar no cache de descartes
                        descartes_para_salvar.append(DescarteCacheEntry(
                            item_id=it["item_id"],
                            promotion_id=promo_id,
                            motivo=motivo,
                        ))

                candidatos_da_promo.sort(
                    key=lambda o: (-o.margem_pct_pos_migracao, -o.sold_quantity),
                )
                oportunidades.extend(candidatos_da_promo)

        # opt 4: salva descartes detectados (best-effort)
        if self._descarte_cache_repo is not None and descartes_para_salvar:
            try:
                await self._descarte_cache_repo.adicionar_lote(
                    profile_id, descartes_para_salvar,
                )
                logger.info(
                    "deteccao_descartes_salvos",
                    qtd=len(descartes_para_salvar),
                )
            except Exception as e:
                logger.warning(
                    "deteccao_falha_salvar_descartes",
                    error=str(e),
                )

        logger.info(
            "deteccao_concluida",
            profile_id=str(profile_id),
            campaign_id=str(campaign_origem_id),
            total_oportunidades=len(oportunidades),
            margem_min=margem_min,
            margem_max=margem_max,
        )

        # opt 2: salva no cache em memória
        if cache is not None:
            cache.set(
                profile_id, campaign_origem_id,
                skus_origem_frozen, oportunidades,
            )

        return oportunidades


async def _listar_candidates_da_promocao(
    ml: MLClient, promo_id: str, promo_type: str,
) -> list[dict[str, Any]]:
    """Lista items em status 'candidate' duma promoção, com dados de preço.

    Diferente da `listar_items_da_promocao` que devolve só item_ids, aqui
    precisamos do `min_discounted_price`, `max_discounted_price`, `original_price`,
    etc — todo o payload do item dentro da promoção.
    """
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    distribuicao_status: dict[str, int] = {}
    total_recebidos = 0
    offset = 0
    while True:
        try:
            resp = await ml.get(
                f"/seller-promotions/promotions/{promo_id}/items",
                params={
                    "promotion_type": promo_type,
                    "app_version": "v2",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
        except Exception as e:
            logger.warning(
                "candidates_list_failed",
                promotion_id=promo_id,
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
            total_recebidos += 1
            item_id = it.get("id")
            status_norm = _normalizar_status(it.get("status"))
            key = status_norm or "<sem_status>"
            distribuicao_status[key] = distribuicao_status.get(key, 0) + 1
            if not item_id:
                continue
            # Pra migração interessam só candidates — items que o ML convidou
            # pra promoção mas o seller ainda não aceitou. SKUs em started/
            # programmed/active já estão lá, não há o que migrar.
            # (Aqui NÃO filtramos por ITEM_FORA_DA_CAMPANHA — esse set inclui
            # "candidate", o que faz sentido pro listar_items da campanha
            # guarda-chuva, mas no contexto de OPORTUNIDADE de migração, é
            # exatamente o candidate que queremos.)
            if status_norm != "candidate":
                continue
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            items.append({
                "item_id": item_id,
                "status": status_norm,
                "original_price": it.get("original_price"),
                "min_discounted_price": it.get("min_discounted_price"),
                "max_discounted_price": it.get("max_discounted_price"),
                "suggested_discounted_price": it.get("suggested_discounted_price"),
            })

        paging = resp.get("paging", {})
        total = paging.get("total", 0) if isinstance(paging, dict) else 0
        offset += PAGE_SIZE
        if offset >= total:
            break

    logger.info(
        "candidates_listed",
        promotion_id=promo_id,
        promotion_type=promo_type,
        total_recebidos=total_recebidos,
        candidates_unicos=len(items),
        distribuicao_status=distribuicao_status,
    )
    return items


async def _avaliar_sku_em_promocao(
    *,
    ml: MLClient,
    item_id: str,
    item_data: dict[str, Any],
    custos: dict[str, float],
    aliquota: float,
    cep: str,
    freight_cache: FreightCache,
    margem_min: float,
    margem_max: float,
    promo_id: str,
    promo_type: str,
    promo_nome: str | None,
    promo_status: str,
    campanha_origem_id: UUID,
) -> OportunidadeMigracao | None:
    """Avalia se um SKU vale migrar pra essa promoção destino.

    NOVO critério (após feedback do usuário):
    Só migra se conseguir oferecer ao cliente o MESMO preço (ou menor) que
    está pagando hoje na guarda-chuva, E a margem líquida ficar >= a margem
    atual.

    Isso preserva competitividade (cliente não vê preço maior) e só migra
    quando a campanha ML tem benefício real (geralmente comissão promocional
    reduzida).

    Algoritmo:
    1. Pega `preco_atual` = preço que cliente paga AGORA (com desconto da
       guarda-chuva aplicado, vem do GET /items/{id})
    2. Calcula `margem_atual` nesse preço atual
    3. Se `preco_atual` está fora do range [min_discounted_price,
       max_discounted_price] da campanha destino → ML não aceita → não migra
    4. Calcula margem-destino com `deal_price = preco_atual`
    5. Migra se `margem_destino >= margem_atual` E margem está dentro da
       faixa configurada [margem_min, margem_max]

    Retorna OportunidadeMigracao se viável, None se não.
    """
    min_price = item_data.get("min_discounted_price")
    max_price = item_data.get("max_discounted_price")
    if min_price is None or max_price is None:
        return None

    # Busca SKU + dados extras via /items/{id}
    try:
        item_info = await ml.get(f"/items/{item_id}")
    except Exception as e:
        logger.warning("item_fetch_failed", item_id=item_id, error=str(e))
        return None
    if not isinstance(item_info, dict):
        return None

    sku = _extrair_sku_simples(item_info)
    titulo = item_info.get("title")
    sold_quantity = int(item_info.get("sold_quantity") or 0)
    # preco_atual = preço que cliente paga AGORA. Se SKU está em campanha,
    # já vem com desconto aplicado (price = preço final). Senão = preço cheio.
    preco_atual = float(item_info.get("price") or 0)
    if preco_atual <= 0:
        return None

    custo, _ = buscar_custo(sku, item_id, custos, None)
    if custo is None:
        logger.info("deteccao_sku_sem_custo", item_id=item_id, sku=sku)
        return None

    # 1) Margem atual (preço que cliente paga hoje)
    margem_atual, _ = await calcular_margem_para_preco(
        ml=ml,
        item_id=item_id,
        preco_simulado=preco_atual,
        custo=custo,
        aliquota=aliquota,
        cep=cep,
        freight_cache=freight_cache,
    )
    if margem_atual is None:
        return None

    # 2) Se ML não aceita preço <= atual, descarta (cliente não pode pagar mais)
    min_p = float(min_price)
    max_p = float(max_price)
    if min_p > preco_atual:
        # ML exige preço MAIOR que o atual. Cliente pagaria mais caro.
        logger.info(
            "deteccao_descarta_min_acima_atual",
            item_id=item_id,
            preco_atual=preco_atual,
            min_discounted_price=min_p,
        )
        return None

    # 3) Define a janela onde podemos colocar o deal_price:
    #    - Não pode passar do que ML aceita (max_p)
    #    - Não pode passar do preço atual (cliente não paga mais)
    #    - Não pode ficar abaixo do min ML
    teto = min(max_p, preco_atual)
    piso = min_p

    # 4) Tenta primeiro o TETO (= mais lucro, menor desconto pro cliente)
    #    e verifica se a margem resultante cai dentro da faixa configurada.
    margem_no_teto, _ = await calcular_margem_para_preco(
        ml=ml,
        item_id=item_id,
        preco_simulado=teto,
        custo=custo,
        aliquota=aliquota,
        cep=cep,
        freight_cache=freight_cache,
    )
    if margem_no_teto is None:
        return None

    deal_price_escolhido: float | None = None
    margem_escolhida: float | None = None
    margem_brl_escolhida: float | None = None

    if margem_min <= margem_no_teto <= margem_max:
        # Topo da janela está dentro da faixa → ótimo
        deal_price_escolhido = teto
        margem_escolhida = margem_no_teto
    elif margem_no_teto > margem_max:
        # Margem alta demais — preço precisa baixar pra "encaixar" na faixa
        # Procura o preço que dá margem_max (busca binária)
        preco_pra_max, _ = await buscar_preco_para_margem(
            ml=ml,
            item_id=item_id,
            custo=custo,
            margem_alvo=margem_max,
            aliquota=aliquota,
            cep=cep,
            freight_cache=freight_cache,
            preco_max=teto,
        )
        if preco_pra_max is not None and piso <= preco_pra_max <= teto:
            deal_price_escolhido = preco_pra_max
            margem_escolhida = margem_max
        else:
            # Preço ideal cairia abaixo do piso ML — descarta
            logger.info(
                "deteccao_descarta_margem_alta_sem_encaixe",
                item_id=item_id,
                margem_no_teto=margem_no_teto,
                margem_max=margem_max,
                piso_ml=piso,
            )
            return None
    else:
        # margem_no_teto < margem_min → mesmo no melhor preço a margem fica
        # abaixo do aceitável. Descarta (não vale a pena migrar).
        logger.info(
            "deteccao_descarta_margem_abaixo_min",
            item_id=item_id,
            margem_no_teto=margem_no_teto,
            margem_min=margem_min,
        )
        return None

    # 5) Calcula margem em R$ no preço escolhido (pra ter margem_brl_pos_migracao)
    _, margem_brl_escolhida = await calcular_margem_para_preco(
        ml=ml,
        item_id=item_id,
        preco_simulado=deal_price_escolhido,
        custo=custo,
        aliquota=aliquota,
        cep=cep,
        freight_cache=freight_cache,
    )
    if margem_brl_escolhida is None:
        return None

    return OportunidadeMigracao(
        item_id=item_id,
        sku=sku,
        titulo=titulo,
        sold_quantity=sold_quantity,
        campanha_origem_id=campanha_origem_id,
        campanha_destino_ml_id=promo_id,
        campanha_destino_ml_nome=promo_nome,
        campanha_destino_ml_tipo=promo_type,
        campanha_destino_ml_status=promo_status,
        deal_price_sugerido=round(deal_price_escolhido, 2),
        preco_atual=preco_atual,
        margem_pct_pos_migracao=round(margem_escolhida, 4),
        margem_brl_pos_migracao=margem_brl_escolhida,
        margem_pct_atual=round(margem_atual, 4),
        pode_migrar_agora=(promo_status == "started"),
    )


def _extrair_sku_simples(item: dict[str, Any]) -> str | None:
    """Extrai SKU do item ML (seller_custom_field → SELLER_SKU attribute)."""
    sku = item.get("seller_custom_field")
    if sku:
        return str(sku)
    for attr in item.get("attributes") or []:
        if attr.get("id") == "SELLER_SKU":
            val = attr.get("value_name") or attr.get("value_id")
            if val:
                return str(val)
    return None
