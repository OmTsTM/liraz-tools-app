import { apiRequest } from "@/api/client";
import type {
  ActiveProfileResponse,
  CreateProfileRequest,
  ListProfilesResponse,
  Profile,
  UpdateProfileRequest,
} from "@/types/api";

export const profilesApi = {
  async list(includeArchived = false): Promise<ListProfilesResponse> {
    const query = includeArchived ? "?include_archived=true" : "";
    return apiRequest<ListProfilesResponse>(`/api/profiles${query}`);
  },

  async get(id: string): Promise<Profile> {
    return apiRequest<Profile>(`/api/profiles/${id}`);
  },

  async create(data: CreateProfileRequest): Promise<Profile> {
    return apiRequest<Profile>("/api/profiles", {
      method: "POST",
      body: data,
    });
  },

  async update(id: string, data: UpdateProfileRequest): Promise<Profile> {
    return apiRequest<Profile>(`/api/profiles/${id}`, {
      method: "PATCH",
      body: data,
    });
  },

  /**
   * Soft delete — preserva dados. Funciona em qualquer estado.
   */
  async archive(id: string): Promise<Profile> {
    return apiRequest<Profile>(`/api/profiles/${id}/archive`, {
      method: "POST",
    });
  },

  /**
   * Desarquivar — inteligente: vai pra CONNECTED se tem credenciais,
   * pra DRAFT caso contrário.
   */
  async unarchive(id: string): Promise<Profile> {
    return apiRequest<Profile>(`/api/profiles/${id}/unarchive`, {
      method: "POST",
    });
  },

  /**
   * Hard delete — apaga permanentemente. Backend só permite em DRAFT.
   * Lança erro 409 se o perfil já foi conectado.
   */
  async delete(id: string): Promise<void> {
    await apiRequest<void>(`/api/profiles/${id}`, { method: "DELETE" });
  },

  async getActive(): Promise<ActiveProfileResponse> {
    return apiRequest<ActiveProfileResponse>("/api/profiles/active");
  },

  async activate(id: string): Promise<ActiveProfileResponse> {
    return apiRequest<ActiveProfileResponse>(`/api/profiles/${id}/activate`, { method: "POST" });
  },
};
