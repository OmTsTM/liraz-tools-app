/**
 * Tipos da API espelhando os schemas Pydantic do backend.
 *
 * NOTA: estes tipos serão substituídos automaticamente quando você rodar
 * `pnpm generate:api` — que gera `schema.gen.ts` direto do OpenAPI.
 *
 * Mantemos esses tipos como fallback pra desenvolvimento inicial sem
 * dependência circular (frontend não precisa esperar backend rodar pra
 * compilar).
 */

export type ProfileStatus = "draft" | "connected" | "disconnected" | "archived";

export interface ProfileConfig {
  aliquota_imposto: number;
  cep_destino: string;
  custos_xlsx_path: string | null;
  /** Planilha opcional de tarifas reais por anúncio (export da extensão Chrome). */
  tarifas_ml_xlsx_path: string | null;
  /** Q2 — margem do PREÇO FINAL DE VENDA (P). Campanha infla/desconta de volta. */
  margem_alvo_campanha: number;
  /** R2 — margem mínima inviolável (escada Filosofia B). */
  margem_minima: number;
  /** T2 — % de inflação da campanha: publica P*(1+T2), desconta de volta a P. */
  pct_inflacao_campanha: number;
  margem_min_migracao: number;
  // ─── Obsoletos (modelo antigo de reprecificação a 50%) — mantidos só
  // pra compatibilidade de serialização; o cálculo não os usa mais.
  margem_alvo_aumento?: number;
  limite_margem_aumento?: number;
  teto_quebra_frete_gratis?: number;
  margem_max_migracao: number;
  /** Migração automática deste perfil (Leva 5.9.4.C.3). */
  migracao_automatica_ativa: boolean;
  /** Modo simulação — não chama o ML, só registra. */
  migracao_dry_run: boolean;
  /** Intervalo entre execuções do scheduler para este perfil. */
  migracao_intervalo_horas: number;
  /** Relatório diário automático (Fatia 2). */
  relatorio_diario_ativo: boolean;
  /** Horário BRT (HH:MM) pra gerar o relatório do dia anterior. */
  relatorio_diario_hora_brt: string;
}

export interface Profile {
  id: string;
  name: string;
  slug: string;
  status: ProfileStatus;
  config: ProfileConfig;
  ml_user_id: number | null;
  ml_nickname: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface ListProfilesResponse {
  items: Profile[];
  total: number;
}

export interface ActiveProfileResponse {
  profile: Profile | null;
}

export interface CreateProfileRequest {
  name: string;
}

export interface UpdateProfileRequest {
  name?: string;
  config?: ProfileConfig;
}

export interface SaveCredentialsRequest {
  client_id: string;
  client_secret: string;
  redirect_uri?: string;
}

export interface AuthorizationUrlResponse {
  url: string;
}

export interface ImportFromMCPRequest {
  tokens_db_path: string;
  client_id: string;
  client_secret: string;
  redirect_uri: string;
}

export interface MLConnectionTestResponse {
  id: number | null;
  nickname: string | null;
  email: string | null;
  site_id: string | null;
  country_id: string | null;
  user_type: string | null;
  registration_date: string | null;
}

// ─── Pricing / Listings ────────────────────────────────────────────────────

export type MatchType =
  | "manual"
  | "exato"
  | "case_insensitive"
  | "prefixo"
  | "prefixo_divergente"
  | "nao_encontrado";

export interface ListingFees {
  item_id: string;
  sku: string | null;
  title: string | null;
  status: string | null;
  modalidade: string;
  modalidade_id: string | null;
  preco: number;
  comissao_valor: number;
  comissao_percentual: number;
  tarifa_fixa: number;
  tarifa_fixa_fonte: string;
  frete_vendedor: number;
  frete_fonte: string;
  free_shipping: boolean;
  valor_liquido: number;
  custo_produto: number | null;
  custo_fonte: MatchType;
  custo_fonte_detalhe: string | null;
  lucro_bruto: number | null;
  imposto_valor: number | null;
  lucro_liquido: number | null;
  margem_liquida_percentual: number | null;
  erro: string | null;
}

export interface FeeReport {
  profile_id: string;
  profile_slug: string;
  generated_at: string;
  cep_destino: string;
  aliquota_imposto: number;
  custos_xlsx_path: string;

  listings: ListingFees[];
  total_anuncios: number;
  skus_no_custos_xlsx: number;

  matches: Record<string, number>;
  anuncios_sem_custo: Array<{
    item_id: string;
    sku: string | null;
    titulo: string | null;
  }>;
  anuncios_com_fallback: Array<{
    item_id: string;
    sku: string | null;
    fonte: string;
  }>;
}

export interface UploadCustosXLSXResponse {
  saved_path: string;
  size_bytes: number;
  skus_carregados: number;
}

// ─── Simulations / Repricing ───────────────────────────────────────────────

export type SimulationState = "running" | "completed" | "failed" | "interrupted";

export interface TaxasParaPlanilha {
  preco: number;
  tarifa_classico: number;
  tarifa_premium: number;
  percentual_tarifa: number;
  tarifa_fixa: number;
  frete: number;
}

export interface RepricingLineItem {
  item_id: string;
  sku: string | null;
  titulo: string | null;
  modalidade: string | null;
  custo: number;
  fonte_custo: string;
  taxas_atual: TaxasParaPlanilha;
  taxas_novo: TaxasParaPlanilha;
  taxas_deal: TaxasParaPlanilha | null;
  margem_atual_pct: number;
  liq_final_atual: number;
  // "subir"/"baixar"/"mantido" (modelo planilha passo3); os 2 últimos são
  // legados de snapshots do modelo antigo (50%).
  fase1_acao: "mantido" | "subir" | "baixar" | "preco_aumentado" | "preco_travado_abaixo_79";
  fase1_motivo: string;
  preco_novo: number;
  deal_price: number;
  desconto_pct: number;
  liq_final_deal_projetado: number;

  margem_campanha_pct: number;
  /** Margem padrão da campanha (default 20%, da config do perfil). */

  margem_override_pct: number | null;
  /** Se preenchido, é a margem customizada aplicada nesse item. */

  /** Leva 5.8: item já está em alguma promoção/campanha ativa no ML? */
  em_campanha?: boolean;
  /** Nomes das promoções em que está. Vazio quando em_campanha=false. */
  nomes_campanha?: string[];

  deal_price_original: number | null;
  desconto_pct_original: number | null;
  liq_final_deal_projetado_original: number | null;
  taxas_deal_original: TaxasParaPlanilha | null;
}

export interface RepricingException {
  item_id: string;
  sku: string | null;
  titulo: string | null;
  tipo: string;
  motivo?: string | null;
  detalhe?: string | null;
}

export interface SimulationSummary {
  simulation_id: string;
  criado_em: string;
  estado: SimulationState;
  total_ativos_analisados: number;
  total_simulados: number;
  total_excecoes: number;
  total_processados: number;
  retomado_de_checkpoint: boolean;
}

export interface SimulationDetail {
  simulation_id: string;
  criado_em: string;
  atualizado_em: string | null;
  estado: SimulationState;
  aliquota_imposto: number;
  cep_destino: string;
  custos_xlsx_path: string | null;
  concorrencia_usada: number;
  retomado_de_checkpoint: boolean;

  margem_alvo_aumento: number;
  limite_margem_aumento: number;
  margem_alvo_campanha: number;

  total_ativos_analisados: number;
  total_simulados: number;
  total_excecoes: number;
  total_processados: number;
  erro_fatal: string | null;

  simulacoes: RepricingLineItem[];
  excecoes: RepricingException[];
}

// ─── Campaigns ─────────────────────────────────────────────────────────────

export type CampaignStatus =
  | "rascunho"
  | "agendada"
  | "executando"
  | "ativa"
  | "finalizada"
  | "cancelada"
  | "falha";

export interface Campaign {
  id: string;
  profile_id: string;
  nome: string;
  simulacao_id: string | null;
  data_inicio: string; // ISO date (YYYY-MM-DD)
  data_fim: string;
  hora_disparo: string; // "HH:MM:SS"
  hora_fim: string | null; // "HH:MM:SS" ou null
  skus_selecionados: string[] | null;
  status: CampaignStatus;
  /** Null pra campanhas vindas do ML (origem="ml"). */
  created_at: string | null;
  /** Null pra campanhas vindas do ML (origem="ml"). */
  updated_at: string | null;
  ml_campaign_id: string | null;
  aplicacao_id: string | null;
  rollback_id: string | null;
  erro: string | null;
  /** ISO datetime quando foi auto-arquivada (15 dias após data_fim). Null = ativa. */
  archived_at: string | null;
  // Derivados
  duracao_dias: number;
  is_editable: boolean;
  /** Leva 5.9.1 — "local" (criada no app) ou "ml" (importada do ML, read-only). */
  origem?: "local" | "ml";
}

/** Leva 5.9.3 — info enriquecida de um item participante de campanha. */
export interface CampaignItemInfo {
  item_id: string;
  sku: string | null;
  titulo: string | null;
  preco: number | null;
  modalidade: string | null;
  margem_liquida_pct: number | null;
}

/** Leva 5.9.3 — item do catálogo com flag de elegibilidade. */
export interface CampaignItemEligible extends CampaignItemInfo {
  em_outra_campanha: boolean;
  nomes_outras_campanhas: string[];
}

export interface CreateCampaignPayload {
  nome: string;
  data_inicio: string; // YYYY-MM-DD
  data_fim: string;
  hora_disparo?: string; // "HH:MM"
  hora_fim?: string | null; // "HH:MM" ou null
  simulacao_id?: string | null;
  skus_selecionados?: string[] | null;
  forcar_nome?: boolean;
}

export interface UpdateCampaignPayload {
  nome?: string;
  data_inicio?: string;
  data_fim?: string;
  hora_disparo?: string;
  hora_fim?: string | null;
  simulacao_id?: string | null;
  skus_selecionados?: string[] | null;
  forcar_nome?: boolean;
}

/**
 * Payload de erro 409 retornado pelo backend quando há conflito de nome.
 * Frontend usa pra oferecer o nome sugerido no modal de confirmação.
 */
export interface NomeDuplicadoError {
  message: string;
  sugestao: string;
  nome_tentado: string;
}

// ─── Applications (Leva 5.4) ───────────────────────────────────────────────

export type ApplicationStatus = "running" | "completed" | "failed" | "interrupted";

export type ApplicationPhase =
  | "applying_prices"
  | "creating_campaign"
  | "adding_items"
  | "reverting"
  | "done";

export interface ApplicationItem {
  item_id: string;
  sku: string | null;
  title: string | null;
  preco_anterior: number;
  preco_novo: number;
  deal_price: number | null;
  status: "pendente" | "aplicado" | "falha" | "pulado";
  erro: string | null;
  aplicado_em: string | null;
}

export interface Application {
  application_id: string;
  campaign_id: string;
  profile_slug: string;
  simulacao_id: string;
  estado: ApplicationStatus;
  fase: ApplicationPhase;
  criado_em: string;
  finalizado_em: string | null;
  total_itens: number;
  itens_aplicados: number;
  itens_pulados: number;
  itens_falha: number;
  itens: ApplicationItem[];
  rollback_id: string | null;
  ml_campaign_id: string | null;
  erro: string | null;
}

export interface StartCampaignPayload {
  /** Default true. Quando true, ajusta data_inicio+hora_disparo pra agora. */
  imediato?: boolean;
}
