import { apiRequest } from "@/api/client";
import type {
  Campaign,
  CampaignItemEligible,
  CampaignItemInfo,
  CreateCampaignPayload,
  UpdateCampaignPayload,
} from "@/types/api";

export const campaignsApi = {
  /** Lista todas as campanhas do perfil, mais recentes primeiro. */
  async list(profileId: string, includeArchived = false): Promise<Campaign[]> {
    const qs = includeArchived ? "?include_archived=true" : "";
    return apiRequest<Campaign[]>(`/api/profiles/${profileId}/campaigns${qs}`);
  },

  async get(profileId: string, campaignId: string): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/${campaignId}`);
  },

  async create(profileId: string, payload: CreateCampaignPayload): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns`, {
      method: "POST",
      body: payload,
    });
  },

  async update(
    profileId: string,
    campaignId: string,
    payload: UpdateCampaignPayload,
  ): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/${campaignId}`, {
      method: "PATCH",
      body: payload,
    });
  },

  async delete(profileId: string, campaignId: string): Promise<void> {
    await apiRequest<void>(`/api/profiles/${profileId}/campaigns/${campaignId}`, {
      method: "DELETE",
    });
  },

  /** rascunho → agendada. Requer simulacao_id definido. */
  async schedule(profileId: string, campaignId: string): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/${campaignId}/schedule`, {
      method: "POST",
    });
  },

  /** agendada → rascunho. */
  async unschedule(profileId: string, campaignId: string): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/${campaignId}/unschedule`, {
      method: "POST",
    });
  },

  /** Cancela campanha (apenas rascunho/agendada na Leva 5.3). */
  async cancel(profileId: string, campaignId: string): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/${campaignId}/cancel`, {
      method: "POST",
    });
  },

  /** Reativa campanha cancelada. Vira agendada (se tem simulação) ou rascunho. */
  async reactivate(profileId: string, campaignId: string): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/${campaignId}/reactivate`, {
      method: "POST",
    });
  },

  /**
   * Importa uma SELLER_CAMPAIGN do ML como Campaign local (Leva 5.9.2).
   * Idempotente: chamar 2x retorna a mesma campanha.
   */
  async importFromML(
    profileId: string,
    mlPromotionId: string,
    fullScan = false,
  ): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/import-from-ml`, {
      method: "POST",
      body: { ml_promotion_id: mlPromotionId, full_scan: fullScan },
    });
  },

  /** Leva 5.9.3: info enriquecida (sku/titulo/preço) dos SKUs da campanha. */
  async getSkusInfo(profileId: string, campaignId: string): Promise<CampaignItemInfo[]> {
    return apiRequest<CampaignItemInfo[]>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/skus-info`,
    );
  },

  /** Leva 5.9.3: lista catálogo com flag em_outra_campanha. */
  async listSkusEligible(profileId: string): Promise<CampaignItemEligible[]> {
    return apiRequest<CampaignItemEligible[]>(`/api/profiles/${profileId}/campaigns/skus-eligible`);
  },

  /** Leva 5.9.3: atualiza só a lista de SKUs (mais permissivo que update geral). */
  async updateSkus(
    profileId: string,
    campaignId: string,
    skus: string[] | null,
  ): Promise<Campaign> {
    return apiRequest<Campaign>(`/api/profiles/${profileId}/campaigns/${campaignId}/skus`, {
      method: "PATCH",
      body: { skus_selecionados: skus },
    });
  },

  /**
   * Leva 5.12: cria SELLER_CAMPAIGN no ML JÁ COM os SKUs adicionados.
   *
   * Diferente de `importFromML` (importa campanha que já existe no ML)
   * e de `create` (cria campanha LOCAL, sem mexer no ML), este endpoint
   * cria a campanha NO ML e adiciona os SKUs em um único fluxo.
   *
   * Resposta inclui `skus_adicionados`, `skus_ja_estavam` e `erros[]` —
   * a UI mostra um resumo se algum SKU específico falhou ao ser adicionado.
   */
  async criarMlCompleto(
    profileId: string,
    payload: {
      nome: string;
      data_inicio: string; // YYYY-MM-DD
      data_fim: string; // YYYY-MM-DD
      skus_selecionados: string[];
      /** Mapa item_id → preço final pós-desconto. Obrigatório pra
       *  SELLER_CAMPAIGN FLEXIBLE_PERCENTAGE (ML rejeita sem o preço). */
      deal_prices?: Record<string, number>;
      /** Fase 1: item_id → preço de listagem a inflar antes de adicionar. */
      inflar_precos?: Record<string, number>;
      /** item_id → deal conservador pra retry em ERROR_CREDIBILITY. */
      fallback_deal_prices?: Record<string, number>;
    },
  ): Promise<CriarMlCompletoResult> {
    return apiRequest<CriarMlCompletoResult>(
      `/api/profiles/${profileId}/campaigns/criar-ml-completo`,
      { method: "POST", body: payload },
    );
  },
  /**
   * Leva 5.12 follow-up: aplica adições E remoções de SKUs em lote
   * em uma campanha existente. Pra `origem='ml'` chama POST/DELETE no
   * ML em sequência; pra `origem='local'` só atualiza o registro local.
   */
  async syncSkusBatch(
    profileId: string,
    campaignId: string,
    payload: {
      adicionar: string[];
      remover: string[];
      /** Mapa item_id → preço final pós-desconto. Obrigatório pra
       *  itens em `adicionar` quando a campanha é SELLER_CAMPAIGN
       *  FLEXIBLE_PERCENTAGE. */
      deal_prices?: Record<string, number>;
      /** Rev8: mapa item_id → novo preço pra Fase 1 (inflação). Aplicado
       *  via PUT /items/{id} ANTES de adicionar à campanha. Usado quando
       *  algum SKU tem margem-base abaixo do limite do perfil. */
      inflar_precos?: Record<string, number>;
      /** Leva 5.13 — mapa item_id → deal conservador (>=79) pra retry
       *  automático quando o ML rejeitar o deal agressivo (<79) com
       *  ERROR_CREDIBILITY_DISCOUNTED_PRICE. */
      fallback_deal_prices?: Record<string, number>;
    },
  ): Promise<SyncSkusBatchResult> {
    return apiRequest<SyncSkusBatchResult>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/skus/sync-batch`,
      { method: "POST", body: payload },
    );
  },

  /**
   * Fluxo 2-passos (mai/2026): 1ª etapa — APENAS infla preços (PUT /items)
   * sem mexer na campanha. Depois de retornar, o frontend libera o botão
   * "Confirmar" que dispara o sync-batch SEM `inflar_precos` (só fase 2).
   */
  async inflarPrecos(
    profileId: string,
    campaignId: string,
    inflar_precos: Record<string, number>,
  ): Promise<InflarPrecosResult> {
    return apiRequest<InflarPrecosResult>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/skus/inflar-precos`,
      { method: "POST", body: { inflar_precos } },
    );
  },

  /**
   * Editor de margens dentro da campanha (mai/2026):
   * lista por SKU com deal/preço-base/desconto/margem real e os limites
   * `ml_min`/`ml_max` que o ML aceita pro deal_price.
   *
   * Pesado (1–3 chamadas ML por item, ~20s pra 200 SKUs). Frontend só carrega
   * quando o usuário abre o painel de margens.
   */
  async listarMargensCampanha(
    profileId: string,
    campaignId: string,
  ): Promise<MargemCampanhaItem[]> {
    return apiRequest<MargemCampanhaItem[]>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/skus/margens`,
    );
  },

  /**
   * Edita o `deal_price` de N SKUs pra atingir uma `margem_alvo` (fração 0-1).
   * Pula itens cujo deal calculado ultrapassa `ml_max` (decisão de design:
   * não infla preço-base automaticamente).
   */
  async editarMargemSkus(
    profileId: string,
    campaignId: string,
    payload: { item_ids: string[]; margem_alvo: number },
  ): Promise<EditarMargemResponse> {
    return apiRequest<EditarMargemResponse>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/skus/editar-margem`,
      { method: "POST", body: payload },
    );
  },

  /**
   * Leva 5.12 rev4: pra cada SKU informado, calcula deal_price que atinge
   * a margem-alvo (busca binária com base no custos.xlsx + taxas ML).
   * Respeita MINIMUM_DISCOUNT_PERCENT da campanha — quando atinge a margem
   * com desconto abaixo do min, ajusta pro min (margem real fica acima).
   */
  async sugerirDealPrices(
    profileId: string,
    campaignId: string,
    payload: { item_ids: string[]; margem_alvo: number },
  ): Promise<SugestaoDealPricesResponse> {
    return apiRequest<SugestaoDealPricesResponse>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/skus/sugestao-deal-prices`,
      { method: "POST", body: payload },
    );
  },

  /** Preview de sugestão de deal_price SEM campanha criada (rev11).
   *
   *  Usado no fluxo "criar nova campanha" antes do save. Assume min%=5%
   *  conservador. Quando a campanha for criada e SKUs adicionados de
   *  fato, o backend recalcula com o min% real da campanha. */
  async sugerirDealPricesPreview(
    profileId: string,
    payload: { item_ids: string[]; margem_alvo: number },
  ): Promise<SugestaoDealPricesResponse> {
    return apiRequest<SugestaoDealPricesResponse>(
      `/api/profiles/${profileId}/skus/sugestao-deal-prices-preview`,
      { method: "POST", body: payload },
    );
  },
};

/** Resposta do endpoint /campaigns/criar-ml-completo (Leva 5.12). */
export interface CriarMlCompletoResult {
  id: string;
  profile_id: string;
  nome: string;
  data_inicio: string;
  data_fim: string;
  ml_campaign_id: string;
  ml_status: string;
  status_local: string;
  origem: string;
  skus_adicionados: string[];
  skus_ja_estavam: string[];
  /** Itens inflados (Fase 1) que entraram na campanha. */
  inflados: string[];
  /** Inflados negados por outro motivo → revertidos ao preço original. */
  revertidos: string[];
  /** Negados por ERROR_CREDIBILITY → desinflados pro preço de 20% de margem
   *  (fora da campanha). */
  reprecificados_20pct: string[];
  /** Entraram via deal conservador após retry de credibilidade. */
  fallbacks_usados: string[];
  /** ML retornou HTTP 423 LockedEntity mesmo após retries. Preço-base
   *  ficou inflado (NÃO foi revertido) porque o ML costuma adicionar o item
   *  assincronamente depois de destravar. Monitorar o ML pra confirmar. */
  pendentes_lock: string[];
  erros: Array<{ item_id: string; erro: string }>;
}

/** Resposta do endpoint /campaigns/{id}/skus/inflar-precos (fluxo 2-passos). */
export interface InflarPrecosResult {
  ok: boolean;
  /** Itens cujo PUT de inflação teve sucesso (preço-base agora em U). */
  inflados: string[];
  /** Itens que já estavam no preço U antes da chamada (idempotência). */
  ja_no_preco: string[];
  /** PUTs que falharam — caller decide se vai descartar esses na fase 2. */
  erros: Array<{ item_id: string; operacao: string; erro: string }>;
}

/** Resposta do endpoint /campaigns/{id}/skus/sync-batch (Leva 5.12 follow-up). */
export interface SyncSkusBatchResult {
  ok: boolean;
  operacoes: number;
  total_skus_apos: number;
  adicionados: string[];
  removidos: string[];
  inflados: string[];
  /** Itens que foram inflados mas NÃO entraram na campanha — tiveram o preço
   *  revertido ao original automaticamente e ficaram de fora da campanha. */
  revertidos: string[];
  /** Itens negados pelo ML por ERROR_CREDIBILITY — foram reprecificados pro
   *  preço de 20% de margem líquida (desinflados) e ficaram fora da campanha. */
  reprecificados_20pct: string[];
  /** Leva 5.13 — itens que entraram via deal conservador após o ML
   *  rejeitar o agressivo com ERROR_CREDIBILITY_DISCOUNTED_PRICE. */
  fallbacks_usados: string[];
  /** CLAMP (mai/2026) — itens onde o deal foi ajustado pra caber em
   *  [ML_min, ML_max] da faixa de credibilidade do ML. Entram na campanha. */
  clamped: string[];
  /** CLAMP — itens que o ML não aceita como candidate da promoção (regra do
   *  ML: condição, categoria, etc.). Não entram. */
  pulados_ausente: string[];
  /** CLAMP — itens onde o clamp violaria o piso de margem (default 17%).
   *  Pulados pra não sangrar margem. */
  pulados_por_margem: string[];
  /** ML retornou HTTP 423 LockedEntity mesmo após retries. Preço-base
   *  ficou inflado (NÃO foi revertido). Monitorar o ML pra confirmar
   *  adição assíncrona. */
  pendentes_lock: string[];
  ja_estavam: string[];
  nao_estavam: string[];
  erros: Array<{ item_id: string; operacao: string; erro: string }>;
}

/** Sugestão de deal_price por SKU pra atingir margem-alvo (Leva 5.12 rev8).
 *
 *  Fluxo 2 fases:
 *  - Fase 1 (precisa_inflacao=true): preço-base do anúncio está com margem
 *    abaixo do limite do perfil. App calcula `preco_inflado` que atinge a
 *    margem-alvo do perfil (Fase 1). Caller pode aplicar via inflar_precos
 *    no sync-batch antes de adicionar à campanha.
 *  - Fase 2: `deal_price` calculado a partir do preço inflado (ou atual,
 *    se !precisa_inflacao). Atinge a `margem_alvo` da campanha.
 */
export interface SugestaoDealPriceItem {
  item_id: string;
  sku: string | null;
  preco_atual: number | null;
  margem_atual_pct: number | null;
  precisa_inflacao: boolean;
  preco_inflado: number | null;
  deal_price: number | null;
  desconto_pct: number | null;
  margem_real_pct: number | null;
  min_aplicado: boolean;
  aviso_degrau: {
    deal_price: number;
    margem_pct: number;
    desconto_pct: number;
  } | null;
  /** Leva 5.13 — true quando o deal_price foi forçado pra <79 (quebra
   *  de frete grátis). UI deve mostrar visual diferenciado. */
  quebra_frete_gratis_aplicada: boolean;
  /** Leva 5.13 — deal_price conservador (>=79 com frete) guardado pra
   *  retry caso ML rejeite o agressivo com ERROR_CREDIBILITY. UI passa
   *  esse valor no body do sync-batch via `fallback_deal_prices`. */
  fallback_deal_price: number | null;
  /** Modelo passo3 — true quando o preço recomendado cruza pra ≥R$79 mas o
   *  item está hoje <79: o frete ≥79 não é confiável (subestimado). Esses
   *  itens NÃO entram na campanha pelo app — exigem ajuste manual. */
  frete_a_confirmar: boolean;
  erro: string | null;
}

export interface SugestaoDealPricesResponse {
  ml_campaign_id: string;
  margem_alvo_pct: number;
  results: SugestaoDealPriceItem[];
}

/** Linha do editor de margens dentro da campanha (mai/2026). */
export interface MargemCampanhaItem {
  item_id: string;
  sku: string | null;
  titulo: string | null;
  /** Preço-base do anúncio no ML (`original_price` da promoção = U inflado). */
  preco_base: number | null;
  /** Deal_price ativo na campanha (o que o cliente paga). */
  deal_price: number | null;
  /** Percentual de desconto exibido (= 1 − deal/base). */
  desconto_pct: number | null;
  /** Margem líquida REAL no deal_price (modelo passo3). */
  margem_pct: number | null;
  /** Limites de credibilidade do ML pra esse item nessa promoção. */
  ml_min: number | null;
  ml_max: number | null;
  /** Componentes pra debug + permitir cálculos client-side de preview. */
  custo: number | null;
  comissao_pct: number | null;
  tarifa_fixa: number | null;
  frete: number | null;
  aliquota: number | null;
  erro: string | null;
}

/** Resposta do POST /skus/editar-margem. */
export interface EditarMargemItem {
  item_id: string;
  sku: string | null;
  deal_anterior: number | null;
  deal_novo: number | null;
  margem_anterior_pct: number | null;
  margem_nova_pct: number | null;
  status: "aplicado" | "pulado_ml_max" | "pulado_ml_min" | "sem_mudanca" | "erro";
  motivo: string | null;
}

export interface EditarMargemResponse {
  results: EditarMargemItem[];
}
