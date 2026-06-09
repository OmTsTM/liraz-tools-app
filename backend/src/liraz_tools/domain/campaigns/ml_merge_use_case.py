"""Use case: lista campanhas locais + campanhas SELLER_CAMPAIGN do ML (Leva 5.9.1).

Resultado é a união:
- Todas as campanhas locais do perfil (criadas no app)
- + SELLER_CAMPAIGN do ML que NÃO foram criadas pelo app (ou seja, criadas
  direto no ML pelo vendedor) — read-only no grid

Campanhas locais que já foram disparadas no ML têm `ml_campaign_id`
preenchido. Usamos esse campo pra deduplicar — uma campanha que VOCÊ criou
no app e disparou aparece como local (não duplica).

Status do ML é traduzido pro vocabulário local:
- `started` (rodando agora) → `ativa`
- `pending` (aprovada, vai começar) → `agendada`
- `finished` (já terminou) → `finalizada`

Campanhas-do-ML aparecem com `origem="ml"` no response. Frontend desabilita
botões de edição/delete pra elas.

Sync reverso (fix bug "campanha excluída no ML continua no app"):
Quando a chamada `listar_seller_campaigns_completas` retorna sucesso,
comparamos a lista do ML com as campanhas locais `origem='ml'`. Campanhas
locais que sumiram do ML são auto-arquivadas (archived_at=now). Se voltam
a aparecer (raro mas possível), são desarquivadas. Crucial: o sync reverso
SÓ roda se a chamada ML teve sucesso — falha de rede NUNCA arquiva nada.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.entity import Campaign
from liraz_tools.domain.profiles.repository import ProfileRepository
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotions_lookup import (
    ML_STATUS_TO_LOCAL,
    listar_seller_campaigns_completas,
)
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)

logger = get_logger(__name__)


class ListCampaignsWithMLMergeUseCase:
    """Lista campanhas locais + campanhas ML não-vinculadas."""

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
        self,
        profile_id: UUID,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """Retorna lista de dicts (não entities) — campanhas locais e ML
        misturadas, ordenadas por data_inicio decrescente.

        Em caso de falha ao consultar ML, retorna só as locais — o app
        nunca fica vazio por culpa da consulta ML.
        """
        profile = await self._profile_repo.get_by_id(profile_id)

        # 1) Campanhas locais. Inclui arquivadas SEMPRE no fetch — precisamos
        #    delas pro sync reverso decidir o que desarquivar. Filtragem
        #    final por include_archived acontece na saída.
        locais = await self._campaign_repo.list_by_profile(
            profile_id, include_archived=True,
        )

        # 2) Conjunto de ml_promotion_ids já vinculados a campanhas locais —
        #    pra deduplicar. Se VOCÊ criou no app e disparou, a local tem
        #    `ml_campaign_id` preenchido e a ML não deve ser duplicada.
        ml_ids_vinculados: set[str] = {
            c.ml_campaign_id for c in locais if c.ml_campaign_id
        }

        # 3) Tenta puxar campanhas do ML (defensivo — pula em qualquer erro)
        ml_campaigns: list[dict[str, Any]] = []
        ml_response_ok = False
        if profile.status.value == "connected" and profile.ml_user_id is not None:
            try:
                creds = self._creds_repo.get_app_credentials(profile.slug)
                tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

                async def save_refreshed(new_tokens: Any) -> None:
                    self._creds_repo.save_tokens(profile.slug, new_tokens)

                async with MLClient(
                    creds, tokens, on_tokens_refreshed=save_refreshed,
                ) as ml:
                    ml_campaigns = await listar_seller_campaigns_completas(
                        ml, profile.ml_user_id,
                    )
                ml_response_ok = True
            except Exception as e:
                # Falha ao puxar ML não é fatal — segue só com locais.
                # Frontend pode mostrar aviso sutil se quiser.
                logger.warning(
                    "ml_campaigns_merge_failed",
                    profile_id=str(profile_id),
                    error=str(e),
                )

        # 3.5) Sync reverso — só roda se ML respondeu OK (defesa crítica
        #      contra arquivar tudo por causa de timeout de rede).
        if ml_response_ok:
            locais = await self._sync_reverso_ml(
                profile_id, locais, ml_campaigns,
            )

        # 4) Filtragem final por include_archived (depois do sync reverso
        #    pra refletir as novas arquivações/desarquivações).
        if not include_archived:
            locais = [c for c in locais if c.archived_at is None]

        # 5) Monta lista final — primeiro locais, depois ML não-vinculadas
        result: list[dict[str, Any]] = [
            _local_to_dict(c) for c in locais
        ]
        for mc in ml_campaigns:
            ml_id = mc["ml_promotion_id"]
            if ml_id in ml_ids_vinculados:
                continue  # Já tem cópia local — não duplica
            result.append(_ml_to_dict(profile_id, mc))

        # 6) Ordena por data_inicio desc
        result.sort(
            key=lambda d: d.get("data_inicio", ""),
            reverse=True,
        )

        logger.info(
            "campaigns_listed_with_ml_merge",
            profile_id=str(profile_id),
            locais=len(locais),
            ml_total=len(ml_campaigns),
            ml_added=len([m for m in ml_campaigns
                          if m["ml_promotion_id"] not in ml_ids_vinculados]),
            final=len(result),
            ml_response_ok=ml_response_ok,
        )
        return result

    async def _sync_reverso_ml(
        self,
        profile_id: UUID,
        locais: list[Campaign],
        ml_campaigns: list[dict[str, Any]],
    ) -> list[Campaign]:
        """Reconcilia campanhas locais `origem='ml'` com a lista atual do ML.

        Pra cada campanha local `origem='ml'`:
        - Se NÃO está mais no ML → arquiva (archived_at = now)
        - Se está arquivada mas VOLTOU no ML → desarquiva (archived_at = None)
          (raro — mas cobre o caso de re-criar campanha com mesmo ID)

        Devolve a lista de locais atualizada (com `archived_at` refletindo
        o sync). Não muda nada se já está consistente.

        IMPORTANTE: este método SÓ deve ser chamado quando a resposta do ML
        foi bem-sucedida — chamar com `ml_campaigns=[]` por erro de rede
        arquivaria todas as campanhas ML do perfil indevidamente.
        """
        ml_ids_atuais: set[str] = {
            mc["ml_promotion_id"] for mc in ml_campaigns
        }

        agora = datetime.now(UTC)
        atualizadas: list[Campaign] = []
        qtd_arquivadas = 0
        qtd_desarquivadas = 0

        for c in locais:
            if c.origem != "ml" or c.ml_campaign_id is None:
                atualizadas.append(c)
                continue

            esta_no_ml = c.ml_campaign_id in ml_ids_atuais

            if not esta_no_ml and c.archived_at is None:
                # Sumiu do ML — arquiva
                updated = c.model_copy(update={"archived_at": agora})
                saved = await self._campaign_repo.update(updated)
                atualizadas.append(saved)
                qtd_arquivadas += 1
                logger.info(
                    "ml_campaign_sync_reverso_arquivada",
                    campaign_id=str(c.id),
                    ml_id=c.ml_campaign_id,
                    nome=c.nome,
                )
            elif esta_no_ml and c.archived_at is not None:
                # Voltou — desarquiva
                updated = c.model_copy(update={"archived_at": None})
                saved = await self._campaign_repo.update(updated)
                atualizadas.append(saved)
                qtd_desarquivadas += 1
                logger.info(
                    "ml_campaign_sync_reverso_desarquivada",
                    campaign_id=str(c.id),
                    ml_id=c.ml_campaign_id,
                    nome=c.nome,
                )
            else:
                atualizadas.append(c)

        if qtd_arquivadas or qtd_desarquivadas:
            logger.info(
                "ml_campaigns_sync_reverso_aplicado",
                profile_id=str(profile_id),
                qtd_arquivadas=qtd_arquivadas,
                qtd_desarquivadas=qtd_desarquivadas,
            )
        return atualizadas


def _local_to_dict(c: Campaign) -> dict[str, Any]:
    """Converte Campaign entity pra dict no formato do response.

    Campanhas locais sempre têm `origem="local"` — editáveis no app.
    """
    return {
        "id": str(c.id),
        "profile_id": str(c.profile_id),
        "nome": c.nome,
        "simulacao_id": c.simulacao_id,
        "data_inicio": c.data_inicio.isoformat(),
        "data_fim": c.data_fim.isoformat(),
        "hora_disparo": c.hora_disparo.isoformat(),
        "hora_fim": c.hora_fim.isoformat() if c.hora_fim else None,
        "skus_selecionados": c.skus_selecionados,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "ml_campaign_id": c.ml_campaign_id,
        "aplicacao_id": c.aplicacao_id,
        "rollback_id": c.rollback_id,
        "erro": c.erro,
        "archived_at": c.archived_at.isoformat() if c.archived_at else None,
        "duracao_dias": c.duracao_dias,
        "is_editable": c.is_editable,
        "origem": c.origem,
    }


def _ml_to_dict(profile_id: UUID, mc: dict[str, Any]) -> dict[str, Any]:
    """Converte campanha vinda do ML pra dict no formato do response.

    Como não temos um Campaign entity persistido, geramos um UUID
    deterministico baseado no `ml_promotion_id` — assim o mesmo
    ml_id sempre gera o mesmo UUID (idempotente, frontend pode usar
    como key).

    Datas vêm do ML em ISO com timezone (ex: "2025-04-27T12:03:00-03:00"
    ou "2024-01-21T05:59:59Z"). Convertemos pra date local.
    """
    ml_id = mc["ml_promotion_id"]
    # UUID5 estável: mesma promotion_id no ML sempre gera mesmo UUID
    deterministic_id = uuid5(NAMESPACE_URL, f"ml-campaign:{ml_id}")

    status_local = ML_STATUS_TO_LOCAL.get(mc["ml_status"], "ativa")

    # Parse datas. ML pode mandar com timezone ou Z — _parse_date_iso lida
    data_inicio = _parse_date_iso(mc.get("start_date_iso", ""))
    data_fim = _parse_date_iso(mc.get("finish_date_iso", ""))

    return {
        "id": str(deterministic_id),
        "profile_id": str(profile_id),
        "nome": mc["name"],
        "simulacao_id": None,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "hora_disparo": "09:00:00",  # ML não expõe — usa placeholder
        "hora_fim": None,
        "skus_selecionados": None,
        "status": status_local,
        "created_at": None,
        "updated_at": None,
        "ml_campaign_id": ml_id,
        "aplicacao_id": None,
        "rollback_id": None,
        "erro": None,
        "archived_at": None,
        "duracao_dias": _calc_duracao(data_inicio, data_fim),
        "is_editable": False,  # ML não-vinculada = read-only no app
        "origem": "ml",
    }


def _parse_date_iso(s: str) -> str:
    """Extrai a parte de data (YYYY-MM-DD) de uma string ISO do ML."""
    if not s:
        return ""
    # Tira sufixo de hora se vier completo
    try:
        # ML manda "2025-04-27T12:03:00-03:00" ou "2024-01-21T05:59:59Z"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, TypeError):
        # Fallback: tenta pegar os primeiros 10 chars
        return s[:10] if len(s) >= 10 else s


def _calc_duracao(d1: str, d2: str) -> int:
    """Dias entre duas strings ISO de data."""
    if not d1 or not d2:
        return 0
    try:
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        return (b - a).days
    except (ValueError, TypeError):
        return 0
