import { AlertCircle, CheckCircle2, Loader2, Sparkles, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { Application, ApplicationPhase } from "@/types/api";

interface ApplicationProgressProps {
  application: Application;
  /** Quando passado, mostra um X no canto pra usuário dispensar o painel.
   *  Caller é responsável por persistir o estado (ex.: localStorage). */
  onDismiss?: () => void;
}

const PHASE_LABELS: Record<ApplicationPhase, string> = {
  applying_prices: "Aplicando preços novos nos anúncios",
  creating_campaign: "Criando campanha no Mercado Livre",
  adding_items: "Adicionando anúncios à campanha",
  reverting: "Revertendo preços ao estado anterior",
  done: "Concluído",
};

const isReverting = (fase: ApplicationPhase): boolean => fase === "reverting";

/**
 * Painel grande que mostra o estado de uma aplicação em andamento.
 * Aparece na campaign-detail enquanto status=executando — ou depois,
 * com resultado final, enquanto status in {ativa, falha}.
 */
export function ApplicationProgress({ application, onDismiss }: ApplicationProgressProps) {
  const { estado, fase, total_itens, itens_aplicados, itens_pulados, itens_falha, erro } =
    application;

  const isRunning = estado === "running";
  const isFailed = estado === "failed";
  const isCompleted = estado === "completed";
  const reverting =
    isReverting(fase) ||
    (application.itens.length > 0 && application.application_id.startsWith("rev_"));

  // Progresso da Fase 1 — só faz sentido enquanto applying_prices
  const progressoFase1 =
    total_itens === 0
      ? 100
      : Math.round(((itens_aplicados + itens_falha + itens_pulados) / total_itens) * 100);

  return (
    <Card
      className={cn(
        "border-2",
        isRunning && "border-warning/50 bg-warning/5",
        isCompleted && "border-success/50 bg-success/5",
        isFailed && "border-destructive/50 bg-destructive/5",
      )}
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            {isRunning && <Loader2 className="h-5 w-5 animate-spin text-warning" />}
            {isCompleted && <CheckCircle2 className="h-5 w-5 text-success" />}
            {isFailed && <AlertCircle className="h-5 w-5 text-destructive" />}
            {isRunning && (reverting ? "Reversão em andamento" : "Execução em andamento")}
            {isCompleted &&
              (reverting ? "Reversão concluída com sucesso" : "Campanha criada com sucesso")}
            {isFailed && (reverting ? "Falha na reversão" : "Falha na execução")}
          </CardTitle>
          {/* X só pra completed/failed — quando running, esconder seria
              perigoso (usuário perderia visibilidade do progresso). */}
          {onDismiss && !isRunning && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onDismiss}
              title="Dispensar resumo (esconde até nova execução)"
              className="-mr-1 -mt-1 h-7 w-7 p-0"
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4 text-sm">
        {/* Linha de fase atual */}
        <div className="flex items-center gap-2">
          {isRunning ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
          ) : (
            <Sparkles className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <span className="font-medium">{PHASE_LABELS[fase]}</span>
        </div>

        {/* Barra de progresso da Fase 1 (sempre relevante — visível durante e
            depois pra mostrar quantos foram afetados) */}
        {total_itens > 0 && (
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-muted-foreground">Anúncios processados</span>
              <span className="font-medium tabular-nums">
                {itens_aplicados + itens_falha + itens_pulados} / {total_itens}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className={cn("h-full transition-all", isFailed ? "bg-destructive" : "bg-primary")}
                style={{ width: `${progressoFase1}%` }}
              />
            </div>

            {/* Contadores detalhados */}
            <div className="flex gap-4 text-xs">
              <span className="text-success">
                <span className="tabular-nums">{itens_aplicados}</span> aplicado
                {itens_aplicados !== 1 && "s"}
              </span>
              {itens_pulados > 0 && (
                <span className="text-muted-foreground">
                  <span className="tabular-nums">{itens_pulados}</span> pulado
                  {itens_pulados !== 1 && "s"}{" "}
                  <span className="opacity-60">(preço já estava ok)</span>
                </span>
              )}
              {itens_falha > 0 && (
                <span className="text-destructive">
                  <span className="tabular-nums">{itens_falha}</span> com falha
                </span>
              )}
            </div>
          </div>
        )}

        {/* Mensagem de erro */}
        {erro && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
            <p className="mb-1 font-medium">Mensagem do servidor:</p>
            <p className="break-words font-mono">{erro}</p>
          </div>
        )}

        {/* Mensagem de sucesso com link — só pra criação de campanha, não revert */}
        {isCompleted && !reverting && application.ml_campaign_id && (
          <div className="rounded-md border border-success/40 bg-success/5 p-3 text-xs">
            Campanha criada no ML com ID{" "}
            <span className="font-mono">{application.ml_campaign_id}</span>.
          </div>
        )}

        {/* Mensagem específica de reversão concluída */}
        {isCompleted && reverting && (
          <div className="rounded-md border border-success/40 bg-success/5 p-3 text-xs">
            ✓ Preços revertidos com sucesso. <span className="font-medium">{itens_aplicados}</span>{" "}
            anúncio{itens_aplicados !== 1 && "s"} voltaram ao preço anterior.
            <p className="mt-1 text-muted-foreground">
              A SELLER_CAMPAIGN no ML pode continuar visível no painel — exclua manualmente se
              quiser.
            </p>
          </div>
        )}

        {/* Indicação de rollback disponível em caso de falha */}
        {isFailed && itens_aplicados > 0 && (
          <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-xs">
            ⚠ <span className="font-medium">{itens_aplicados}</span> preços já foram alterados antes
            da falha. Use a função de reverter (próxima leva) pra voltar ao estado anterior.
            {application.rollback_id && (
              <p className="mt-1 font-mono opacity-70">rollback_id: {application.rollback_id}</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
