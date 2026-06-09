import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { repricingMassaApi } from "@/api/repricing-massa";

/**
 * Simula reprecificação em massa: pra cada item_id, calcula P passo3
 * (margem alvo Q2 do perfil) + margem prevista. Não muta nada.
 */
export function useSimularRepricingMassa() {
  return useMutation({
    mutationFn: ({ profileId, itemIds }: { profileId: string; itemIds: string[] }) =>
      repricingMassaApi.simular(profileId, itemIds),
  });
}

/**
 * Aplica PUT /items em massa pros preços confirmados. Invalida caches
 * de listings/skus pra refletir os novos preços-base na UI.
 */
export function useAplicarRepricingMassa() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      precos,
    }: {
      profileId: string;
      precos: Record<string, number>;
    }) => repricingMassaApi.aplicar(profileId, precos),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({
        queryKey: ["listings", "skus-com-promocoes", profileId],
      });
      // Sessão nova surgiu — invalida lista de sessões pro botão "Reverter
      // última" aparecer com o ID correto.
      qc.invalidateQueries({
        queryKey: ["repricing-massa", "sessoes", profileId],
      });
    },
  });
}

/**
 * Lista as últimas sessões de Reprecificar Tudo desse perfil. Frontend usa
 * pra mostrar botão "Reverter última execução" com data + qtd de itens.
 */
export function useRepricingSessoes(profileId: string | undefined) {
  return useQuery({
    queryKey: ["repricing-massa", "sessoes", profileId ?? ""],
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      return repricingMassaApi.listarSessoes(profileId, 5);
    },
    enabled: Boolean(profileId),
    // Sessões só mudam quando você aplica novo repricing — não precisa polling
    staleTime: 60 * 1000,
  });
}

/**
 * Reverte uma sessão específica via PUT pros preços anteriores. Idempotente.
 */
export function useReverterSessaoRepricing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      sessionId,
    }: {
      profileId: string;
      sessionId: string;
    }) => repricingMassaApi.reverterSessao(profileId, sessionId),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({
        queryKey: ["listings", "skus-com-promocoes", profileId],
      });
      qc.invalidateQueries({
        queryKey: ["repricing-massa", "sessoes", profileId],
      });
    },
  });
}
