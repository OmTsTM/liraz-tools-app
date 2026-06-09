import type { Campaign } from "@/types/api";

/** Janela mínima entre agora e o fim — sincronizada com o backend (30min). */
export const MIN_JANELA_MS = 30 * 60 * 1000;

export interface PlayCheckResult {
  /** True se pode disparar agora. */
  podeDisparar: boolean;
  /** Mensagem amigável quando não pode (pra tooltip). */
  motivoNaoPode: string | null;
  /** Milissegundos até o fim — útil pra mostrar contagem regressiva. */
  msAteOFim: number;
}

/**
 * Verifica se uma campanha pode ser disparada agora (botão Play habilitado).
 *
 * Regras (modelo passo3 — mai/2026):
 *   - Status precisa ser `agendada` ou `rascunho` com SKUs (ou simulação)
 *   - Rascunho aceita SKUs sem simulação — deal_price é computado no disparo
 *     via `StartCampaignPasso3UseCase`. Simulação ainda é aceita pra compat.
 *   - `data_fim + hora_fim` (ou 23:59 se hora_fim=null) precisa estar pelo
 *     menos 30min à frente de agora
 */
export function checkCanPlay(campaign: Campaign): PlayCheckResult {
  // Calcula data_fim + hora_fim em datetime local
  const horaFimStr = campaign.hora_fim ?? "23:59:00";
  const dtFim = new Date(`${campaign.data_fim}T${horaFimStr}`);
  const agora = new Date();
  const msAteOFim = dtFim.getTime() - agora.getTime();

  const temSkusOuSimulacao =
    Boolean(campaign.simulacao_id) || (campaign.skus_selecionados?.length ?? 0) > 0;
  const statusOk =
    campaign.status === "agendada" || (campaign.status === "rascunho" && temSkusOuSimulacao);

  if (!statusOk) {
    if (campaign.status === "rascunho" && !temSkusOuSimulacao) {
      return {
        podeDisparar: false,
        motivoNaoPode: 'Adicione anúncios em "Adicionar/editar SKUs" antes de disparar.',
        msAteOFim,
      };
    }
    if (campaign.status === "executando") {
      return {
        podeDisparar: false,
        motivoNaoPode: "Campanha já está executando.",
        msAteOFim,
      };
    }
    if (campaign.status === "ativa") {
      return {
        podeDisparar: false,
        motivoNaoPode: "Campanha já está ativa no ML.",
        msAteOFim,
      };
    }
    if (campaign.status === "finalizada") {
      return {
        podeDisparar: false,
        motivoNaoPode: "Campanha já finalizou.",
        msAteOFim,
      };
    }
    if (campaign.status === "cancelada") {
      return {
        podeDisparar: false,
        motivoNaoPode: "Reative a campanha antes de disparar.",
        msAteOFim,
      };
    }
    if (campaign.status === "falha") {
      return {
        podeDisparar: false,
        motivoNaoPode: "Campanha em estado de falha. Crie uma nova.",
        msAteOFim,
      };
    }
  }

  // Janela de tempo
  if (msAteOFim < MIN_JANELA_MS) {
    if (msAteOFim < 0) {
      return {
        podeDisparar: false,
        motivoNaoPode: "Data de fim já passou — aumente data_fim antes de disparar.",
        msAteOFim,
      };
    }
    const minutosRestantes = Math.floor(msAteOFim / 60_000);
    return {
      podeDisparar: false,
      motivoNaoPode: `Tempo até o fim (${minutosRestantes}min) é menor que o mínimo de 30min. Aumente data_fim ou hora_fim.`,
      msAteOFim,
    };
  }

  return { podeDisparar: true, motivoNaoPode: null, msAteOFim };
}

/**
 * Texto humano da duração restante até o fim da campanha (pro texto descritivo
 * do modal). Ex: "vai durar 3 dias e 2 horas", "vai durar 45 minutos".
 */
export function formatarJanelaRestante(msAteOFim: number): string {
  const totalMin = Math.floor(msAteOFim / 60_000);
  const dias = Math.floor(totalMin / (60 * 24));
  const horas = Math.floor((totalMin % (60 * 24)) / 60);
  const min = totalMin % 60;

  const partes: string[] = [];
  if (dias > 0) partes.push(`${dias} ${dias === 1 ? "dia" : "dias"}`);
  if (horas > 0) partes.push(`${horas} ${horas === 1 ? "hora" : "horas"}`);
  if (min > 0 && dias === 0) partes.push(`${min} ${min === 1 ? "minuto" : "minutos"}`);

  if (partes.length === 0) return "alguns minutos";
  if (partes.length === 1) return partes[0] ?? "alguns minutos";
  const ultimo = partes[partes.length - 1] ?? "";
  return `${partes.slice(0, -1).join(", ")} e ${ultimo}`;
}
