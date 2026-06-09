"""Use cases do contexto Campaigns.

Leva 5.3: CRUD + transições de status entre rascunho/agendada/cancelada.
Sem execução no ML ainda — botão "Disparar agora" e scheduler vêm nas Levas 5.4-5.5.

Validações de negócio:
- data_fim >= hoje (não permite campanhas no passado)
- data_inicio >= hoje em criação/edição (mas se já passou, mantém — campanha
  pode estar em andamento)
- Editar só se status in {rascunho, agendada}
- Promover pra agendada exige simulacao_id definido
- Cancelar só se ainda não disparou (rascunho/agendada/executando)
- Reativar uma cancelada exige que data_fim ainda esteja no futuro
"""
from __future__ import annotations

import re
from datetime import date, time
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.campaigns.entity import Campaign, CampaignStatus
from liraz_tools.domain.profiles.repository import ProfileRepository
from liraz_tools.infrastructure.repositories.campaign_repository import (
    CampaignRepository,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)

logger = get_logger(__name__)


class CampaignError(Exception):
    """Erro genérico de domínio (validação, regra de negócio)."""


class CampaignNotEditableError(CampaignError):
    """Tentativa de modificar campanha em status que não permite."""


class SimulationNotReadyError(CampaignError):
    """Simulação associada não existe ou não está completa."""


class InvalidDateError(CampaignError):
    """Data inválida — no passado ou em ordem incorreta."""


class CampaignNameTakenError(CampaignError):
    """Existe outra campanha com o mesmo nome neste perfil.

    Atributo `sugestao` traz o próximo nome livre seguindo o padrão de
    filesystem ('Nome' → 'Nome (1)' → 'Nome (2)'). O frontend captura
    isso e oferece o nome sugerido pro usuário.
    """

    def __init__(self, nome: str, sugestao: str) -> None:
        super().__init__(f"campanha com nome '{nome}' já existe")
        self.nome = nome
        self.sugestao = sugestao


_NOME_NUMERADO_RE = re.compile(r"^(.+?) \((\d+)\)$")


def _proximo_nome_livre(nome_base: str, nomes_existentes: set[str]) -> str:
    """Gera o próximo nome livre seguindo o padrão de filesystem.

    Examples:
        Se 'Liquidação' existe e 'Liquidação (1)' não → retorna 'Liquidação (1)'
        Se 'Liquidação' e 'Liquidação (1)' existem → retorna 'Liquidação (2)'
        Se nome_base já tem '(N)', usa o stem como base e gera próximo.

    A busca é incremental — começa do 1 e vai subindo. Pra 100+ campanhas
    com mesmo prefixo isso seria O(N) mas no uso real (poucas campanhas
    com mesmo nome) é trivial.
    """
    # Se o nome veio com '(N)', usa o stem como base
    m = _NOME_NUMERADO_RE.match(nome_base)
    stem = m.group(1) if m else nome_base

    n = 1
    while True:
        candidato = f"{stem} ({n})"
        if candidato not in nomes_existentes:
            return candidato
        n += 1
        if n > 9999:  # defensivo, nunca deveria acontecer
            raise CampaignError(
                f"não foi possível gerar nome único pra '{nome_base}' após 9999 tentativas"
            )


def _hoje() -> date:
    """Helper testável (pode ser mockado se precisar)."""
    return date.today()


def _validar_datas_futuras(
    data_inicio: date,
    data_fim: date,
    permitir_inicio_no_passado: bool = False,
) -> None:
    """Levanta InvalidDateError se as datas não fazem sentido.

    Regras:
    - data_fim sempre >= hoje (não dá pra ter campanha que já terminou)
    - data_inicio >= hoje no caso normal (criar nova / re-agendar)
      mas permite no passado quando estamos lidando com campanha em
      andamento (ex: editando uma agendada que já começou hoje)
    """
    hoje = _hoje()
    if data_fim < hoje:
        raise InvalidDateError(
            f"data de fim ({data_fim.strftime('%d/%m/%Y')}) já passou — "
            f"não dá pra criar campanha no passado"
        )
    if not permitir_inicio_no_passado and data_inicio < hoje:
        raise InvalidDateError(
            f"data de início ({data_inicio.strftime('%d/%m/%Y')}) já passou — "
            f"escolha uma data de hoje em diante"
        )


# ─── Create ─────────────────────────────────────────────────────────────────


class CreateCampaignUseCase:
    """Cria nova campanha.

    Se `simulacao_id` foi passado e simulação está completa, status inicial
    é `agendada` (decisão da UX: campanhas com tudo pronto já entram
    direto na fila do scheduler). Senão, vira `rascunho` pra ser
    completada depois.

    `hora_disparo` é opcional — default 09:00.
    """

    def __init__(
        self,
        profile_repo: ProfileRepository,
        campaign_repo: CampaignRepository,
        snapshots_repo: SnapshotsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._snapshots_repo = snapshots_repo

    async def execute(
        self,
        profile_id: UUID,
        nome: str,
        data_inicio: date,
        data_fim: date,
        hora_disparo: time | None = None,
        hora_fim: time | None = None,
        simulacao_id: str | None = None,
        skus_selecionados: list[str] | None = None,
        forcar_nome: bool = False,
    ) -> Campaign:
        """Cria a campanha.

        `forcar_nome=True` pula a validação de nome único — usado quando o
        usuário já confirmou (modal "Já existe, criar como X (1)?"). O frontend
        re-envia o request com forcar_nome=True após o user confirmar.

        Quando `forcar_nome=False` (default) e existe outra campanha com mesmo
        nome no perfil, levanta `CampaignNameTakenError` com sugestão de
        próximo nome livre.
        """
        # Verifica que o perfil existe (e implicitamente lança 404 se não)
        profile = await self._profile_repo.get_by_id(profile_id)

        # Valida datas (criação não permite início no passado)
        _validar_datas_futuras(data_inicio, data_fim)

        # Valida nome único (a menos que esteja explicitamente forçando)
        if not forcar_nome:
            nomes_existentes = await self._campaign_repo.list_nomes_do_perfil(
                profile_id
            )
            if nome in nomes_existentes:
                sugestao = _proximo_nome_livre(nome, nomes_existentes)
                raise CampaignNameTakenError(nome, sugestao)

        # Se passou simulacao_id, valida que existe e está completa
        if simulacao_id:
            await self._validate_simulation(profile.slug, simulacao_id)

        # Auto-status: passo3 só exige SKUs selecionados (deal_price é
        # computado no disparo); simulacao_id ainda aceito por compat.
        tem_skus = bool(skus_selecionados)
        status: CampaignStatus = (
            "agendada" if (simulacao_id or tem_skus) else "rascunho"
        )

        campaign = Campaign(
            profile_id=profile_id,
            nome=nome,
            simulacao_id=simulacao_id,
            data_inicio=data_inicio,
            data_fim=data_fim,
            hora_disparo=hora_disparo or time(9, 0),
            hora_fim=hora_fim,
            skus_selecionados=skus_selecionados,
            status=status,
        )
        created = await self._campaign_repo.create(campaign)
        logger.info(
            "campaign_created",
            campaign_id=str(created.id),
            profile_id=str(profile_id),
            nome=nome,
            status=status,
            skus_selecionados_count=(
                len(skus_selecionados) if skus_selecionados is not None else None
            ),
        )
        return created

    async def _validate_simulation(self, slug: str, simulacao_id: str) -> None:
        snap = self._snapshots_repo.get(slug, simulacao_id)
        if snap is None:
            raise SimulationNotReadyError(
                f"simulação '{simulacao_id}' não encontrada"
            )
        estado = snap.get("estado")
        if estado != "completed":
            raise SimulationNotReadyError(
                f"simulação '{simulacao_id}' está em estado '{estado}' "
                f"— só simulações 'completed' podem ser usadas em campanhas"
            )


# ─── Read ────────────────────────────────────────────────────────────────────


class GetCampaignUseCase:
    """Carrega uma campanha pelo id."""

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(self, profile_id: UUID, campaign_id: UUID) -> Campaign:
        return await self._campaign_repo.get_by_id(profile_id, campaign_id)


class ListCampaignsUseCase:
    """Lista campanhas do perfil."""

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(
        self,
        profile_id: UUID,
        include_archived: bool = False,
    ) -> list[Campaign]:
        return await self._campaign_repo.list_by_profile(
            profile_id, include_archived=include_archived,
        )


# ─── Update ──────────────────────────────────────────────────────────────────


class UpdateCampaignUseCase:
    """Atualiza campos editáveis duma campanha.

    Só permite edição enquanto status in {rascunho, agendada}. Depois disso
    a campanha vira imutável.

    Edição PERMITE data_inicio no passado se a campanha estava agendada e
    o usuário não está mudando a data — mas se mudar pra outra data, exige
    futuro. Pragmaticamente: se a data nova é a mesma que estava antes,
    aceita; caso contrário valida futuro.
    """

    def __init__(
        self,
        profile_repo: ProfileRepository,
        campaign_repo: CampaignRepository,
        snapshots_repo: SnapshotsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._campaign_repo = campaign_repo
        self._snapshots_repo = snapshots_repo

    async def execute(
        self,
        profile_id: UUID,
        campaign_id: UUID,
        nome: str | None = None,
        data_inicio: date | None = None,
        data_fim: date | None = None,
        hora_disparo: time | None = None,
        hora_fim: time | None | object = ...,
        simulacao_id: str | None | object = ...,
        skus_selecionados: list[str] | None | object = ...,
        forcar_nome: bool = False,
    ) -> Campaign:
        existing = await self._campaign_repo.get_by_id(profile_id, campaign_id)
        if not existing.is_editable:
            raise CampaignNotEditableError(
                f"campanha está em status '{existing.status}' — "
                f"não pode ser modificada"
            )

        new_nome = nome if nome is not None else existing.nome
        new_data_inicio = data_inicio if data_inicio is not None else existing.data_inicio
        new_data_fim = data_fim if data_fim is not None else existing.data_fim
        new_hora = hora_disparo if hora_disparo is not None else existing.hora_disparo

        # hora_fim usa sentinel `...` pra distinguir "não passou" de "passou None".

        if hora_fim is ...:  # noqa: SIM108
            new_hora_fim = existing.hora_fim
        else:
            new_hora_fim = hora_fim  # type: ignore[assignment]

        if simulacao_id is ...:  # noqa: SIM108
            new_sim_id = existing.simulacao_id
        else:
            new_sim_id = simulacao_id  # type: ignore[assignment]

        # skus_selecionados também usa sentinel — None explicito vs omitido
        if skus_selecionados is ...:  # noqa: SIM108
            new_skus = existing.skus_selecionados
        else:
            new_skus = skus_selecionados  # type: ignore[assignment]

        # Se o NOME foi alterado, valida unicidade (ignora a própria campanha)
        nome_mudou = new_nome != existing.nome
        if nome_mudou and not forcar_nome:
            nomes_existentes = await self._campaign_repo.list_nomes_do_perfil(
                profile_id,
                exclude_campaign_id=campaign_id,
            )
            if new_nome in nomes_existentes:
                sugestao = _proximo_nome_livre(new_nome, nomes_existentes)
                raise CampaignNameTakenError(new_nome, sugestao)

        # Valida datas — permite início no passado SÓ se a data não está
        # sendo alterada (ex: usuário está só mudando o nome de uma campanha
        # que já entrou no período de execução)
        data_inicio_mudou = new_data_inicio != existing.data_inicio
        _validar_datas_futuras(
            new_data_inicio,
            new_data_fim,
            permitir_inicio_no_passado=not data_inicio_mudou,
        )

        # Valida simulação nova se mudou
        if new_sim_id and new_sim_id != existing.simulacao_id:
            profile = await self._profile_repo.get_by_id(profile_id)
            snap = self._snapshots_repo.get(profile.slug, new_sim_id)
            if snap is None:
                raise SimulationNotReadyError(
                    f"simulação '{new_sim_id}' não encontrada"
                )
            if snap.get("estado") != "completed":
                raise SimulationNotReadyError(
                    f"simulação '{new_sim_id}' não está completa"
                )

        # Auto-promote: rascunho + ganhou SKUs (ou simulação no fluxo
        # antigo) = agendada. No passo3 o disparo computa deal_price sozinho,
        # então ter SKUs já é suficiente pra entrar na fila do scheduler.
        novo_status = existing.status
        if existing.status == "rascunho":
            ganhou_sim = new_sim_id is not None and existing.simulacao_id is None
            tinha_skus = bool(existing.skus_selecionados)
            ganhou_skus = (
                new_skus is not None and bool(new_skus) and not tinha_skus
            )
            if ganhou_sim or ganhou_skus:
                novo_status = "agendada"

        updated = existing.model_copy(update={
            "nome": new_nome,
            "data_inicio": new_data_inicio,
            "data_fim": new_data_fim,
            "hora_disparo": new_hora,
            "hora_fim": new_hora_fim,
            "simulacao_id": new_sim_id,
            "skus_selecionados": new_skus,
            "status": novo_status,
        })
        # Pydantic não roda model_validator no model_copy, valida explicitamente
        Campaign.model_validate(updated.model_dump())

        result = await self._campaign_repo.update(updated)
        logger.info(
            "campaign_updated",
            campaign_id=str(campaign_id),
            changed_simulacao=new_sim_id != existing.simulacao_id,
            auto_promoted=novo_status != existing.status,
            skus_count=(len(new_skus) if new_skus is not None else None),
        )
        return result


# ─── Status transitions ─────────────────────────────────────────────────────


class ScheduleCampaignUseCase:
    """Promove uma campanha de `rascunho` pra `agendada`.

    No modelo passo3 (atual), basta ter SKUs selecionados — o `deal_price`
    de cada item é computado NO DISPARO via `StartCampaignPasso3UseCase`,
    não mais via simulação pré-rodada. `simulacao_id` ainda é aceito como
    critério alternativo pra compat com campanhas do modelo antigo.
    """

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(self, profile_id: UUID, campaign_id: UUID) -> Campaign:
        existing = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        if existing.status != "rascunho":
            raise CampaignNotEditableError(
                f"só campanhas em 'rascunho' podem ser agendadas "
                f"(atual: '{existing.status}')"
            )
        if not existing.pronta_para_agendar:
            raise CampaignError(
                "campanha precisa ter SKUs selecionados (ou uma simulação "
                "associada, no fluxo antigo) pra ser agendada"
            )

        updated = existing.model_copy(update={"status": "agendada"})
        result = await self._campaign_repo.update(updated)
        logger.info("campaign_scheduled", campaign_id=str(campaign_id))
        return result


class UnscheduleCampaignUseCase:
    """Despromove uma campanha de `agendada` pra `rascunho`."""

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(self, profile_id: UUID, campaign_id: UUID) -> Campaign:
        existing = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        if existing.status != "agendada":
            raise CampaignNotEditableError(
                f"só campanhas em 'agendada' podem voltar pra rascunho "
                f"(atual: '{existing.status}')"
            )

        updated = existing.model_copy(update={"status": "rascunho"})
        result = await self._campaign_repo.update(updated)
        logger.info("campaign_unscheduled", campaign_id=str(campaign_id))
        return result


class CancelCampaignUseCase:
    """Cancela uma campanha. Permitido em rascunho/agendada.

    Leva 5.4+ vai expandir pra cancelar em execução / ativa (precisa
    coordenar com cancelamento no ML).
    """

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(self, profile_id: UUID, campaign_id: UUID) -> Campaign:
        existing = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        if existing.status not in {"rascunho", "agendada"}:
            raise CampaignNotEditableError(
                f"cancelamento de campanhas em '{existing.status}' "
                f"ainda não foi implementado (chega na Leva 5.4+)"
            )

        updated = existing.model_copy(update={"status": "cancelada"})
        result = await self._campaign_repo.update(updated)
        logger.info("campaign_cancelled", campaign_id=str(campaign_id))
        return result


class ReactivateCampaignUseCase:
    """Reativa uma campanha `cancelada`.

    Vira `agendada` se tem simulacao_id, ou `rascunho` se não. Validamos
    que data_fim ainda esteja no futuro — não faz sentido reativar
    campanha que já passou da data.
    """

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(self, profile_id: UUID, campaign_id: UUID) -> Campaign:
        existing = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        if existing.status != "cancelada":
            raise CampaignNotEditableError(
                f"só campanhas 'cancelada' podem ser reativadas "
                f"(atual: '{existing.status}')"
            )

        if existing.data_fim < _hoje():
            raise InvalidDateError(
                f"data de fim ({existing.data_fim.strftime('%d/%m/%Y')}) "
                f"já passou — não dá pra reativar. Crie uma nova campanha "
                f"com datas futuras."
            )

        novo_status = "agendada" if existing.pronta_para_agendar else "rascunho"
        updated = existing.model_copy(update={"status": novo_status})
        result = await self._campaign_repo.update(updated)
        logger.info(
            "campaign_reactivated",
            campaign_id=str(campaign_id),
            novo_status=novo_status,
        )
        return result


# ─── Delete ──────────────────────────────────────────────────────────────────


class DeleteCampaignUseCase:
    """Apaga uma campanha. Só permitido em rascunho/cancelada.

    Pra campanhas que já rodaram (ativa, finalizada, falha), o histórico
    deve ser preservado pra auditoria — só permite cancelar.
    """

    def __init__(self, campaign_repo: CampaignRepository) -> None:
        self._campaign_repo = campaign_repo

    async def execute(self, profile_id: UUID, campaign_id: UUID) -> bool:
        existing = await self._campaign_repo.get_by_id(profile_id, campaign_id)

        if existing.status not in {"rascunho", "cancelada"}:
            raise CampaignNotEditableError(
                f"só campanhas em 'rascunho' ou 'cancelada' podem ser "
                f"deletadas (atual: '{existing.status}'). Cancele primeiro."
            )

        deleted = await self._campaign_repo.delete(profile_id, campaign_id)
        if deleted:
            logger.info("campaign_deleted", campaign_id=str(campaign_id))
        return deleted
