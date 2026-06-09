import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { simulationsApi } from "@/api/simulations";
import type { SimulationDetail } from "@/types/api";

export const simulationKeys = {
  all: ["simulations"] as const,
  list: (profileId: string) => ["simulations", "list", profileId] as const,
  detail: (profileId: string, simulationId: string) =>
    ["simulations", "detail", profileId, simulationId] as const,
};

/**
 * Lista de simulações do perfil. Refetch a cada 5s pra refletir mudanças
 * de estado (running → completed) em simulações que estão sendo executadas
 * em background.
 */
export function useSimulations(profileId: string | undefined) {
  return useQuery({
    queryKey: simulationKeys.list(profileId ?? ""),
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      return simulationsApi.list(profileId);
    },
    enabled: Boolean(profileId),
    refetchInterval: (query) => {
      // Se alguma simulação está running, refetch agressivo
      const data = query.state.data;
      if (data?.some((s) => s.estado === "running")) return 5_000;
      return 60_000; // 1 minuto pra refletir mudanças manuais
    },
  });
}

/**
 * Detalhe de uma simulação com polling automático enquanto estado=running.
 *
 * Polling intervalo:
 * - 2s enquanto running (resposta fresca pro progresso)
 * - Para de polling quando completed/failed/interrupted
 */
export function useSimulation(profileId: string | undefined, simulationId: string | undefined) {
  return useQuery({
    queryKey: simulationKeys.detail(profileId ?? "", simulationId ?? ""),
    queryFn: () => {
      if (!profileId || !simulationId) throw new Error("ids required");
      return simulationsApi.get(profileId, simulationId);
    },
    enabled: Boolean(profileId && simulationId),
    refetchInterval: (query) => {
      const data = query.state.data as SimulationDetail | undefined;
      // Polling 2s só enquanto rodando
      if (data?.estado === "running") return 2_000;
      return false;
    },
  });
}

export function useStartSimulation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      concorrencia = 8,
    }: {
      profileId: string;
      concorrencia?: number;
    }) => simulationsApi.start(profileId, concorrencia),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({ queryKey: simulationKeys.list(profileId) });
    },
  });
}

export function useResumeSimulation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      simulationId,
    }: {
      profileId: string;
      simulationId: string;
    }) => simulationsApi.resume(profileId, simulationId),
    onSuccess: (_, { profileId, simulationId }) => {
      qc.invalidateQueries({ queryKey: simulationKeys.list(profileId) });
      qc.invalidateQueries({
        queryKey: simulationKeys.detail(profileId, simulationId),
      });
    },
  });
}

export function useDeleteSimulation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      simulationId,
    }: {
      profileId: string;
      simulationId: string;
    }) => simulationsApi.delete(profileId, simulationId),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({ queryKey: simulationKeys.list(profileId) });
    },
  });
}

/**
 * Aplica margem custom num item. Atualiza o cache da simulação in-place
 * com o item retornado pelo backend (mais leve que refetch da simulação
 * inteira, que pode ter 100+ itens).
 */
export function useOverrideItemMargin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      simulationId,
      itemId,
      margemAlvo,
    }: {
      profileId: string;
      simulationId: string;
      itemId: string;
      margemAlvo: number;
    }) => simulationsApi.overrideMargin(profileId, simulationId, itemId, margemAlvo),

    onSuccess: (updatedItem, { profileId, simulationId, itemId }) => {
      // Atualiza in-place no cache da simulação
      qc.setQueryData<SimulationDetail>(simulationKeys.detail(profileId, simulationId), (old) => {
        if (!old) return old;
        return {
          ...old,
          simulacoes: old.simulacoes.map((s) =>
            s.item_id === itemId ? (updatedItem as SimulationDetail["simulacoes"][number]) : s,
          ),
        };
      });
    },
  });
}

export function useRevertItemMargin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      simulationId,
      itemId,
    }: {
      profileId: string;
      simulationId: string;
      itemId: string;
    }) => simulationsApi.revertMargin(profileId, simulationId, itemId),

    onSuccess: (revertedItem, { profileId, simulationId, itemId }) => {
      qc.setQueryData<SimulationDetail>(simulationKeys.detail(profileId, simulationId), (old) => {
        if (!old) return old;
        return {
          ...old,
          simulacoes: old.simulacoes.map((s) =>
            s.item_id === itemId ? (revertedItem as SimulationDetail["simulacoes"][number]) : s,
          ),
        };
      });
    },
  });
}
