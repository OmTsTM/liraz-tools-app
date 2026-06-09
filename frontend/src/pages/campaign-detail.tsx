import {
  ArrowLeft,
  Calendar,
  Clock,
  FileText,
  Loader2,
  Pencil,
  Play,
  RefreshCw,
  RotateCcw,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { ApplicationProgress } from "@/features/applications/application-progress";
import {
  useLatestApplication,
  useRevertCampaign,
  useStartCampaign,
} from "@/features/applications/hooks";
import { checkCanPlay, formatarJanelaRestante } from "@/features/applications/play-check";
import { StartCampaignDialog } from "@/features/applications/start-campaign-dialog";
import { getCampaignColor } from "@/features/campaigns/campaign-colors";
import { CampaignForm } from "@/features/campaigns/campaign-form";
import { CampaignSkusPanel } from "@/features/campaigns/campaign-skus-panel";
import { CampaignStatusBadge } from "@/features/campaigns/campaign-status-badge";
import {
  useCampaign,
  useCancelCampaign,
  useDeleteCampaign,
  useImportMLCampaign,
  useReactivateCampaign,
  useScheduleCampaign,
  useUnscheduleCampaign,
  useUpdateCampaign,
} from "@/features/campaigns/hooks";
import { MargensEditorCard } from "@/features/campaigns/margens-editor-card";
import {
  NomeDuplicadoDialog,
  parseNomeDuplicado,
} from "@/features/campaigns/nome-duplicado-dialog";
import { MigracaoTab } from "@/features/migracao/migracao-tab";
import { cn } from "@/lib/utils";

// Registro PERSISTIDO (localStorage) do último sync ML por campanha. O
// auto-sync ao abrir o detalhe roda a varredura reversa do ML (~20s, lista
// TODOS os anúncios da loja) — sem isto, voltar pra tela re-roda tudo toda
// vez. Com o registro, re-navegar dentro da janela TTL pula a varredura
// (dados locais já frescos).
//
// Persiste em localStorage pra SOBREVIVER A F5/reload — antes o `Map` em
// memória sumia a cada reload e o usuário via o banner toda vez que voltava
// pra tela. Com TTL de 30 min, edições externas no painel ML aparecem em
// até meia hora; o botão "Atualizar do ML" sempre força sync imediato.
const SYNC_ML_STORAGE_KEY = "liraz_tools.ultimoSyncMl";
const SYNC_ML_TTL_MS = 30 * 60 * 1000; // 30 min

// Storage key + TTL pro auto-esconde do ApplicationProgress (resumo de
// "Campanha criada/Reversão/Falha"). Dois mecanismos somam:
//   1. Auto-TTL: depois de 24h do `finalizado_em`, esconde sozinho.
//   2. Dismiss manual: botão X marca application_id no localStorage; some
//      até nova execução criar uma Application diferente.
// Pra `running` nenhum dos dois aplica — o painel fica sempre visível
// pra o usuário acompanhar o progresso.
const APP_DISMISSED_STORAGE_KEY = "liraz_tools.dismissedApps";
const APP_AUTO_HIDE_MS = 24 * 60 * 60 * 1000; // 24h

function loadUltimoSyncMap(): Record<string, number> {
  try {
    const raw = window.localStorage.getItem(SYNC_ML_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object") {
      return parsed as Record<string, number>;
    }
  } catch {
    // localStorage indisponível ou JSON corrompido — começa do zero
  }
  return {};
}

function getUltimoSync(mlPromotionId: string): number | undefined {
  const map = loadUltimoSyncMap();
  return map[mlPromotionId];
}

function setUltimoSync(mlPromotionId: string, ts: number): void {
  try {
    const map = loadUltimoSyncMap();
    map[mlPromotionId] = ts;
    window.localStorage.setItem(SYNC_ML_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // localStorage cheio ou bloqueado — ignora, próximo render re-sincroniza
  }
}

function isAppDismissed(applicationId: string): boolean {
  try {
    const raw = window.localStorage.getItem(APP_DISMISSED_STORAGE_KEY);
    if (!raw) return false;
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return false;
    return Boolean((parsed as Record<string, unknown>)[applicationId]);
  } catch {
    return false;
  }
}

function dismissApp(applicationId: string): void {
  try {
    const raw = window.localStorage.getItem(APP_DISMISSED_STORAGE_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, number>) : ({} as Record<string, number>);
    map[applicationId] = Date.now();
    window.localStorage.setItem(APP_DISMISSED_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // ignora — próximo render mostra de novo, sem prejuízo
  }
}

export function CampaignDetailPage() {
  const { id, campaign_id } = useParams<{ id: string; campaign_id: string }>();
  const navigate = useNavigate();
  const location = useLocation();

  // State passado pelo handleCampaignClick da campaigns-list:
  //   needsSync     → dispara refresh em background ao montar
  //   mlPromotionId → usado quando a campanha é fantasma (GET dá 404 e
  //                   precisamos importar primeira vez)
  const navState = (location.state ?? {}) as {
    needsSync?: boolean;
    mlPromotionId?: string;
  };

  const { data: campaign, isPending, isError } = useCampaign(id, campaign_id);
  const updateMutation = useUpdateCampaign();
  const scheduleMutation = useScheduleCampaign();
  const unscheduleMutation = useUnscheduleCampaign();
  const cancelMutation = useCancelCampaign();
  const reactivateMutation = useReactivateCampaign();
  const deleteMutation = useDeleteCampaign();
  const startMutation = useStartCampaign();
  const revertMutation = useRevertCampaign();
  const importMLMutation = useImportMLCampaign();

  // ─── Sync ML em background (fix loading travado) ─────────────────────
  // Máquina de estado simples:
  //   idle    → ainda não disparou (mas pode disparar se navState pedir)
  //   running → mutation em curso, banner aparece
  //   done    → terminou (sucesso ou erro), banner some, não dispara mais
  //
  // Usar state ao invés de ref evita a categoria de bug em que o banner
  // ficava "preso" porque o reset/cleanup do ref não disparava re-render.
  const [syncState, setSyncState] = useState<"idle" | "running" | "done">("idle");
  // Latch síncrono pra disparar o sync UMA vez por mount. `syncState` não serve
  // de guard contra o double-invoke do StrictMode (em dev): as duas execuções do
  // efeito enxergam o MESMO valor "idle" capturado no render, então sem o ref o
  // import dispararia 2x (cada disparo = uma varredura reversa de TODOS os
  // anúncios da loja — caríssimo). O ref muda de forma síncrona e é visto pela
  // 2ª execução. Persiste pela vida do componente; re-navegar cria outro mount.
  const syncFiredRef = useRef(false);

  // Deps mínimas de propósito: não inclui `importMLMutation` (objeto novo a cada
  // render — incluir causa re-disparo em loop). O `syncFiredRef` + `syncState`
  // são os guards reais de disparo único.
  // biome-ignore lint/correctness/useExhaustiveDependencies: importMLMutation é estável o suficiente; incluir causa re-disparo em cada render
  useEffect(() => {
    if (syncFiredRef.current || syncState !== "idle") return;
    if (!id || !navState.needsSync || !navState.mlPromotionId) return;
    // Espera o GET inicial terminar antes de decidir (sucesso ou 404).
    if (isPending) return;

    syncFiredRef.current = true;

    // Sincronizado há pouco? Pula a varredura (dados frescos) — sem banner.
    // Persistido em localStorage, então TTL sobrevive a F5/reload.
    const ultimoSync = getUltimoSync(navState.mlPromotionId);
    if (ultimoSync && Date.now() - ultimoSync < SYNC_ML_TTL_MS) {
      setSyncState("done");
      return;
    }

    const syncKey = navState.mlPromotionId;
    setSyncState("running");
    importMLMutation.mutate(
      { profileId: id, mlPromotionId: navState.mlPromotionId },
      {
        onSuccess: (created) => {
          setUltimoSync(syncKey, Date.now());
          setSyncState("done");
          // Se foi fantasma (404 original) → redirect pro id real.
          // Senão fica na mesma URL — o invalidate do hook já refresca os dados.
          if (created.id !== campaign_id) {
            navigate(`/profiles/${id}/campaigns/${created.id}`, {
              replace: true,
            });
          }
        },
        onError: (err) => {
          setSyncState("done");
          toast.error("Falha ao sincronizar campanha do ML", {
            description: err instanceof Error ? err.message : "Erro desconhecido",
          });
        },
      },
    );
  }, [id, isPending, syncState, navState.needsSync, navState.mlPromotionId, campaign_id, navigate]);

  // Última aplicação da campanha (polling 2s enquanto running)
  const { data: latestApp } = useLatestApplication(id, campaign_id);

  // Estado pra forçar re-render quando o usuário clica no X (dismiss
  // grava em localStorage, mas o React não rerenderiza por isso).
  const [dismissedTick, setDismissedTick] = useState(0);

  // Decide se mostra o ApplicationProgress quando estado != running:
  // - `dismissed` (X foi clicado): esconde
  // - `finalizado_em` > 24h atrás: esconde silenciosamente
  // - rodando ou recém-concluído: mostra
  const shouldShowAppProgress = (() => {
    if (!latestApp) return false;
    if (latestApp.estado === "running") return true;
    if (isAppDismissed(latestApp.application_id)) return false;
    if (latestApp.finalizado_em) {
      const ageMs = Date.now() - new Date(latestApp.finalizado_em).getTime();
      if (ageMs > APP_AUTO_HIDE_MS) return false;
    }
    return true;
  })();
  // `dismissedTick` faz o React re-avaliar `shouldShowAppProgress` após dismiss
  void dismissedTick;

  const [editing, setEditing] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [showStartConfirm, setShowStartConfirm] = useState(false);
  const [showRevertConfirm, setShowRevertConfirm] = useState(false);

  // Conflito de nome em edição. Tem que ficar antes de qualquer early-return
  // pra respeitar a regra dos hooks (sempre mesma ordem entre renders).
  const [conflitoNome, setConflitoNome] = useState<{
    nome_tentado: string;
    sugestao: string;
    pendingPayload: {
      nome: string;
      data_inicio: string;
      data_fim: string;
      hora_disparo: string;
      hora_fim: string | null;
      simulacao_id: string | null;
      skus_selecionados: string[] | null;
    };
  } | null>(null);

  // Banner do sync ML: depende da máquina de estado `syncState`, NÃO de
  // `importMLMutation.isPending` (que pode ficar pendurada em casos raros).
  // - importandoPrimeiraVez: bloqueia a tela enquanto importa pela 1ª vez
  // - atualizandoEmBackground: banner não-bloqueante quando refresca em bg
  const importandoPrimeiraVez = syncState === "running" && Boolean(navState.needsSync);
  const atualizandoEmBackground =
    syncState === "running" && !isPending && !isError && Boolean(campaign);

  if (isPending) {
    return (
      <div className="space-y-4">
        {importandoPrimeiraVez && (
          <Card>
            <CardContent className="flex items-center gap-3 p-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Importando campanha do ML pela primeira vez. Isso pode levar alguns segundos pra lojas
              com muitos itens…
            </CardContent>
          </Card>
        )}
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  // Campanha fantasma vinda da listagem: GET local deu 404 mas a mutation
  // de import está rolando ou prestes a rolar. Mostra "Importando..." em
  // vez do "não encontrada" — sumiço dura segundos até o import retornar
  // e o useEffect navegar pro id real.
  if (isError || !campaign) {
    if (importandoPrimeiraVez) {
      return (
        <div className="space-y-4">
          <Card>
            <CardContent className="flex items-center gap-3 p-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Importando campanha do ML pela primeira vez. Isso pode levar alguns segundos pra lojas
              com muitos itens…
            </CardContent>
          </Card>
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-64 w-full" />
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${id}/campaigns`)}>
          <ArrowLeft className="h-4 w-4" />
          Voltar
        </Button>
        <Card>
          <CardContent className="p-8 text-center text-sm text-muted-foreground">
            Campanha não encontrada.
          </CardContent>
        </Card>
      </div>
    );
  }

  const submitUpdate = async (
    data: {
      nome: string;
      data_inicio: string;
      data_fim: string;
      hora_disparo: string;
      hora_fim: string | null;
      simulacao_id: string | null;
      skus_selecionados: string[] | null;
    },
    forcarNome = false,
  ) => {
    if (!id || !campaign_id) return;
    try {
      await updateMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
        payload: { ...data, forcar_nome: forcarNome },
      });
      toast.success("Campanha atualizada");
      setConflitoNome(null);
      setEditing(false);
    } catch (err) {
      const dup = parseNomeDuplicado(err);
      if (dup) {
        setConflitoNome({ ...dup, pendingPayload: data });
        return;
      }
      toast.error("Falha ao salvar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleUpdate = async (data: {
    nome: string;
    data_inicio: string;
    data_fim: string;
    hora_disparo: string;
    hora_fim: string | null;
    simulacao_id: string | null;
    skus_selecionados: string[] | null;
  }) => {
    await submitUpdate(data);
  };

  const handleConfirmarSugestaoEdit = () => {
    if (!conflitoNome) return;
    submitUpdate({ ...conflitoNome.pendingPayload, nome: conflitoNome.sugestao }, true);
  };

  const handleSchedule = async () => {
    if (!id || !campaign_id) return;
    try {
      await scheduleMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
      });
      toast.success("Campanha agendada");
    } catch (err) {
      toast.error("Falha ao agendar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleUnschedule = async () => {
    if (!id || !campaign_id) return;
    try {
      await unscheduleMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
      });
      toast.success("Voltou pra rascunho");
    } catch (err) {
      toast.error("Falha", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleCancel = async () => {
    if (!id || !campaign_id) return;
    try {
      await cancelMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
      });
      toast.success("Campanha cancelada");
      setShowCancelConfirm(false);
    } catch (err) {
      toast.error("Falha ao cancelar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleReactivate = async () => {
    if (!id || !campaign_id) return;
    try {
      const result = await reactivateMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
      });
      toast.success(
        result.status === "agendada"
          ? "Campanha reativada e agendada"
          : "Campanha reativada como rascunho",
      );
    } catch (err) {
      toast.error("Falha ao reativar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleRevert = async () => {
    if (!id || !campaign_id) return;
    try {
      await revertMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
      });
      toast.success("Reversão iniciada", {
        description: "Acompanhe o progresso abaixo.",
      });
      setShowRevertConfirm(false);
    } catch (err) {
      toast.error("Falha ao reverter", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleStart = async () => {
    if (!id || !campaign_id) return;
    try {
      await startMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
        payload: { imediato: true },
      });
      toast.success("Campanha iniciada", {
        description: "Acompanhe o progresso abaixo. Pode levar alguns minutos.",
      });
      setShowStartConfirm(false);
    } catch (err) {
      toast.error("Falha ao iniciar campanha", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleDelete = async () => {
    if (!id || !campaign_id) return;
    try {
      await deleteMutation.mutateAsync({
        profileId: id,
        campaignId: campaign_id,
      });
      toast.success("Campanha apagada");
      navigate(`/profiles/${id}/campaigns`);
    } catch (err) {
      toast.error("Falha ao apagar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleRefreshFromML = async () => {
    if (!id || !campaign?.ml_campaign_id) return;
    try {
      const synced = await importMLMutation.mutateAsync({
        profileId: id,
        mlPromotionId: campaign.ml_campaign_id,
        // Botão manual = varredura COMPLETA (descobre SKUs adicionados no painel
        // ML, inclusive legados MLB4xxx). O auto-sync ao abrir é direcionado.
        fullScan: true,
      });
      setUltimoSync(campaign.ml_campaign_id, Date.now());
      const skusAntes = campaign.skus_selecionados?.length ?? 0;
      const skusDepois = synced.skus_selecionados?.length ?? 0;
      // Toast sempre mostra os números — facilita diagnóstico
      if (skusAntes !== skusDepois) {
        toast.success("Sincronizado com o ML", {
          description: `Anúncios: ${skusAntes} → ${skusDepois}`,
        });
      } else {
        toast.success("Já estava sincronizado", {
          description: `${skusDepois} anúncios (ML e local conferem)`,
        });
      }
    } catch (err) {
      toast.error("Falha ao sincronizar do ML", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  // Renderização: form em edit mode, ou painel em view mode
  if (editing) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
          <ArrowLeft className="h-4 w-4" />
          Voltar ao detalhe
        </Button>
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Editar campanha</h2>
          <p className="mt-1 text-sm text-muted-foreground">{campaign.nome}</p>
        </div>
        {id && (
          <CampaignForm
            profileId={id}
            initial={campaign}
            submitLabel={updateMutation.isPending ? "Salvando..." : "Salvar alterações"}
            isSubmitting={updateMutation.isPending}
            onSubmit={handleUpdate}
            onCancel={() => setEditing(false)}
          />
        )}
      </div>
    );
  }

  const dataInicio = new Date(`${campaign.data_inicio}T00:00:00`);
  const dataFim = new Date(`${campaign.data_fim}T00:00:00`);

  // Cor estável da campanha — mesma cor usada no calendário e na lista
  const campaignColor = getCampaignColor(campaign.id);

  const canEdit = campaign.is_editable;
  // No modelo passo3, rascunho com SKUs já pode ser agendado (deal_price
  // é computado no disparo via `StartCampaignPasso3UseCase`). Simulação
  // continua sendo aceita pra compat com o fluxo antigo.
  const temSkusOuSimulacao =
    Boolean(campaign.simulacao_id) || (campaign.skus_selecionados?.length ?? 0) > 0;
  const canSchedule = campaign.status === "rascunho" && temSkusOuSimulacao;
  const canUnschedule = campaign.status === "agendada";
  const canCancel = campaign.status === "rascunho" || campaign.status === "agendada";
  const canReactivate = campaign.status === "cancelada";
  const canDelete = campaign.status === "rascunho" || campaign.status === "cancelada";

  // Campanha ML pode ser sincronizada — re-busca dados do painel do ML
  // pra refletir mudanças feitas lá (SKUs adicionados/removidos, etc).
  // Refresh do ML faz sentido sempre que a campanha já está lá — não
  // depende de ter sido criada no painel ML ou no app. O que importa é
  // ter `ml_campaign_id` (= presença real no ML).
  const canRefreshFromML = Boolean(campaign.ml_campaign_id);

  // Pode reverter: tem rollback_id E está em estado pós-aplicação.
  // Não permite reverter durante `executando` pra evitar conflito.
  const canRevert =
    Boolean(campaign.rollback_id) &&
    (campaign.status === "ativa" ||
      campaign.status === "finalizada" ||
      campaign.status === "falha");

  // Verifica se pode disparar (status compatível + janela ≥30min).
  // checkCanPlay encapsula toda a lógica — botão mostrado sempre que
  // status indica que faz sentido (rascunho com SKUs/sim ou agendada),
  // mas desabilitado se a janela é insuficiente.
  const playCheck = checkCanPlay(campaign);
  const mostrarPlay =
    campaign.status === "agendada" || (campaign.status === "rascunho" && temSkusOuSimulacao);

  // Texto descritivo do modal — diz quanto tempo vai durar + quantos itens
  const descricaoModal = (() => {
    const duracao = formatarJanelaRestante(playCheck.msAteOFim);
    const hora = new Date().toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
    });
    return `• Aplicar os preços novos da simulação aos anúncios no ML\n• Criar a campanha promocional no ML com os deal_prices da simulação\n• Começar AGORA (${hora}) e durar até o fim definido (${duracao})`;
  })();

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${id}/campaigns`)}>
        <ArrowLeft className="h-4 w-4" />
        Voltar pra lista
      </Button>

      {/* Banner não-bloqueante de sincronização (fix loading travado).
          Aparece quando a tela já renderizou mas a mutation de import-from-ml
          está rolando em background pra refrescar dados do ML. O user pode
          navegar livremente — quando o sync terminar, o cache atualiza
          sozinho via invalidateQueries do hook. */}
      {atualizandoEmBackground && (
        <Card className="border-blue-500/30 bg-blue-500/5">
          <CardContent className="flex items-center gap-3 p-3 text-sm">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-500" />
            <span className="text-muted-foreground">
              Atualizando dados do ML em segundo plano. Você pode continuar navegando — os dados
              serão atualizados quando o sync terminar.
            </span>
          </CardContent>
        </Card>
      )}

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            {/* Swatch da cor da campanha — conecta visualmente com a faixa
                lateral na listagem e os chips no calendário */}
            <div
              className={cn("h-6 w-1.5 shrink-0 rounded-full", campaignColor.border)}
              title="Cor identificadora da campanha"
            />
            <h2 className="text-2xl font-semibold tracking-tight">{campaign.nome}</h2>
            <CampaignStatusBadge status={campaign.status} />
          </div>
          <p className="mt-1 font-mono text-xs text-muted-foreground">ID: {campaign.id}</p>
        </div>

        <div className="flex flex-wrap gap-2">
          {mostrarPlay && (
            <Button
              size="sm"
              variant={playCheck.podeDisparar ? "default" : "outline"}
              onClick={() => setShowStartConfirm(true)}
              disabled={!playCheck.podeDisparar || startMutation.isPending}
              title={playCheck.motivoNaoPode ?? "Disparar agora"}
              className={playCheck.podeDisparar ? "bg-success hover:bg-success/90" : ""}
            >
              {startMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              Iniciar agora
            </Button>
          )}
          {canRevert && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setShowRevertConfirm(true)}
              disabled={revertMutation.isPending}
              title="Reverter preços ao estado anterior"
              className="border-warning/50 text-warning hover:bg-warning/10"
            >
              {revertMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Undo2 className="h-4 w-4" />
              )}
              Reverter
            </Button>
          )}
          {canRefreshFromML && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleRefreshFromML}
              disabled={importMLMutation.isPending}
              title="Re-buscar dados do ML (SKUs, status)"
            >
              {importMLMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Atualizar do ML
            </Button>
          )}
          {canEdit && (
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil className="h-4 w-4" />
              Editar
            </Button>
          )}
          {canSchedule && (
            <Button size="sm" onClick={handleSchedule} disabled={scheduleMutation.isPending}>
              {scheduleMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Clock className="h-4 w-4" />
              )}
              Agendar
            </Button>
          )}
          {canUnschedule && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleUnschedule}
              disabled={unscheduleMutation.isPending}
            >
              {unscheduleMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <FileText className="h-4 w-4" />
              )}
              Voltar pra rascunho
            </Button>
          )}
          {canCancel && (
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => setShowCancelConfirm(true)}
              disabled={cancelMutation.isPending}
            >
              <X className="h-4 w-4" />
              Cancelar
            </Button>
          )}
          {canReactivate && (
            <Button size="sm" onClick={handleReactivate} disabled={reactivateMutation.isPending}>
              {reactivateMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="h-4 w-4" />
              )}
              Reativar
            </Button>
          )}
          {canDelete && (
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => setShowDeleteConfirm(true)}
            >
              <Trash2 className="h-4 w-4" />
              Apagar
            </Button>
          )}
        </div>
      </div>

      {/* Período */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Período</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <DetailRow label="Início" value={dataInicio.toLocaleDateString("pt-BR")} />
          <DetailRow label="Fim" value={dataFim.toLocaleDateString("pt-BR")} />
          <DetailRow label="Hora de disparo" value={campaign.hora_disparo.slice(0, 5)} />
          {campaign.hora_fim && (
            <DetailRow label="Hora de término" value={campaign.hora_fim.slice(0, 5)} />
          )}
          <DetailRow
            label="Duração"
            value={`${campaign.duracao_dias} ${campaign.duracao_dias === 1 ? "dia" : "dias"}${
              campaign.duracao_dias > 31 ? " (acima do limite ML)" : ""
            }`}
            valueClassName={campaign.duracao_dias > 31 ? "text-warning" : undefined}
          />
        </CardContent>
      </Card>

      {/* Aviso quando não pode agendar (sem SKUs E sem simulação).
          No modelo passo3, basta ter SKUs — simulação virou opcional. */}
      {campaign.status === "rascunho" && !temSkusOuSimulacao && (
        <Card className="border-warning/40 bg-warning/5">
          <CardContent className="flex items-start gap-3 p-4 text-sm">
            <Calendar className="h-4 w-4 shrink-0 text-warning" />
            <p>
              Pra agendar esta campanha, adicione anúncios em "Adicionar/editar SKUs". O preço de
              venda na campanha é calculado <span className="font-medium">na hora do disparo</span>{" "}
              com base na margem-alvo do perfil.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Anúncios incluídos — lista é o conteúdo principal da campanha. */}
      {id && <CampaignSkusPanel profileId={id} campaign={campaign} />}

      {/* Editor de margens dentro da campanha — só faz sentido quando a
          campanha já está no ML (tem `ml_campaign_id`). Carrega sob demanda
          porque é pesado (1-3 chamadas ML por SKU). */}
      {id && campaign.ml_campaign_id && (
        <MargensEditorCard profileId={id} campaignId={campaign.id} />
      )}

      {/* Aba Migração (automação) — funciona em qualquer campanha que esteja
          no ML (independente de ter sido criada no painel ou disparada pelo
          app). Cobertura/Oportunidades/Histórico/Automação dependem de ter
          `ml_campaign_id` setado pra fazer as operações reais no ML. */}
      {campaign.ml_campaign_id && id && <MigracaoTab campaign={campaign} profileId={id} />}

      {/* Painel de execução — aparece em 3 modos:
          1. Há application (running, OU completed/failed e não dismissed e
             concluído há <24h): mostra ApplicationProgress
          2. Sem application + status compatível: card informativo sobre o Play
          3. Outros: nada (rascunho sem simulação, finalizada, etc) */}
      {latestApp && shouldShowAppProgress ? (
        <ApplicationProgress
          application={latestApp}
          onDismiss={() => {
            dismissApp(latestApp.application_id);
            setDismissedTick((t) => t + 1);
          }}
        />
      ) : mostrarPlay ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="text-base">Execução da campanha</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              Clique em <span className="font-medium text-foreground">"Iniciar agora"</span> acima
              pra disparar imediatamente, ou aguarde o scheduler disparar automaticamente na data de
              início.
            </p>
            <ol className="ml-5 list-decimal space-y-1 text-xs">
              <li>
                Aplicar os preços novos da simulação a cada anúncio via PUT /items/&#123;id&#125;
              </li>
              <li>Gerar snapshot de rollback automático</li>
              <li>Criar campanha SELLER_CAMPAIGN no ML com os deal_prices da simulação</li>
            </ol>
            {!playCheck.podeDisparar && playCheck.motivoNaoPode && (
              <p className="pt-2 text-xs text-warning">⚠ {playCheck.motivoNaoPode}</p>
            )}
          </CardContent>
        </Card>
      ) : null}

      {/* Confirmação de disparo manual ('Iniciar agora') */}
      <StartCampaignDialog
        open={showStartConfirm}
        onOpenChange={setShowStartConfirm}
        campanhaNome={campaign.nome}
        descricao={descricaoModal}
        isStarting={startMutation.isPending}
        onConfirm={handleStart}
      />

      {/* Modal de nome duplicado (edição) */}
      {conflitoNome && (
        <NomeDuplicadoDialog
          open={true}
          onOpenChange={(open) => !open && setConflitoNome(null)}
          nomeTentado={conflitoNome.nome_tentado}
          sugestao={conflitoNome.sugestao}
          isSaving={updateMutation.isPending}
          onConfirm={handleConfirmarSugestaoEdit}
        />
      )}

      {/* Confirmação de reversão */}
      <AlertDialog open={showRevertConfirm} onOpenChange={setShowRevertConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reverter preços ao estado anterior?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <span className="block">
                Esta ação vai aplicar PUT /items em cada anúncio da campanha{" "}
                <span className="font-semibold text-foreground">{campaign.nome}</span>, voltando o
                preço ao valor anterior salvo no rollback.
              </span>
              <span className="mt-2 block rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
                A SELLER_CAMPAIGN no Mercado Livre continuará existindo. Se quiser removê-la, faça
                isso manualmente no painel do ML após a reversão.
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={revertMutation.isPending}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRevert}
              disabled={revertMutation.isPending}
              className="bg-warning text-warning-foreground hover:bg-warning/90"
            >
              {revertMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Undo2 className="h-4 w-4" />
              )}
              Reverter
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirm cancel */}
      <AlertDialog open={showCancelConfirm} onOpenChange={setShowCancelConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancelar esta campanha?</AlertDialogTitle>
            <AlertDialogDescription>
              "{campaign.nome}" será marcada como cancelada. Você pode apagar depois ou deixar no
              histórico pra auditoria.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={cancelMutation.isPending}>Voltar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleCancel}
              disabled={cancelMutation.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {cancelMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <X className="h-4 w-4" />
              )}
              Cancelar campanha
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirm delete */}
      <AlertDialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Apagar esta campanha?</AlertDialogTitle>
            <AlertDialogDescription>
              "{campaign.nome}" será removida permanentemente. Esta ação não pode ser desfeita.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Voltar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
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

function DetailRow({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={valueClassName ?? "font-medium"}>{value}</span>
    </div>
  );
}
