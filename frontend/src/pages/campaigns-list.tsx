import { ArrowLeft, Calendar, ChevronDown, ChevronUp, Play, Plus, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
import { useStartCampaign } from "@/features/applications/hooks";
import { checkCanPlay, formatarJanelaRestante } from "@/features/applications/play-check";
import { StartCampaignDialog } from "@/features/applications/start-campaign-dialog";
import { CampaignCalendar } from "@/features/campaigns/campaign-calendar";
import { getCampaignColor } from "@/features/campaigns/campaign-colors";
import { CampaignStatusBadge } from "@/features/campaigns/campaign-status-badge";
import { useCampaigns, useDeleteCampaign } from "@/features/campaigns/hooks";
import { useProfile } from "@/features/profiles/hooks";
import { cn } from "@/lib/utils";
import type { Campaign } from "@/types/api";

const CALENDAR_VISIBLE_KEY = "campaigns_calendar_visible_v1";
const OCULTAR_FINALIZADAS_KEY = "campaigns_hide_finalized_v1";
const INCLUIR_ARQUIVADAS_KEY = "campaigns_include_archived_v1";

export function CampaignsListPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [pendingDelete, setPendingDelete] = useState<Campaign | null>(null);
  const [pendingPlay, setPendingPlay] = useState<Campaign | null>(null);
  const [calendarVisible, setCalendarVisible] = useState(() => {
    const stored = localStorage.getItem(CALENDAR_VISIBLE_KEY);
    return stored === null ? true : stored === "true";
  });

  // Leva 5.7.1: 2 toggles persistentes em localStorage.
  // ocultarFinalizadas é filtro CLIENTE — não muda a query. Tira finalizadas
  // da lista E do calendário. Default false (mostra).
  // incluirArquivadas é SERVER-side — controla query param ?include_archived.
  // Default false (não inclui arquivadas; soft delete fez o trabalho).
  const [ocultarFinalizadas, setOcultarFinalizadas] = useState(() => {
    return localStorage.getItem(OCULTAR_FINALIZADAS_KEY) === "true";
  });
  const [incluirArquivadas, setIncluirArquivadas] = useState(() => {
    return localStorage.getItem(INCLUIR_ARQUIVADAS_KEY) === "true";
  });

  const { data: profile } = useProfile(id);
  const { data: campaigns, isPending, isError } = useCampaigns(id, incluirArquivadas);
  const deleteMutation = useDeleteCampaign();
  const startMutation = useStartCampaign();

  useEffect(() => {
    localStorage.setItem(CALENDAR_VISIBLE_KEY, String(calendarVisible));
  }, [calendarVisible]);

  useEffect(() => {
    localStorage.setItem(OCULTAR_FINALIZADAS_KEY, String(ocultarFinalizadas));
  }, [ocultarFinalizadas]);

  useEffect(() => {
    localStorage.setItem(INCLUIR_ARQUIVADAS_KEY, String(incluirArquivadas));
  }, [incluirArquivadas]);

  // Filtro cliente: tira finalizadas se o toggle estiver ON. Aplicado tanto
  // na lista quanto no calendário pra manter consistência visual.
  const campaignsVisiveis = useMemo(() => {
    if (!campaigns) return campaigns;
    if (!ocultarFinalizadas) return campaigns;
    return campaigns.filter((c) => c.status !== "finalizada");
  }, [campaigns, ocultarFinalizadas]);

  const handleConfirmDelete = async () => {
    if (!id || !pendingDelete) return;
    try {
      await deleteMutation.mutateAsync({
        profileId: id,
        campaignId: pendingDelete.id,
      });
      toast.success("Campanha removida");
    } catch (err) {
      toast.error("Falha ao remover", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    } finally {
      setPendingDelete(null);
    }
  };

  const handleConfirmPlay = async () => {
    if (!id || !pendingPlay) return;
    try {
      await startMutation.mutateAsync({
        profileId: id,
        campaignId: pendingPlay.id,
        payload: { imediato: true },
      });
      toast.success("Campanha iniciada", {
        description: "Abra o detalhe pra acompanhar o progresso.",
      });
      setPendingPlay(null);
    } catch (err) {
      toast.error("Falha ao iniciar campanha", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  // Não usamos mais importMutation aqui — agora a tela de detalhe é quem
  // dispara o sync em background, sem bloquear navegação (fix loading travado).

  const handleCampaignClick = (c: Campaign) => {
    if (!id) return;

    // Navega IMEDIATAMENTE. Sem await, sem fetch antes. A detail page é quem
    // detecta `origem === 'ml'` no state e dispara o sync em background,
    // mostrando banner não-bloqueante enquanto carrega.
    //
    // Qualquer campanha COM `ml_campaign_id` está no ML — sincroniza ao abrir
    // pra refletir mudanças que possam ter sido feitas no painel ML.
    // `origem='ml'` (importada do painel) era usado aqui, mas campanhas LOCAIS
    // disparadas no ML também precisam do mesmo sync — usar `ml_campaign_id`
    // unifica os dois casos.
    const needsSync = Boolean(c.ml_campaign_id);
    navigate(`/profiles/${id}/campaigns/${c.id}`, {
      state: needsSync ? { needsSync: true, mlPromotionId: c.ml_campaign_id } : undefined,
    });
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
            Campanhas
            {profile && (
              <span className="ml-2 text-base font-normal text-muted-foreground">
                · {profile.name}
              </span>
            )}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Planeje e agende eventos promocionais. Cada campanha usa uma simulação de reprecificação
            como base. Limite do ML BR: <span className="font-medium">1 mês por campanha</span>.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <RoleGate profileId={id} minimum="operator">
            <Button onClick={() => navigate(`/profiles/${id}/campaigns/new`)} size="sm">
              <Plus className="h-4 w-4" />
              Nova campanha
            </Button>
          </RoleGate>
        </div>
      </div>

      {isPending && (
        <>
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-64 w-full" />
        </>
      )}

      {isError && (
        <Card>
          <CardContent className="p-8 text-center text-sm text-muted-foreground">
            Falha ao carregar campanhas.
          </CardContent>
        </Card>
      )}

      {campaigns && campaigns.length === 0 && (
        <Card>
          <CardContent className="p-12 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
              <Calendar className="h-6 w-6 text-muted-foreground" />
            </div>
            <h3 className="text-base font-semibold">Nenhuma campanha ainda</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Crie sua primeira campanha clicando em "Nova campanha". O calendário abaixo te ajuda a
              visualizar disponibilidade do mês.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Toolbar de filtros — só aparece quando tem campanhas pra filtrar.
          Os 2 toggles persistem em localStorage. */}
      {campaigns && campaigns.length > 0 && (
        <div className="flex flex-wrap items-center gap-4 rounded-md border bg-muted/30 px-3 py-2 text-xs">
          <span className="font-medium text-muted-foreground">Filtros:</span>
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={ocultarFinalizadas}
              onChange={(e) => setOcultarFinalizadas(e.target.checked)}
              className="h-4 w-4 rounded border-input accent-primary"
            />
            <span>Ocultar finalizadas</span>
          </label>
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={incluirArquivadas}
              onChange={(e) => setIncluirArquivadas(e.target.checked)}
              className="h-4 w-4 rounded border-input accent-primary"
            />
            <span>
              Incluir arquivadas <span className="text-muted-foreground">(&gt;15 dias)</span>
            </span>
          </label>
        </div>
      )}

      {/* Lista (se tem campanhas visíveis) + calendário (sempre, mesmo vazio).
          Lista PRIMEIRO, calendário DEPOIS. */}
      {campaignsVisiveis && (
        <>
          {campaignsVisiveis.length > 0 ? (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-muted-foreground">
                Todas as campanhas ({campaignsVisiveis.length}
                {campaigns && campaigns.length !== campaignsVisiveis.length && (
                  <span className="text-muted-foreground/70"> de {campaigns.length}</span>
                )}
                )
              </h3>
              {campaignsVisiveis.map((c) => (
                <CampaignRow
                  key={c.id}
                  campaign={c}
                  onOpen={() => handleCampaignClick(c)}
                  onDelete={() => setPendingDelete(c)}
                  onPlay={() => setPendingPlay(c)}
                />
              ))}
            </div>
          ) : (
            campaigns &&
            campaigns.length > 0 && (
              <div className="rounded-md border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                Todas as campanhas estão ocultas pelos filtros atuais.{" "}
                <button
                  type="button"
                  onClick={() => {
                    setOcultarFinalizadas(false);
                    setIncluirArquivadas(true);
                  }}
                  className="font-medium text-foreground underline-offset-2 hover:underline"
                >
                  Mostrar tudo
                </button>
                .
              </div>
            )
          )}

          {/* Toggle do calendário — sempre visível, ajuda planejamento */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => setCalendarVisible((v) => !v)}
              className="flex items-center gap-2 text-sm font-semibold text-muted-foreground transition-colors hover:text-foreground"
            >
              {calendarVisible ? (
                <ChevronUp className="h-4 w-4" />
              ) : (
                <ChevronDown className="h-4 w-4" />
              )}
              {calendarVisible ? "Ocultar calendário" : "Mostrar calendário"}
            </button>
            {calendarVisible && (
              <CampaignCalendar
                campaigns={campaignsVisiveis}
                onCampaignClick={handleCampaignClick}
              />
            )}
          </div>
        </>
      )}

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open: boolean) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Apagar esta campanha?</AlertDialogTitle>
            <AlertDialogDescription>
              "{pendingDelete?.nome}" será removida permanentemente. Esta ação não pode ser
              desfeita.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              disabled={deleteMutation.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              <Trash2 className="h-4 w-4" />
              Apagar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Modal de confirmação do Play (disparar agora) — invocado pelo
          botão Play do CampaignRow. Reutiliza o mesmo StartCampaignDialog
          que o detail page usa, com descrição contextual da campanha. */}
      {pendingPlay && (
        <StartCampaignDialog
          open={true}
          onOpenChange={(open: boolean) => !open && setPendingPlay(null)}
          campanhaNome={pendingPlay.nome}
          descricao={(() => {
            const check = checkCanPlay(pendingPlay);
            const duracao = formatarJanelaRestante(check.msAteOFim);
            const hora = new Date().toLocaleTimeString("pt-BR", {
              hour: "2-digit",
              minute: "2-digit",
            });
            return `• Aplicar os preços novos da simulação aos anúncios no ML\n• Criar a campanha promocional no ML com os deal_prices da simulação\n• Começar AGORA (${hora}) e durar até o fim definido (${duracao})`;
          })()}
          isStarting={startMutation.isPending}
          onConfirm={handleConfirmPlay}
        />
      )}
    </div>
  );
}

function CampaignRow({
  campaign,
  onOpen,
  onDelete,
  onPlay,
}: {
  campaign: Campaign;
  onOpen: () => void;
  onDelete: () => void;
  onPlay: () => void;
}) {
  const dataInicio = new Date(`${campaign.data_inicio}T00:00:00`);
  const dataFim = new Date(`${campaign.data_fim}T00:00:00`);
  const periodo = `${dataInicio.toLocaleDateString("pt-BR")} → ${dataFim.toLocaleDateString("pt-BR")}`;
  const hora = campaign.hora_disparo.slice(0, 5);

  // Leva 5.9.1/5.9.2: campanhas vindas do ML têm origem="ml".
  // - "Fantasma" = ainda só listada via /seller-promotions, sem registro local.
  //   Detectamos via created_at=null. Clicar importa+espelha.
  // - "Importada" = já tem registro no banco (created_at preenchido). Continua
  //   read-only no app — gerenciamento real é no painel do ML.
  const isML = campaign.origem === "ml";
  const isMLFantasma = isML && campaign.created_at === null;

  // Cor estável da campanha (mesma campaign = mesma cor sempre).
  // Mostrada como faixa vertical à esquerda do card.
  const color = getCampaignColor(campaign.id);

  // Mesmas regras do detail (checkCanPlay): mostra botão se faz sentido,
  // habilita só se status+janela permitem
  const playCheck = checkCanPlay(campaign);
  const temSkusOuSimulacao =
    Boolean(campaign.simulacao_id) || (campaign.skus_selecionados?.length ?? 0) > 0;
  const mostrarPlay =
    !isML &&
    (campaign.status === "agendada" || (campaign.status === "rascunho" && temSkusOuSimulacao));

  const canDelete = !isML && (campaign.status === "rascunho" || campaign.status === "cancelada");
  const motivoNaoDelete = isML
    ? "Campanha do ML — gerencie diretamente no painel do Mercado Livre"
    : canDelete
      ? null
      : campaign.status === "agendada"
        ? "Cancele a campanha antes de apagar"
        : campaign.status === "executando" || campaign.status === "ativa"
          ? "Campanha em andamento — não pode ser apagada"
          : "Campanha já executou — histórico preservado pra auditoria";

  return (
    <Card
      className="relative cursor-pointer overflow-hidden transition-colors hover:bg-muted/30"
      onClick={onOpen}
    >
      {/* Faixa colorida vertical à esquerda — identifica visualmente a
          campanha por uma cor estável (mesma campanha = mesma cor sempre).
          Complementa o status badge à direita (que mostra estado). */}
      <div className={cn("absolute left-0 top-0 h-full w-1.5", color.border)} />

      <CardContent className="flex flex-wrap items-center gap-4 p-4 pl-5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-medium">{campaign.nome}</h4>
            {/* Badge "ML" — diferencia campanhas trazidas do ML das locais */}
            {isML && (
              <span
                className="inline-flex items-center rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400"
                title={
                  isMLFantasma
                    ? "Campanha criada no ML — clique pra importar e visualizar os SKUs participantes"
                    : "Campanha criada no Mercado Livre — gerenciamento real no painel do ML"
                }
              >
                ML
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
            {periodo}{" "}
            <span className="opacity-70">
              ({campaign.duracao_dias} {campaign.duracao_dias === 1 ? "dia" : "dias"} · {hora})
            </span>
            {campaign.simulacao_id && (
              <>
                {" · "}
                <span className="font-mono text-[10px]">{campaign.simulacao_id}</span>
              </>
            )}
            {isML && campaign.ml_campaign_id && (
              <>
                {" · "}
                <span className="font-mono text-[10px]">{campaign.ml_campaign_id}</span>
              </>
            )}
          </p>
        </div>

        {/* Botão Play — não aparece pra campanhas ML (gerenciadas fora). */}
        {mostrarPlay && (
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-8 w-8 disabled:opacity-30",
              playCheck.podeDisparar
                ? "text-success hover:bg-success/10 hover:text-success"
                : "text-muted-foreground",
            )}
            disabled={!playCheck.podeDisparar}
            onClick={(e) => {
              e.stopPropagation();
              if (playCheck.podeDisparar) onPlay();
            }}
            title={playCheck.motivoNaoPode ?? "Iniciar agora"}
          >
            <Play className="h-4 w-4" />
          </Button>
        )}

        {/* Botão X — desabilitado pra ML com tooltip explicativo */}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground hover:text-destructive disabled:opacity-30"
          disabled={!canDelete}
          onClick={(e) => {
            e.stopPropagation();
            if (canDelete) onDelete();
          }}
          title={motivoNaoDelete ?? "Apagar campanha"}
        >
          <X className="h-4 w-4" />
        </Button>

        {/* Status à direita, destacado */}
        <CampaignStatusBadge status={campaign.status} />
      </CardContent>
    </Card>
  );
}
