import { CheckCircle2, FileUp, Loader2 } from "lucide-react";
import { useRef } from "react";

import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toaster";
import { useUploadTarifasMLXLSX } from "@/features/pricing/hooks";

interface TarifasMLXLSXPickerProps {
  profileId: string;
  currentPath: string | null;
  /** Chamado quando upload bem-sucedido — passa o path salvo pelo backend. */
  onUploadComplete: (savedPath: string) => void;
}

/**
 * Picker da planilha de TARIFAS REAIS por anúncio. Opcional — quando
 * configurada, o calculator usa os valores do XLSX em vez do teto teórico
 * (6,75 / 8,55) E pula a chamada `/items/{id}/shipping_options` (economia
 * de 1 call por item + foge do 424 flaky do ML).
 *
 * O arquivo é gerado pela extensão Chrome "LiraZ Fees Extractor", que captura
 * os valores reais do painel ML do vendedor.
 */
export function TarifasMLXLSXPicker({
  profileId,
  currentPath,
  onUploadComplete,
}: TarifasMLXLSXPickerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadTarifasMLXLSX();

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      toast.error("Arquivo inválido", {
        description: "Selecione um arquivo .xlsx",
      });
      return;
    }

    try {
      const result = await uploadMutation.mutateAsync({ profileId, file });
      toast.success("Planilha de tarifas enviada", {
        description: `${result.skus_carregados} anúncios cadastrados. Arquivo salvo em ${result.saved_path
          .split(/[\\/]/)
          .pop()}`,
      });
      onUploadComplete(result.saved_path);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      toast.error("Falha no upload", {
        description: err instanceof Error ? err.message : "Erro desconhecido",
      });
    }
  };

  const filename = currentPath?.split(/[\\/]/).pop();
  const isConfigured = Boolean(currentPath);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-sm">
        {isConfigured ? (
          <>
            <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-xs" title={currentPath ?? undefined}>
                {filename}
              </p>
            </div>
          </>
        ) : (
          <p className="italic text-muted-foreground">Opcional — sem ela, usa o teto teórico</p>
        )}
      </div>

      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => fileInputRef.current?.click()}
        disabled={uploadMutation.isPending}
      >
        {uploadMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <FileUp className="h-4 w-4" />
        )}
        {isConfigured ? "Substituir arquivo" : "Selecionar arquivo..."}
      </Button>

      <input
        ref={fileInputRef}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        onChange={handleFileSelect}
      />

      <p className="text-xs text-muted-foreground">
        Aceita o .xlsx exportado pela extensão Chrome{" "}
        <code className="font-mono">LiraZ Fees Extractor</code> (colunas: MLB, Frete, Custo fixo).
        Quando presente, o cálculo de margem usa esses valores reais e economiza chamadas ao ML.
      </p>
    </div>
  );
}
