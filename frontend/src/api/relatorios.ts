import { apiRequest } from "@/api/client";

export interface PDFArquivado {
  filename: string;
  dia: string; // ISO YYYY-MM-DD
  tamanho_bytes: number;
  modificado_em_iso: string;
}

export interface ListaPDFsResponse {
  pdfs: PDFArquivado[];
}

export interface GerarAgoraResponse {
  ok: boolean;
  filename: string;
  dia: string;
  tamanho_bytes: number;
}

export const relatoriosApi = {
  async listarArquivados(profileId: string, limit = 30): Promise<ListaPDFsResponse> {
    return apiRequest<ListaPDFsResponse>(
      `/api/profiles/${profileId}/relatorio/agendamentos?limit=${limit}`,
    );
  },

  /** URL pra download direto (usar em <a href> em vez de fetch). */
  urlDownloadArquivado(profileId: string, filename: string): string {
    return `/api/profiles/${profileId}/relatorio/agendamentos/${encodeURIComponent(filename)}`;
  },

  /** URL pra download do relatório on-demand de um dia específico. */
  urlGerarOnDemand(profileId: string, dia: string): string {
    return `/api/profiles/${profileId}/relatorio?dia=${dia}`;
  },

  async gerarAgora(profileId: string, dia?: string): Promise<GerarAgoraResponse> {
    const query = dia ? `?dia=${dia}` : "";
    return apiRequest<GerarAgoraResponse>(
      `/api/profiles/${profileId}/relatorio/gerar-agora${query}`,
      { method: "POST" },
    );
  },
};
