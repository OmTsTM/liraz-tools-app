import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { applicationsApi } from "@/api/applications";
import { campaignKeys } from "@/features/campaigns/hooks";
import type { Application, StartCampaignPayload } from "@/types/api";

export const applicationKeys = {
  all: ["applications"] as const,
  latest: (profileId: string, campaignId: string) =>
    ["applications", "latest", profileId, campaignId] as const,
  detail: (profileId: string, applicationId: string) =>
    ["applications", "detail", profileId, applicationId] as const,
};

/**
 * Última aplicação da campanha com polling automático.
 *
 * Polling 2s enquanto `estado=running` (igual à simulação). Para de pollar
 * quando termina (completed/failed/interrupted). Retorna `null` se a
 * campanha nunca foi disparada.
 */
export function useLatestApplication(
  profileId: string | undefined,
  campaignId: string | undefined,
) {
  return useQuery({
    queryKey: applicationKeys.latest(profileId ?? "", campaignId ?? ""),
    queryFn: () => {
      if (!profileId || !campaignId) throw new Error("ids required");
      return applicationsApi.getLatest(profileId, campaignId);
    },
    enabled: Boolean(profileId && campaignId),
    refetchInterval: (query) => {
      const data = query.state.data as Application | null | undefined;
      // Polling 2s só enquanto rodando
      if (data?.estado === "running") return 2_000;
      return false;
    },
    // staleTime baixo pra atualizar rápido após mutations
    staleTime: 1_000,
  });
}

/**
 * Dispara a aplicação manual. No sucesso, invalida o cache da campanha
 * (status mudou pra `executando`) e popula o cache de application.
 */
export function useStartCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      payload,
    }: {
      profileId: string;
      campaignId: string;
      payload?: StartCampaignPayload;
    }) => applicationsApi.start(profileId, campaignId, payload ?? {}),

    onSuccess: (application, { profileId, campaignId }) => {
      // Popula o cache de "latest" — polling vai começar imediatamente
      qc.setQueryData(applicationKeys.latest(profileId, campaignId), application);
      // Invalida campaign (status mudou pra executando, +ml_campaign_id futuro)
      qc.invalidateQueries({ queryKey: campaignKeys.detail(profileId, campaignId) });
      qc.invalidateQueries({ queryKey: campaignKeys.list(profileId) });
    },
  });
}

/**
 * Reverte campanha aplicada. Mesma estrutura de cache do start — popula
 * latest pra polling iniciar imediatamente.
 */
export function useRevertCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
    }: {
      profileId: string;
      campaignId: string;
    }) => applicationsApi.revert(profileId, campaignId),

    onSuccess: (application, { profileId, campaignId }) => {
      qc.setQueryData(applicationKeys.latest(profileId, campaignId), application);
      qc.invalidateQueries({ queryKey: campaignKeys.detail(profileId, campaignId) });
      qc.invalidateQueries({ queryKey: campaignKeys.list(profileId) });
    },
  });
}
