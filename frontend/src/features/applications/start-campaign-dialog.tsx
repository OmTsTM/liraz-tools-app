import { Loader2, Play } from "lucide-react";

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

interface StartCampaignDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  campanhaNome: string;
  /**
   * Texto descritivo da campanha mostrado no modal — ex: número de items
   * que vão ser afetados, deal_price range, etc.
   */
  descricao: string;
  isStarting: boolean;
  onConfirm: () => void;
}

/**
 * Modal simples (decisão 5.3.4): "Confirmar disparo agora?" + [Cancelar] [Disparar].
 *
 * Sem checkbox extra, sem digitar palavra — só confirmação direta. Os
 * detalhes ficam na descrição pra ter contexto suficiente antes de
 * apertar o botão definitivo.
 */
export function StartCampaignDialog({
  open,
  onOpenChange,
  campanhaNome,
  descricao,
  isStarting,
  onConfirm,
}: StartCampaignDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Confirmar disparo agora?</AlertDialogTitle>
          <AlertDialogDescription className="space-y-2">
            <span className="block">
              Você está prestes a iniciar a campanha{" "}
              <span className="font-semibold text-foreground">{campanhaNome}</span> imediatamente.
              Isto vai:
            </span>
            <span className="block whitespace-pre-line pt-1 text-sm">{descricao}</span>
            <span className="mt-2 block rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
              Esta ação altera preços reais no Mercado Livre. A operação é reversível pela função
              "Reverter campanha" depois.
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isStarting}>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={isStarting}
            className="bg-primary text-primary-foreground hover:bg-primary/90"
          >
            {isStarting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Disparar
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
