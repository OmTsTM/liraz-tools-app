import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Play,
  Search,
  ShieldCheck,
  TestTube2,
  TimerReset,
  TrendingUp,
} from "lucide-react";
import { useEffect, useState } from "react";

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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SortableTh, useSortableData } from "@/components/ui/sortable-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "@/components/ui/toaster";
import { useProfile, useUpdateProfile } from "@/features/profiles/hooks";
import { cn } from "@/lib/utils";
import type { Campaign } from "@/types/api";

import {
  useCobertura,
  useCorrigirCobertura,
  useExecutarMigracoes,
  useHistoricoMigracao,
  useOportunidades,
  useSchedulerState,
  useTriggerScheduler,
} from "./hooks";

interface MigracaoTabProps {
  campaign: Campaign;
  profileId: string;
}

export function MigracaoTab({ campaign, profileId }: MigracaoTabProps) {
  const { data: profile } = useProfile(profileId);
  const { data: schedulerState } = useSchedulerState(profileId);

  const updateProfileMut = useUpdateProfile();
  const triggerSchedulerMut = useTriggerScheduler();

  const handleToggleAtiva = async (next: boolean) => {
    if (!profile) return;
    await updateProfileMut.mutateAsync({
      id: profileId,
      data: {
        config: {
          ...profile.config,
          migracao_automatica_ativa: next,
        },
      },
    });
    toast.success(
      next ? "Migração automática ativada para este perfil" : "Migração automática desativada",
    );
  };

  const handleToggleDryRun = async (next: boolean) => {
    if (!profile) return;
    await updateProfileMut.mutateAsync({
      id: profileId,
      data: {
        config: {
          ...profile.config,
          migracao_dry_run: next,
        },
      },
    });
    toast.success(
      next ? "Modo SIMULAÇÃO ligado (não chama ML)" : "Modo REAL ligado (chama ML de verdade)",
    );
  };

  const handleTriggerNow = async () => {
    try {
      await triggerSchedulerMut.mutateAsync({ profileId });
      toast.success("Ciclo disparado — veja Histórico e Cobertura");
    } catch (e) {
      toast.error("Falha ao disparar ciclo", {
        description: e instanceof Error ? e.message : "Erro desconhecido",
      });
    }
  };

  return (
    <div className="space-y-6">
      {/* ─── 1) Cobertura ──────────────────────────────────────── */}
      <CoberturaCard campaignId={campaign.id} profileId={profileId} />

      {/* ─── 2) Oportunidades ──────────────────────────────────── */}
      <OportunidadesCard
        campaignId={campaign.id}
        profileId={profileId}
        dryRunPerfil={Boolean(profile?.config.migracao_dry_run)}
      />

      {/* ─── 3) Histórico (filtrado pela campanha) ─────────────── */}
      <HistoricoCard
        campaignId={campaign.id}
        mlCampaignId={campaign.ml_campaign_id}
        profileId={profileId}
      />

      {/* ─── 4) Automação ──────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <TimerReset className="h-5 w-5" />
            Automação
          </CardTitle>
          <CardDescription>
            Quando ligada, o app verifica a cada N horas se algum SKU desta campanha pode ser
            migrado pra outras campanhas ML com melhor margem, e também garante que os SKUs
            continuem cobertos.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="rounded-md border p-3">
              <div className="text-xs font-medium text-muted-foreground">
                Automação rodando no servidor
              </div>
              <div className="mt-1 flex items-center gap-2">
                {schedulerState?.scheduler_globally_enabled ? (
                  <Badge variant="default" className="bg-emerald-600">
                    Ativa
                  </Badge>
                ) : (
                  <Badge variant="outline">Inativa</Badge>
                )}
                <span
                  className="text-xs text-muted-foreground"
                  title="Configuração avançada: requer ENABLE_MIGRATION_SCHEDULER=true no servidor"
                >
                  (configuração do servidor)
                </span>
              </div>
            </div>

            <div className="rounded-md border p-3">
              <div className="text-xs font-medium text-muted-foreground">
                Última execução nesse perfil
              </div>
              <div className="mt-1 text-sm">
                {schedulerState?.ultima_execucao_iso
                  ? new Date(schedulerState.ultima_execucao_iso).toLocaleString("pt-BR")
                  : "—"}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <ToggleRow
              label="Migração automática deste perfil"
              description="Quando ligada, esta loja entra na rotação do scheduler."
              checked={Boolean(profile?.config.migracao_automatica_ativa)}
              onChange={handleToggleAtiva}
              disabled={updateProfileMut.isPending}
            />

            <ToggleRow
              label="Modo simulação (dry_run)"
              description={
                profile?.config.migracao_dry_run
                  ? "Operações só são logadas, não chamam o ML"
                  : "MODO REAL — chama o ML de verdade"
              }
              checked={Boolean(profile?.config.migracao_dry_run)}
              onChange={handleToggleDryRun}
              disabled={updateProfileMut.isPending}
              danger={!profile?.config.migracao_dry_run}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={handleTriggerNow}
              disabled={triggerSchedulerMut.isPending}
            >
              {triggerSchedulerMut.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Play className="mr-2 h-4 w-4" />
              )}
              Forçar ciclo agora
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Subcomponentes
// ────────────────────────────────────────────────────────────────────────────

function ToggleRow({
  label,
  description,
  checked,
  onChange,
  disabled,
  danger,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-3 rounded-md border p-3",
        danger && "border-orange-300 bg-orange-50 dark:bg-orange-950/30",
      )}
    >
      <div className="space-y-1">
        <Label className="text-sm font-medium">{label}</Label>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 transition-colors",
          checked
            ? "border-emerald-600 bg-emerald-600 dark:border-emerald-500 dark:bg-emerald-500"
            : "border-zinc-300 bg-zinc-200 dark:border-zinc-700 dark:bg-zinc-800",
          disabled && "opacity-50",
        )}
      >
        <span
          className={cn(
            "inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform dark:bg-zinc-100",
            checked ? "translate-x-5" : "translate-x-0.5",
          )}
        />
      </button>
    </div>
  );
}

function CoberturaCard({ campaignId, profileId }: { campaignId: string; profileId: string }) {
  const [aberto, setAberto] = useState(false);
  const cobertura = useCobertura(profileId, campaignId, { enabled: aberto });
  const corrigirMut = useCorrigirCobertura();
  const [expandido, setExpandido] = useState(false);
  const [confirmReal, setConfirmReal] = useState(false);

  const handleCorrigir = (dryRun: boolean) => {
    if (!dryRun) {
      setConfirmReal(true);
      return;
    }
    executar(dryRun);
  };

  const executar = async (dryRun: boolean) => {
    try {
      const r = await corrigirMut.mutateAsync({
        profileId,
        campaignId,
        dryRun,
      });
      toast.success(`${dryRun ? "Simulação" : "Execução real"} concluída`, {
        description: `${r.total_corrigidos} corrigidos de ${r.total_descobertos} descobertos`,
      });
    } catch (e) {
      toast.error("Falha ao corrigir", {
        description: e instanceof Error ? e.message : "Erro desconhecido",
      });
    }
  };

  const totalDescobertos = cobertura.data?.descobertos ?? 0;
  const pct = cobertura.data?.percentual_cobertura ?? 0;

  const corCobertura =
    pct >= 100 ? "text-emerald-600" : pct >= 80 ? "text-yellow-600" : "text-red-600";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5" />
              Cobertura
            </CardTitle>
            <CardDescription className="mt-2">
              Garante que todos os SKUs desta guarda-chuva continuem ativos em alguma campanha ML.
              Sem cobertura, anúncios voltam ao preço cheio.
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setAberto((v) => !v)}>
            {aberto ? "Ocultar" : "Verificar"}
          </Button>
        </div>
      </CardHeader>
      {aberto && (
        <CardContent className="space-y-4">
          {cobertura.isLoading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Consultando ML (pode demorar ~30s)...
            </div>
          )}

          {cobertura.data && (
            <>
              <div className="flex items-center gap-4">
                <div className={cn("text-2xl font-bold", corCobertura)}>{pct}%</div>
                <div className="text-sm">
                  <div>
                    <span className="font-medium">{cobertura.data.coberto_na_origem}</span> de{" "}
                    <span className="font-medium">{cobertura.data.total_skus}</span> SKUs cobertos
                    na origem
                  </div>
                  {totalDescobertos > 0 && (
                    <div className="text-red-600">
                      {totalDescobertos} SKU{totalDescobertos > 1 ? "s" : ""} descoberto
                      {totalDescobertos > 1 ? "s" : ""}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => cobertura.refetch()}
                  disabled={cobertura.isFetching}
                >
                  {cobertura.isFetching ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <ShieldCheck className="mr-2 h-4 w-4" />
                  )}
                  Verificar agora
                </Button>

                {totalDescobertos > 0 && (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleCorrigir(true)}
                      disabled={corrigirMut.isPending}
                    >
                      <TestTube2 className="mr-2 h-4 w-4" />
                      Corrigir (simulação)
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => handleCorrigir(false)}
                      disabled={corrigirMut.isPending}
                    >
                      <AlertCircle className="mr-2 h-4 w-4" />
                      Corrigir REAL
                    </Button>
                  </>
                )}
              </div>

              {(cobertura.data.descobertos_detalhe.length > 0 ||
                cobertura.data.coberto_em_outras_detalhe.length > 0) && (
                <>
                  <button
                    type="button"
                    onClick={() => setExpandido((v) => !v)}
                    className="text-sm text-primary hover:underline"
                  >
                    {expandido ? "Ocultar" : "Mostrar"} detalhes
                  </button>
                  {expandido && (
                    <div className="space-y-3">
                      {cobertura.data.descobertos_detalhe.length > 0 && (
                        <div>
                          <div className="mb-1 text-xs font-medium text-red-600">
                            Descobertos ({cobertura.data.descobertos_detalhe.length})
                          </div>
                          <div className="space-y-1 text-xs">
                            {cobertura.data.descobertos_detalhe.map((d) => (
                              <div key={d.item_id} className="font-mono">
                                {d.item_id} {d.sku && `— ${d.sku}`}
                                {d.titulo && (
                                  <span className="text-muted-foreground"> — {d.titulo}</span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {cobertura.data.coberto_em_outras_detalhe.length > 0 && (
                        <div>
                          <div className="mb-1 text-xs font-medium text-yellow-600">
                            Em outras campanhas apenas (
                            {cobertura.data.coberto_em_outras_detalhe.length})
                          </div>
                          <div className="space-y-1 text-xs">
                            {cobertura.data.coberto_em_outras_detalhe.map((c) => (
                              <div key={c.item_id} className="font-mono">
                                {c.item_id} → {c.promocoes_ativas.join(", ")}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </CardContent>
      )}

      <AlertDialog open={confirmReal} onOpenChange={setConfirmReal}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirmar correção REAL</AlertDialogTitle>
            <AlertDialogDescription>
              Vou re-adicionar {totalDescobertos} SKU{totalDescobertos > 1 ? "s" : ""} na campanha
              ML imediatamente. Isso faz chamadas POST de verdade ao ML. Deseja prosseguir?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmReal(false);
                executar(false);
              }}
            >
              Confirmar e executar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

function OportunidadesCard({
  campaignId,
  profileId,
  dryRunPerfil,
}: {
  campaignId: string;
  profileId: string;
  dryRunPerfil: boolean;
}) {
  const [aberto, setAberto] = useState(false);
  const oportunidades = useOportunidades(profileId, campaignId, { enabled: aberto });
  const executarMut = useExecutarMigracoes();
  const [confirmReal, setConfirmReal] = useState(false);
  const [pagina, setPagina] = useState(0);
  const [busca, setBusca] = useState("");
  const POR_PAGINA = 20;

  const totalOp = oportunidades.data?.total_oportunidades ?? 0;
  const skusUnicos = oportunidades.data?.oportunidades
    ? new Set(oportunidades.data.oportunidades.map((o) => o.item_id)).size
    : 0;

  const rawOportunidades = oportunidades.data?.oportunidades ?? [];
  const sortedOp = useSortableData(
    rawOportunidades,
    {
      sku: (o) => o.sku ?? "",
      destino: (o) => o.campanha_destino_ml_nome ?? "",
      preco_atual: (o) => o.preco_atual,
      deal_price: (o) => o.deal_price_sugerido,
      margem: (o) => o.margem_pct_pos_migracao,
      status: (o) => o.campanha_destino_ml_status ?? "",
    },
    busca,
    ["sku", "item_id", "campanha_destino_ml_nome"],
  );
  const todasOportunidades = sortedOp.data;
  const totalPaginas = Math.ceil(todasOportunidades.length / POR_PAGINA);
  const paginadas = todasOportunidades.slice(pagina * POR_PAGINA, (pagina + 1) * POR_PAGINA);

  // Quando busca/ordenação muda e a página atual fica fora do range, volta pra 0.
  useEffect(() => {
    if (pagina > 0 && pagina >= totalPaginas) setPagina(0);
  }, [pagina, totalPaginas]);

  const executar = async () => {
    try {
      const r = await executarMut.mutateAsync({
        profileId,
        campaignId,
        dryRun: false,
      });
      toast.success("Migrações executadas no ML", {
        description: `${r.total_sucesso} de ${r.total_migracoes_tentadas} sucessos`,
      });
      // Aguarda 1.5s pro ML processar e refetch
      setTimeout(() => {
        void oportunidades.refetch();
      }, 1500);
    } catch (e) {
      toast.error("Falha ao executar", {
        description: e instanceof Error ? e.message : "Erro desconhecido",
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex items-center gap-2">
              <TrendingUp className="h-5 w-5" />
              Oportunidades de migração
            </CardTitle>
            <CardDescription className="mt-2">
              SKUs desta campanha que podem migrar pra outras campanhas ML com margem na faixa
              configurada.
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setAberto((v) => !v)}>
            {aberto ? "Ocultar" : "Detectar"}
          </Button>
        </div>
      </CardHeader>
      {aberto && (
        <CardContent className="space-y-4">
          {oportunidades.isLoading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Detectando (pode demorar ~50s)...
            </div>
          )}

          {oportunidades.data && (
            <>
              <div className="text-sm">
                <span className="font-medium">{skusUnicos}</span> SKU
                {skusUnicos !== 1 ? "s" : ""} em <span className="font-medium">{totalOp}</span>{" "}
                oportunidade
                {totalOp !== 1 ? "s" : ""}
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => oportunidades.refetch()}
                  disabled={oportunidades.isFetching}
                >
                  {oportunidades.isFetching ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <TrendingUp className="mr-2 h-4 w-4" />
                  )}
                  Detectar agora
                </Button>

                {totalOp > 0 && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setConfirmReal(true)}
                    disabled={executarMut.isPending}
                  >
                    <AlertCircle className="mr-2 h-4 w-4" />
                    Executar migrações
                  </Button>
                )}
              </div>

              {rawOportunidades.length > 0 && (
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={busca}
                    onChange={(e) => setBusca(e.target.value)}
                    placeholder="Buscar por SKU, MLB ou destino…"
                    className="h-8 pl-8 text-xs"
                  />
                </div>
              )}

              {todasOportunidades.length > 0 && (
                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>
                          <SortableTh
                            sortKey="sku"
                            state={sortedOp.sortState}
                            onSort={sortedOp.toggleSort}
                          >
                            SKU
                          </SortableTh>
                        </TableHead>
                        <TableHead>
                          <SortableTh
                            sortKey="destino"
                            state={sortedOp.sortState}
                            onSort={sortedOp.toggleSort}
                          >
                            Destino
                          </SortableTh>
                        </TableHead>
                        <TableHead className="text-right">
                          <SortableTh
                            sortKey="preco_atual"
                            state={sortedOp.sortState}
                            onSort={sortedOp.toggleSort}
                            className="justify-end"
                          >
                            Preço atual
                          </SortableTh>
                        </TableHead>
                        <TableHead className="text-right">
                          <SortableTh
                            sortKey="deal_price"
                            state={sortedOp.sortState}
                            onSort={sortedOp.toggleSort}
                            className="justify-end"
                          >
                            Deal price
                          </SortableTh>
                        </TableHead>
                        <TableHead className="text-right">
                          <SortableTh
                            sortKey="margem"
                            state={sortedOp.sortState}
                            onSort={sortedOp.toggleSort}
                            className="justify-end"
                          >
                            Margem
                          </SortableTh>
                        </TableHead>
                        <TableHead>
                          <SortableTh
                            sortKey="status"
                            state={sortedOp.sortState}
                            onSort={sortedOp.toggleSort}
                          >
                            Status
                          </SortableTh>
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {paginadas.map((o, i) => (
                        <TableRow key={`${o.item_id}-${o.campanha_destino_ml_id}-${i}`}>
                          <TableCell className="font-mono text-xs">
                            <div>{o.sku ?? "—"}</div>
                            <div className="text-muted-foreground">{o.item_id}</div>
                          </TableCell>
                          <TableCell>{o.campanha_destino_ml_nome}</TableCell>
                          <TableCell className="text-right">
                            R$ {o.preco_atual.toFixed(2)}
                          </TableCell>
                          <TableCell className="text-right font-medium">
                            R$ {o.deal_price_sugerido.toFixed(2)}
                          </TableCell>
                          <TableCell className="text-right">
                            {(o.margem_pct_pos_migracao * 100).toFixed(1)}%
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={
                                o.campanha_destino_ml_status === "started" ? "default" : "outline"
                              }
                            >
                              {o.campanha_destino_ml_status}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {totalPaginas > 1 && (
                    <div className="flex items-center justify-between border-t p-2 text-xs">
                      <span className="text-muted-foreground">
                        Página {pagina + 1} de {totalPaginas} ({todasOportunidades.length} total)
                      </span>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setPagina((p) => Math.max(0, p - 1))}
                          disabled={pagina === 0}
                        >
                          Anterior
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setPagina((p) => Math.min(totalPaginas - 1, p + 1))}
                          disabled={pagina >= totalPaginas - 1}
                        >
                          Próxima
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </CardContent>
      )}

      <AlertDialog open={confirmReal} onOpenChange={setConfirmReal}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirmar execução REAL</AlertDialogTitle>
            <AlertDialogDescription>
              Vou executar {totalOp} migraç{totalOp === 1 ? "ão" : "ões"} em modo REAL. Isso vai
              adicionar SKUs em campanhas ML imediatamente (POST de verdade no ML).
              {!dryRunPerfil && (
                <>
                  {" "}
                  <strong>Atenção:</strong> o perfil também está em modo REAL (dry_run desligado).
                </>
              )}
              <br />
              <br />
              Deseja prosseguir?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmReal(false);
                void executar();
              }}
            >
              Confirmar e executar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

function HistoricoCard({
  campaignId,
  mlCampaignId,
  profileId,
}: {
  campaignId: string;
  mlCampaignId: string | null;
  profileId: string;
}) {
  const [aberto, setAberto] = useState(false);
  const [pagina, setPagina] = useState(0);
  const [busca, setBusca] = useState("");
  const POR_PAGINA = 20;
  const historico = useHistoricoMigracao(profileId, 200, { enabled: aberto });

  // Filtra pela campanha atual: origem local OU destino ML
  const filtradosCampanha = (historico.data?.registros ?? []).filter(
    (r) =>
      r.campanha_origem_id === campaignId ||
      (mlCampaignId && r.campanha_destino_ml_id === mlCampaignId),
  );

  const sortedHist = useSortableData(
    filtradosCampanha,
    {
      quando: (r) => r.timestamp,
      sku: (r) => r.sku ?? "",
      destino: (r) => r.campanha_destino_ml_nome ?? "",
      op: (r) => r.operacao ?? "",
      modo: (r) => (r.dry_run ? "simulação" : "real"),
      resultado: (r) => (r.sucesso ? "ok" : "erro"),
      preco: (r) => r.deal_price ?? null,
      margem: (r) => r.margem_pct_prevista ?? null,
    },
    busca,
    ["sku", "item_id", "campanha_destino_ml_nome", "operacao"],
  );
  const filtrados = sortedHist.data;

  const totalPaginas = Math.ceil(filtrados.length / POR_PAGINA);
  const paginados = filtrados.slice(pagina * POR_PAGINA, (pagina + 1) * POR_PAGINA);

  useEffect(() => {
    if (pagina > 0 && pagina >= totalPaginas) setPagina(0);
  }, [pagina, totalPaginas]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5" />
              Histórico de operações
            </CardTitle>
            <CardDescription className="mt-2">
              Operações de migração e watchdog que envolveram esta campanha (como origem ou
              destino). Inclui simulações e ações reais.
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setAberto((v) => !v)}>
            {aberto ? "Ocultar" : "Ver histórico"}
          </Button>
        </div>
      </CardHeader>
      {aberto && (
        <CardContent>
          {historico.isLoading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Carregando histórico...
            </div>
          )}
          {historico.data && filtradosCampanha.length === 0 && (
            <div className="text-sm text-muted-foreground">
              Nenhuma operação registrada pra esta campanha ainda.
            </div>
          )}
          {filtradosCampanha.length > 0 && (
            <>
              <div className="relative mb-3">
                <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={busca}
                  onChange={(e) => setBusca(e.target.value)}
                  placeholder="Buscar por SKU, MLB, destino ou operação…"
                  className="h-8 pl-8 text-xs"
                />
              </div>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>
                        <SortableTh
                          sortKey="quando"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                        >
                          Quando
                        </SortableTh>
                      </TableHead>
                      <TableHead>
                        <SortableTh
                          sortKey="sku"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                        >
                          SKU
                        </SortableTh>
                      </TableHead>
                      <TableHead>
                        <SortableTh
                          sortKey="destino"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                        >
                          Destino
                        </SortableTh>
                      </TableHead>
                      <TableHead>
                        <SortableTh
                          sortKey="op"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                        >
                          Op
                        </SortableTh>
                      </TableHead>
                      <TableHead>
                        <SortableTh
                          sortKey="modo"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                        >
                          Modo
                        </SortableTh>
                      </TableHead>
                      <TableHead>
                        <SortableTh
                          sortKey="resultado"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                        >
                          Resultado
                        </SortableTh>
                      </TableHead>
                      <TableHead className="text-right">
                        <SortableTh
                          sortKey="preco"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                          className="justify-end"
                        >
                          Preço
                        </SortableTh>
                      </TableHead>
                      <TableHead className="text-right">
                        <SortableTh
                          sortKey="margem"
                          state={sortedHist.sortState}
                          onSort={sortedHist.toggleSort}
                          className="justify-end"
                        >
                          Margem
                        </SortableTh>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {paginados.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell className="whitespace-nowrap text-xs">
                          {new Date(r.timestamp).toLocaleString("pt-BR", {
                            dateStyle: "short",
                            timeStyle: "short",
                          })}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          <div>{r.sku ?? "—"}</div>
                          <div className="text-muted-foreground">{r.item_id}</div>
                        </TableCell>
                        <TableCell className="text-xs">{r.campanha_destino_ml_nome}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs">
                            {r.operacao}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {r.dry_run ? (
                            <Badge variant="outline" className="text-xs">
                              simulação
                            </Badge>
                          ) : (
                            <Badge variant="default" className="text-xs">
                              real
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          {r.sucesso ? (
                            <Badge variant="default" className="bg-emerald-600 text-xs">
                              OK
                            </Badge>
                          ) : (
                            <Badge variant="destructive" className="text-xs">
                              erro
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-right text-xs">
                          {r.deal_price ? `R$ ${r.deal_price.toFixed(2)}` : "—"}
                        </TableCell>
                        <TableCell className="text-right text-xs">
                          {r.margem_pct_prevista
                            ? `${(r.margem_pct_prevista * 100).toFixed(1)}%`
                            : "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {totalPaginas > 1 && (
                  <div className="flex items-center justify-between border-t p-2 text-xs">
                    <span className="text-muted-foreground">
                      Página {pagina + 1} de {totalPaginas} ({filtrados.length} total)
                    </span>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setPagina((p) => Math.max(0, p - 1))}
                        disabled={pagina === 0}
                      >
                        Anterior
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setPagina((p) => Math.min(totalPaginas - 1, p + 1))}
                        disabled={pagina >= totalPaginas - 1}
                      >
                        Próxima
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}
