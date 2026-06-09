import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { profilesApi } from "@/api/profiles";
import type { CreateProfileRequest, UpdateProfileRequest } from "@/types/api";

const profileKeys = {
  all: ["profiles"] as const,
  list: (includeArchived: boolean) => [...profileKeys.all, "list", { includeArchived }] as const,
  detail: (id: string) => [...profileKeys.all, "detail", id] as const,
  active: () => [...profileKeys.all, "active"] as const,
};

export function useProfiles(includeArchived = false) {
  return useQuery({
    queryKey: profileKeys.list(includeArchived),
    queryFn: () => profilesApi.list(includeArchived),
  });
}

export function useProfile(id: string | undefined) {
  return useQuery({
    queryKey: profileKeys.detail(id ?? ""),
    queryFn: () => profilesApi.get(id as string),
    enabled: Boolean(id),
  });
}

export function useActiveProfile() {
  return useQuery({
    queryKey: profileKeys.active(),
    queryFn: () => profilesApi.getActive(),
  });
}

export function useCreateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateProfileRequest) => profilesApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys.all });
    },
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateProfileRequest }) =>
      profilesApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys.all });
    },
  });
}

export function useArchiveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => profilesApi.archive(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys.all });
    },
  });
}

export function useUnarchiveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => profilesApi.unarchive(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys.all });
    },
  });
}

export function useDeleteProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => profilesApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys.all });
    },
  });
}

export function useActivateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => profilesApi.activate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys.active() });
    },
  });
}
