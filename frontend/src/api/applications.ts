import { apiRequest } from "@/api/client";
import type { Application, StartCampaignPayload } from "@/types/api";

export const applicationsApi = {
  /**
   * Dispara aplicação manual de uma campanha. Retorna o Application inicial
   * com `application_id` pra polling. Backend continua executando em background.
   */
  async start(
    profileId: string,
    campaignId: string,
    payload: StartCampaignPayload = {},
  ): Promise<Application> {
    return apiRequest<Application>(`/api/profiles/${profileId}/campaigns/${campaignId}/start`, {
      method: "POST",
      body: { imediato: true, ...payload },
    });
  },

  /**
   * Última aplicação de uma campanha (pra polling). Retorna `null` se nunca
   * foi disparada.
   */
  async getLatest(profileId: string, campaignId: string): Promise<Application | null> {
    return apiRequest<Application | null>(
      `/api/profiles/${profileId}/campaigns/${campaignId}/application`,
    );
  },

  async get(profileId: string, applicationId: string): Promise<Application> {
    return apiRequest<Application>(`/api/profiles/${profileId}/applications/${applicationId}`);
  },

  /**
   * Reverte preços aplicados pela campanha usando o rollback gerado.
   * Cria nova Application com phase=reverting pra auditoria. Polling
   * usa o mesmo `useLatestApplication`.
   */
  async revert(profileId: string, campaignId: string): Promise<Application> {
    return apiRequest<Application>(`/api/profiles/${profileId}/campaigns/${campaignId}/revert`, {
      method: "POST",
      body: {},
    });
  },
};
