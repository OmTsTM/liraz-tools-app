import { apiRequest } from "@/api/client";

/** Uma promoção em que um item está participando. */
export interface PromoNoItem {
  promotion_id: string;
  nome: string | null;
  tipo: string; // SELLER_CAMPAIGN, DEAL, PRICE_DISCOUNT, ...
  status: string; // started, pending
  start_date: string | null; // YYYY-MM-DD
  finish_date: string | null;
}

/** Um anúncio ativo + lista de promoções em que participa. */
export interface SkuComPromocoes {
  item_id: string;
  sku: string | null;
  titulo: string | null;
  preco: number | null;
  promocoes: PromoNoItem[];
}

export interface SkusComPromocoesResponse {
  total: number;
  items_em_promocao: number;
  results: SkuComPromocoes[];
}

export const listingsApi = {
  /**
   * Leva 5.12: lista anúncios ativos + promoções de cada (substitui a
   * dependência de simulação como fonte de SKUs pra criar campanha).
   *
   * Por default inclui promoções `pending` (futuras) também — usuário
   * quer ver conflitos com promoções que vão começar.
   */
  async listarSkusComPromocoes(
    profileId: string,
    incluirProgramadas = true,
  ): Promise<SkusComPromocoesResponse> {
    const qs = `?incluir_programadas=${incluirProgramadas}`;
    return apiRequest<SkusComPromocoesResponse>(
      `/api/profiles/${profileId}/skus-com-promocoes${qs}`,
    );
  },
};
