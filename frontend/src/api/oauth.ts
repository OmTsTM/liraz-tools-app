import { apiRequest } from "@/api/client";
import type {
  AuthorizationUrlResponse,
  ImportFromMCPRequest,
  MLConnectionTestResponse,
  Profile,
  SaveCredentialsRequest,
} from "@/types/api";

export const oauthApi = {
  async saveCredentials(profileId: string, data: SaveCredentialsRequest): Promise<void> {
    await apiRequest<void>(`/api/profiles/${profileId}/oauth/credentials`, {
      method: "POST",
      body: data,
    });
  },

  async getAuthorizationUrl(profileId: string): Promise<AuthorizationUrlResponse> {
    return apiRequest<AuthorizationUrlResponse>(`/api/profiles/${profileId}/oauth/authorize-url`);
  },

  async importFromMCP(profileId: string, data: ImportFromMCPRequest): Promise<Profile> {
    return apiRequest<Profile>(`/api/profiles/${profileId}/oauth/import-mcp`, {
      method: "POST",
      body: data,
    });
  },

  async disconnect(profileId: string): Promise<Profile> {
    return apiRequest<Profile>(`/api/profiles/${profileId}/oauth/disconnect`, { method: "POST" });
  },

  async testConnection(profileId: string): Promise<MLConnectionTestResponse> {
    return apiRequest<MLConnectionTestResponse>(`/api/profiles/${profileId}/ml-test`);
  },
};
