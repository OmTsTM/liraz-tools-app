import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { Calendar as CalendarIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

interface DatePickerProps {
  /** Data selecionada (string ISO YYYY-MM-DD ou null). */
  value: string | null;
  /** Callback ao selecionar — recebe ISO YYYY-MM-DD ou null. */
  onChange: (value: string | null) => void;
  /** Placeholder quando vazio. */
  placeholder?: string;
  /** Datas anteriores ao `disabledBefore` ficam clicáveis mas riscadas. */
  disabledBefore?: Date;
  /** id do trigger (pra <Label htmlFor>). */
  id?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * Date picker visual — popover com mini-calendário ao clicar no botão.
 * Substitui o `<input type="date">` nativo que tem UX ruim em alguns
 * browsers e ignora estilo de tema.
 *
 * Formato exibido: dd/mm/aaaa (pt-BR).
 * Formato armazenado: ISO YYYY-MM-DD (compatível com API).
 */
export function DatePicker({
  value,
  onChange,
  placeholder = "Selecionar data",
  disabledBefore,
  id,
  disabled,
  className,
}: DatePickerProps) {
  const date = value ? new Date(`${value}T00:00:00`) : undefined;

  const handleSelect = (selected: Date | undefined) => {
    if (!selected) {
      onChange(null);
      return;
    }
    // Formata como YYYY-MM-DD em fuso local pra evitar mismatch UTC vs local
    const yyyy = selected.getFullYear();
    const mm = String(selected.getMonth() + 1).padStart(2, "0");
    const dd = String(selected.getDate()).padStart(2, "0");
    onChange(`${yyyy}-${mm}-${dd}`);
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          disabled={disabled}
          className={cn(
            "w-full justify-start text-left font-normal",
            !date && "text-muted-foreground",
            className,
          )}
        >
          <CalendarIcon className="mr-2 h-4 w-4" />
          {date ? format(date, "dd/MM/yyyy", { locale: ptBR }) : placeholder}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={date}
          onSelect={handleSelect}
          disabled={disabledBefore ? { before: disabledBefore } : undefined}
          initialFocus
        />
      </PopoverContent>
    </Popover>
  );
}
