import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { migracaoApi } from "@/api/migracao";

export const migracaoKeys = {
  all: ["migracao"] as const,
  cobertura: (profileId: string, campaignId: string) =>
    ["migracao", "cobertura", profileId, campaignId] as const,
  oportunidades: (profileId: string, campaignId: string) =>
    ["migracao", "oportunidades", profileId, campaignId] as const,
  historico: (profileId: string) => ["migracao", "historico", profileId] as const,
  schedulerState: (profileId: string) => ["migracao", "scheduler-state", profileId] as const,
};

export function useCobertura(
  profileId: string | undefined,
  campaignId: string | undefined,
  options: { enabled?: boolean } = {},
) {
  const { enabled = true } = options;
  return useQuery({
    queryKey: migracaoKeys.cobertura(profileId ?? "", campaignId ?? ""),
    queryFn: () => {
      if (!profileId || !campaignId) throw new Error("ids required");
      return migracaoApi.cobertura(profileId, campaignId);
    },
    enabled: enabled && Boolean(profileId && campaignId),
    // Pesado (consulta 100+ chamadas ML), só puxa quando user pede explicitamente
    // ou abre a aba. Não auto-refetch.
    refetchOnWindowFocus: false,
    staleTime: 1000 * 60 * 5, // 5min
  });
}

export function useCorrigirCobertura() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      dryRun,
    }: {
      profileId: string;
      campaignId: string;
      dryRun: boolean;
    }) => migracaoApi.corrigirCobertura(profileId, campaignId, dryRun),
    onSuccess: (_, { profileId, campaignId }) => {
      qc.invalidateQueries({
        queryKey: migracaoKeys.cobertura(profileId, campaignId),
      });
      qc.invalidateQueries({ queryKey: migracaoKeys.historico(profileId) });
    },
  });
}

export function useOportunidades(
  profileId: string | undefined,
  campaignId: string | undefined,
  options: { enabled?: boolean } = {},
) {
  const { enabled = true } = options;
  return useQuery({
    queryKey: migracaoKeys.oportunidades(profileId ?? "", campaignId ?? ""),
    queryFn: () => {
      if (!profileId || !campaignId) throw new Error("ids required");
      return migracaoApi.oportunidades(profileId, campaignId);
    },
    enabled: enabled && Boolean(profileId && campaignId),
    refetchOnWindowFocus: false,
    staleTime: 1000 * 60 * 5,
  });
}

export function useExecutarMigracoes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      dryRun,
      apenasSkus,
    }: {
      profileId: string;
      campaignId: string;
      dryRun: boolean;
      apenasSkus?: string[];
    }) => migracaoApi.executar(profileId, campaignId, dryRun, apenasSkus),
    onSuccess: (_, { profileId, campaignId }) => {
      qc.invalidateQueries({
        queryKey: migracaoKeys.oportunidades(profileId, campaignId),
      });
      qc.invalidateQueries({
        queryKey: migracaoKeys.cobertura(profileId, campaignId),
      });
      qc.invalidateQueries({ queryKey: migracaoKeys.historico(profileId) });
    },
  });
}

export function useHistoricoMigracao(
  profileId: string | undefined,
  limit = 200,
  options: { enabled?: boolean } = {},
) {
  const { enabled = true } = options;
  return useQuery({
    queryKey: [...migracaoKeys.historico(profileId ?? ""), limit],
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      return migracaoApi.historico(profileId, limit);
    },
    enabled: enabled && Boolean(profileId),
    refetchOnWindowFocus: false,
    staleTime: 1000 * 30,
  });
}

export function useSchedulerState(profileId: string | undefined) {
  return useQuery({
    queryKey: migracaoKeys.schedulerState(profileId ?? ""),
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      return migracaoApi.schedulerState(profileId);
    },
    enabled: Boolean(profileId),
    refetchInterval: 60000, // refresh a cada 1min
  });
}

export function useTriggerScheduler() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId }: { profileId: string }) => migracaoApi.schedulerTrigger(profileId),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({ queryKey: migracaoKeys.historico(profileId) });
      qc.invalidateQueries({
        queryKey: migracaoKeys.schedulerState(profileId),
      });
    },
  });
}
