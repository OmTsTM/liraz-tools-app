import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { campaignsApi } from "@/api/campaigns";
import type { Campaign, CreateCampaignPayload, UpdateCampaignPayload } from "@/types/api";

export const campaignKeys = {
  all: ["campaigns"] as const,
  list: (profileId: string, includeArchived = false) =>
    ["campaigns", "list", profileId, { includeArchived }] as const,
  detail: (profileId: string, campaignId: string) =>
    ["campaigns", "detail", profileId, campaignId] as const,
};

export function useCampaigns(profileId: string | undefined, includeArchived = false) {
  return useQuery({
    queryKey: campaignKeys.list(profileId ?? "", includeArchived),
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      return campaignsApi.list(profileId, includeArchived);
    },
    enabled: Boolean(profileId),
  });
}

export function useCampaign(profileId: string | undefined, campaignId: string | undefined) {
  return useQuery({
    queryKey: campaignKeys.detail(profileId ?? "", campaignId ?? ""),
    queryFn: () => {
      if (!profileId || !campaignId) throw new Error("ids required");
      return campaignsApi.get(profileId, campaignId);
    },
    enabled: Boolean(profileId && campaignId),
    // Dados da campanha quase não mudam — evita refetch ao voltar pra página
    staleTime: 1000 * 60 * 5, // 5 min
    refetchOnWindowFocus: false,
  });
}

export function useCreateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      payload,
    }: {
      profileId: string;
      payload: CreateCampaignPayload;
    }) => campaignsApi.create(profileId, payload),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
    },
  });
}

export function useUpdateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      payload,
    }: {
      profileId: string;
      campaignId: string;
      payload: UpdateCampaignPayload;
    }) => campaignsApi.update(profileId, campaignId, payload),
    onSuccess: (updated, { profileId, campaignId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      qc.setQueryData<Campaign>(campaignKeys.detail(profileId, campaignId), updated);
    },
  });
}

export function useDeleteCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
    }: {
      profileId: string;
      campaignId: string;
    }) => campaignsApi.delete(profileId, campaignId),
    onSuccess: (_, { profileId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
    },
  });
}

export function useScheduleCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
    }: {
      profileId: string;
      campaignId: string;
    }) => campaignsApi.schedule(profileId, campaignId),
    onSuccess: (updated, { profileId, campaignId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      qc.setQueryData(campaignKeys.detail(profileId, campaignId), updated);
    },
  });
}

export function useUnscheduleCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
    }: {
      profileId: string;
      campaignId: string;
    }) => campaignsApi.unschedule(profileId, campaignId),
    onSuccess: (updated, { profileId, campaignId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      qc.setQueryData(campaignKeys.detail(profileId, campaignId), updated);
    },
  });
}

export function useCancelCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
    }: {
      profileId: string;
      campaignId: string;
    }) => campaignsApi.cancel(profileId, campaignId),
    onSuccess: (updated, { profileId, campaignId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      qc.setQueryData(campaignKeys.detail(profileId, campaignId), updated);
    },
  });
}

export function useReactivateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
    }: {
      profileId: string;
      campaignId: string;
    }) => campaignsApi.reactivate(profileId, campaignId),
    onSuccess: (updated, { profileId, campaignId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      qc.setQueryData(campaignKeys.detail(profileId, campaignId), updated);
    },
  });
}

/**
 * Importa campanha ML como Campaign local (Leva 5.9.2).
 * Idempotente — chamar com mesmo ml_id devolve a mesma campaign.
 */
export function useImportMLCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      mlPromotionId,
      fullScan,
    }: {
      profileId: string;
      mlPromotionId: string;
      fullScan?: boolean;
    }) => campaignsApi.importFromML(profileId, mlPromotionId, fullScan ?? false),
    onSuccess: (created, { profileId }) => {
      // Invalida TUDO relacionado a campanhas: lista, detail, e skus-info.
      // Não usamos setQueryData direto porque o resultado pode ter mais dados
      // (auto-sync) que precisam ser re-buscados pra consistência.
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      qc.invalidateQueries({
        queryKey: campaignKeys.detail(profileId, created.id),
      });
      qc.invalidateQueries({
        queryKey: ["campaigns", "skus-info", profileId, created.id],
      });
    },
  });
}

/** Leva 5.9.3: info enriquecida dos SKUs de uma campanha. */
export function useCampaignSkusInfo(profileId: string | undefined, campaignId: string | undefined) {
  return useQuery({
    queryKey: ["campaigns", "skus-info", profileId, campaignId],
    queryFn: () => {
      if (!profileId || !campaignId) throw new Error("ids required");
      return campaignsApi.getSkusInfo(profileId, campaignId);
    },
    enabled: Boolean(profileId && campaignId),
  });
}

/** Leva 5.9.3: lista de items elegíveis pra entrar em campanha. */
export function useSkusEligible(profileId: string | undefined) {
  return useQuery({
    queryKey: ["campaigns", "skus-eligible", profileId],
    queryFn: () => {
      if (!profileId) throw new Error("profileId required");
      return campaignsApi.listSkusEligible(profileId);
    },
    enabled: Boolean(profileId),
  });
}

/** Leva 5.9.3: PATCH só dos skus_selecionados. */
export function useUpdateCampaignSkus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      skus,
    }: {
      profileId: string;
      campaignId: string;
      skus: string[] | null;
    }) => campaignsApi.updateSkus(profileId, campaignId, skus),
    onSuccess: (updated, { profileId, campaignId }) => {
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      qc.invalidateQueries({
        queryKey: ["campaigns", "skus-info", profileId, campaignId],
      });
      qc.setQueryData(campaignKeys.detail(profileId, campaignId), updated);
    },
  });
}

/**
 * Leva 5.12: cria SELLER_CAMPAIGN no ML JÁ com os SKUs adicionados.
 *
 * Usado pelo botão "Iniciar agora" na nova tela de criação de campanha.
 * Resposta inclui resumo granular (adicionados/já-estavam/erros) que a UI
 * mostra antes de redirecionar pro detalhe.
 */
export function useCriarMlCompleto() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      payload,
    }: {
      profileId: string;
      payload: {
        nome: string;
        data_inicio: string;
        data_fim: string;
        skus_selecionados: string[];
        deal_prices?: Record<string, number>;
        inflar_precos?: Record<string, number>;
        fallback_deal_prices?: Record<string, number>;
      };
    }) => campaignsApi.criarMlCompleto(profileId, payload),
    onSuccess: (created, { profileId }) => {
      // Invalida lista pra refletir a campanha nova
      qc.invalidateQueries({ queryKey: ["campaigns", "list", profileId] });
      // Invalida skus-com-promocoes pq alguns items agora têm uma promo nova
      qc.invalidateQueries({
        queryKey: ["listings", "skus-com-promocoes", profileId],
      });
      // Pré-popula detail cache do id local recém criado
      qc.invalidateQueries({
        queryKey: campaignKeys.detail(profileId, created.id),
      });
    },
  });
}

/**
 * Leva 5.12 follow-up: sync em lote (add+remove) de SKUs em campanha
 * existente. Usado pelo botão "Adicionar/editar SKUs" na detail page.
 */
export function useSyncSkusBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      payload,
    }: {
      profileId: string;
      campaignId: string;
      payload: {
        adicionar: string[];
        remover: string[];
        deal_prices?: Record<string, number>;
        inflar_precos?: Record<string, number>;
        fallback_deal_prices?: Record<string, number>;
      };
    }) => campaignsApi.syncSkusBatch(profileId, campaignId, payload),
    onSuccess: (_, { profileId, campaignId }) => {
      // Atualiza detail (skus_selecionados mudou) + skus-info enriquecido
      qc.invalidateQueries({
        queryKey: campaignKeys.detail(profileId, campaignId),
      });
      qc.invalidateQueries({
        queryKey: ["campaigns", "skus-info", profileId, campaignId],
      });
      // O cruzamento com promoções mudou pra esses items
      qc.invalidateQueries({
        queryKey: ["listings", "skus-com-promocoes", profileId],
      });
    },
  });
}

/**
 * Fluxo 2-passos (mai/2026): 1ª etapa "Inflar Preços". Faz só PUT /items
 * pros itens com `inflar_precos`, sem tocar na campanha. Quando termina, o
 * frontend libera o botão "Confirmar" que dispara `useSyncSkusBatch` SEM
 * `inflar_precos` (só fase 2: clamp + POST + retry + descarte).
 */
export function useInflarPrecos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      inflar_precos,
    }: {
      profileId: string;
      campaignId: string;
      inflar_precos: Record<string, number>;
    }) => campaignsApi.inflarPrecos(profileId, campaignId, inflar_precos),
    onSuccess: (_, { profileId, campaignId }) => {
      // Preços-base mudaram — invalida tudo que depende disso
      qc.invalidateQueries({
        queryKey: ["campaigns", "skus-info", profileId, campaignId],
      });
      qc.invalidateQueries({
        queryKey: ["listings", "skus-com-promocoes", profileId],
      });
    },
  });
}

/**
 * Leva 5.12 rev4: sugere deal_prices baseado em margem-alvo. Não muta
 * nada — só calcula. Usado pelo modal de seleção de SKUs em campanha ML.
 */
export function useSugerirDealPrices() {
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      payload,
    }: {
      profileId: string;
      campaignId: string;
      payload: { item_ids: string[]; margem_alvo: number };
    }) => campaignsApi.sugerirDealPrices(profileId, campaignId, payload),
  });
}

/**
 * Rev11: versão preview sem campaignId. Usado no fluxo de criar nova
 * campanha pra calcular deal_prices reais (com Fase 1 inflação + Fase 2
 * desconto) antes da campanha existir.
 */
export function useSugerirDealPricesPreview() {
  return useMutation({
    mutationFn: ({
      profileId,
      payload,
    }: {
      profileId: string;
      payload: { item_ids: string[]; margem_alvo: number };
    }) => campaignsApi.sugerirDealPricesPreview(profileId, payload),
  });
}

/**
 * Lista margens dos SKUs dentro da campanha (editor de margens, mai/2026).
 * Carrega sob demanda — pesado (~20s pra 200 SKUs). `enabled` controla
 * quando dispara.
 */
export function useMargensCampanha(profileId: string, campaignId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["campaigns", "margens", profileId, campaignId],
    queryFn: () => campaignsApi.listarMargensCampanha(profileId, campaignId),
    enabled: enabled && !!profileId && !!campaignId,
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Edita o deal_price de N SKUs pra atingir uma margem alvo. Invalida as
 * queries de margens + skus-info pra UI refrescar com os valores novos.
 */
export function useEditarMargemSkus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      campaignId,
      payload,
    }: {
      profileId: string;
      campaignId: string;
      payload: { item_ids: string[]; margem_alvo: number };
    }) => campaignsApi.editarMargemSkus(profileId, campaignId, payload),
    onSuccess: (_, { profileId, campaignId }) => {
      qc.invalidateQueries({
        queryKey: ["campaigns", "margens", profileId, campaignId],
      });
      qc.invalidateQueries({
        queryKey: ["campaigns", "skus-info", profileId, campaignId],
      });
    },
  });
}
