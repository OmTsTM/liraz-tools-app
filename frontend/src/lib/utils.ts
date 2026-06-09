import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Combina classes Tailwind resolvendo conflitos.
 *
 * Padrão shadcn/ui — usa em todo componente que aceita className.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
