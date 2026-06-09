import { CheckCircle2, FileUp, Loader2 } from "lucide-react";
import { useRef } from "react";

import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toaster";
import { useUploadCustosXLSX } from "@/features/pricing/hooks";

interface CustosXLSXPickerProps {
  profileId: string;
  currentPath: string | null;
  /** Chamado quando upload bem-sucedido — passa o path salvo pelo backend. */
  onUploadComplete: (savedPath: string) => void;
}

/**
 * Picker da planilha de custos: abre o file picker do navegador, faz upload
 * pro backend (salvo em <data>/_shared/) e atualiza config.custos_xlsx_path.
 */
export function CustosXLSXPicker({
  profileId,
  currentPath,
  onUploadComplete,
}: CustosXLSXPickerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadCustosXLSX();

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
      toast.success("Planilha enviada", {
        description: `${result.skus_carregados} SKUs carregados. Arquivo salvo em ${result.saved_path
          .split(/[\\/]/)
          .pop()}`,
      });
      // Sincroniza o form com o path que o backend salvou — sem isso, o
      // submit do form em modo de edição sobrescreveria com valor antigo.
      onUploadComplete(result.saved_path);
      // Reset input pra permitir re-upload do mesmo arquivo
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
      {/* Estado atual */}
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
          <p className="italic text-muted-foreground">Não configurada</p>
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
        Aceita .xlsx com aba <code className="font-mono">Produtos</code> (coluna D = SKU, coluna H =
        SOMA). O arquivo é copiado pra pasta interna do app.
      </p>
    </div>
  );
}
