import { apiRequest } from "@/api/client";

// ─── Cobertura ──────────────────────────────────────────────────────────────

export interface CoberturaResponse {
  profile_id: string;
  campaign_id: string;
  ml_campaign_id: string;
  total_skus: number;
  coberto_na_origem: number;
  coberto_em_outras_apenas: number;
  descobertos: number;
  erros_consulta: number;
  percentual_cobertura: number;
  descobertos_detalhe: Array<{
    item_id: string;
    sku: string | null;
    titulo: string | null;
  }>;
  coberto_em_outras_detalhe: Array<{
    item_id: string;
    promocoes_ativas: string[];
  }>;
  erros_detalhe: Array<{ item_id: string; erro: string }>;
}

export interface CorrecaoCoberturaResponse {
  profile_id: string;
  campaign_id: string;
  dry_run: boolean;
  total_descobertos: number;
  total_corrigidos: number;
  total_falhas: number;
  total_skip_circuit_breaker: number;
  total_sem_custo_xlsx: number;
  primeiras_falhas: string[];
}

// ─── Oportunidades + execução ────────────────────────────────────────────────

export interface OportunidadeMigracao {
  item_id: string;
  sku: string | null;
  titulo: string | null;
  sold_quantity: number;
  campanha_destino_ml_id: string;
  campanha_destino_ml_nome: string | null;
  campanha_destino_ml_tipo: string;
  campanha_destino_ml_status: string;
  deal_price_sugerido: number;
  preco_atual: number;
  margem_pct_pos_migracao: number;
  margem_brl_pos_migracao: number;
  margem_pct_atual: number;
  pode_migrar_agora: boolean;
}

export interface OportunidadesResponse {
  campaign_origem_id: string;
  total_oportunidades: number;
  incluir_programadas: boolean;
  oportunidades: OportunidadeMigracao[];
}

export interface ExecutarMigracoesResponse {
  profile_id: string;
  campaign_origem_id: string;
  dry_run: boolean;
  total_oportunidades: number;
  total_skus_unicos: number;
  total_migracoes_tentadas: number;
  total_sucesso: number;
  total_ja_estava_no_ml: number;
  total_erros: number;
  primeiros_erros: string[];
}

// ─── Histórico ───────────────────────────────────────────────────────────────

export interface HistoricoRegistro {
  id: string;
  timestamp: string;
  campanha_origem_id: string | null;
  campanha_destino_ml_id: string;
  campanha_destino_ml_nome: string | null;
  campanha_destino_ml_tipo: string;
  item_id: string;
  sku: string | null;
  operacao: string;
  destino_status: string | null;
  deal_price: number | null;
  margem_pct_prevista: number | null;
  sucesso: boolean;
  dry_run: boolean;
  erro_detalhe: string | null;
}

export interface HistoricoResponse {
  profile_id: string;
  total: number;
  registros: HistoricoRegistro[];
}

// ─── Scheduler ───────────────────────────────────────────────────────────────

export interface SchedulerStateResponse {
  profile_id: string;
  scheduler_globally_enabled: boolean;
  ultima_execucao_iso: string | null;
}

// ─── API ─────────────────────────────────────────────────────────────────────

export const migracaoApi = {
  cobertura(profileId: string, campaignId: string): Promise<CoberturaResponse> {
    return apiRequest<CoberturaResponse>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/cobertura`,
    );
  },

  corrigirCobertura(
    profileId: string,
    campaignId: string,
    dryRun: boolean,
  ): Promise<CorrecaoCoberturaResponse> {
    return apiRequest<CorrecaoCoberturaResponse>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/cobertura/corrigir?dry_run=${dryRun}`,
      { method: "POST" },
    );
  },

  oportunidades(
    profileId: string,
    campaignId: string,
    incluirProgramadas = true,
  ): Promise<OportunidadesResponse> {
    return apiRequest<OportunidadesResponse>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/oportunidades-migracao?incluir_programadas=${incluirProgramadas}`,
    );
  },

  executar(
    profileId: string,
    campaignId: string,
    dryRun: boolean,
    apenasSkus?: string[],
  ): Promise<ExecutarMigracoesResponse> {
    let qs = `?dry_run=${dryRun}`;
    if (apenasSkus && apenasSkus.length > 0) {
      qs += `&apenas_skus=${encodeURIComponent(apenasSkus.join(","))}`;
    }
    return apiRequest<ExecutarMigracoesResponse>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/executar-migracoes${qs}`,
      { method: "POST" },
    );
  },

  historico(profileId: string, limit = 200): Promise<HistoricoResponse> {
    return apiRequest<HistoricoResponse>(
      `/api/profiles/${profileId}/migracao/historico?limit=${limit}`,
    );
  },

  schedulerState(profileId: string): Promise<SchedulerStateResponse> {
    return apiRequest<SchedulerStateResponse>(
      `/api/profiles/${profileId}/migracao/scheduler-state`,
    );
  },

  schedulerTrigger(profileId: string): Promise<{ status: string }> {
    return apiRequest<{ status: string }>(`/api/profiles/${profileId}/migracao/scheduler-trigger`, {
      method: "POST",
    });
  },
};
