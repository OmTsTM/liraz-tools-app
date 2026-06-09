/**
 * Mapeia um ID de campanha pra uma cor distinta e estável.
 *
 * Inspirado no esquema do Google Calendar / Calendly: cada evento ganha
 * uma cor consistente, derivada de hash determinístico do ID. Cada vez
 * que renderizamos a mesma campanha, ela tem a mesma cor.
 *
 * A paleta tem 12 cores escolhidas pra:
 * - Funcionar em dark E light mode (usa CSS vars de Tailwind onde possível)
 * - Ter contraste razoável entre vizinhas (evita "todas as agendadas viram azul")
 * - Não conflitar com cores de status (que são reservadas: verde=ativa,
 *   azul=agendada, vermelho=falha, etc). Por isso a paleta privilegia
 *   tons saturados e quentes.
 */

export interface CampaignColor {
  /** Background (claro) — usado em chips do calendário. */
  bg: string;
  /** Borda lateral — usado como faixa colorida em rows da listagem. */
  border: string;
  /** Texto sobre o background claro. */
  text: string;
}

/**
 * Paleta de 12 cores. Usam classes Tailwind com tons que renderizam bem
 * em light e dark mode. A ordem é alternada (warm/cool/warm) pra que IDs
 * sequenciais (ex: criados em ordem) tenham cores bem diferentes.
 */
const PALETTE: CampaignColor[] = [
  { bg: "bg-rose-500/20", border: "bg-rose-500", text: "text-rose-700 dark:text-rose-300" },
  { bg: "bg-amber-500/20", border: "bg-amber-500", text: "text-amber-700 dark:text-amber-300" },
  {
    bg: "bg-emerald-500/20",
    border: "bg-emerald-500",
    text: "text-emerald-700 dark:text-emerald-300",
  },
  { bg: "bg-sky-500/20", border: "bg-sky-500", text: "text-sky-700 dark:text-sky-300" },
  { bg: "bg-violet-500/20", border: "bg-violet-500", text: "text-violet-700 dark:text-violet-300" },
  { bg: "bg-pink-500/20", border: "bg-pink-500", text: "text-pink-700 dark:text-pink-300" },
  { bg: "bg-orange-500/20", border: "bg-orange-500", text: "text-orange-700 dark:text-orange-300" },
  { bg: "bg-lime-500/20", border: "bg-lime-500", text: "text-lime-700 dark:text-lime-300" },
  { bg: "bg-teal-500/20", border: "bg-teal-500", text: "text-teal-700 dark:text-teal-300" },
  { bg: "bg-indigo-500/20", border: "bg-indigo-500", text: "text-indigo-700 dark:text-indigo-300" },
  {
    bg: "bg-fuchsia-500/20",
    border: "bg-fuchsia-500",
    text: "text-fuchsia-700 dark:text-fuchsia-300",
  },
  { bg: "bg-cyan-500/20", border: "bg-cyan-500", text: "text-cyan-700 dark:text-cyan-300" },
];

/**
 * Hash determinístico de string → índice na paleta.
 *
 * Usa djb2 (Dan Bernstein). Não é criptográfico, mas é rápido, sem deps,
 * e tem distribuição uniforme suficiente pra essa aplicação (queremos só
 * que IDs diferentes caiam em buckets diferentes na maior parte do tempo).
 */
function hashStringToIndex(s: string, bucketCount: number): number {
  let hash = 5381;
  for (let i = 0; i < s.length; i++) {
    // hash * 33 + char — clássico do djb2
    hash = (hash * 33) ^ s.charCodeAt(i);
  }
  // >>> 0 converte pra unsigned 32-bit; depois módulo
  return (hash >>> 0) % bucketCount;
}

/**
 * Cor estável da campanha. Mesma `campaignId` sempre retorna a mesma cor.
 */
export function getCampaignColor(campaignId: string): CampaignColor {
  const index = hashStringToIndex(campaignId, PALETTE.length);
  // index sempre está em [0, PALETTE.length) por construção do módulo,
  // mas o TS com noUncheckedIndexedAccess não infere isso. Usamos o
  // primeiro item como fallback defensivo (nunca dispara em prática).
  const color = PALETTE[index];
  if (color) return color;
  // Fallback impossível na prática — PALETTE tem pelo menos 1 item por design
  return { bg: "bg-muted", border: "bg-muted-foreground", text: "text-foreground" };
}
