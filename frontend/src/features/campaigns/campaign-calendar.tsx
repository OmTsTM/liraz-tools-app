import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { getCampaignColor } from "@/features/campaigns/campaign-colors";
import { CampaignStatusDot } from "@/features/campaigns/campaign-status-badge";
import { cn } from "@/lib/utils";
import type { Campaign } from "@/types/api";

interface CampaignCalendarProps {
  campaigns: Campaign[];
  /** Callback ao clicar numa campanha. */
  onCampaignClick?: (campaign: Campaign) => void;
}

const NOMES_MESES = [
  "janeiro",
  "fevereiro",
  "março",
  "abril",
  "maio",
  "junho",
  "julho",
  "agosto",
  "setembro",
  "outubro",
  "novembro",
  "dezembro",
];

const NOMES_DIAS_SEMANA = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

/**
 * Calendário mensal compacto com campanhas como chips dentro de cada dia.
 *
 * Não tenta posicionar campanhas como barras horizontais atravessando dias
 * (Google Calendar style) porque isso requer lógica complexa de overlap/
 * stacking. Em vez disso, cada dia que está dentro do intervalo de uma
 * campanha mostra um chip compacto com o nome dela.
 *
 * Clicar num chip dispara `onCampaignClick`.
 *
 * Navegação: setas pra prev/próximo mês + botão "Hoje" pra voltar ao mês
 * atual.
 */
export function CampaignCalendar({ campaigns, onCampaignClick }: CampaignCalendarProps) {
  const hoje = useMemo(() => new Date(), []);
  // Mês visível: armazenado como { ano, mes } onde mes é 0-indexed (0=jan)
  const [visivel, setVisivel] = useState({
    ano: hoje.getFullYear(),
    mes: hoje.getMonth(),
  });

  const irPara = (deltaMeses: number) => {
    const novaData = new Date(visivel.ano, visivel.mes + deltaMeses, 1);
    setVisivel({ ano: novaData.getFullYear(), mes: novaData.getMonth() });
  };

  const irParaHoje = () => {
    setVisivel({ ano: hoje.getFullYear(), mes: hoje.getMonth() });
  };

  // Grid de dias do mês: usa um array com 42 slots (6 semanas × 7 dias)
  const grid = useMemo(() => {
    return montarGridMes(visivel.ano, visivel.mes, campaigns);
  }, [visivel.ano, visivel.mes, campaigns]);

  const ehMesAtual = visivel.ano === hoje.getFullYear() && visivel.mes === hoje.getMonth();

  return (
    <Card className="overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b bg-muted/30 px-4 py-3">
        <h3 className="text-sm font-semibold capitalize">
          {NOMES_MESES[visivel.mes]} {visivel.ano}
        </h3>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={irParaHoje}
            disabled={ehMesAtual}
            className="h-7 px-2 text-xs"
          >
            Hoje
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-7 w-7"
            onClick={() => irPara(-1)}
            title="Mês anterior"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-7 w-7"
            onClick={() => irPara(1)}
            title="Próximo mês"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Cabeçalho dos dias da semana */}
      <div className="grid grid-cols-7 border-b bg-muted/20 text-center text-xs font-medium text-muted-foreground">
        {NOMES_DIAS_SEMANA.map((nome) => (
          <div key={nome} className="py-1.5 capitalize">
            {nome}
          </div>
        ))}
      </div>

      {/* Grid de dias */}
      <div className="grid grid-cols-7">
        {grid.map((celula, idx) => (
          <div
            key={`${idx}-${celula.data?.toISOString() ?? "vazio"}`}
            className={cn(
              "min-h-[56px] border-b border-r p-1 last-of-type:border-r-0",
              !celula.noMes && "bg-muted/10",
              celula.ehHoje && "bg-primary/5",
            )}
          >
            {celula.data && (
              <>
                <div
                  className={cn(
                    "mb-1 text-right text-xs tabular-nums",
                    !celula.noMes && "text-muted-foreground/50",
                    celula.ehHoje && "font-semibold text-primary",
                  )}
                >
                  {celula.data.getDate()}
                </div>
                <div className="flex flex-col gap-0.5">
                  {celula.campanhas.slice(0, 2).map((c) => {
                    const color = getCampaignColor(c.id);
                    return (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => onCampaignClick?.(c)}
                        className={cn(
                          "flex items-center gap-1 truncate rounded px-1 py-0.5 text-left text-[10px] shadow-sm transition-opacity hover:opacity-80",
                          color.bg,
                          color.text,
                        )}
                        title={`${c.nome} · ${c.status}`}
                      >
                        {/* Dot continua mostrando o STATUS, complementar à
                            cor de fundo que mostra a IDENTIDADE da campanha */}
                        <CampaignStatusDot status={c.status} />
                        <span className="truncate font-medium">{c.nome}</span>
                      </button>
                    );
                  })}
                  {celula.campanhas.length > 2 && (
                    <span className="px-1 text-[10px] text-muted-foreground">
                      +{celula.campanhas.length - 2} mais
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

interface CelulaCalendario {
  data: Date | null;
  noMes: boolean;
  ehHoje: boolean;
  campanhas: Campaign[];
}

/**
 * Monta um array com 42 células (6 semanas × 7 dias) representando o mês
 * visível. Os primeiros dias do array podem ser do mês anterior, os
 * últimos do mês seguinte — pra preencher o grid retangular.
 */
function montarGridMes(ano: number, mes: number, campaigns: Campaign[]): CelulaCalendario[] {
  const primeiroDoMes = new Date(ano, mes, 1);
  const diaSemanaInicial = primeiroDoMes.getDay(); // 0=dom

  // Volta atrás pra começar no domingo da primeira semana
  const dataInicial = new Date(ano, mes, 1 - diaSemanaInicial);

  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);

  // Pré-processa: converte data_inicio/data_fim das campanhas em Date pra
  // não fazer parsing toda iteração
  const campaignsComDatas = campaigns.map((c) => ({
    campanha: c,
    inicio: new Date(`${c.data_inicio}T00:00:00`),
    fim: new Date(`${c.data_fim}T00:00:00`),
  }));

  const celulas: CelulaCalendario[] = [];
  for (let i = 0; i < 42; i++) {
    const dataIter = new Date(dataInicial);
    dataIter.setDate(dataInicial.getDate() + i);
    dataIter.setHours(0, 0, 0, 0);

    const campanhasDoDia = campaignsComDatas
      .filter(({ inicio, fim }) => dataIter >= inicio && dataIter <= fim)
      .map((c) => c.campanha);

    celulas.push({
      data: dataIter,
      noMes: dataIter.getMonth() === mes,
      ehHoje: dataIter.getTime() === hoje.getTime(),
      campanhas: campanhasDoDia,
    });
  }
  return celulas;
}
