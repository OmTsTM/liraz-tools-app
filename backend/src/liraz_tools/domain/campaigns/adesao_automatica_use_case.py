"""Adesão automática de SKUs novos a campanhas locais ativas (ago/2026).

Cenário: seller clonou/criou um anúncio novo. ML detecta como elegível
pra uma SELLER_CAMPAIGN e o marca como `candidate` na promoção. Sem
ação manual, ele fica em `candidate` pra sempre — nunca entra na promo.

Este use case, rodado periodicamente pelo `MigrationScheduler`, faz o
trabalho por você: pra cada campanha `origem=local` ATIVA do perfil,
pega os `candidate` que ainda NÃO estão em `skus_selecionados` local e
tenta adicionar seguindo uma escada de 3 tentativas:

  1. Inflar preço-base pra U (passo3) + adicionar com deal_price → 20%
     margem líquida real.
  2. Se ERROR_CREDIBILITY: rollback do preço-base pro original, tentar
     adicionar com deal_price no preço atual. Margem cai (deal em cima
     do preço-base baixo) mas SKU entra na promo.
  3. Se ainda falhar (item não candidato, outro erro): reprecificar o
     anúncio pro preço de margem alvo (venda direta sem promo).

Ativado por `profile.config.adesao_automatica_ativa`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.skus.sugestao_use_case import (
    SugerirDealPricesPorMargemUseCase,
)
from liraz_tools.infrastructure.ml.campaign_skus_apply import (
    aplicar_adicoes_skus_em_campanha,
)
from liraz_tools.infrastructure.ml.items_update import (
    MLItemUpdateError,
    atualizar_preco_item,
)
from liraz_tools.infrastructure.repositories.migracao_executada_repository import (
    MigracaoExecutadaRecord,
)

if TYPE_CHECKING:
    from liraz_tools.domain.profiles.entity import Profile
    from liraz_tools.infrastructure.ml.client import MLClient
    from liraz_tools.infrastructure.repositories.campaign_repository import (
        CampaignRepository,
    )
    from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
        CostOverridesRepository,
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
class AdesaoResult:
    """Resumo do que rolou pra um perfil num tick do scheduler."""

    profile_id: UUID
    detectados: int = 0  # SKUs `candidate` encontrados fora do local
    adicionados_com_inflar: list[str] = field(default_factory=list)
    adicionados_sem_inflar: list[str] = field(default_factory=list)
    reprecificados_solo: list[str] = field(default_factory=list)
    # SKUs que iam pra tentativa 3 (reprecificar solo) mas foram PULADOS
    # porque já estão em OUTRA SELLER_CAMPAIGN started — reprecificar
    # derrubaria o preço-base inflado usado nessa outra promo.
    pulados_ja_em_outra_promo: list[str] = field(default_factory=list)
    falharam: list[tuple[str, str]] = field(default_factory=list)


async def _sku_em_outra_seller_campaign_started(
    ml: MLClient, item_id: str, promocao_atual: str,
) -> str | None:
    """Se o SKU está em ALGUMA SELLER_CAMPAIGN started diferente da atual,
    retorna o id dessa outra promoção. Senão, None.

    Serve pra evitar que a tentativa 3 (reprecificar solo pro deal_price
    passo3) derrube o preço-base de um SKU que já está inflado
    propositalmente pra outra promo ativa. Sem essa checagem, o app baixa
    o preço-base e sabota a promo ativa (bug detectado ago/2026).
    """
    try:
        promos = await ml.get(
            f"/seller-promotions/items/{item_id}?app_version=v2",
        )
    except Exception:
        # Se falhar a checagem, o mais seguro é NÃO reprecificar (podemos
        # quebrar algo sem saber). Retorna um marcador especial ("unknown")
        # que o caller trata como "não mexe".
        return "__unknown__"
    if not isinstance(promos, list):
        return None
    for p in promos:
        if (
            p.get("type") == "SELLER_CAMPAIGN"
            and p.get("status") == "started"
            and p.get("id") != promocao_atual
        ):
            return str(p.get("id"))
    return None


async def _listar_candidates_da_promocao(
    ml: MLClient, promotion_id: str,
) -> set[str]:
    """Lista item_ids em status `candidate` de uma SELLER_CAMPAIGN.

    Não usa `listar_items_da_promocao` porque aquele filtra fora
    justamente os candidates. Iteração local, paginação até 200 items.
    """
    out: set[str] = set()
    offset = 0
    limit = 50
    for _ in range(20):  # safety cap: 20 paginas x 50 = 1000 items
        resp = await ml.get(
            f"/seller-promotions/promotions/{promotion_id}/items",
            params={
                "promotion_type": "SELLER_CAMPAIGN",
                "app_version": "v2",
                "limit": limit,
                "offset": offset,
            },
        )
        results = (
            resp.get("results") if isinstance(resp, dict) else resp
        ) or []
        if not results:
            break
        for r in results:
            if not isinstance(r, dict):
                continue
            status = str(r.get("status") or "").lower()
            iid = r.get("id")
            if status == "candidate" and isinstance(iid, str):
                out.add(iid)
        if len(results) < limit:
            break
        offset += limit
    return out


class AdesaoAutomaticaUseCase:
    """Orquestra a adesão automática pra um perfil."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        campaign_repo: CampaignRepository,
        creds_repo: PerProfileCredentialsRepository,
        overrides_repo: CostOverridesRepository,
        historico_repo: MigracaoExecutadaRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._creds_repo = creds_repo
        self._overrides_repo = overrides_repo
        self._historico_repo = historico_repo

    async def execute(self, profile: Profile, ml: MLClient) -> AdesaoResult:
        res = AdesaoResult(profile_id=profile.id)

        if not profile.config.adesao_automatica_ativa:
            return res
        if profile.status.value != "connected" or profile.ml_user_id is None:
            return res

        # 1) Lista campanhas locais ATIVAS
        camps = await self._campaign_repo.list_by_profile(
            profile.id, include_archived=False,
        )
        def _st(c: Any) -> str:
            s = c.status
            return s.value if hasattr(s, "value") else str(s)
        camps_local_ativas = [
            c for c in camps
            if _st(c) == "ativa"
            and c.origem == "local"
            and c.ml_campaign_id
        ]
        if not camps_local_ativas:
            return res

        margem_alvo = profile.config.margem_alvo_campanha or 0.20
        sug_uc = SugerirDealPricesPorMargemUseCase(
            self._profile_repo, self._creds_repo, self._overrides_repo,
        )

        for camp in camps_local_ativas:
            try:
                await self._processar_campanha(
                    profile=profile, camp=camp, ml=ml,
                    margem_alvo=margem_alvo, sug_uc=sug_uc, res=res,
                )
            except Exception:
                logger.exception(
                    "adesao_camp_falhou",
                    profile_id=str(profile.id),
                    ml_campaign_id=camp.ml_campaign_id,
                )

        if (
            res.adicionados_com_inflar
            or res.adicionados_sem_inflar
            or res.reprecificados_solo
            or res.pulados_ja_em_outra_promo
            or res.falharam
        ):
            logger.info(
                "adesao_automatica_concluida",
                profile_id=str(profile.id),
                detectados=res.detectados,
                com_inflar=len(res.adicionados_com_inflar),
                sem_inflar=len(res.adicionados_sem_inflar),
                reprecificados_solo=len(res.reprecificados_solo),
                pulados_ja_em_outra_promo=len(res.pulados_ja_em_outra_promo),
                falharam=len(res.falharam),
            )
        return res

    async def _processar_campanha(
        self,
        *,
        profile: Profile,
        camp: Any,  # Campaign entity
        ml: MLClient,
        margem_alvo: float,
        sug_uc: SugerirDealPricesPorMargemUseCase,
        res: AdesaoResult,
    ) -> None:
        # SKUs que o ML sugere como candidates HOJE
        candidates_ml = await _listar_candidates_da_promocao(
            ml, camp.ml_campaign_id,
        )
        # SKUs que já estão no local (o app já sabe deles)
        ja_no_local = set(camp.skus_selecionados or [])
        novos = candidates_ml - ja_no_local
        if not novos:
            return

        res.detectados += len(novos)
        logger.info(
            "adesao_candidates_novos_detectados",
            profile_id=str(profile.id),
            ml_campaign_id=camp.ml_campaign_id,
            campanha_nome=camp.nome,
            qtd=len(novos),
            samples=list(novos)[:5],
        )

        item_ids = list(novos)

        # 2) Preview passo3 pra saber deal_price + preço inflado ideal
        sugestoes = await sug_uc.execute(
            profile.id, ml_campaign_id=camp.ml_campaign_id,
            item_ids=item_ids, margem_alvo=margem_alvo,
        )
        by_sug = {s.item_id: s for s in sugestoes}

        # 3) TENTATIVA 1: inflar + adicionar (fluxo padrão do sync-batch)
        inflar_precos: dict[str, float] = {}
        deal_prices: dict[str, float] = {}
        for iid in item_ids:
            sg = by_sug.get(iid)
            if not sg or sg.erro or not sg.deal_price:
                continue
            if sg.precisa_inflacao and sg.preco_inflado is not None:
                inflar_precos[iid] = sg.preco_inflado
            deal_prices[iid] = sg.deal_price

        # Filtra pra só rodar tentativa 1 nos que têm sugestão válida
        ids_com_sug = list(deal_prices.keys())
        if ids_com_sug:
            res_t1 = await aplicar_adicoes_skus_em_campanha(
                ml=ml, ml_campaign_id=camp.ml_campaign_id,
                item_ids=ids_com_sug,
                inflar_precos=inflar_precos,
                deal_prices=deal_prices,
                fallback_deal_prices={},
                logger=logger,
            )
            res.adicionados_com_inflar.extend(res_t1.adicionados)

            # Tentativa 2: os que caíram por credibility OU pulados por
            # inflação falha. Rollback já foi feito pelo aplicar_adicoes
            # (revertidos). Agora tenta adicionar SEM inflar (deal_prices só).
            precisam_tentativa2 = (
                set(ids_com_sug)
                - set(res_t1.adicionados)
                - set(res_t1.ja_estavam)
                - set(res_t1.pendentes_lock)
            )
            if precisam_tentativa2:
                # Deal price alvo pra tent 2 = mesmo do passo3, mas agora
                # sobre preço-base atual (que voltou ao original via revert).
                # Se ML aceitar, margem fica menor que alvo — trade-off que
                # o user aceitou pra garantir o SKU entrar na promo.
                deal_t2 = {i: deal_prices[i] for i in precisam_tentativa2}
                res_t2 = await aplicar_adicoes_skus_em_campanha(
                    ml=ml, ml_campaign_id=camp.ml_campaign_id,
                    item_ids=list(precisam_tentativa2),
                    inflar_precos={},  # não infla
                    deal_prices=deal_t2,
                    fallback_deal_prices={},
                    logger=logger,
                )
                res.adicionados_sem_inflar.extend(res_t2.adicionados)

                # Tentativa 3: reprecificar solo (preço-base = deal_price
                # passo3, sem entrar em promo).
                # GATE anti-sabotagem: se o SKU já está em OUTRA
                # SELLER_CAMPAIGN started, o preço-base atual foi setado
                # propositalmente pra sustentar aquela promo — não podemos
                # baixar aqui. Pula silenciosamente e loga.
                ainda_fora = (
                    precisam_tentativa2
                    - set(res_t2.adicionados)
                    - set(res_t2.ja_estavam)
                    - set(res_t2.pendentes_lock)
                )
                for iid in ainda_fora:
                    sg = by_sug.get(iid)
                    if not sg or not sg.deal_price:
                        res.falharam.append((iid, "sem sugestão válida"))
                        continue
                    outra_promo = await _sku_em_outra_seller_campaign_started(
                        ml, iid, camp.ml_campaign_id,
                    )
                    if outra_promo is not None:
                        res.pulados_ja_em_outra_promo.append(iid)
                        logger.info(
                            "adesao_reprecificar_pulado_outra_promo",
                            item_id=iid,
                            ml_campaign_id_tentativa=camp.ml_campaign_id,
                            outra_promo_ativa=outra_promo,
                            preco_alvo_solo_ignorado=sg.deal_price,
                            motivo=(
                                "SKU já está started em outra SELLER_CAMPAIGN; "
                                "reprecificar solo derrubaria o preço-base "
                                "usado por ela"
                            ),
                        )
                        continue
                    try:
                        await atualizar_preco_item(ml, iid, sg.deal_price)
                        res.reprecificados_solo.append(iid)
                        logger.info(
                            "adesao_reprecificado_solo",
                            item_id=iid,
                            ml_campaign_id=camp.ml_campaign_id,
                            preco_alvo=sg.deal_price,
                        )
                    except MLItemUpdateError as e:
                        res.falharam.append((iid, f"reprecificar: {e}"))

        # 4) Persiste `skus_selecionados` da camp local: adiciona os que
        # entraram na promo (tentativa 1 ou 2). Reprecificados_solo NÃO
        # entram em skus_selecionados (não estão na promo).
        adicionados_efetivo = set(
            res.adicionados_com_inflar + res.adicionados_sem_inflar,
        ) & set(item_ids)
        if adicionados_efetivo:
            atuais = set(camp.skus_selecionados or [])
            atuais.update(adicionados_efetivo)
            camp.skus_selecionados = sorted(atuais)
            camp.updated_at = datetime.now(UTC)
            await self._campaign_repo.update(camp)

        # 5) Auditoria: log em migracoes_executadas (mesma tabela porque
        # conceitualmente é "adicionar SKU a campanha"; distinguido
        # pelo `operacao='adesao_automatica'`)
        agora = datetime.now(UTC)
        for iid in list(adicionados_efetivo):
            sg = by_sug.get(iid)
            await self._historico_repo.add(record=MigracaoExecutadaRecord(
                id=uuid4(),
                profile_id=profile.id,
                campanha_origem_id=None,
                campanha_destino_ml_id=camp.ml_campaign_id,
                campanha_destino_ml_nome=camp.nome,
                campanha_destino_ml_tipo="SELLER_CAMPAIGN",
                item_id=iid,
                sku=None,
                operacao="adesao_automatica",
                destino_status="started",
                deal_price=sg.deal_price if sg else None,
                margem_pct_prevista=(
                    sg.margem_real_pct / 100
                    if sg and sg.margem_real_pct is not None else None
                ),
                sucesso=True,
                dry_run=False,
                erro_detalhe=None,
                timestamp=agora,
            ))
