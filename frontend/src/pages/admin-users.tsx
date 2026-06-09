/**
 * Gestão de usuários e ACL (admin do sistema only).
 *
 * - Lista todos os usuários (nome, email, admin?, ativo?)
 * - Criar novo usuário (admin ou não-admin)
 * - Ativar/desativar
 * - Para cada user não-admin: gerenciar acessos por loja (role por loja)
 *
 * Esconde admin global da lista de ACL — admin global vê tudo, não precisa
 * de linha em `user_profile_access`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Power, Shield, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";

import { type AuthUser, type ProfileRole, authApi } from "@/api/auth";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/auth-context";
import { useUsers } from "@/features/auth/hooks";
import { useProfiles } from "@/features/profiles/hooks";

export function AdminUsersPage() {
  const { user: me } = useAuth();
  const queryClient = useQueryClient();
  const { data: users = [], isLoading } = useUsers();
  const [openCreate, setOpenCreate] = useState(false);
  const [aclTarget, setAclTarget] = useState<AuthUser | null>(null);

  const deactivateMut = useMutation({
    mutationFn: (userId: string) => authApi.deactivateUser(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth", "users"] }),
  });
  const activateMut = useMutation({
    mutationFn: (userId: string) => authApi.activateUser(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth", "users"] }),
  });
  const deleteMut = useMutation({
    mutationFn: (userId: string) => authApi.deleteUser(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth", "users"] }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Usuários</h1>
          <p className="text-sm text-muted-foreground">
            Gerencie quem pode acessar o LiraZ Tools e em quais lojas.
          </p>
        </div>
        <Button onClick={() => setOpenCreate(true)}>
          <UserPlus className="mr-2 h-4 w-4" />
          Novo usuário
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Lista de usuários</CardTitle>
          <CardDescription>
            Admin global enxerga todas as lojas — usuários comuns só veem as que você liberar via
            "Gerenciar acessos".
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center gap-2 py-6 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-sm">Carregando...</span>
            </div>
          ) : users.length === 0 ? (
            <p className="py-6 text-sm text-muted-foreground">Nenhum usuário ainda.</p>
          ) : (
            <ul className="divide-y">
              {users.map((u) => {
                const isMe = u.id === me?.id;
                return (
                  <li key={u.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{u.nome || "—"}</span>
                        {u.is_admin && (
                          <span className="inline-flex items-center gap-1 rounded bg-foreground/10 px-1.5 py-0.5 text-[10px] font-medium">
                            <Shield className="h-3 w-3" />
                            Admin
                          </span>
                        )}
                        {!u.is_active && (
                          <span className="rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive">
                            Desativado
                          </span>
                        )}
                        {isMe && (
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            você
                          </span>
                        )}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">{u.email}</div>
                    </div>
                    <div className="flex gap-1">
                      {!u.is_admin && (
                        <Button variant="outline" size="sm" onClick={() => setAclTarget(u)}>
                          Gerenciar acessos
                        </Button>
                      )}
                      {u.is_active ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={isMe || deactivateMut.isPending}
                          onClick={() => deactivateMut.mutate(u.id)}
                          title={isMe ? "Você não pode desativar a si mesmo" : "Desativar"}
                        >
                          <Power className="h-4 w-4" />
                        </Button>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={() => activateMut.mutate(u.id)}>
                          Reativar
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={isMe || deleteMut.isPending}
                        onClick={() => {
                          if (confirm(`Apagar definitivamente ${u.email}? Não dá pra desfazer.`)) {
                            deleteMut.mutate(u.id);
                          }
                        }}
                        title={isMe ? "Você não pode apagar a si mesmo" : "Apagar"}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      <CreateUserDialog open={openCreate} onOpenChange={setOpenCreate} />
      <ManageAccessDialog user={aclTarget} onOpenChange={(open) => !open && setAclTarget(null)} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Dialogs
// ─────────────────────────────────────────────────────────────────────────

function CreateUserDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [form, setForm] = useState({
    email: "",
    password: "",
    nome: "",
    is_admin: false,
  });
  const queryClient = useQueryClient();

  const createMut = useMutation({
    mutationFn: () =>
      authApi.createUser({
        email: form.email.trim(),
        password: form.password,
        nome: form.nome.trim() || null,
        is_admin: form.is_admin,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auth", "users"] });
      toast.success("Usuário criado", {
        description: `${form.email} pode fazer login agora`,
      });
      onOpenChange(false);
      setForm({ email: "", password: "", nome: "", is_admin: false });
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.message : "Erro desconhecido";
      toast.error("Falha ao criar usuário", { description: msg });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Novo usuário</DialogTitle>
          <DialogDescription>
            Depois de criar, libere acessos por loja (a não ser que seja admin do sistema — esses já
            enxergam tudo).
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="new-email">E-mail</Label>
            <Input
              id="new-email"
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="usuario@empresa.com"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="new-nome">Nome (opcional)</Label>
            <Input
              id="new-nome"
              value={form.nome}
              onChange={(e) => setForm({ ...form, nome: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="new-pwd">Senha (mín 8 chars)</Label>
            <Input
              id="new-pwd"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
            <p className="text-[11px] text-muted-foreground">
              O usuário pode trocar depois em "Trocar senha".
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.is_admin}
              onChange={(e) => setForm({ ...form, is_admin: e.target.checked })}
              className="h-4 w-4"
            />
            <span>Admin do sistema (vê todas as lojas, pode gerenciar usuários)</span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending || !form.email.trim() || form.password.length < 8}
          >
            {createMut.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-2 h-4 w-4" />
            )}
            Criar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ManageAccessDialog({
  user,
  onOpenChange,
}: {
  user: AuthUser | null;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { data: profilesResp } = useProfiles(false);
  const profiles = profilesResp?.items ?? [];
  const { data: accesses = [] } = useQuery({
    queryKey: ["auth", "user-access", user?.id],
    queryFn: () => (user ? authApi.listAccessForUser(user.id) : Promise.resolve([])),
    enabled: !!user,
  });

  const grantMut = useMutation({
    mutationFn: ({
      profileId,
      role,
    }: {
      profileId: string;
      role: ProfileRole;
    }) =>
      user ? authApi.grantAccess(user.id, profileId, role) : Promise.reject(new Error("sem user")),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["auth", "user-access", user?.id],
      });
    },
  });
  const revokeMut = useMutation({
    mutationFn: (profileId: string) =>
      user ? authApi.revokeAccess(user.id, profileId) : Promise.reject(new Error("sem user")),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["auth", "user-access", user?.id],
      });
    },
  });

  const byProfile = new Map(accesses.map((a) => [a.profile_id, a.role]));

  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Acessos por loja</DialogTitle>
          <DialogDescription>
            {user?.email}: defina o role pra cada loja. Sem role = sem acesso.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-1 overflow-y-auto">
          {profiles.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">Nenhuma loja cadastrada ainda.</p>
          ) : (
            profiles.map((p) => {
              const role = byProfile.get(p.id) ?? null;
              return (
                <div
                  key={p.id}
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{p.name}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {role ? `acesso atual: ${role}` : "sem acesso"}
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <select
                      className="h-8 rounded-md border bg-background px-2 text-xs"
                      value={role ?? ""}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === "") {
                          revokeMut.mutate(p.id);
                        } else {
                          grantMut.mutate({
                            profileId: p.id,
                            role: v as ProfileRole,
                          });
                        }
                      }}
                    >
                      <option value="">Sem acesso</option>
                      <option value="viewer">Viewer</option>
                      <option value="operator">Operator</option>
                      <option value="admin">Admin</option>
                    </select>
                  </div>
                </div>
              );
            })
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Fechar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
