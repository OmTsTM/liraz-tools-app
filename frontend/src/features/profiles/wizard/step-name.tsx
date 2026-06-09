import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const schema = z.object({
  name: z.string().min(1, "Nome obrigatório").max(80, "Máximo 80 caracteres").trim(),
});

type FormValues = z.infer<typeof schema>;

interface WizardStepNameProps {
  defaultName?: string;
  onSubmit: (name: string) => void;
}

export function WizardStepName({ defaultName, onSubmit }: WizardStepNameProps) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: defaultName ?? "" },
  });

  return (
    <form onSubmit={handleSubmit((data) => onSubmit(data.name))} className="space-y-6">
      <div className="space-y-2">
        <Label htmlFor="name">Nome de exibição</Label>
        <Input
          id="name"
          placeholder="Ex: Toque Rico"
          autoComplete="off"
          autoFocus
          {...register("name")}
        />
        {errors.name && <p className="text-sm text-destructive">{errors.name.message}</p>}
        <p className="text-xs text-muted-foreground">
          Esse nome aparece só pra você. Pode ser "Loja Principal", "Conta da Maria", "Filial SP" ou
          qualquer coisa.
        </p>
      </div>

      <div className="flex justify-end">
        <Button type="submit" disabled={isSubmitting}>
          Próximo
        </Button>
      </div>
    </form>
  );
}
