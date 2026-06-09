/**
 * Cliente da API de autenticação (login, logout, /me, gestão de usuários, ACL).
 */
import { apiRequest } from "@/api/client";

export type ProfileRole = "admin" | "operator" | "viewer";

export interface AuthUser {
  id: string;
  email: string;
  nome: string | null;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface ProfileAccess {
  id: string;
  user_id: string;
  profile_id: string;
  role: ProfileRole;
  created_at: string;
}

export const authApi = {
  async login(email: string, password: string): Promise<AuthUser> {
    return apiRequest<AuthUser>("/api/auth/login", {
      method: "POST",
      body: { email, password },
    });
  },

  async logout(): Promise<void> {
    return apiRequest<void>("/api/auth/logout", { method: "POST" });
  },

  async me(): Promise<AuthUser> {
    return apiRequest<AuthUser>("/api/auth/me");
  },

  async myAccess(): Promise<ProfileAccess[]> {
    return apiRequest<ProfileAccess[]>("/api/auth/my-access");
  },

  async changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return apiRequest<void>("/api/auth/change-password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    });
  },

  // ── Gestão de usuários (admin only) ────────────────────────────────────
  async listUsers(): Promise<AuthUser[]> {
    return apiRequest<AuthUser[]>("/api/auth/users");
  },

  async createUser(input: {
    email: string;
    password: string;
    nome: string | null;
    is_admin: boolean;
  }): Promise<AuthUser> {
    return apiRequest<AuthUser>("/api/auth/users", {
      method: "POST",
      body: input,
    });
  },

  async deactivateUser(userId: string): Promise<void> {
    return apiRequest<void>(`/api/auth/users/${userId}/deactivate`, { method: "POST" });
  },

  async activateUser(userId: string): Promise<void> {
    return apiRequest<void>(`/api/auth/users/${userId}/activate`, { method: "POST" });
  },

  async deleteUser(userId: string): Promise<void> {
    return apiRequest<void>(`/api/auth/users/${userId}`, { method: "DELETE" });
  },

  // ── ACL por loja ────────────────────────────────────────────────────────
  async listAccessForUser(userId: string): Promise<ProfileAccess[]> {
    return apiRequest<ProfileAccess[]>(`/api/auth/users/${userId}/profile-access`);
  },

  async grantAccess(userId: string, profileId: string, role: ProfileRole): Promise<ProfileAccess> {
    return apiRequest<ProfileAccess>(`/api/auth/users/${userId}/profile-access`, {
      method: "POST",
      body: { profile_id: profileId, role },
    });
  },

  async revokeAccess(userId: string, profileId: string): Promise<void> {
    return apiRequest<void>(`/api/auth/users/${userId}/profile-access/${profileId}`, {
      method: "DELETE",
    });
  },
};
