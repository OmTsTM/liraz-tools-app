import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { pricingApi } from "@/api/pricing";
import type { FeeReport } from "@/types/api";

export const pricingKeys = {
  all: ["pricing"] as const,
  feeReport: (profileId: string) => ["pricing", "feeReport", profileId] as const,
};

/**
 * Busca o relatório de taxas/margem.
 *
 * staleTime de 15min bate com o TTL do cache do backend — não chega a ser
 * estritamente necessário porque o backend é fonte da verdade, mas evita
 * round-trips desnecessários.
 *
 * Como a geração inicial pode levar 30-60s (chama API ML pra cada anúncio),
 * o React Query mostra `isPending=true` durante esse período.
 */
export function useFeeReport(profileId: string | undefined) {
  return useQuery({
    queryKey: pricingKeys.feeReport(profileId ?? ""),
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      // Sem passar AbortSignal de propósito: quando o usuário navega
      // pra outra tela, queremos que a request continue rodando em
      // background. Ao voltar pra ListingsPage, o cache já estará
      // populado e a UI mostra direto sem reload.
      return pricingApi.getFeeReport(profileId);
    },
    enabled: Boolean(profileId),
    staleTime: 15 * 60 * 1000, // 15 minutos
    retry: 1, // 1 retry — se for erro de config, não adianta retentar
  });
}

export function useRefreshFeeReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => pricingApi.refreshFeeReport(profileId),
    onSuccess: (_, profileId) => {
      qc.invalidateQueries({ queryKey: pricingKeys.feeReport(profileId) });
    },
  });
}

export function useUploadCustosXLSX() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      file,
    }: {
      profileId: string;
      file: File;
    }) => pricingApi.uploadCustosXLSX(profileId, file),
    onSuccess: (_, { profileId }) => {
      // Recarrega perfil (config.custos_xlsx_path mudou)
      qc.invalidateQueries({ queryKey: ["profiles"] });
      // Invalida cache de fee report (config relevante mudou)
      qc.invalidateQueries({ queryKey: pricingKeys.feeReport(profileId) });
    },
  });
}

export function useUploadTarifasMLXLSX() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      file,
    }: {
      profileId: string;
      file: File;
    }) => pricingApi.uploadTarifasMLXLSX(profileId, file),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
      qc.invalidateQueries({ queryKey: pricingKeys.feeReport(profileId) });
    },
  });
}

/**
 * Salva override de custo + atualiza o cache do React Query in-place
 * (substitui a linha afetada no array) pra evitar refetch completo.
 */
export function useSetCostOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      key,
      value,
    }: {
      profileId: string;
      key: string;
      value: number;
    }) => pricingApi.setCostOverride(profileId, key, value),
    onSuccess: (response, { profileId }) => {
      // Atualiza a linha afetada no cache do react-query pra refletir
      // lucro/margem novos imediatamente, sem refetch.
      qc.setQueryData<FeeReport>(pricingKeys.feeReport(profileId), (old) => {
        if (!old || !response.updated_listing) return old;
        return mergeUpdatedListing(old, response.updated_listing);
      });
    },
  });
}

export function useRemoveCostOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      key,
    }: {
      profileId: string;
      key: string;
    }) => pricingApi.removeCostOverride(profileId, key),
    onSuccess: (response, { profileId }) => {
      qc.setQueryData<FeeReport>(pricingKeys.feeReport(profileId), (old) => {
        if (!old || !response.updated_listing) return old;
        return mergeUpdatedListing(old, response.updated_listing);
      });
    },
  });
}

/**
 * Substitui uma linha no array de listings pelo objeto recalculado.
 * Match por item_id (sempre presente, único).
 */
function mergeUpdatedListing(report: FeeReport, updated: FeeReport["listings"][number]): FeeReport {
  return {
    ...report,
    listings: report.listings.map((l) => (l.item_id === updated.item_id ? updated : l)),
  };
}
