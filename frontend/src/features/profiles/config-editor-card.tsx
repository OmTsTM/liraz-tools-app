import { zodResolver } from "@hookform/resolvers/zod";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toaster";
import { CustosXLSXPicker } from "@/features/pricing/custos-xlsx-picker";
import { TarifasMLXLSXPicker } from "@/features/pricing/tarifas-ml-xlsx-picker";
import { useUpdateProfile } from "@/features/profiles/hooks";
import type { Profile, ProfileConfig } from "@/types/api";

const schema = z
  .object({
    aliquota_imposto_pct: z.coerce.number().min(0, "Mínimo 0%").max(100, "Máximo 100%"),
    cep_destino: z
      .string()
      .min(8, "CEP precisa de 8 dígitos")
      .max(9, "Máximo 9 caracteres")
      .regex(/^\d{5}-?\d{3}$/, "Formato: 00000-000 ou 00000000"),
    margem_alvo_campanha_pct: z.coerce.number().min(0, "Mínimo 0%").max(100, "Máximo 100%"),
    margem_minima_pct: z.coerce.number().min(0, "Mínimo 0%").max(100, "Máximo 100%"),
    pct_inflacao_campanha_pct: z.coerce.number().min(0, "Mínimo 0%").max(200, "Máximo 200%"),
    margem_min_migracao_pct: z.coerce.number().min(0, "Mínimo 0%").max(100, "Máximo 100%"),
    margem_max_migracao_pct: z.coerce.number().min(0, "Mínimo 0%").max(100, "Máximo 100%"),
    custos_xlsx_path: z.string().optional(),
    tarifas_ml_xlsx_path: z.string().optional(),
  })
  .refine((data) => data.margem_max_migracao_pct >= data.margem_min_migracao_pct, {
    message: "Máximo deve ser ≥ mínimo",
    path: ["margem_max_migracao_pct"],
  })
  .refine((data) => data.margem_minima_pct <= data.margem_alvo_campanha_pct, {
    message: "Margem mínima (R2) deve ser ≤ margem alvo (Q2)",
    path: ["margem_minima_pct"],
  });

type FormValues = z.infer<typeof schema>;

interface ConfigEditorCardProps {
  profile: Profile;
}

export function ConfigEditorCard({ profile }: ConfigEditorCardProps) {
  const [editing, setEditing] = useState(false);
  const updateProfile = useUpdateProfile();

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: configToForm(profile.config),
  });

  const handleStartEdit = () => {
    reset(configToForm(profile.config));
    setEditing(true);
  };

  const handleCancel = () => {
    setEditing(false);
  };

  const onSubmit = async (data: FormValues) => {
    const newConfig: ProfileConfig = {
      aliquota_imposto: data.aliquota_imposto_pct / 100,
      cep_destino: data.cep_destino.replace(/\D/g, ""),
      custos_xlsx_path: data.custos_xlsx_path?.trim() || null,
      tarifas_ml_xlsx_path: data.tarifas_ml_xlsx_path?.trim() || null,
      margem_alvo_campanha: data.margem_alvo_campanha_pct / 100,
      margem_minima: data.margem_minima_pct / 100,
      pct_inflacao_campanha: data.pct_inflacao_campanha_pct / 100,
      margem_min_migracao: data.margem_min_migracao_pct / 100,
      margem_max_migracao: data.margem_max_migracao_pct / 100,
      // Preserva os campos do scheduler — editáveis na aba Migração, não aqui
      migracao_automatica_ativa: profile.config.migracao_automatica_ativa ?? false,
      migracao_dry_run: profile.config.migracao_dry_run ?? true,
      migracao_intervalo_horas: profile.config.migracao_intervalo_horas ?? 6,
      adesao_automatica_ativa: profile.config.adesao_automatica_ativa ?? false,
      // Preserva config do relatório diário — editável no card próprio
      relatorio_diario_ativo: profile.config.relatorio_diario_ativo ?? false,
      relatorio_diario_hora_brt: profile.config.relatorio_diario_hora_brt ?? "07:00",
    };

    try {
      await updateProfile.mutateAsync({
        id: profile.id,
        data: { config: newConfig },
      });
      toast.success("Configuração salva");
      setEditing(false);
    } catch (err) {
      toast.error("Falha ao salvar", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  if (!editing) {
    return (
      <Card>
        <CardHeader className="flex flex-row items-start justify-between space-y-0">
          <div className="space-y-1">
            <CardTitle className="text-base">Configuração</CardTitle>
            <CardDescription>Parâmetros desta loja</CardDescription>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={handleStartEdit}
            title="Editar configuração"
          >
            <Pencil className="h-4 w-4" />
          </Button>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <ReadOnlyRow
            label="Alíquota imposto"
            value={`${(profile.config.aliquota_imposto * 100).toFixed(1)}%`}
          />
          <ReadOnlyRow label="CEP padrão" value={profile.config.cep_destino} mono />
          <ReadOnlyRow
            label="Margem alvo (Q2)"
            value={`${(profile.config.margem_alvo_campanha * 100).toFixed(0)}%`}
          />
          <ReadOnlyRow
            label="Margem mínima (R2)"
            value={`${((profile.config.margem_minima ?? 0.15) * 100).toFixed(0)}%`}
          />
          <ReadOnlyRow
            label="Inflação campanha (T2)"
            value={`${((profile.config.pct_inflacao_campanha ?? 0.2) * 100).toFixed(0)}%`}
          />
          <ReadOnlyRow
            label="Faixa migração ML"
            value={`${((profile.config.margem_min_migracao ?? 0.15) * 100).toFixed(0)}% – ${((profile.config.margem_max_migracao ?? 0.2) * 100).toFixed(0)}%`}
          />
          <ReadOnlyRow
            label="Planilha de custos"
            value={
              profile.config.custos_xlsx_path
                ? (profile.config.custos_xlsx_path.split(/[\\/]/).pop() ?? "")
                : "Não configurada"
            }
            mono={Boolean(profile.config.custos_xlsx_path)}
            muted={!profile.config.custos_xlsx_path}
            truncate
          />
          <ReadOnlyRow
            label="Planilha de tarifas reais"
            value={
              profile.config.tarifas_ml_xlsx_path
                ? (profile.config.tarifas_ml_xlsx_path.split(/[\\/]/).pop() ?? "")
                : "Opcional — sem ela, usa teto teórico"
            }
            mono={Boolean(profile.config.tarifas_ml_xlsx_path)}
            muted={!profile.config.tarifas_ml_xlsx_path}
            truncate
          />
        </CardContent>
      </Card>
    );
  }

  const currentCustosPath = watch("custos_xlsx_path");
  const currentTarifasPath = watch("tarifas_ml_xlsx_path");

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0">
        <div className="space-y-1">
          <CardTitle className="text-base">Configuração</CardTitle>
          <CardDescription>Editando parâmetros desta loja</CardDescription>
        </div>
        <div className="flex gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={handleCancel}
            disabled={updateProfile.isPending}
            title="Cancelar"
          >
            <X className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-success hover:text-success"
            onClick={handleSubmit(onSubmit)}
            disabled={updateProfile.isPending}
            title="Salvar"
          >
            {updateProfile.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Check className="h-4 w-4" />
            )}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <EditField
            id="aliquota_imposto_pct"
            label="Alíquota imposto"
            suffix="%"
            register={register}
            error={errors.aliquota_imposto_pct?.message}
          />
          <EditField
            id="cep_destino"
            label="CEP padrão"
            placeholder="01310-100"
            register={register}
            error={errors.cep_destino?.message}
            hint="Usado pra calcular o frete vendedor em cada anúncio"
          />
          <EditField
            id="margem_alvo_campanha_pct"
            label="Margem alvo (Q2)"
            suffix="%"
            register={register}
            error={errors.margem_alvo_campanha_pct?.message}
            hint="Margem do preço final de venda (P). O cliente paga P com ou sem campanha."
          />
          <EditField
            id="margem_minima_pct"
            label="Margem mínima (R2)"
            suffix="%"
            register={register}
            error={errors.margem_minima_pct?.message}
            hint="Piso inviolável. Abaixo de R$79, o preço só fica em R$78,99 se a margem lá ≥ R2."
          />
          <EditField
            id="pct_inflacao_campanha_pct"
            label="Inflação campanha (T2)"
            suffix="%"
            register={register}
            error={errors.pct_inflacao_campanha_pct?.message}
            hint="Publica P×(1+T2) no ML e desconta de volta a P. Padrão 20%."
          />
          <div className="grid grid-cols-2 gap-3">
            <EditField
              id="margem_min_migracao_pct"
              label="Mín. migração ML"
              suffix="%"
              register={register}
              error={errors.margem_min_migracao_pct?.message}
              hint="Piso aceitável pra entrar em campanha ML"
            />
            <EditField
              id="margem_max_migracao_pct"
              label="Máx. migração ML"
              suffix="%"
              register={register}
              error={errors.margem_max_migracao_pct?.message}
              hint="Teto pra preferir preço maior dentro da faixa"
            />
          </div>

          {/* Planilha de custos — file picker + modo manual */}
          <div className="space-y-2">
            <Label>Planilha de custos (.xlsx)</Label>
            <CustosXLSXPicker
              profileId={profile.id}
              currentPath={currentCustosPath || profile.config.custos_xlsx_path}
              onUploadComplete={(savedPath) =>
                setValue("custos_xlsx_path", savedPath, { shouldDirty: true })
              }
            />
            {errors.custos_xlsx_path && (
              <p className="text-xs text-destructive">{errors.custos_xlsx_path.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label>Planilha de tarifas reais (.xlsx) — opcional</Label>
            <TarifasMLXLSXPicker
              profileId={profile.id}
              currentPath={currentTarifasPath || profile.config.tarifas_ml_xlsx_path}
              onUploadComplete={(savedPath) =>
                setValue("tarifas_ml_xlsx_path", savedPath, { shouldDirty: true })
              }
            />
            {errors.tarifas_ml_xlsx_path && (
              <p className="text-xs text-destructive">{errors.tarifas_ml_xlsx_path.message}</p>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function configToForm(config: ProfileConfig): FormValues {
  return {
    aliquota_imposto_pct: config.aliquota_imposto * 100,
    cep_destino: config.cep_destino,
    margem_alvo_campanha_pct: config.margem_alvo_campanha * 100,
    // Fallbacks pros campos novos (modelo planilha passo3) — perfis antigos
    // podem vir sem eles até o usuário salvar a config uma vez.
    margem_minima_pct: (config.margem_minima ?? 0.15) * 100,
    pct_inflacao_campanha_pct: (config.pct_inflacao_campanha ?? 0.2) * 100,
    margem_min_migracao_pct: (config.margem_min_migracao ?? 0.15) * 100,
    margem_max_migracao_pct: (config.margem_max_migracao ?? 0.2) * 100,
    custos_xlsx_path: config.custos_xlsx_path ?? "",
    tarifas_ml_xlsx_path: config.tarifas_ml_xlsx_path ?? "",
  };
}

function ReadOnlyRow({
  label,
  value,
  mono,
  muted,
  truncate,
}: {
  label: string;
  value: string;
  mono?: boolean;
  muted?: boolean;
  truncate?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span
        className={`${mono ? "font-mono text-xs" : "font-medium"} ${
          muted ? "italic text-muted-foreground" : ""
        } ${truncate ? "truncate text-right" : "text-right"}`}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

interface EditFieldProps {
  id: keyof FormValues;
  label: string;
  suffix?: string;
  placeholder?: string;
  hint?: string;
  mono?: boolean;
  // biome-ignore lint/suspicious/noExplicitAny: register é genérico do RHF
  register: any;
  error?: string;
}

function EditField({
  id,
  label,
  suffix,
  placeholder,
  hint,
  mono,
  register,
  error,
}: EditFieldProps) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          placeholder={placeholder}
          className={mono ? "font-mono text-xs" : ""}
          {...register(id)}
        />
        {suffix && <span className="text-sm text-muted-foreground">{suffix}</span>}
      </div>
      {hint && !error && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
