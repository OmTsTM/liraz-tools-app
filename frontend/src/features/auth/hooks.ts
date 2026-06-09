/**
 * Hooks de auth: role do user logado por loja, queries de gestão (admin).
 */
import { useQuery } from "@tanstack/react-query";

import { type ProfileRole, authApi } from "@/api/auth";
import { useAuth } from "@/contexts/auth-context";

const ROLE_NIVEL: Record<ProfileRole, number> = {
  admin: 3,
  operator: 2,
  viewer: 1,
};

/**
 * Compara níveis. `hasRole("operator", "viewer")` = true (operator >= viewer).
 * Admin global (system) implica admin em qualquer loja (caller passa "admin").
 */
export function roleAtLeast(have: ProfileRole, minimum: ProfileRole): boolean {
  return ROLE_NIVEL[have] >= ROLE_NIVEL[minimum];
}

/**
 * Devolve as ACLs do user logado via `/api/auth/my-access`. Admin global:
 * cache vazio (admin não precisa de linha — bypassa via `is_admin`).
 */
export function useMyProfileAccess() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ["auth", "my-access", user?.id],
    queryFn: () => authApi.myAccess(),
    enabled: !!user,
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Role do user logado numa loja específica. Admin global → "admin".
 * Sem acesso → null.
 */
export function useMyRoleInProfile(profileId: string | undefined): ProfileRole | null {
  const { user } = useAuth();
  const { data: accesses = [] } = useMyProfileAccess();
  if (!user || !profileId) return null;
  if (user.is_admin) return "admin";
  const access = accesses.find((a) => a.profile_id === profileId);
  return access ? access.role : null;
}

export function useUsers() {
  const { isAdmin } = useAuth();
  return useQuery({
    queryKey: ["auth", "users"],
    queryFn: () => authApi.listUsers(),
    enabled: isAdmin,
    staleTime: 60 * 1000,
  });
}

export function useUserAccess(userId: string | undefined) {
  const { isAdmin } = useAuth();
  return useQuery({
    queryKey: ["auth", "user-access", userId],
    queryFn: () => (userId ? authApi.listAccessForUser(userId) : Promise.resolve([])),
    enabled: isAdmin && !!userId,
    staleTime: 60 * 1000,
  });
}
