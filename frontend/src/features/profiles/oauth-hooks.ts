import { useMutation, useQueryClient } from "@tanstack/react-query";

import { oauthApi } from "@/api/oauth";
import type { ImportFromMCPRequest, SaveCredentialsRequest } from "@/types/api";

export function useSaveCredentials() {
  return useMutation({
    mutationFn: ({
      profileId,
      data,
    }: {
      profileId: string;
      data: SaveCredentialsRequest;
    }) => oauthApi.saveCredentials(profileId, data),
  });
}

export function useGetAuthorizationUrl() {
  return useMutation({
    mutationFn: (profileId: string) => oauthApi.getAuthorizationUrl(profileId),
  });
}

export function useImportFromMCP() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      data,
    }: {
      profileId: string;
      data: ImportFromMCPRequest;
    }) => oauthApi.importFromMCP(profileId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useDisconnectProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => oauthApi.disconnect(profileId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useTestMLConnection() {
  return useMutation({
    mutationFn: (profileId: string) => oauthApi.testConnection(profileId),
  });
}
