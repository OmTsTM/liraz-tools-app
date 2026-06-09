"""Use cases do contexto Repricing (simulação de reprecificação).

`StartRepricingSimulationUseCase` é o principal — dispara a simulação em
background com checkpointing.

Pipeline orquestrador:
1. Lista todos anúncios ativos (paginação)
2. Carrega custos do XLSX
3. Carrega overrides manuais (cost_overrides.json) — Leva 5.1
4. Cria snapshot inicial `estado=running` no disco
5. Em batches de 20 (CHECKPOINT_A_CADA):
   - Processa cada item em paralelo (até 8 simultâneos via semaphore do MLClient)
   - Append resultados nas listas `simulacoes` / `excecoes`
   - Atualiza snapshot no disco (atomicamente)
6. Marca `estado=completed` no final
7. Em caso de exceção: `estado=failed` com `erro_fatal`

Background:
- O use case `execute()` é uma corrotina assíncrona
- Disparada via `JobRunner.start()` que cria asyncio.Task
- Frontend faz polling no GET /simulations/{id} pra ver progresso
"""
from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.entity import TokenSet
from liraz_tools.domain.pricing.repricing_entity import (
    RepricingSimulation,
    RepricingSummary,
)
from liraz_tools.infrastructure.background.job_runner import (
    JobRunner,
    get_job_runner,
)
from liraz_tools.infrastructure.ml.client import MLClient
from liraz_tools.infrastructure.ml.promotions_lookup import montar_mapa_items_em_promocao
from liraz_tools.infrastructure.pricing.costs_loader import (
    CustosXLSXError,
    carregar_custos,
    carregar_tarifas_ml,
)
from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
from liraz_tools.infrastructure.pricing.repricing_calculator import (
    processar_um_item,
)
from liraz_tools.infrastructure.pricing.repricing_xlsx_renderer import (
    render_simulation_xlsx,
)
from liraz_tools.infrastructure.repositories.cost_overrides_repository import (
    CostOverridesRepository,
)
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    PerProfileCredentialsRepository,
)
from liraz_tools.infrastructure.repositories.profile_repository import (
    SQLAlchemyProfileRepository,
)
from liraz_tools.infrastructure.repositories.snapshots_repository import (
    SnapshotsRepository,
)

logger = get_logger(__name__)


CHECKPOINT_A_CADA = 20
"""Tamanho do batch entre checkpoints (igual MCP)."""

CONCORRENCIA_PADRAO = 8


# ─── Erros ──────────────────────────────────────────────────────────────────


class RepricingError(Exception):
    """Erro genérico de simulação."""


class NoCustosConfiguredError(RepricingError):
    """Perfil não tem custos_xlsx_path configurado."""


class NotConnectedError(RepricingError):
    """Perfil sem tokens ML válidos."""


# ─── Use cases ──────────────────────────────────────────────────────────────


class StartRepricingSimulationUseCase:
    """Dispara nova simulação em background.

    O método `execute()` retorna o `simulation_id` imediatamente — a
    execução real continua em uma asyncio.Task gerenciada pelo JobRunner.

    Pra acompanhar progresso, o frontend faz polling no GET pra
    `GetRepricingSimulationUseCase`.
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        snapshots_repo: SnapshotsRepository,
        overrides_repo: CostOverridesRepository,
        job_runner: JobRunner | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._snapshots_repo = snapshots_repo
        self._overrides_repo = overrides_repo
        self._job_runner = job_runner or get_job_runner()

    async def execute(
        self,
        profile_id: UUID,
        concorrencia: int = CONCORRENCIA_PADRAO,
    ) -> str:
        """Cria snapshot inicial + dispara task. Retorna `simulation_id`."""
        profile = await self._profile_repo.get_by_id(profile_id)

        if profile.ml_user_id is None:
            raise NotConnectedError(
                f"perfil '{profile.name}' não está conectado ao ML"
            )

        if not profile.config.custos_xlsx_path:
            raise NoCustosConfiguredError(
                f"perfil '{profile.name}' não tem planilha de custos configurada"
            )

        if not (1 <= concorrencia <= 20):
            raise RepricingError(
                f"concorrencia deve estar entre 1 e 20 (recebi {concorrencia})"
            )

        # Gera ID e cria snapshot inicial vazio com estado=running
        simulation_id = SnapshotsRepository.new_simulation_id()
        snapshot_inicial = RepricingSimulation(
            simulation_id=simulation_id,
            criado_em=RepricingSimulation.now_iso(),
            estado="running",
            aliquota_imposto=profile.config.aliquota_imposto,
            cep_destino=profile.config.cep_destino,
            custos_xlsx_path=profile.config.custos_xlsx_path,
            concorrencia_usada=concorrencia,
            margem_alvo_aumento=profile.config.margem_alvo_aumento,
            limite_margem_aumento=profile.config.limite_margem_aumento,
            margem_alvo_campanha=profile.config.margem_alvo_campanha,
        )
        self._snapshots_repo.save(
            profile.slug, simulation_id, snapshot_inicial.model_dump(),
        )

        # Captura refs locais pro closure (evita auto-referência cíclica).
        # custos_xlsx_path já foi validado não-nulo acima (raise se vazio) —
        # capturar num local preserva o narrowing dentro do closure.
        slug = profile.slug
        custos_xlsx_path: str = profile.config.custos_xlsx_path
        tarifas_ml_xlsx_path = profile.config.tarifas_ml_xlsx_path
        snapshots_repo = self._snapshots_repo
        overrides_repo = self._overrides_repo
        creds_repo = self._creds_repo

        async def _run() -> None:
            """Roda no background. Atualiza snapshot a cada checkpoint."""
            try:
                await _orquestrar_simulacao(
                    profile_slug=slug,
                    profile_id=profile.id,
                    ml_user_id=profile.ml_user_id,  # type: ignore[arg-type]
                    aliquota=profile.config.aliquota_imposto,
                    cep=profile.config.cep_destino,
                    custos_xlsx_path=custos_xlsx_path,
                    concorrencia=concorrencia,
                    margem_alvo_campanha=profile.config.margem_alvo_campanha,
                    margem_minima=profile.config.margem_minima,
                    pct_inflacao=profile.config.pct_inflacao_campanha,
                    simulation_id=simulation_id,
                    snapshots_repo=snapshots_repo,
                    overrides_repo=overrides_repo,
                    creds_repo=creds_repo,
                    tarifas_ml_xlsx_path=tarifas_ml_xlsx_path,
                )
            except Exception as e:
                logger.exception(
                    "simulation_failed",
                    simulation_id=simulation_id,
                    error=str(e),
                )
                # Marca snapshot como failed com mensagem
                snap_data = snapshots_repo.get(slug, simulation_id) or {}
                snap_data["estado"] = "failed"
                snap_data["erro_fatal"] = str(e)
                snapshots_repo.save(slug, simulation_id, snap_data)

        self._job_runner.start(simulation_id, _run)
        return simulation_id


async def _orquestrar_simulacao(
    profile_slug: str,
    profile_id: UUID,  # noqa: ARG001  (mantido por simetria com os call sites)
    ml_user_id: int,
    aliquota: float,
    cep: str,
    custos_xlsx_path: str,
    concorrencia: int,
    margem_alvo_campanha: float,
    margem_minima: float,
    pct_inflacao: float,
    simulation_id: str,
    snapshots_repo: SnapshotsRepository,
    overrides_repo: CostOverridesRepository,
    creds_repo: PerProfileCredentialsRepository,
    tarifas_ml_xlsx_path: str | None = None,
) -> None:
    """Função top-level que roda a simulação.

    Extraída pra fora da classe pra evitar issues com asyncio.Task
    capturando self.
    """
    # 1) Carrega custos do XLSX
    try:
        custos = carregar_custos(custos_xlsx_path)
    except CustosXLSXError as e:
        raise RepricingError(f"falha ao carregar custos: {e}") from e

    # 2) Carrega overrides manuais (Leva 5.1)
    overrides = overrides_repo.load_all(profile_slug)

    # 3) Carrega override de tarifas reais por anúncio (tarifas_ml.xlsx,
    # alimentado pela extensão Chrome). Opcional — sem ele, segue cálculo
    # teórico (teto R$ 8,55 / frete da API). Quando presente, processar_um_item
    # usa o valor real do painel ML por MLB em vez do teto.
    tarifas_overrides = carregar_tarifas_ml(tarifas_ml_xlsx_path)

    # 3) Busca tokens ML
    creds = creds_repo.get_app_credentials(profile_slug)
    tokens = creds_repo.get_tokens(profile_slug, ml_user_id)

    async def save_refreshed(new_tokens: TokenSet) -> None:
        creds_repo.save_tokens(profile_slug, new_tokens)

    freight_cache = FreightCache(profile_slug)

    logger.info(
        "simulation_starting",
        simulation_id=simulation_id,
        skus_custos=len(custos),
        overrides=len(overrides),
        concorrencia=concorrencia,
    )

    # 4) Lista todos anúncios ativos + processa em batches
    async with MLClient(
        creds, tokens,
        on_tokens_refreshed=save_refreshed,
        max_concurrent=concorrencia,
    ) as ml:
        item_ids = await _listar_todos_ativos(ml, ml_user_id)
        logger.info(
            "simulation_listings_fetched",
            simulation_id=simulation_id,
            total=len(item_ids),
        )

        # Leva 5.8: mapa de items em promoções ativas no ML.
        # Custo: 1 + N_promoções requests (NÃO escala com items). Falha não
        # quebra simulação — só perde as colunas "Em campanha?" no relatório.
        mapa_promocoes = await montar_mapa_items_em_promocao(ml, ml_user_id)
        logger.info(
            "simulation_promotions_mapped",
            simulation_id=simulation_id,
            items_em_promocao=len(mapa_promocoes),
        )

        # Carrega checkpoint atual (caso retomada)
        snap_data = snapshots_repo.get(profile_slug, simulation_id) or {}
        ja_processados: set[str] = {
            s["item_id"] for s in snap_data.get("simulacoes", [])
        } | {
            e["item_id"] for e in snap_data.get("excecoes", []) if e.get("item_id")
        }

        pendentes = [iid for iid in item_ids if iid not in ja_processados]
        retomado = len(ja_processados) > 0

        # Atualiza total_ativos_analisados no snapshot
        snap_data["total_ativos_analisados"] = len(item_ids)
        snap_data["retomado_de_checkpoint"] = retomado
        if "simulacoes" not in snap_data:
            snap_data["simulacoes"] = []
        if "excecoes" not in snap_data:
            snap_data["excecoes"] = []
        snapshots_repo.save(profile_slug, simulation_id, snap_data)

        # Processa em batches sequenciais, paralelizando dentro de cada batch
        import asyncio
        for batch_start in range(0, len(pendentes), CHECKPOINT_A_CADA):
            batch = pendentes[batch_start: batch_start + CHECKPOINT_A_CADA]

            resultados = await asyncio.gather(
                *[
                    processar_um_item(
                        ml=ml,
                        item_id=iid,
                        custos=custos,
                        custos_manuais=overrides,
                        aliquota=aliquota,
                        cep=cep,
                        freight_cache=freight_cache,
                        margem_alvo_campanha=margem_alvo_campanha,
                        margem_minima=margem_minima,
                        pct_inflacao=pct_inflacao,
                        promocoes_do_item=mapa_promocoes.get(iid, []),
                        tarifas_override=tarifas_overrides,
                    )
                    for iid in batch
                ],
                return_exceptions=False,
            )

            # Append nos arrays e classifica
            for r in resultados:
                tipo = r.pop("tipo_resultado")
                if tipo == "simulacao":
                    snap_data["simulacoes"].append(r)
                else:
                    snap_data["excecoes"].append(r)

            # Atualiza contadores e persiste (CHECKPOINT)
            snap_data["total_simulados"] = len(snap_data["simulacoes"])
            snap_data["total_excecoes"] = len(snap_data["excecoes"])
            snap_data["total_processados"] = (
                snap_data["total_simulados"] + snap_data["total_excecoes"]
            )
            snapshots_repo.save(profile_slug, simulation_id, snap_data)

            logger.info(
                "simulation_checkpoint",
                simulation_id=simulation_id,
                processados=snap_data["total_processados"],
                total=len(item_ids),
            )

    # 5) Marca como completed
    snap_data["estado"] = "completed"
    snapshots_repo.save(profile_slug, simulation_id, snap_data)
    logger.info(
        "simulation_completed",
        simulation_id=simulation_id,
        total_simulados=snap_data["total_simulados"],
        total_excecoes=snap_data["total_excecoes"],
    )


async def _listar_todos_ativos(ml: MLClient, ml_user_id: int) -> list[str]:
    """Pagina /users/{id}/items/search?status=active até esgotar."""
    item_ids: list[str] = []
    offset = 0
    limit = 50

    while True:
        data = await ml.get(
            f"/users/{ml_user_id}/items/search",
            params={"status": "active", "limit": limit, "offset": offset},
        )
        results = data.get("results") or []
        item_ids.extend(results)

        total = data.get("paging", {}).get("total", 0)
        if offset + limit >= total or not results:
            break
        offset += limit

    return item_ids


# ─── Use cases secundários ──────────────────────────────────────────────────


class GetRepricingSimulationUseCase:
    """Carrega snapshot completo de uma simulação."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        snapshots_repo: SnapshotsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._snapshots_repo = snapshots_repo

    async def execute(
        self, profile_id: UUID, simulation_id: str,
    ) -> RepricingSimulation | None:
        profile = await self._profile_repo.get_by_id(profile_id)
        data = self._snapshots_repo.get(profile.slug, simulation_id)
        if data is None:
            return None
        return RepricingSimulation.model_validate(data)


class ListRepricingSimulationsUseCase:
    """Lista simulações do perfil (versão leve, sem arrays gigantes)."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        snapshots_repo: SnapshotsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._snapshots_repo = snapshots_repo

    async def execute(self, profile_id: UUID) -> list[RepricingSummary]:
        profile = await self._profile_repo.get_by_id(profile_id)
        raw = self._snapshots_repo.list_all(profile.slug)
        return [RepricingSummary.model_validate(s) for s in raw]


class DeleteRepricingSimulationUseCase:
    """Remove um snapshot do disco. Falha silenciosamente se já não existe."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        snapshots_repo: SnapshotsRepository,
        job_runner: JobRunner | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._snapshots_repo = snapshots_repo
        self._job_runner = job_runner or get_job_runner()

    async def execute(self, profile_id: UUID, simulation_id: str) -> bool:
        profile = await self._profile_repo.get_by_id(profile_id)
        # Cancela task em execução se houver (best-effort)
        self._job_runner.cancel(simulation_id)
        return self._snapshots_repo.delete(profile.slug, simulation_id)


class ExportRepricingXLSXUseCase:
    """Renderiza XLSX a partir do snapshot persistido."""

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        snapshots_repo: SnapshotsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._snapshots_repo = snapshots_repo

    async def execute(
        self, profile_id: UUID, simulation_id: str,
    ) -> tuple[bytes, str] | None:
        profile = await self._profile_repo.get_by_id(profile_id)
        data = self._snapshots_repo.get(profile.slug, simulation_id)
        if data is None:
            return None

        xlsx_bytes = render_simulation_xlsx(data)
        filename = f"simulacao_{profile.slug}_{simulation_id}.xlsx"
        return xlsx_bytes, filename


class ResumeInterruptedSimulationsUseCase:
    """No startup do backend, detecta simulações órfãs.

    Simulações com `estado=running` no disco mas sem task no JobRunner = órfãs.
    Marcamos como `interrupted` no snapshot e deixamos o usuário decidir se
    quer retomar manualmente (na UI).

    Não retomamos automaticamente porque:
    1. Pode haver razão legítima pro restart (deploy, mudança de config)
    2. Retomar sem aviso pode confundir o usuário
    3. Mais seguro pedir confirmação
    """

    def __init__(self, snapshots_repo: SnapshotsRepository) -> None:
        self._snapshots_repo = snapshots_repo

    async def execute(self, profile_slugs: list[str]) -> int:
        """Marca todas as simulações órfãs como interrupted. Retorna contagem."""
        marked = 0
        for slug in profile_slugs:
            for sim_id in self._snapshots_repo.list_interrupted(slug):
                data = self._snapshots_repo.get(slug, sim_id)
                if data is None:
                    continue
                data["estado"] = "interrupted"
                self._snapshots_repo.save(slug, sim_id, data)
                marked += 1
                logger.info(
                    "simulation_marked_interrupted",
                    simulation_id=sim_id,
                    profile_slug=slug,
                )
        return marked


class ResumeSimulationUseCase:
    """Retoma uma simulação `interrupted` reabrindo a task no JobRunner.

    Reaproveita todo o trabalho já feito (simulacoes + excecoes persistidos no
    snapshot) — só processa os item_ids ainda pendentes.
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        snapshots_repo: SnapshotsRepository,
        overrides_repo: CostOverridesRepository,
        job_runner: JobRunner | None = None,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._snapshots_repo = snapshots_repo
        self._overrides_repo = overrides_repo
        self._job_runner = job_runner or get_job_runner()

    async def execute(self, profile_id: UUID, simulation_id: str) -> bool:
        profile = await self._profile_repo.get_by_id(profile_id)
        data = self._snapshots_repo.get(profile.slug, simulation_id)
        if data is None:
            return False

        if data.get("estado") not in ("interrupted", "failed"):
            raise RepricingError(
                f"simulação não pode ser retomada (estado: {data.get('estado')})"
            )

        if profile.ml_user_id is None:
            raise NotConnectedError("perfil sem ML conectado")

        # Volta pra running e dispara task
        data["estado"] = "running"
        data["erro_fatal"] = None
        self._snapshots_repo.save(profile.slug, simulation_id, data)

        slug = profile.slug
        snapshots_repo = self._snapshots_repo
        overrides_repo = self._overrides_repo
        creds_repo = self._creds_repo

        async def _run() -> None:
            try:
                # Margens do snapshot original (preservam o que foi configurado
                # na hora de disparar a simulação, mesmo se o perfil mudou depois)
                from liraz_tools.infrastructure.pricing.repricing_calculator import (
                    MARGEM_ALVO_CAMPANHA,
                )

                await _orquestrar_simulacao(
                    profile_slug=slug,
                    profile_id=profile.id,
                    ml_user_id=profile.ml_user_id,  # type: ignore[arg-type]
                    aliquota=data["aliquota_imposto"],
                    cep=data["cep_destino"],
                    custos_xlsx_path=data["custos_xlsx_path"],
                    concorrencia=data.get("concorrencia_usada", CONCORRENCIA_PADRAO),
                    margem_alvo_campanha=data.get(
                        "margem_alvo_campanha", MARGEM_ALVO_CAMPANHA,
                    ),
                    margem_minima=profile.config.margem_minima,
                    pct_inflacao=profile.config.pct_inflacao_campanha,
                    simulation_id=simulation_id,
                    snapshots_repo=snapshots_repo,
                    overrides_repo=overrides_repo,
                    creds_repo=creds_repo,
                    tarifas_ml_xlsx_path=profile.config.tarifas_ml_xlsx_path,
                )
            except Exception as e:
                logger.exception("simulation_resume_failed", simulation_id=simulation_id)
                snap_data = snapshots_repo.get(slug, simulation_id) or {}
                snap_data["estado"] = "failed"
                snap_data["erro_fatal"] = str(e)
                snapshots_repo.save(slug, simulation_id, snap_data)

        self._job_runner.start(simulation_id, _run)
        return True


# ─── Override manual de margem por item ────────────────────────────────────


class ItemNotFoundError(RepricingError):
    """Item não está na lista de simulações (ou é uma exceção)."""


class InvalidMarginError(RepricingError):
    """Margem fora dos limites válidos ou não convergiu."""


class OverrideItemMarginUseCase:
    """Aplica margem customizada num item específico da simulação.

    Recalcula `deal_price` (Preço na Campanha) pra que o líquido projetado
    daquele item bata exatamente na margem informada. Preserva `preco_novo`
    intacto (a regra de venda continua a mesma — só o desconto da campanha
    muda).

    Faz busca binária real via ML pra obter taxas corretas no novo preço.
    Operação assíncrona típica leva 2-5s (15-20 chamadas ao ML).

    Guarda os valores originais na primeira vez que o override é aplicado,
    pra permitir reverter via `RevertItemMarginUseCase`.
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        creds_repo: PerProfileCredentialsRepository,
        snapshots_repo: SnapshotsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._creds_repo = creds_repo
        self._snapshots_repo = snapshots_repo

    async def execute(
        self,
        profile_id: UUID,
        simulation_id: str,
        item_id: str,
        margem_alvo: float,
    ) -> dict[str, Any]:
        from liraz_tools.infrastructure.ml.client import MLClient
        from liraz_tools.infrastructure.pricing.calculator import (
            calcular_taxas_anuncio,
        )
        from liraz_tools.infrastructure.pricing.costs_loader import (
            carregar_tarifas_ml,
        )
        from liraz_tools.infrastructure.pricing.freight_cache import FreightCache
        from liraz_tools.infrastructure.pricing.repricing_calculator import (
            _extrair_taxas_para_planilha,
            _margem_liquida,
            buscar_preco_para_margem,
        )

        if not (0.01 <= margem_alvo <= 0.80):
            raise InvalidMarginError(
                f"margem deve estar entre 1% e 80% (recebi "
                f"{margem_alvo * 100:.1f}%)"
            )

        profile = await self._profile_repo.get_by_id(profile_id)
        snap_data = self._snapshots_repo.get(profile.slug, simulation_id)
        if snap_data is None:
            raise RepricingError(f"simulação {simulation_id} não encontrada")

        # Acha o item
        item_index = None
        item = None
        for i, s in enumerate(snap_data.get("simulacoes", [])):
            if s["item_id"] == item_id:
                item_index = i
                item = s
                break

        if item is None or item_index is None:
            raise ItemNotFoundError(
                f"item {item_id} não está na lista de simulações da "
                f"{simulation_id} (pode estar nas exceções)"
            )

        if profile.ml_user_id is None:
            raise NotConnectedError("perfil sem ML conectado")

        # Busca binária pelo novo deal_price com margem custom via ML
        creds = self._creds_repo.get_app_credentials(profile.slug)
        tokens = self._creds_repo.get_tokens(profile.slug, profile.ml_user_id)

        async def save_refreshed(new_tokens: TokenSet) -> None:
            self._creds_repo.save_tokens(profile.slug, new_tokens)

        freight_cache = FreightCache(profile.slug)
        aliquota = snap_data["aliquota_imposto"]
        cep = snap_data["cep_destino"]
        custo = item["custo"]
        preco_novo = item["preco_novo"]
        # Overrides reais da planilha de tarifas (separada, alimentada pela
        # extensão Chrome). Opcional — sem ela, segue cálculo teórico.
        tarifas_overrides = carregar_tarifas_ml(profile.config.tarifas_ml_xlsx_path)

        async with MLClient(
            creds, tokens, on_tokens_refreshed=save_refreshed,
        ) as ml:
            novo_deal_price, erro = await buscar_preco_para_margem(
                ml=ml,
                item_id=item_id,
                custo=custo,
                margem_alvo=margem_alvo,
                aliquota=aliquota,
                cep=cep,
                freight_cache=freight_cache,
                preco_max=preco_novo,
                tarifas_override=tarifas_overrides,
            )
            if erro or novo_deal_price is None:
                raise InvalidMarginError(
                    f"não foi possível encontrar preço pra margem "
                    f"{margem_alvo * 100:.1f}%: {erro or 'sem solução'}"
                )

            # Captura taxas e líquido do novo deal price
            taxas_novo_deal = await calcular_taxas_anuncio(
                ml=ml,
                item_id=item_id,
                cep_destino=cep,
                freight_cache=freight_cache,
                preco_simulado=novo_deal_price,
                tarifas_override=tarifas_overrides,
            )
            if "erro" in taxas_novo_deal:
                raise RepricingError(
                    f"erro ao recalcular taxas: {taxas_novo_deal['erro']}"
                )

            liq_ml_deal, _ = _margem_liquida(
                taxas_novo_deal, novo_deal_price, aliquota,
            )
            liq_final_deal = liq_ml_deal - custo
            desconto_pct = (1 - novo_deal_price / preco_novo) * 100

            if desconto_pct < 0:
                raise InvalidMarginError(
                    f"essa margem geraria preço acima do preço novo "
                    f"(R$ {novo_deal_price:.2f} > R$ {preco_novo:.2f}). "
                    f"Use uma margem menor."
                )

        # Preserva originais na primeira vez que aplica override
        if item.get("margem_override_pct") is None:
            item["deal_price_original"] = item["deal_price"]
            item["desconto_pct_original"] = item["desconto_pct"]
            item["liq_final_deal_projetado_original"] = (
                item["liq_final_deal_projetado"]
            )
            item["taxas_deal_original"] = item.get("taxas_deal")

        # Defensivo: simulações antigas (pré-Leva 5.2.2) podem não ter
        # `margem_campanha_pct` no item. Preenche com a margem global da
        # simulação pra que reversões futuras tenham um valor pra voltar.
        if "margem_campanha_pct" not in item:
            margem_global = snap_data.get("margem_alvo_campanha", 0.20)
            item["margem_campanha_pct"] = round(margem_global * 100, 2)

        # Aplica novo valor
        item["margem_override_pct"] = round(margem_alvo * 100, 2)
        item["deal_price"] = round(novo_deal_price, 2)
        item["desconto_pct"] = round(desconto_pct, 2)
        item["liq_final_deal_projetado"] = round(liq_final_deal, 2)
        item["taxas_deal"] = _extrair_taxas_para_planilha(taxas_novo_deal)

        snap_data["simulacoes"][item_index] = item
        self._snapshots_repo.save(profile.slug, simulation_id, snap_data)

        logger.info(
            "margin_override_applied",
            simulation_id=simulation_id,
            item_id=item_id,
            margem=margem_alvo,
            novo_deal_price=novo_deal_price,
        )
        return cast("dict[str, Any]", item)


class RevertItemMarginUseCase:
    """Reverte um override de margem pro valor original calculado pela
    simulação inicial. Não precisa chamar o ML — só restaura do snapshot.
    """

    def __init__(
        self,
        profile_repo: SQLAlchemyProfileRepository,
        snapshots_repo: SnapshotsRepository,
    ) -> None:
        self._profile_repo = profile_repo
        self._snapshots_repo = snapshots_repo

    async def execute(
        self, profile_id: UUID, simulation_id: str, item_id: str,
    ) -> dict[str, Any]:
        profile = await self._profile_repo.get_by_id(profile_id)
        snap_data = self._snapshots_repo.get(profile.slug, simulation_id)
        if snap_data is None:
            raise RepricingError(f"simulação {simulation_id} não encontrada")

        item_index = None
        item = None
        for i, s in enumerate(snap_data.get("simulacoes", [])):
            if s["item_id"] == item_id:
                item_index = i
                item = s
                break

        if item is None or item_index is None:
            raise ItemNotFoundError(f"item {item_id} não encontrado")

        if item.get("margem_override_pct") is None:
            raise RepricingError(
                f"item {item_id} não tem override de margem pra reverter"
            )

        # Restaura originais
        item["deal_price"] = item["deal_price_original"]
        item["desconto_pct"] = item["desconto_pct_original"]
        item["liq_final_deal_projetado"] = (
            item["liq_final_deal_projetado_original"]
        )
        if item.get("taxas_deal_original") is not None:
            item["taxas_deal"] = item["taxas_deal_original"]

        item["margem_override_pct"] = None
        item["deal_price_original"] = None
        item["desconto_pct_original"] = None
        item["liq_final_deal_projetado_original"] = None
        item["taxas_deal_original"] = None

        snap_data["simulacoes"][item_index] = item
        self._snapshots_repo.save(profile.slug, simulation_id, snap_data)

        logger.info(
            "margin_override_reverted",
            simulation_id=simulation_id,
            item_id=item_id,
        )
        return cast("dict[str, Any]", item)
