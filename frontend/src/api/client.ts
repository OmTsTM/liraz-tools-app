/**
 * Cliente HTTP do app — wrapper fino sobre fetch.
 *
 * Usa o proxy do Vite (vite.config.ts) que redireciona /api/* pra localhost:8000.
 * Em produção (servidor empacotado), o frontend e o backend rodam na mesma
 * origem, então /api/* funciona direto sem proxy.
 */

/**
 * Base URL pra montar URLs absolutas (downloads de arquivos, uploads).
 *
 * Em dev e em prod fica vazio porque o proxy do Vite e o servidor empacotado
 * lidam com /api/* na própria origem. Se um dia subir backend em outro host,
 * troca por uma env var.
 */
export const API_BASE_URL = "";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Callback global pra reagir a 401 (sessão expirada / não autenticado).
 * Auth context registra um handler que redireciona pra /login + limpa user.
 */
type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;

  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
    // Garante envio do cookie de sessão (mesma origem com proxy do Vite — só
    // por garantia caso o front e o backend vivam em hosts diferentes um dia).
    credentials: "include",
  });

  // 401 = sessão expirada/não autenticada. Notifica auth context, que decide
  // se redireciona pra /login. NÃO dispara em /api/auth/login (evita loop e
  // permite a tela de login mostrar "senha inválida").
  if (response.status === 401 && !path.startsWith("/api/auth/login")) {
    onUnauthorized?.();
  }

  if (response.status === 204) {
    // No Content — sem body pra parsear
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");

  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const detail =
      isJson && typeof payload === "object" && payload !== null
        ? ((payload as { detail?: unknown }).detail ?? payload)
        : payload;
    const message = typeof detail === "string" ? detail : `HTTP ${response.status} em ${path}`;
    throw new ApiError(message, response.status, detail);
  }

  return payload as T;
}
