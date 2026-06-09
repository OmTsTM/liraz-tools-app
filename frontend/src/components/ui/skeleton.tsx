import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Skeleton com shimmer animation.
 *
 * Mais bonito que o pulse padrão — passa uma faixa de luz pela esquerda
 * pra direita, dando sensação de "conteúdo sendo carregado".
 */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-md bg-muted",
        "before:absolute before:inset-0",
        "before:-translate-x-full",
        "before:bg-gradient-to-r before:from-transparent before:via-white/20 before:to-transparent",
        "before:animate-shimmer",
        className,
      )}
      {...props}
    />
  );
}
