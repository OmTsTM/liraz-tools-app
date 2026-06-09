import { Copy } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface NomeDuplicadoDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  nomeTentado: string;
  sugestao: string;
  isSaving: boolean;
  /** Chamado quando o user aceita criar com o nome sugerido. */
  onConfirm: () => void;
}

/**
 * Modal que aparece quando o backend recusa criar/renomear por nome duplicado.
 *
 * Mostra o nome que foi tentado e a sugestão (ex: "Liquidação (1)") e pergunta
 * se quer prosseguir com a sugestão. Confirmar dispara nova requisição com
 * `forcar_nome: true`.
 */
export function NomeDuplicadoDialog({
  open,
  onOpenChange,
  nomeTentado,
  sugestao,
  isSaving,
  onConfirm,
}: NomeDuplicadoDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Nome já existe nesta loja</AlertDialogTitle>
          <AlertDialogDescription className="space-y-3">
            <span className="block">
              Já existe uma campanha chamada{" "}
              <span className="font-semibold text-foreground">"{nomeTentado}"</span> neste perfil.
            </span>
            <span className="block rounded-md border bg-muted/50 p-3">
              <span className="block text-xs text-muted-foreground">Sugestão:</span>
              <span className="mt-1 block font-semibold text-foreground">"{sugestao}"</span>
            </span>
            <span className="block text-xs text-muted-foreground">
              Clique em "Usar sugestão" pra criar com esse nome, ou cancele pra escolher outro
              manualmente.
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isSaving}>Cancelar</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} disabled={isSaving}>
            <Copy className="h-4 w-4" />
            Usar sugestão
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * Helper pra extrair payload de nome duplicado de um erro de API.
 *
 * Retorna `null` se não é um 409 de nome duplicado.
 */
export function parseNomeDuplicado(
  err: unknown,
): { nome_tentado: string; sugestao: string } | null {
  // ApiError vem com detail como objeto quando 409 de nome
  if (
    typeof err === "object" &&
    err !== null &&
    "status" in err &&
    (err as { status: unknown }).status === 409 &&
    "detail" in err
  ) {
    const detail = (err as { detail: unknown }).detail;
    if (
      typeof detail === "object" &&
      detail !== null &&
      "sugestao" in detail &&
      "nome_tentado" in detail
    ) {
      return {
        nome_tentado: String((detail as { nome_tentado: string }).nome_tentado),
        sugestao: String((detail as { sugestao: string }).sugestao),
      };
    }
  }
  return null;
}
