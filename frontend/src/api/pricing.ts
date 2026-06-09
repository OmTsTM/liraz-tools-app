import { apiRequest } from "@/api/client";
import type { FeeReport, ListingFees, UploadCustosXLSXResponse } from "@/types/api";

export interface CostOverrideMutationResponse {
  key: string;
  updated_listing: ListingFees | null;
}

export const pricingApi = {
  /**
   * Retorna o relatório completo de taxas/margem.
   * Backend cacheia 15min — chamadas seguidas são instantâneas.
   */
  async getFeeReport(profileId: string, signal?: AbortSignal): Promise<FeeReport> {
    return apiRequest<FeeReport>(`/api/profiles/${profileId}/listings/with-fees`, { signal });
  },

  /**
   * Invalida o cache pra forçar regeração no próximo /with-fees.
   */
  async refreshFeeReport(profileId: string): Promise<void> {
    await apiRequest<void>(`/api/profiles/${profileId}/listings/with-fees/refresh`, {
      method: "POST",
    });
  },

  /**
   * URL absoluta do XLSX. Usar em window.open ou <a download>.
   */
  getXlsxDownloadUrl(profileId: string): string {
    const base = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
    return `${base}/api/profiles/${profileId}/listings/with-fees/xlsx`;
  },

  /**
   * Faz upload da planilha de custos (.xlsx) via multipart/form-data.
   */
  async uploadCustosXLSX(profileId: string, file: File): Promise<UploadCustosXLSXResponse> {
    const formData = new FormData();
    formData.append("file", file);

    const base = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
    const response = await fetch(`${base}/api/profiles/${profileId}/config/custos-xlsx`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body.detail) detail = body.detail;
      } catch {
        // ignora — usa o status code
      }
      throw new Error(detail);
    }

    return response.json();
  },

  /**
   * Faz upload da planilha de TARIFAS REAIS por anúncio (.xlsx) — alimentada
   * pela extensão Chrome de captura do painel ML. Opcional: quando presente,
   * os MLBs cadastrados sobrescrevem o teto teórico no cálculo de margem E
   * o calculator pula a chamada `/items/{id}/shipping_options`.
   */
  async uploadTarifasMLXLSX(profileId: string, file: File): Promise<UploadCustosXLSXResponse> {
    const formData = new FormData();
    formData.append("file", file);

    const base = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
    const response = await fetch(`${base}/api/profiles/${profileId}/config/tarifas-ml-xlsx`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body.detail) detail = body.detail;
      } catch {
        // ignora
      }
      throw new Error(detail);
    }

    return response.json();
  },

  /**
   * Salva/atualiza override manual de custo. Key pode ser SKU ou MLB.
   * Backend recalcula lucro/margem da linha afetada e devolve a versão nova.
   */
  async setCostOverride(
    profileId: string,
    key: string,
    value: number,
  ): Promise<CostOverrideMutationResponse> {
    return apiRequest<CostOverrideMutationResponse>(`/api/profiles/${profileId}/cost-overrides`, {
      method: "POST",
      body: { key, value },
    });
  },

  /**
   * Remove override — backend recarrega o custo natural (XLSX ou null).
   */
  async removeCostOverride(profileId: string, key: string): Promise<CostOverrideMutationResponse> {
    return apiRequest<CostOverrideMutationResponse>(
      `/api/profiles/${profileId}/cost-overrides/${encodeURIComponent(key)}`,
      { method: "DELETE" },
    );
  },
};
