import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Loader2,
  Pause,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { RoleGate } from "@/components/auth/role-gate";
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
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { useProfile } from "@/features/profiles/hooks";
import {
  useDeleteSimulation,
  useResumeSimulation,
  useSimulations,
  useStartSimulation,
} from "@/features/simulations/hooks";
import { cn } from "@/lib/utils";
import type { SimulationState, SimulationSummary } from "@/types/api";

export function SimulationsListPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: profile } = useProfile(id);
  const { data: simulations, isPending, isError } = useSimulations(id);

  const startMutation = useStartSimulation();
  const resumeMutation = useResumeSimulation();
  const deleteMutation = useDeleteSimulation();

  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const handleStart = async () => {
    if (!id) return;
    try {
      const result = await startMutation.mutateAsync({ profileId: id });
      toast.success("Simulação iniciada", {
        description: "Pode levar 3-5 minutos. Acompanhe o progresso na lista.",
      });
      navigate(`/profiles/${id}/simulations/${result.simulation_id}`);
    } catch (err) {
      toast.error("Falha ao iniciar simulação", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleResume = async (simulationId: string) => {
    if (!id) return;
    try {
      await resumeMutation.mutateAsync({ profileId: id, simulationId });
      toast.success("Simulação retomada");
    } catch (err) {
      toast.error("Falha ao retomar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleConfirmDelete = async () => {
    if (!id || !pendingDelete) return;
    try {
      await deleteMutation.mutateAsync({
        profileId: id,
        simulationId: pendingDelete,
      });
      toast.success("Simulação removida");
    } catch (err) {
      toast.error("Falha ao remover", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    } finally {
      setPendingDelete(null);
    }
  };

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${id}`)}>
        <ArrowLeft className="h-4 w-4" />
        Voltar pro dashboard
      </Button>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">
            Simulações de reprecificação
            {profile && (
              <span className="ml-2 text-base font-normal text-muted-foreground">
                · {profile.name}
              </span>
            )}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Calcula o preço final de venda (margem alvo Q2{" "}
            <span className="font-medium text-foreground">
              {profile ? `${(profile.config.margem_alvo_campanha * 100).toFixed(0)}%` : "—"}
            </span>
            , piso R2{" "}
            <span className="font-medium text-foreground">
              {profile ? `${((profile.config.margem_minima ?? 0.15) * 100).toFixed(0)}%` : "—"}
            </span>
            ) e o preço inflado da campanha pra todos os anúncios ativos. Não muda nada no ML.
          </p>
        </div>

        <RoleGate profileId={id} minimum="operator">
          <Button onClick={handleStart} disabled={startMutation.isPending} size="sm">
            {startMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            Nova simulação
          </Button>
        </RoleGate>
      </div>

      {/* Lista */}
      {isPending && <LoadingState />}
      {isError && (
        <Card>
          <CardContent className="p-8 text-center text-sm text-muted-foreground">
            Falha ao carregar simulações.
          </CardContent>
        </Card>
      )}
      {simulations && simulations.length === 0 && (
        <Card>
          <CardContent className="p-12 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
              <Clock className="h-6 w-6 text-muted-foreground" />
            </div>
            <h3 className="text-base font-semibold">Nenhuma simulação ainda</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Crie a primeira simulação no botão acima.
            </p>
          </CardContent>
        </Card>
      )}

      {simulations && simulations.length > 0 && (
        <div className="space-y-2">
          {simulations.map((sim) => (
            <SimulationRow
              key={sim.simulation_id}
              simulation={sim}
              onOpen={() => navigate(`/profiles/${id}/simulations/${sim.simulation_id}`)}
              onResume={() => handleResume(sim.simulation_id)}
              onDelete={() => setPendingDelete(sim.simulation_id)}
              resumeLoading={resumeMutation.isPending}
            />
          ))}
        </div>
      )}

      {/* Confirmação de delete */}
      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open: boolean) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Apagar esta simulação?</AlertDialogTitle>
            <AlertDialogDescription>
              O snapshot vai ser removido do disco permanentemente. Essa ação não pode ser desfeita.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              disabled={deleteMutation.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              Apagar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function SimulationRow({
  simulation,
  onOpen,
  onResume,
  onDelete,
  resumeLoading,
}: {
  simulation: SimulationSummary;
  onOpen: () => void;
  onResume: () => void;
  onDelete: () => void;
  resumeLoading: boolean;
}) {
  const dt = new Date(simulation.criado_em);
  const progress =
    simulation.total_ativos_analisados > 0
      ? simulation.total_processados / simulation.total_ativos_analisados
      : 0;

  return (
    <Card className="cursor-pointer transition-colors hover:bg-muted/30" onClick={onOpen}>
      <CardContent className="flex flex-wrap items-center gap-4 p-4">
        {/* Estado */}
        <div className="shrink-0">
          <StateBadge state={simulation.estado} />
        </div>

        {/* Identificação */}
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs text-muted-foreground">{simulation.simulation_id}</p>
          <p className="text-sm">{dt.toLocaleString("pt-BR")}</p>
        </div>

        {/* Progresso (se running) */}
        {simulation.estado === "running" && (
          <div className="w-48 shrink-0">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {simulation.total_processados}/{simulation.total_ativos_analisados}
              </span>
              <span>{Math.round(progress * 100)}%</span>
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress * 100}%` }}
              />
            </div>
          </div>
        )}

        {/* Contadores (estado terminal) */}
        {simulation.estado !== "running" && (
          <div className="flex gap-4 text-xs text-muted-foreground">
            <div>
              <span className="font-medium text-foreground">{simulation.total_simulados}</span>{" "}
              simulados
            </div>
            <div>
              <span className="font-medium text-foreground">{simulation.total_excecoes}</span>{" "}
              exceções
            </div>
          </div>
        )}

        {/* Ações */}
        <div className="flex shrink-0 gap-1" onClick={(e) => e.stopPropagation()}>
          {(simulation.estado === "interrupted" || simulation.estado === "failed") && (
            <Button
              variant="outline"
              size="sm"
              onClick={onResume}
              disabled={resumeLoading}
              title="Retomar simulação"
            >
              {resumeLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
            </Button>
          )}
          {simulation.estado !== "running" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-destructive"
              onClick={onDelete}
              title="Apagar"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function StateBadge({ state }: { state: SimulationState }) {
  const config: Record<
    SimulationState,
    { label: string; icon: React.ReactNode; className: string }
  > = {
    running: {
      label: "Executando",
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
      className: "bg-primary/10 text-primary",
    },
    completed: {
      label: "Concluída",
      icon: <CheckCircle2 className="h-3 w-3" />,
      className: "bg-success/10 text-success",
    },
    failed: {
      label: "Falhou",
      icon: <AlertCircle className="h-3 w-3" />,
      className: "bg-destructive/10 text-destructive",
    },
    interrupted: {
      label: "Interrompida",
      icon: <Pause className="h-3 w-3" />,
      className: "bg-warning/10 text-warning",
    },
  };
  const c = config[state];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        c.className,
      )}
    >
      {c.icon}
      {c.label}
    </span>
  );
}

function LoadingState() {
  return (
    <div className="space-y-2">
      {[1, 2, 3].map((i) => (
        <Card key={i}>
          <CardContent className="p-4">
            <div className="flex items-center gap-4">
              <Skeleton className="h-6 w-24" />
              <div className="flex-1 space-y-1">
                <Skeleton className="h-3 w-48" />
                <Skeleton className="h-4 w-36" />
              </div>
              <Skeleton className="h-4 w-32" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
