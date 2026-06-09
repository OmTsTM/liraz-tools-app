import { apiRequest } from "@/api/client";

export interface RepricingMassaSimulateItem {
  item_id: string;
  sku: string | null;
  titulo: string | null;
  preco_atual: number | null;
  preco_novo: number | null;
  margem_atual_pct: number | null;
  margem_nova_pct: number | null;
  custo: number | null;
  fonte_custo: string | null;
  erro: string | null;
}

export interface RepricingMassaSimulateResponse {
  results: RepricingMassaSimulateItem[];
}

export interface RepricingMassaApplyErro {
  item_id: string;
  operacao: string;
  erro: string;
}

export interface RepricingMassaApplyResponse {
  ok: boolean;
  aplicados: string[];
  ja_no_preco: string[];
  erros: RepricingMassaApplyErro[];
  /** UUID da sessão persistida no backend. None se nenhum PUT foi feito. */
  session_id: string | null;
}

export interface RepricingMassaSessionResumo {
  session_id: string;
  qtd_itens: number;
  criado_em_iso: string;
  revertido_em_iso: string | null;
}

export interface RepricingMassaSessionsResponse {
  sessions: RepricingMassaSessionResumo[];
}

export interface RepricingMassaRevertResponse {
  ok: boolean;
  revertidos: string[];
  ja_no_preco: string[];
  erros: RepricingMassaApplyErro[];
}

export const repricingMassaApi = {
  async simular(profileId: string, item_ids: string[]): Promise<RepricingMassaSimulateResponse> {
    return apiRequest<RepricingMassaSimulateResponse>(
      `/api/profiles/${profileId}/repricing-massa/simular`,
      { method: "POST", body: { item_ids } },
    );
  },

  async aplicar(
    profileId: string,
    precos: Record<string, number>,
  ): Promise<RepricingMassaApplyResponse> {
    return apiRequest<RepricingMassaApplyResponse>(
      `/api/profiles/${profileId}/repricing-massa/aplicar`,
      { method: "POST", body: { precos } },
    );
  },

  async listarSessoes(profileId: string, limit = 10): Promise<RepricingMassaSessionsResponse> {
    return apiRequest<RepricingMassaSessionsResponse>(
      `/api/profiles/${profileId}/repricing-massa/sessoes?limit=${limit}`,
    );
  },

  async reverterSessao(
    profileId: string,
    sessionId: string,
  ): Promise<RepricingMassaRevertResponse> {
    return apiRequest<RepricingMassaRevertResponse>(
      `/api/profiles/${profileId}/repricing-massa/reverter/${sessionId}`,
      { method: "POST" },
    );
  },
};
