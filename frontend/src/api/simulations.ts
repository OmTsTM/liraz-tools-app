import { apiRequest } from "@/api/client";
import type { RepricingLineItem, SimulationDetail, SimulationSummary } from "@/types/api";

export const simulationsApi = {
  /**
   * Dispara nova simulação em background. Retorna `simulation_id`
   * pra polling subsequente.
   */
  async start(profileId: string, concorrencia = 8): Promise<{ simulation_id: string }> {
    return apiRequest<{ simulation_id: string }>(`/api/profiles/${profileId}/simulations`, {
      method: "POST",
      body: { concorrencia },
    });
  },

  /** Lista todas as simulações do perfil (mais recentes primeiro). */
  async list(profileId: string): Promise<SimulationSummary[]> {
    return apiRequest<SimulationSummary[]>(`/api/profiles/${profileId}/simulations`);
  },

  /** Detalhe completo (arrays + progresso). Usado pelo polling. */
  async get(profileId: string, simulationId: string): Promise<SimulationDetail> {
    return apiRequest<SimulationDetail>(`/api/profiles/${profileId}/simulations/${simulationId}`);
  },

  /** Retoma simulação interrupted/failed. */
  async resume(profileId: string, simulationId: string): Promise<void> {
    await apiRequest<{ resumed: boolean }>(
      `/api/profiles/${profileId}/simulations/${simulationId}/resume`,
      { method: "POST" },
    );
  },

  /** Apaga snapshot do disco. */
  async delete(profileId: string, simulationId: string): Promise<void> {
    await apiRequest<void>(`/api/profiles/${profileId}/simulations/${simulationId}`, {
      method: "DELETE",
    });
  },

  /** URL absoluta do XLSX (usar com window.open ou <a download>). */
  getXlsxDownloadUrl(profileId: string, simulationId: string): string {
    const base = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
    return `${base}/api/profiles/${profileId}/simulations/${simulationId}/xlsx`;
  },

  /**
   * Aplica margem custom num item específico. Backend recalcula deal_price
   * via busca binária no ML — operação demora ~2-5s.
   */
  async overrideMargin(
    profileId: string,
    simulationId: string,
    itemId: string,
    margemAlvo: number,
  ): Promise<RepricingLineItem> {
    return apiRequest<RepricingLineItem>(
      `/api/profiles/${profileId}/simulations/${simulationId}/items/${itemId}/override-margin`,
      { method: "POST", body: { margem_alvo: margemAlvo } },
    );
  },

  /** Reverte override pro valor calculado originalmente. */
  async revertMargin(
    profileId: string,
    simulationId: string,
    itemId: string,
  ): Promise<RepricingLineItem> {
    return apiRequest<RepricingLineItem>(
      `/api/profiles/${profileId}/simulations/${simulationId}/items/${itemId}/override-margin`,
      { method: "DELETE" },
    );
  },
};
