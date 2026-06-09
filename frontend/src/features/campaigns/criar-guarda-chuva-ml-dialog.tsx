/**
 * Dialog pra criar guarda-chuva no ML manualmente (Leva 5.10 Fase 2).
 *
 * **NÃO ESTÁ PLUGADO NA UI ATUAL** — depois da discussão na sessão,
 * decidimos esconder esse fluxo da lista de campanhas porque a Leva 5.11
 * (cobertura total automatizada) vai cuidar de criar guarda-chuvas
 * automaticamente quando necessário. Este componente fica preservado pra:
 * - Casos especiais de bootstrap manual
 * - Re-plugar no futuro se aparecer necessidade
 * - Servir de referência pra criar variações do fluxo
 *
 * O endpoint backend (`POST /campaigns/criar-ml-direto`) continua ativo
 * e será chamado internamente pelo scheduler da 5.11.
 */
import { useState } from "react";

import { apiRequest } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toaster";
import { Loader2 } from "lucide-react";

interface CriarMLDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profileId: string;
  onCreated?: (campaignId: string) => void;
}

interface CriarMLResponse {
  id: string;
  ml_campaign_id: string;
  ml_status: string;
  nome: string;
  data_inicio: string;
  data_fim: string;
}

export function CriarGuardaChuvaMLDialog({
  open,
  onOpenChange,
  profileId,
  onCreated,
}: CriarMLDialogProps) {
  const hoje = new Date().toISOString().slice(0, 10);
  // Sugere "DD-Mes" como padrão de nome
  const sugestaoNome = (() => {
    const d = new Date();
    const meses = [
      "Janeiro",
      "Fevereiro",
      "Marco",
      "Abril",
      "Maio",
      "Junho",
      "Julho",
      "Agosto",
      "Setembro",
      "Outubro",
      "Novembro",
      "Dezembro",
    ];
    return `${String(d.getDate()).padStart(2, "0")}-${meses[d.getMonth()]}`;
  })();

  const [nome, setNome] = useState(sugestaoNome);
  const [dataInicio, setDataInicio] = useState(hoje);
  // Sugere 30 dias depois
  const [dataFim, setDataFim] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 30);
    return d.toISOString().slice(0, 10);
  });
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!nome.trim()) {
      toast.error("Nome obrigatório");
      return;
    }
    setSubmitting(true);
    try {
      const qs = new URLSearchParams({
        nome: nome.trim(),
        data_inicio: dataInicio,
        data_fim: dataFim,
      }).toString();
      const r = await apiRequest<CriarMLResponse>(
        `/api/profiles/${profileId}/campaigns/criar-ml-direto?${qs}`,
        { method: "POST" },
      );
      toast.success("Guarda-chuva criada no ML", {
        description: `${r.nome} — ${r.ml_campaign_id} (${r.ml_status})`,
      });
      onOpenChange(false);
      onCreated?.(r.id);
    } catch (e) {
      toast.error("Falha ao criar guarda-chuva", {
        description: e instanceof Error ? e.message : "Erro desconhecido",
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Criar guarda-chuva no ML</DialogTitle>
          <DialogDescription>
            Cria uma SELLER_CAMPAIGN (tipo guarda-chuva) diretamente no Mercado Livre e espelha
            aqui. A campanha começa sem SKUs — você adiciona depois pelo painel do ML ou aguarda o
            scheduler de cobertura adicionar automaticamente os órfãos.
            <br />
            <br />
            Limite do ML: data de início no máximo 60 dias no futuro.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div>
            <Label htmlFor="nome">Nome</Label>
            <Input
              id="nome"
              value={nome}
              onChange={(e) => setNome(e.target.value)}
              placeholder="Ex: 24-Maio"
              disabled={submitting}
            />
            <p className="mt-1 text-xs text-muted-foreground">Convenção: DD-Mes (ex: 24-Maio)</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="ini">Data início</Label>
              <Input
                id="ini"
                type="date"
                value={dataInicio}
                onChange={(e) => setDataInicio(e.target.value)}
                min={hoje}
                disabled={submitting}
              />
            </div>
            <div>
              <Label htmlFor="fim">Data fim</Label>
              <Input
                id="fim"
                type="date"
                value={dataFim}
                onChange={(e) => setDataFim(e.target.value)}
                min={dataInicio}
                disabled={submitting}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancelar
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Criar no ML
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
