import { Clock, ListChecks } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DatePicker } from "@/components/ui/date-picker";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SimulationPicker } from "@/features/campaigns/simulation-picker";
import { SkuSelectorDialog } from "@/features/campaigns/sku-selector-dialog";
import { SkuSelectorSemSimulacao } from "@/features/campaigns/sku-selector-sem-simulacao";
import type { Campaign } from "@/types/api";

export type CampaignFormSubmitData = {
  nome: string;
  data_inicio: string;
  data_fim: string;
  hora_disparo: string;
  hora_fim: string | null;
  simulacao_id: string | null;
  skus_selecionados: string[] | null;
  /** Deal_prices calculados pelo modal de seleção. Só usado em
   *  mode='create' + onSubmitIniciar (criação completa no ML). Vazio se
   *  user salvou como rascunho ou o modal não foi aberto. */
  deal_prices: Record<string, number>;
  /** Fase 1: item_id → preço de listagem a inflar ANTES de adicionar à
   *  campanha (pra atingir a margem alvo). Vazio se nenhum item precisa. */
  inflar_precos: Record<string, number>;
  /** item_id → deal conservador (≥R$79 c/ frete) pra retry em ERROR_CREDIBILITY. */
  fallback_deal_prices: Record<string, number>;
};

interface CampaignFormProps {
  profileId: string;
  initial?: Campaign;
  submitLabel: string;
  isSubmitting?: boolean;
  /**
   * 'create' = tela de criar campanha nova (Leva 5.12, sem simulação).
   * 'edit' = editar campanha existente (mantém SimulationPicker pra
   *   campanhas antigas que usam simulação).
   * Default 'edit' por retrocompat.
   */
  mode?: "create" | "edit";
  onSubmit: (data: CampaignFormSubmitData) => void;
  /**
   * Só em `mode='create'`: callback do botão "Iniciar agora", que cria
   * a campanha NO ML imediatamente (em vez de salvar como rascunho local).
   * Quando omitido, só aparece o botão de "Salvar".
   */
  onSubmitIniciar?: (data: CampaignFormSubmitData) => void;
  isIniciando?: boolean;
  onCancel?: () => void;
}

/**
 * Form pra criar ou editar campanha.
 *
 * Validações cliente-side:
 * - Nome obrigatório
 * - Datas obrigatórias
 * - data_fim >= data_inicio
 * - data_inicio >= hoje (a menos que esteja editando uma campanha existente
 *   onde a data não foi alterada)
 * - data_fim >= hoje
 * - Aviso visual quando duração > 30 dias (limite ML BR pra SELLER_CAMPAIGN),
 *   não bloqueia — só sinaliza
 *
 * Componentes usados:
 * - DatePicker (popover + calendário pt-BR) substituindo input date HTML
 * - SimulationPicker (dialog com cards) substituindo o select feio
 * - Input type=time pra hora_disparo (HH:MM)
 */

/** Limite de duração (em dias de diferença) duma SELLER_CAMPAIGN no ML BR.
 *  ML aceita até "1 mês" de vigência — ou seja, até 31 dias entre data_inicio
 *  e data_fim (ex: 21/05 a 21/06 = 31 dias, está OK; 21/05 a 22/06 = 32, acima). */
const DURACAO_MAXIMA_DIAS = 31;
export function CampaignForm({
  profileId,
  initial,
  submitLabel,
  isSubmitting = false,
  mode = "edit",
  onSubmit,
  onSubmitIniciar,
  isIniciando = false,
  onCancel,
}: CampaignFormProps) {
  const [nome, setNome] = useState(initial?.nome ?? "");
  const [dataInicio, setDataInicio] = useState<string | null>(initial?.data_inicio ?? null);
  const [dataFim, setDataFim] = useState<string | null>(initial?.data_fim ?? null);
  const [horaDisparo, setHoraDisparo] = useState(
    initial?.hora_disparo ? initial.hora_disparo.slice(0, 5) : "09:00",
  );
  // hora_fim é opcional: ativada via checkbox. Se initial tem, começa ativada
  const [horaFimAtiva, setHoraFimAtiva] = useState<boolean>(Boolean(initial?.hora_fim));
  const [horaFim, setHoraFim] = useState(
    initial?.hora_fim ? initial.hora_fim.slice(0, 5) : "18:00",
  );
  const [simulacaoId, setSimulacaoId] = useState<string | null>(initial?.simulacao_id ?? null);
  // null = todos os items aplicáveis (default — sem filtro de seleção)
  const [skusSelecionados, setSkusSelecionados] = useState<string[] | null>(
    initial?.skus_selecionados ?? null,
  );
  // Margem líquida alvo inicial do modal de seleção (mode='create'). Default 20%.
  const margemAlvoInicialCampanha = 20;
  // Deal_prices calculados pelo modal (mode='create' usa fallback simples;
  // mode='edit' não usa esse caminho pra criar — só pra editar SKUs).
  const [dealPricesCreate, setDealPricesCreate] = useState<Record<string, number>>({});
  const [inflarPrecosCreate, setInflarPrecosCreate] = useState<Record<string, number>>({});
  const [fallbackDealPricesCreate, setFallbackDealPricesCreate] = useState<Record<string, number>>(
    {},
  );
  const [showSkuSelector, setShowSkuSelector] = useState(false);

  useEffect(() => {
    if (initial) {
      setNome(initial.nome);
      setDataInicio(initial.data_inicio);
      setDataFim(initial.data_fim);
      setHoraDisparo(initial.hora_disparo.slice(0, 5));
      setHoraFimAtiva(Boolean(initial.hora_fim));
      setHoraFim(initial.hora_fim ? initial.hora_fim.slice(0, 5) : "18:00");
      setSimulacaoId(initial.simulacao_id);
      setSkusSelecionados(initial.skus_selecionados);
    }
  }, [initial]);

  // Se a simulação muda, descarta a seleção anterior (não faz mais sentido).
  // Detecta mudança usando ref do simulacao_id "estável" (initial vs atual).
  const simulacaoIdAnterior = initial?.simulacao_id ?? null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: simulacaoIdAnterior é estável durante a vida do componente (deriva de initial); incluir nas deps causaria reset incorreto da seleção quando o user salva e o "initial" muda
  useEffect(() => {
    if (simulacaoId !== simulacaoIdAnterior) {
      // Voltou pro default (todos) — usuário pode redefinir via modal
      setSkusSelecionados(null);
    }
  }, [simulacaoId]);

  const hoje = useMemo(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  }, []);

  const erros = useMemo(() => {
    const errs: string[] = [];
    if (dataInicio && dataFim) {
      const inicio = new Date(`${dataInicio}T00:00:00`);
      const fim = new Date(`${dataFim}T00:00:00`);
      if (fim < inicio) {
        errs.push("Data de fim não pode ser anterior à data de início.");
      }
      if (fim < hoje) {
        errs.push("Data de fim já passou — escolha uma data futura.");
      }
      const inicioNaoMudou = initial?.data_inicio === dataInicio;
      if (inicio < hoje && !inicioNaoMudou) {
        errs.push("Data de início já passou — escolha uma data de hoje em diante.");
      }
    }
    return errs;
  }, [dataInicio, dataFim, hoje, initial]);

  const duracaoDias = useMemo(() => {
    if (!dataInicio || !dataFim) return null;
    const inicio = new Date(`${dataInicio}T00:00:00`);
    const fim = new Date(`${dataFim}T00:00:00`);
    if (fim < inicio) return null;
    // Diferença em dias, sem +1. Bate com o cálculo do ML (1 mês = 30 dias
    // entre data_inicio e data_fim) e com a noção natural de "X dias até Y".
    return Math.round((fim.getTime() - inicio.getTime()) / (1000 * 60 * 60 * 24));
  }, [dataInicio, dataFim]);

  const acimaDoLimite = duracaoDias !== null && duracaoDias > DURACAO_MAXIMA_DIAS;

  // Se hora_fim está ativa, valida formato HH:MM (input type=time já garante,
  // mas defensivo). Se desativada, o valor é ignorado.
  const horaFimValida = !horaFimAtiva || /^\d{2}:\d{2}$/.test(horaFim);

  const podeSubmeter =
    nome.trim().length > 0 &&
    dataInicio !== null &&
    dataFim !== null &&
    erros.length === 0 &&
    /^\d{2}:\d{2}$/.test(horaDisparo) &&
    horaFimValida &&
    !isSubmitting;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!podeSubmeter || !dataInicio || !dataFim) return;
    onSubmit({
      nome: nome.trim(),
      data_inicio: dataInicio,
      data_fim: dataFim,
      hora_disparo: horaDisparo,
      hora_fim: horaFimAtiva ? horaFim : null,
      simulacao_id: simulacaoId,
      skus_selecionados: skusSelecionados,
      deal_prices: dealPricesCreate,
      inflar_precos: inflarPrecosCreate,
      fallback_deal_prices: fallbackDealPricesCreate,
    });
  };

  // disabledBefore na data_inicio: hoje (ou nenhum, se for a mesma data
  // já salva no initial — porque queremos permitir manter sem mudar).
  const inicioDisabledBefore =
    initial?.data_inicio === dataInicio && dataInicio !== null ? undefined : hoje;
  // disabledBefore na data_fim: data_inicio se selecionada, senão hoje
  const fimDisabledBefore = dataInicio ? new Date(`${dataInicio}T00:00:00`) : hoje;

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <Card>
        <CardContent className="space-y-5 p-6">
          <div className="space-y-1.5">
            <Label htmlFor="nome">Nome da campanha</Label>
            <Input
              id="nome"
              value={nome}
              onChange={(e) => setNome(e.target.value)}
              placeholder="Ex: Black Friday Toque Rico"
              maxLength={200}
              disabled={isSubmitting}
              required
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="data_inicio">Data de início</Label>
              <DatePicker
                id="data_inicio"
                value={dataInicio}
                onChange={setDataInicio}
                disabled={isSubmitting}
                disabledBefore={inicioDisabledBefore}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="data_fim">Data de fim</Label>
              <DatePicker
                id="data_fim"
                value={dataFim}
                onChange={setDataFim}
                disabled={isSubmitting}
                disabledBefore={fimDisabledBefore}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="hora_disparo" className="flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5" />
              Hora de disparo
            </Label>
            <Input
              id="hora_disparo"
              type="time"
              value={horaDisparo}
              onChange={(e) => setHoraDisparo(e.target.value)}
              disabled={isSubmitting}
              className="w-32"
              required
            />
            <p className="text-xs text-muted-foreground">
              Quando o scheduler vai disparar no dia de início. Se o app estiver offline na hora
              marcada, dispara assim que voltar a rodar (recuperação automática).
            </p>
          </div>

          {/* Hora de término — opcional, ativada via checkbox */}
          <div className="space-y-1.5">
            <label
              htmlFor="hora_fim_ativa"
              className="flex cursor-pointer items-center gap-2 text-sm font-medium"
            >
              <input
                id="hora_fim_ativa"
                type="checkbox"
                checked={horaFimAtiva}
                onChange={(e) => setHoraFimAtiva(e.target.checked)}
                disabled={isSubmitting}
                className="h-4 w-4 rounded border-input accent-primary"
              />
              <Clock className="h-3.5 w-3.5" />
              Definir hora de término (opcional)
            </label>
            {horaFimAtiva && (
              <div className="pl-6">
                <Input
                  id="hora_fim"
                  type="time"
                  value={horaFim}
                  onChange={(e) => setHoraFim(e.target.value)}
                  disabled={isSubmitting}
                  className="w-32"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  No <span className="font-medium">último dia da campanha</span>, ela será
                  finalizada nesse horário. Útil pra campanhas com janela de horário restrita (ex:
                  das 9h às 18h).
                </p>
              </div>
            )}
            {!horaFimAtiva && (
              <p className="pl-6 text-xs text-muted-foreground">
                Sem hora de término definida, a campanha vai até{" "}
                <span className="font-medium">23:59</span> do último dia.
              </p>
            )}
          </div>

          {erros.length > 0 && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
              <ul className="ml-4 list-disc space-y-1 text-destructive">
                {erros.map((erro) => (
                  <li key={erro}>{erro}</li>
                ))}
              </ul>
            </div>
          )}

          {duracaoDias !== null && erros.length === 0 && (
            <div
              className={
                acimaDoLimite
                  ? "rounded-md border border-warning/40 bg-warning/5 p-3 text-sm"
                  : "text-sm text-muted-foreground"
              }
            >
              Duração: <span className="font-medium">{duracaoDias} dias</span>
              {acimaDoLimite && (
                <p className="mt-1 text-xs text-warning">
                  ⚠ Acima do limite de 1 mês (até {DURACAO_MAXIMA_DIAS} dias entre as datas) do ML
                  BR pra SELLER_CAMPAIGN. Ao disparar, o ML pode rejeitar. Considere quebrar em
                  campanhas seguidas.
                </p>
              )}
            </div>
          )}

          {/* Modo EDIT: mantém SimulationPicker pra retrocompat com campanhas
              antigas. Modo CREATE (Leva 5.12): pula simulação direto pro
              seletor de SKUs. */}
          {mode === "edit" && (
            <div className="space-y-1.5">
              <Label>Simulação de reprecificação</Label>
              <SimulationPicker
                profileId={profileId}
                value={simulacaoId}
                onChange={setSimulacaoId}
                disabled={isSubmitting}
              />
              <p className="text-xs text-muted-foreground">
                Vincule uma simulação se quiser que o disparo da campanha aplique os preços novos
                calculados. Opcional — campanhas sem simulação rodam a regra de desconto da
                SELLER_CAMPAIGN diretamente.
              </p>
            </div>
          )}

          {/* Seleção de SKUs — em modo create sempre aparece; em modo edit
              só com simulação (lógica antiga). */}
          {mode === "create" ? (
            <div className="space-y-1.5">
              <Label>Anúncios incluídos</Label>
              <div className="flex flex-wrap items-center gap-3 rounded-md border p-3">
                <div className="flex-1 text-sm">
                  {!skusSelecionados || skusSelecionados.length === 0 ? (
                    <span className="text-muted-foreground">Nenhum anúncio selecionado ainda</span>
                  ) : (
                    <>
                      <span className="font-medium tabular-nums">{skusSelecionados.length}</span>{" "}
                      <span className="text-muted-foreground">
                        anúncio{skusSelecionados.length !== 1 && "s"} selecionado
                        {skusSelecionados.length !== 1 && "s"}
                      </span>
                    </>
                  )}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowSkuSelector(true)}
                  disabled={isSubmitting || !dataInicio || !dataFim}
                  title={
                    !dataInicio || !dataFim ? "Defina as datas da campanha primeiro" : undefined
                  }
                >
                  <ListChecks className="h-4 w-4" />
                  {!skusSelecionados || skusSelecionados.length === 0
                    ? "Selecionar anúncios..."
                    : "Editar seleção..."}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Você pode iniciar a campanha sem anúncios e adicionar depois pela tela de detalhes —
                mas o desconto só vai valer quando algum SKU estiver dentro.
              </p>
            </div>
          ) : (
            simulacaoId && (
              <div className="space-y-1.5">
                <Label>Anúncios incluídos</Label>
                <div className="flex flex-wrap items-center gap-3 rounded-md border p-3">
                  <div className="flex-1 text-sm">
                    {skusSelecionados === null ? (
                      <span className="font-medium">Todos os anúncios da simulação</span>
                    ) : skusSelecionados.length === 0 ? (
                      <span className="text-destructive">
                        ⚠ Nenhum anúncio selecionado — disparo será bloqueado
                      </span>
                    ) : (
                      <>
                        <span className="font-medium tabular-nums">{skusSelecionados.length}</span>{" "}
                        <span className="text-muted-foreground">
                          anúncio{skusSelecionados.length !== 1 && "s"} selecionado
                          {skusSelecionados.length !== 1 && "s"} manualmente
                        </span>
                      </>
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setShowSkuSelector(true)}
                    disabled={isSubmitting}
                  >
                    <ListChecks className="h-4 w-4" />
                    {skusSelecionados === null ? "Selecionar anúncios..." : "Editar seleção..."}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Por padrão, todos os anúncios elegíveis da simulação entram (exceto os marcados
                  como "mantido"). Use a seleção manual pra incluir só um subconjunto.
                </p>
              </div>
            )
          )}
        </CardContent>
      </Card>

      {/* Modais de seleção — montados fora do form pra evitar bubbling */}
      {mode === "create" && dataInicio && dataFim && (
        <SkuSelectorSemSimulacao
          open={showSkuSelector}
          onOpenChange={setShowSkuSelector}
          profileId={profileId}
          /* Sem campaignId: modal opera em modo fallback (cliente calcula
             deal_price simples). Pra ter sugestão por margem-alvo no fluxo
             "Iniciar agora", crie como rascunho primeiro e adicione SKUs
             depois pela tela de detalhes. */
          periodo={{ data_inicio: dataInicio, data_fim: dataFim }}
          selecionadosAtuais={skusSelecionados ?? []}
          margemAlvoInicialPct={margemAlvoInicialCampanha}
          /* Fluxo create: a campanha ainda não existe, então não dá pra
             chamar o endpoint `/skus/inflar-precos`. A inflação real só
             acontece no submit do form ("Iniciar agora"), via backend.
             Aqui `onInflar` apenas captura o map calculado pelo modal e
             devolve "tudo OK" — libera o botão Confirmar imediatamente.
             O `inflar_precos` vai pro form state e é repassado pro
             backend no submit (que infla, adiciona, faz fallback e
             descarta). */
          onInflar={async (inflarPrecos) => {
            setInflarPrecosCreate(inflarPrecos);
            return {
              inflados: Object.keys(inflarPrecos),
              ja_no_preco: [],
              erros: [],
            };
          }}
          onConfirmar={async (items, dealPrices, fallbackDealPrices) => {
            setSkusSelecionados(items);
            setDealPricesCreate(dealPrices);
            setFallbackDealPricesCreate(fallbackDealPrices);
          }}
        />
      )}
      {mode === "edit" && simulacaoId && (
        <SkuSelectorDialog
          open={showSkuSelector}
          onOpenChange={setShowSkuSelector}
          profileId={profileId}
          simulacaoId={simulacaoId}
          selecionadosAtuais={skusSelecionados}
          onSave={setSkusSelecionados}
        />
      )}

      <div className="flex justify-end gap-2">
        {onCancel && (
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={isSubmitting || isIniciando}
          >
            Cancelar
          </Button>
        )}
        <Button
          type="submit"
          variant={mode === "create" && onSubmitIniciar ? "outline" : "default"}
          disabled={!podeSubmeter || isIniciando}
        >
          {submitLabel}
        </Button>
        {mode === "create" && onSubmitIniciar && (
          <Button
            type="button"
            disabled={!podeSubmeter || isSubmitting || isIniciando}
            onClick={() => {
              if (!dataInicio || !dataFim) return;
              onSubmitIniciar({
                nome: nome.trim(),
                data_inicio: dataInicio,
                data_fim: dataFim,
                hora_disparo: horaDisparo,
                hora_fim: horaFimAtiva ? horaFim : null,
                simulacao_id: null,
                skus_selecionados: skusSelecionados,
                deal_prices: dealPricesCreate,
                inflar_precos: inflarPrecosCreate,
                fallback_deal_prices: fallbackDealPricesCreate,
              });
            }}
          >
            {isIniciando ? "Criando no ML..." : "Iniciar agora"}
          </Button>
        )}
      </div>
    </form>
  );
}
