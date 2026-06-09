"""Entidades do contexto Repricing (simulação de reprecificação).

`RepricingSimulation` é o objeto completo serializado em snapshot JSON.
`RepricingLineItem` é uma linha (caso feliz) da lista `simulacoes`.
`RepricingException` é uma linha da lista `excecoes`.

Estado da simulação:
- `running`: em execução (asyncio.Task ativa, checkpoint sendo atualizado)
- `completed`: terminou com sucesso (mesmo que com algumas exceções)
- `failed`: erro fatal no orquestrador (raro)
- `interrupted`: backend foi reiniciado no meio; pode ser retomado

Compatibilidade com MCP: mantemos os mesmos field names e schema do JSON
que `tools/reprecificacao.py` produz, então um snapshot gerado aqui
pode ser lido pelo MCP e vice-versa.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SimulationState = Literal["running", "completed", "failed", "interrupted"]


Fase1Acao = Literal["mantido", "preco_aumentado", "preco_travado_abaixo_79"]


ExceptionType = Literal[
    "erro_taxas_atuais",
    "modalidade_free",
    "sem_custo_cadastrado",
    "falha_busca_preco",
    "nao_alcanca_25pct_abaixo_79",
    "falha_calculo_deal_price",
    "desconto_fora_intervalo",
    "erro_inesperado",
]


class TaxasParaPlanilha(BaseModel):
    """Sub-bloco com taxas formatadas pra layout da planilha.

    Modalidade "Clássico" preenche `tarifa_classico`, "Premium" preenche
    `tarifa_premium`. O outro fica 0. Facilita XLSX renderer.
    """
    model_config = ConfigDict(extra="ignore")

    preco: float
    tarifa_classico: float
    tarifa_premium: float
    percentual_tarifa: float
    tarifa_fixa: float
    frete: float


class RepricingLineItem(BaseModel):
    """Uma linha de simulação (caso feliz — vai pro XLSX principal)."""
    model_config = ConfigDict(extra="ignore")

    item_id: str
    sku: str | None
    titulo: str | None
    modalidade: str | None
    custo: float
    fonte_custo: str

    taxas_atual: TaxasParaPlanilha
    taxas_novo: TaxasParaPlanilha
    taxas_deal: TaxasParaPlanilha | None = None
    """Taxas do `deal_price` (preço da campanha). Pode ser None em snapshots
    antigos antes desta feature."""

    margem_atual_pct: float
    liq_final_atual: float

    fase1_acao: Fase1Acao
    fase1_motivo: str

    preco_novo: float
    deal_price: float
    desconto_pct: float
    liq_final_deal_projetado: float

    margem_campanha_pct: float = 20.0
    """Margem da campanha em % (0-100). Default 20% — global da simulação.
    Pode ser sobrescrita por item via override."""

    # Campos de override (preenchidos só se o usuário editou a margem)
    margem_override_pct: float | None = None
    """Se preenchido, é a margem alvo usada pra recalcular este item.
    Senão, usou a `margem_campanha_pct` global."""

    deal_price_original: float | None = None
    """Snapshot do `deal_price` calculado pela simulação inicial. Permite
    reverter o override depois."""
    desconto_pct_original: float | None = None
    liq_final_deal_projetado_original: float | None = None
    taxas_deal_original: TaxasParaPlanilha | None = None


class RepricingException(BaseModel):
    """Uma linha de exceção — algo impediu a simulação completa do item."""
    model_config = ConfigDict(extra="ignore")

    item_id: str
    sku: str | None = None
    titulo: str | None = None
    tipo: ExceptionType
    motivo: str | None = None
    detalhe: str | None = None


class RepricingSimulation(BaseModel):
    """Snapshot completo da simulação. Serializa direto pro JSON."""
    model_config = ConfigDict(extra="ignore")

    simulation_id: str
    criado_em: str  # ISO datetime
    atualizado_em: str | None = None  # ISO datetime; atualizado a cada save
    estado: SimulationState

    # Config usada (preservada no snapshot — mudanças posteriores no perfil
    # não afetam simulações antigas)
    aliquota_imposto: float
    cep_destino: str
    custos_xlsx_path: str | None
    concorrencia_usada: int
    retomado_de_checkpoint: bool = False

    # Margens configuradas (decimal 0-1)
    margem_alvo_aumento: float = 0.50
    limite_margem_aumento: float = 0.30
    margem_alvo_campanha: float = 0.20

    # Contadores (atualizados ao longo do processamento)
    total_ativos_analisados: int = 0
    total_simulados: int = 0
    total_excecoes: int = 0
    total_processados: int = 0
    """Soma de simulados + excecoes. Usado pra mostrar progresso na UI."""

    # Mensagem de erro caso estado='failed' (orquestrador falhou)
    erro_fatal: str | None = None

    # Listas (vão crescendo a cada checkpoint)
    simulacoes: list[dict[str, Any]] = []
    excecoes: list[dict[str, Any]] = []

    @classmethod
    def now_iso(cls) -> str:
        """ISO datetime UTC pra criado_em/atualizado_em."""
        return datetime.now(UTC).isoformat()

    @property
    def progress_pct(self) -> float:
        """Progresso 0-1. Útil pra UI mostrar barra de progresso."""
        if self.total_ativos_analisados == 0:
            return 0.0
        return self.total_processados / self.total_ativos_analisados


class RepricingSummary(BaseModel):
    """Versão "leve" pra listagem — sem os arrays gigantes de simulações.

    Usado pelo endpoint GET /api/profiles/{id}/simulations que lista todas.
    """
    model_config = ConfigDict(extra="ignore")

    simulation_id: str
    criado_em: str
    estado: SimulationState
    total_ativos_analisados: int
    total_simulados: int
    total_excecoes: int
    total_processados: int
    retomado_de_checkpoint: bool
