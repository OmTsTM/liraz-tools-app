import { useQuery } from "@tanstack/react-query";

import { listingsApi } from "@/api/listings";

/**
 * Leva 5.12: lista anúncios ativos do vendedor + promoções de cada.
 *
 * Endpoint demora ~8-15s pra loja com 200 anúncios (várias chamadas ML
 * em paralelo, sem cache server-side). Cache do React Query em 5min
 * pra não refazer ao reabrir o modal.
 */
export function useSkusComPromocoes(
  profileId: string | undefined,
  incluirProgramadas = true,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ["listings", "skus-com-promocoes", profileId, incluirProgramadas],
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      return listingsApi.listarSkusComPromocoes(profileId, incluirProgramadas);
    },
    // `enabled` permite adiar a busca: o endpoint varre TODOS os anúncios da loja
    // (~14s) — só faz sentido quando o modal de seleção abre, não no load da tela.
    enabled: Boolean(profileId) && (options?.enabled ?? true),
    // Endpoint é caro. Cache 5min cobre o caso de abrir/fechar/reabrir o modal.
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
