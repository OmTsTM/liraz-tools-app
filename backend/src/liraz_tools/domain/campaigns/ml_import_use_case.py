"""Use case: importar/sincronizar localmente uma campanha SELLER_CAMPAIGN do ML.

Quando o user clica em uma campanha vinda do ML no grid, este use case:
1. Se a loja está conectada: SEMPRE consulta o ML pra dados frescos
   (nome, datas, status, items participantes)
2. Se já existe local com mesmo `ml_campaign_id`: faz auto-sync — atualiza
   skus_selecionados e status do registro local pra refletir mudanças feitas
   no painel do ML (SKUs adicionados/removidos, campanha finalizada, etc).
   Mantém id local, criado_em e tudo mais imutável.
3. Se não existe local: cria do zero com:
   - origem="ml" pra distinguir nas UI
   - skus_selecionados preenchido com items participantes
   - status mapeado (ML started → ativa, pending → agendada, finished →
     finalizada)
   - ml_campaign_id vinculado pra dedup futura
4. Loja desconectada + já existe local: retorna o local sem tocar no ML
   (fallback offline)

A campanha local NÃO é disparada pelo scheduler (já está rodando no ML).
NÃO tem rollback_id local (não temos snapshot dos preços anteriores).
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.entity import Campaign
from liraz_tools.domain.profiles.repository import ProfileRepository
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotions_lookup import (
    ML_STATUS_TO_LOCAL,
    listar_items_da_promocao_completo,
    listar_items_da_promocao_direcionado,
    listar_seller_campaigns_completas,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)

logger = get_logger(__name__)


class MLCampaignNotFoundError(Exception):
    """Promotion id passado não foi encontrado entre as campanhas do ML."""


class MLImportError(Exception):
    """Erro genérico de importação do ML."""


class ImportMLCampaignUseCase:
    def __init__(
        self,
        profile_repo: ProfileRepository,
        campaign_repo: CampaignRepository,
        creds_repo: PerProfileCredentialsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._creds_repo = creds_repo

    async def execute(
        self, profile_id: UUID, ml_promotion_id: str, *, full_scan: bool = False,
    ) -> Campaign:
        # 1) Perfil precisa estar conectado pra consultar ML (mesmo pra sync)
        profile = await self._profile_repo.get_by_id(profile_id)
        if profile.status.value != "connected" or profile.ml_user_id is None:
            # Sem conexão, devolve o que tiver local — não dá pra sincronizar
            existing = await self._campaign_repo.find_by_ml_id(
                profile_id, ml_promotion_id,
            )
            if existing:
                logger.info(
                    "ml_campaign_import_offline_fallback",
                    campaign_id=str(existing.id),
                    ml_id=ml_promotion_id,
                )
                return existing
            raise MLImportError(
                f"loja '{profile.name}' não está conectada ao ML"
            )

        # Já espelhada antes? Precisamos saber ANTES de buscar os itens pra
        # decidir entre varredura completa e direcionada.
        existing = await self._campaign_repo.find_by_ml_id(
            profile_id, ml_promotion_id,
        )

        # 2) Busca dados frescos do ML (sempre — pra sincronização)
        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: Any) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            todas = await listar_seller_campaigns_completas(ml, profile.ml_user_id)
            match = next(
                (c for c in todas if c["ml_promotion_id"] == ml_promotion_id),
                None,
            )
            if match is None:
                raise MLCampaignNotFoundError(
                    f"campanha {ml_promotion_id} não encontrada no ML "
                    f"(pode ter sido apagada lá depois da listagem)"
                )

            # apenas_desconto_vivo=False: queremos TODOS os SKUs comprometidos
            # com a campanha (incluindo programmed/pending), não só os que estão
            # com desconto vivo neste segundo. O painel do ML conta assim.
            #
            # DIRECIONADO vs COMPLETO:
            # - Primeira importação (sem `existing`) OU `full_scan` (botão
            #   "Atualizar do ML"): varredura COMPLETA — descobre SKUs novos
            #   adicionados no painel ML, inclusive legados MLB4xxx. Cara.
            # - Auto-sync de campanha já espelhada: varredura DIRECIONADA —
            #   confere só os SKUs conhecidos (+ endpoint direto pra MLB6 novos).
            #   ~5x mais barata; não pega adições legadas (pra isso, o botão).
            usar_direcionado = existing is not None and not full_scan
            # Condição inline (não a flag) pra o mypy estreitar `existing`.
            if existing is not None and not full_scan:
                item_ids = await listar_items_da_promocao_direcionado(
                    ml, ml_promotion_id, "SELLER_CAMPAIGN",
                    existing.skus_selecionados or [],
                    apenas_desconto_vivo=False,
                )
            else:
                item_ids = await listar_items_da_promocao_completo(
                    ml, profile.ml_user_id, ml_promotion_id, "SELLER_CAMPAIGN",
                    apenas_desconto_vivo=False,
                )

        logger.info(
            "ml_import_fetched_items",
            ml_promotion_id=ml_promotion_id,
            qtd_items_do_ml=len(item_ids),
            modo="direcionado" if usar_direcionado else "completo",
            amostra_primeiros_5=item_ids[:5],
        )

        # 3) Já espelhada antes? Sincroniza skus_selecionados + status.
        # Auto-sync porque o user pode ter mexido na campanha no painel do ML
        # depois de já ter importado aqui (adicionando/removendo SKUs).
        if existing:
            return await self._sync_existing(existing, match, item_ids)

        # 4) Primeira importação — cria do zero
        data_inicio = _parse_date(match.get("start_date_iso", ""))
        data_fim = _parse_date(match.get("finish_date_iso", ""))
        if data_inicio is None or data_fim is None:
            raise MLImportError(
                f"datas inválidas vindas do ML: "
                f"inicio={match.get('start_date_iso')}, "
                f"fim={match.get('finish_date_iso')}"
            )

        status_local = ML_STATUS_TO_LOCAL.get(match.get("ml_status", ""), "ativa")
        campaign = Campaign(
            profile_id=profile_id,
            nome=match.get("name") or f"Campanha {ml_promotion_id}",
            simulacao_id=None,
            data_inicio=data_inicio,
            data_fim=data_fim,
            hora_disparo=time(9, 0),  # ML não expõe horário fino
            hora_fim=None,
            skus_selecionados=item_ids if item_ids else None,
            status=status_local,  # type: ignore[arg-type]
            ml_campaign_id=ml_promotion_id,
            origem="ml",
        )
        created = await self._campaign_repo.create(campaign)
        logger.info(
            "ml_campaign_imported",
            campaign_id=str(created.id),
            ml_id=ml_promotion_id,
            nome=created.nome,
            status=created.status,
            items_count=len(item_ids),
        )
        return created

    async def _sync_existing(
        self,
        existing: Campaign,
        match: dict[str, Any],
        item_ids: list[str],
    ) -> Campaign:
        """Atualiza skus_selecionados/status duma campanha ML já espelhada.

        Mantém id local, criado_em, etc — só reflete mudanças que vieram do
        painel do ML (SKUs adicionados/removidos, campanha finalizada).
        """
        novos_skus = item_ids if item_ids else None
        novo_status = ML_STATUS_TO_LOCAL.get(match.get("ml_status", ""), "ativa")

        skus_atuais = existing.skus_selecionados or []
        mudou_skus = sorted(skus_atuais) != sorted(item_ids)
        mudou_status = existing.status != novo_status

        logger.info(
            "ml_sync_compare",
            campaign_id=str(existing.id),
            qtd_local=len(skus_atuais),
            qtd_remoto=len(item_ids),
            status_local=existing.status,
            status_remoto=novo_status,
            mudou_skus=mudou_skus,
            mudou_status=mudou_status,
        )

        if not mudou_skus and not mudou_status:
            logger.info(
                "ml_campaign_sync_no_change",
                campaign_id=str(existing.id),
                items_count=len(item_ids),
            )
            return existing

        updated = existing.model_copy(update={
            "skus_selecionados": novos_skus,
            "status": novo_status,
        })
        saved = await self._campaign_repo.update(updated)
        logger.info(
            "ml_campaign_synced",
            campaign_id=str(saved.id),
            antes_skus=len(skus_atuais),
            depois_skus=len(item_ids),
            mudou_skus=mudou_skus,
            mudou_status=mudou_status,
            antes_status=existing.status,
            depois_status=novo_status,
        )
        return saved


def _parse_date(iso: str) -> date | None:
    """Extrai date de string ISO do ML (com timezone ou Z)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.date()
    except (ValueError, TypeError):
        return None
