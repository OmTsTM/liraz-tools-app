import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000, // 30s — dados ficam "frescos" pra evitar refetch agressivo
      refetchOnWindowFocus: false,
      retry: (failureCount, error: unknown) => {
        // Não retry em 4xx (erro do cliente, não vai melhorar)
        if (
          error instanceof Error &&
          "status" in error &&
          typeof error.status === "number" &&
          error.status >= 400 &&
          error.status < 500
        ) {
          return false;
        }
        return failureCount < 2;
      },
    },
    mutations: {
      retry: false,
    },
  },
});
