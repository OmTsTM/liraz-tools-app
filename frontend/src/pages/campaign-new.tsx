import { ArrowLeft, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { toast } from "@/components/ui/toaster";
import { CampaignForm, type CampaignFormSubmitData } from "@/features/campaigns/campaign-form";
import { useCreateCampaign, useCriarMlCompleto } from "@/features/campaigns/hooks";
import {
  NomeDuplicadoDialog,
  parseNomeDuplicado,
} from "@/features/campaigns/nome-duplicado-dialog";
import { useProfile } from "@/features/profiles/hooks";

export function CampaignNewPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: profile } = useProfile(id);
  const createMutation = useCreateCampaign();
  const criarMlMutation = useCriarMlCompleto();

  // Conflito de nome — guarda payload + qual ação ia ser feita pra retentar.
  const [conflitoNome, setConflitoNome] = useState<{
    nome_tentado: string;
    sugestao: string;
    pendingPayload: CampaignFormSubmitData;
    acao: "rascunho" | "iniciar";
  } | null>(null);

  // ─── Salvar como rascunho (cria Campaign local, sem mexer no ML) ──
  const submitRascunho = async (data: CampaignFormSubmitData, forcarNome = false) => {
    if (!id) return;
    try {
      const created = await createMutation.mutateAsync({
        profileId: id,
        payload: { ...data, forcar_nome: forcarNome },
      });
      toast.success("Rascunho salvo", { description: created.nome });
      setConflitoNome(null);
      navigate(`/profiles/${id}/campaigns/${created.id}`);
    } catch (err) {
      const dup = parseNomeDuplicado(err);
      if (dup) {
        setConflitoNome({ ...dup, pendingPayload: data, acao: "rascunho" });
        return;
      }
      toast.error("Falha ao salvar rascunho", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  // ─── Iniciar agora (cria no ML + adiciona SKUs síncrono) ──────────
  const submitIniciar = async (data: CampaignFormSubmitData) => {
    if (!id) return;
    const skus = data.skus_selecionados ?? [];

    // O backend infla (Fase 1), adiciona com retry de fallback e descarta os
    // negados — reprecificando os negados por ERROR_CREDIBILITY pro preço de
    // 20% de margem (fora da campanha) e revertendo os demais ao original.

    try {
      const result = await criarMlMutation.mutateAsync({
        profileId: id,
        payload: {
          nome: data.nome,
          data_inicio: data.data_inicio,
          data_fim: data.data_fim,
          skus_selecionados: skus,
          deal_prices: data.deal_prices,
          inflar_precos: data.inflar_precos,
          fallback_deal_prices: data.fallback_deal_prices,
        },
      });

      const totalOk = result.skus_adicionados.length + result.skus_ja_estavam.length;
      const totalErros = result.erros.length;
      const reprec = result.reprecificados_20pct?.length ?? 0;
      const extra =
        reprec > 0 ? ` · ${reprec} negado(s) → desinflado(s) pra 20% (fora da campanha)` : "";
      if (totalErros === 0) {
        toast.success("Campanha criada no ML", {
          description:
            skus.length === 0
              ? "Sem SKUs ainda — adicione depois pela tela de detalhes."
              : `${totalOk} SKU${totalOk !== 1 ? "s" : ""} adicionado${totalOk !== 1 ? "s" : ""}.${extra}`,
        });
      } else {
        toast.success("Campanha criada — com avisos", {
          description: `${totalOk} adicionado${totalOk !== 1 ? "s" : ""}, ${totalErros} falhou.${extra}`,
        });
      }
      setConflitoNome(null);
      navigate(`/profiles/${id}/campaigns/${result.id}`);
    } catch (err) {
      const dup = parseNomeDuplicado(err);
      if (dup) {
        setConflitoNome({ ...dup, pendingPayload: data, acao: "iniciar" });
        return;
      }
      toast.error("Falha ao iniciar campanha", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const handleConfirmarSugestao = () => {
    if (!conflitoNome) return;
    const payload = { ...conflitoNome.pendingPayload, nome: conflitoNome.sugestao };
    if (conflitoNome.acao === "iniciar") {
      submitIniciar(payload);
    } else {
      submitRascunho(payload, true);
    }
  };

  const algumPendente = createMutation.isPending || criarMlMutation.isPending;

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${id}/campaigns`)}>
        <ArrowLeft className="h-4 w-4" />
        Voltar pra lista
      </Button>

      <div>
        <h2 className="text-2xl font-semibold tracking-tight">
          Nova campanha
          {profile && (
            <span className="ml-2 text-base font-normal text-muted-foreground">
              · {profile.name}
            </span>
          )}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Crie uma SELLER_CAMPAIGN. Salve como rascunho pra editar com calma, ou{" "}
          <span className="font-medium">inicie agora</span> pra disparar imediatamente no ML.
        </p>
      </div>

      {/* Resumo de erros parciais do último Iniciar agora (não esconde no toast) */}
      {criarMlMutation.data && criarMlMutation.data.erros.length > 0 && (
        <Card className="border-warning/40 bg-warning/5">
          <CardContent className="space-y-2 p-4 text-sm">
            <div className="flex items-center gap-2 font-medium">
              <CheckCircle2 className="h-4 w-4 text-warning" />
              Campanha criada com erros parciais
            </div>
            <p className="text-muted-foreground">
              {criarMlMutation.data.skus_adicionados.length +
                criarMlMutation.data.skus_ja_estavam.length}{" "}
              SKU(s) adicionado(s),{" "}
              <span className="font-medium">{criarMlMutation.data.erros.length} falhou</span>. A
              campanha foi criada — abra a tela de detalhes pra adicionar os faltantes.
            </p>
            <ul className="ml-4 list-disc space-y-0.5 text-xs">
              {criarMlMutation.data.erros.slice(0, 3).map((e) => (
                <li key={e.item_id}>
                  <span className="font-mono">{e.item_id}</span>: {e.erro}
                </li>
              ))}
              {criarMlMutation.data.erros.length > 3 && (
                <li className="text-muted-foreground">
                  …e mais {criarMlMutation.data.erros.length - 3}
                </li>
              )}
            </ul>
          </CardContent>
        </Card>
      )}

      {id && (
        <CampaignForm
          profileId={id}
          mode="create"
          submitLabel={createMutation.isPending ? "Salvando..." : "Salvar como rascunho"}
          isSubmitting={createMutation.isPending}
          isIniciando={criarMlMutation.isPending}
          onSubmit={(data) => submitRascunho(data)}
          onSubmitIniciar={(data) => submitIniciar(data)}
          onCancel={() => navigate(`/profiles/${id}/campaigns`)}
        />
      )}

      {conflitoNome && (
        <NomeDuplicadoDialog
          open={true}
          onOpenChange={(open) => !open && setConflitoNome(null)}
          nomeTentado={conflitoNome.nome_tentado}
          sugestao={conflitoNome.sugestao}
          isSaving={algumPendente}
          onConfirm={handleConfirmarSugestao}
        />
      )}
    </div>
  );
}
