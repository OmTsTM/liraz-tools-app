/**
 * Renderiza `children` SE o user logado tem role >= `minimum` na loja
 * `profileId`. Admin global passa em qualquer lugar.
 *
 * - Sem render quando o user não cumpre o role (default).
 * - `fallback`: opcional, mostrado no lugar (ex: botão disabled com tooltip).
 *
 * É um helper visual — NÃO substitui o gate de servidor (que sempre roda).
 * Usar pra esconder botões/links que disparariam 403, melhorando UX.
 */
import type { ReactNode } from "react";

import type { ProfileRole } from "@/api/auth";
import { roleAtLeast, useMyRoleInProfile } from "@/features/auth/hooks";

export function RoleGate({
  profileId,
  minimum,
  children,
  fallback = null,
}: {
  profileId: string | undefined;
  minimum: ProfileRole;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const myRole = useMyRoleInProfile(profileId);
  if (!myRole || !roleAtLeast(myRole, minimum)) {
    return <>{fallback}</>;
  }
  return <>{children}</>;
}
