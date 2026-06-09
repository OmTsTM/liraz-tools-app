# LiraZ Tools

App desktop (web local) para gerenciar lojas no Mercado Livre. Monorepo:
- `backend/` — FastAPI + Python 3.12 (gerenciado com `uv`), arquitetura Clean/Hexagonal
- `frontend/` — Vite + React 18 + TS (gerenciado com `pnpm`)

## Como rodar

Backend (porta 8000) — subir **antes** do frontend:

```powershell
$env:ENABLE_MIGRATION_SCHEDULER='true'; $env:ENABLE_RELATORIO_SCHEDULER='true'; uv run --project backend uvicorn liraz_tools.main:app --host 127.0.0.1 --port 8000
```

Duas env vars opcionais — cada uma liga um scheduler de background:

- **`ENABLE_MIGRATION_SCHEDULER=true`** → loop de migrações automáticas a cada
  6h por perfil com `migracao_automatica_ativa=true`. Confira via
  `GET /api/profiles/{id}/migracao/scheduler-state` (`scheduler_globally_enabled`
  deve ser `true`).
- **`ENABLE_RELATORIO_SCHEDULER=true`** → loop que gera 1 PDF/dia do dia
  anterior pra cada perfil com `relatorio_diario_ativo=true` no horário
  `relatorio_diario_hora_brt`. PDFs vão pra `%LOCALAPPDATA%/LirazTools/relatorios/{slug}/`.

**Esquecer qualquer uma = scheduler some silenciosamente** — sem erro, sem
warning na UI. As env vars não persistem entre sessões do PowerShell; se vai
fechar e abrir terminal de novo, precisa setar a cada vez. Pra fixar de vez,
adicione no `$PROFILE` (uma vez só):
`[Environment]::SetEnvironmentVariable('ENABLE_MIGRATION_SCHEDULER','true','User')`
e a equivalente pro `ENABLE_RELATORIO_SCHEDULER` — aí qualquer terminal novo
já abre com elas.

Frontend (porta 5173):

```powershell
pnpm --dir frontend dev
```

## IMPORTANTE: backend NÃO usa --reload

O `uvicorn --reload` é **não confiável neste setup (Windows + uv)**: o reloader
serve código ANTIGO mesmo após editar o fonte. Já testado com
`WATCHFILES_FORCE_POLLING=true` — não resolve. **Não use `--reload`.**

Após editar qualquer arquivo em `backend/src/liraz_tools/**`, **reinicie o backend
manualmente** antes de testar comportamento via API/UI:

```powershell
# 1) Mata processos do backend (inclui worker multiprocessing.spawn órfão)
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'liraz_tools.main|multiprocessing.spawn' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2) Confirma porta 8000 livre (se presa, há worker órfão — repita o passo 1)
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue

# 3) Sobe de novo COM as DUAS env vars (comando da seção acima)
# Esquecer qualquer uma derruba o scheduler correspondente silenciosamente.
```

Notas:
- Verifique comportamento pelo **servidor ao vivo** (ex.: endpoint
  `GET /api/profiles/{id}/debug-tarifa-fixa/{item_id}`), não só por scripts
  `uv run python ...` — scripts leem o fonte atualizado e mascaram um servidor
  rodando código velho.
- O cache do relatório de margens (`FeeReportCache`) é em memória, TTL 15min, e
  some no restart — reiniciar já o limpa.

## Comandos úteis

```powershell
# Backend (ferramentas dev ficam no extra "dev")
uv run --extra dev --project backend ruff check backend/src
# IMPORTANTE: --config-file é obrigatório — rodando da raiz, o mypy não acha o
# backend/pyproject.toml e roda SEM strict (silenciando ~75 erros). Com ele, strict aplica.
uv run --extra dev --project backend mypy backend/src --config-file backend/pyproject.toml
uv run --extra dev --project backend pytest

# Frontend
pnpm --dir frontend typecheck
pnpm --dir frontend lint
```

## Histórico de decisões (precificação & campanhas)

Regras de negócio acumuladas — **leia antes de mexer em cálculo de preço/margem**.
São lógica de dinheiro real (definem preço de anúncio no ML).

### ⚠️ MODELO ATUAL DE PREÇO — planilha "passo3" (mai/2026, SUBSTITUI o anterior)
Migração do cálculo pro modelo da planilha `change.xlsx`/`change.md`. **Validado:
0 divergência em 430 linhas reais + smoke ao vivo (MLB2137639951 → P=41,47).**
- **Comissão = G% que ESCALA com o preço** (entra no denominador), NÃO mais R$
  fixo. `preco_recomendado` em `repricing_calculator.py`.
- **Preço final de venda P** (escada Filosofia B, prioriza ficar < R$ 79):
  `(custo+H)/(1−G−K2−Q2)` se <79 → senão **78,99** se margem lá ≥ R2 → senão
  `(custo+I)/(1−G−K2−Q2)` se ≥79 → senão `MAX(79,(custo+I)/(1−G−K2−R2))`.
- **Q2 = margem do PREÇO FINAL DE VENDA** (não confundir com campanha). Cliente
  paga P com ou sem campanha.
- **Campanha = vitrine**: publica `U = P×(1+T2)`, desconta de volta a P
  (`inflar_para_campanha`). Não come margem real.
- **Parâmetros (config do perfil)**: `aliquota_imposto` (K2), `margem_alvo_campanha`
  (Q2=20%), **`margem_minima`** (R2=15%, novo, piso inviolável), **`pct_inflacao_campanha`**
  (T2=20%, novo). `margem_alvo_aumento`/`limite_margem_aumento`/`teto_quebra_frete_gratis`
  ficaram **OBSOLETOS** (mantidos só por compat de serialização; o cálculo ignora).
- **Custo logístico dos 2 regimes via `list_cost` cru** (fix mai/2026): a escada
  precisa de H (custo fixo <79) E I (frete ≥79) pra QUALQUER item, mas a API só dá
  o do regime do preço ATUAL (item hoje ≥79 → tarifa_fixa=0; item <79 → frete=0).
  Bug: itens hoje ≥79 calculavam o ramo "<79" sem tarifa fixa → recomendavam preço
  inviável abaixo de 79 (ex.: MLB5138296360 dava R$74,59 em vez de R$108,56).
  **Fix**: `calcular_taxas_anuncio` expõe `list_cost` (frete de tabela, INDEPENDENTE
  do preço); `preco_recomendado`/`margem_liquida_pct` derivam H = `min(list_cost,
  8,55)` (custo fixo <79) e I = `list_cost` (frete ≥79). Resolve os DOIS lados:
  - item hoje ≥79 (tarifa_fixa=0): o ramo <79 passa a usar `min(list_cost,8,55)` →
    não recomenda mais preço inviável abaixo de 79 (MLB5138296360: 74,59→108,56).
  - item hoje <79 (frete=0): o ramo ≥79 passa a usar `list_cost` → frete entra
    (MLB5278805726: 90,54→104,50).
  - **I NÃO é condicionado a `free_shipping`**: acima de R$79 o frete grátis é
    OBRIGATÓRIO no ML, então o vendedor sempre paga o frete nesse regime, mesmo que
    o item hoje (< 79) não seja frete grátis.
  (`fixed_fee` do `/listing_prices` é sempre 0 — não serve; a tarifa fixa abaixo
  de 79 vem mesmo do frete de tabela limitado ao teto.)
- **Fluxos migrados**: seletor/deal_price (`sugestao_use_case`), simulação de
  reprecificação (`processar_um_item` + renderer XLSX), config. A reprecificação
  **deixou de mirar 50%** — preço recomendado agora é P@Q2. Fallback ERROR_CREDIBILITY
  preservado (retry com desconto menor).
- **As subseções abaixo descrevem o modelo ANTIGO** (comissão R$ fixa, custo fixo
  min(list_cost,8.55), quebra de frete teto R$87). Mantidas como histórico —
  **superadas pelo modelo passo3 acima** no que toca P/deal/inflação.

### Custo fixo, frete e o degrau de R$ 79 (`infrastructure/pricing/calculator.py`)
- A coluna **Frete** e o cálculo usam o `list_cost` da opção **`recommended`** de
  `GET /items/{id}/shipping_options` — NÃO o campo `cost` (pode ser 0), nem "a
  primeira opção com list_cost > 0".
- Regime do custo logístico do vendedor:
  - **≥ R$ 79 com frete grátis efetivo** (`free_shipping` E preço ≥ 79): vendedor
    paga o **frete** (= `list_cost`); custo fixo = 0.
  - **< R$ 79**: comprador paga o frete; vendedor paga **custo fixo** =
    `min(list_cost, TETO_CUSTO_FIXO)`; abaixo de R$ 12,50 = 50% do preço.
  - Ao cruzar o degrau de R$ 79, o regime é **recalculado** (não fixa o que valia
    no preço atual). `TETO_CUSTO_FIXO = 8,55` (validado no painel ML, mai/2026).
- **Custo 0/negativo** no `custos.xlsx` é tratado como "sem custo" (sinaliza +
  auto-desmarca) — evita deal absurdo (ex.: R$ 0,51 num item com custo 0).

### Preço ideal de campanha (deal_price) — calibrado pela planilha de referência
- Fórmula **FECHADA** (não busca binária), espelha a coluna V de
  `liraz-precos-backup`, em `repricing_calculator.preco_ideal_para_margem`:
  `preço = (comissão + tarifa_fixa + frete + custo) / (1 − alíquota − margem_alvo)`,
  tratando comissão/tarifa/frete/custo como **R$ fixos do anúncio atual**.
  - **Quebra de frete grátis**: se o preço-com-frete cai em [R$ 79, teto] e o
    preço-sem-frete fica < R$ 79, usa o sem-frete (mais competitivo).
  - Margem é conferida por `margem_liquida_fixa` (= coluna T da planilha).
- Default da margem líquida alvo no seletor de anúncios = **20%**.
- Teto de quebra de frete grátis: campo do perfil (`teto_quebra_frete_gratis`),
  atualmente **R$ 87** (= W2 da planilha).

### Adicionar SKUs à campanha — pipeline compartilhado
- `infrastructure/ml/campaign_skus_apply.aplicar_adicoes_skus_em_campanha`:
  inflar (com **snapshot** do preço atual) → adicionar com **retry de fallback**
  (deal conservador ≥ R$ 79 em `ERROR_CREDIBILITY`) → **descarte**.
- Usado pelos DOIS fluxos (mesma lógica): `sync-batch` (editar campanha) e
  `criar_ml_completo` ("Iniciar agora" de campanha nova).
- **Descarte** dos itens que não entram na campanha:
  - negado por `ERROR_CREDIBILITY` → **reprecifica o anúncio pro preço de 20% de
    margem** (desinfla) e deixa **FORA** da campanha.
  - negado por outro motivo → **reverte ao preço original** (desfaz a inflação).

### Limpezas de UI (mai/2026)
- Planilha de custos na config da loja: **só upload** (removido "colar caminho").
- Removidos: botão "Exportar conferência" (CSV temporário de conferência), botão
  "Desmarcar problemáticos" (função já coberta por "Marcar todos" + "Calcular
  descontos"), e a feature **"Reverter preços via planilha"** inteira
  (botão + página + rota + endpoint backend).
- Reordenação do detalhe da campanha: **Período → Anúncios incluídos → Cobertura
  → Oportunidades de migração → Histórico de operações → Automação** (a lista de
  anúncios vem cedo, é o mais importante). Removido o card "Simulação base".
- Linhas da lista de SKUs (seletor): preço alinhado à direita, deal logo abaixo,
  indicadores no tooltip, badge discreto, `aviso_degrau` como sub-linha.

### Segurança (revisão sênior, mai/2026)
App é local single-user (bind `127.0.0.1`). Correções aplicadas:
- **XSS refletido no callback OAuth** (`api/routes/oauth_callback.py`): `error`/
  `error_description` (query params) e `nickname` (do ML) eram interpolados no HTML
  via `.format()` sem escaping. Agora tudo passa por `html.escape()`.
- **`custos_xlsx_path` só gravável via upload validado**: o PATCH de perfil
  trocava o config inteiro (cliente podia apontar pra arquivo arbitrário).
  `UpdateProfileConfigUseCase.execute` agora preserva o path existente a menos que
  `permitir_custos_path=True` (só o endpoint de upload em `config_files.py` passa).
- **`python-multipart>=0.0.18`** (CVE-2024-53981 / ReDoS) — floor; instalado já era 0.0.29.
- **GET→POST nos endpoints com efeito colateral** (parte barata do #2): `force-resync`
  e os 3 debug (`debug-ml-items`, `debug-legados`, `debug-tarifa-fixa`) agora são
  POST — fecha o vetor drive-by `<img>`/link cross-site (GET retorna 405). Nenhum é
  usado pela UI, então sem impacto no front. **Não fecha** CSRF via `<form method=post>`
  (POST simples sem corpo) — isso depende do segredo de sessão (resto do #2).
- **EM ABERTO (#2, exige design)**: a API local **não tem autenticação** — só
  CORS allowlist + bind localhost. Qualquer processo local chama tudo. Plano:
  segredo de sessão front↔back exigido em todo `/api/*` (menos `/api/auth/callback`).
- Pontos OK (não mexer achando que é bug): `state` OAuth via `secrets.token_urlsafe`
  uso único; tokens/secrets criptografados (Fernet) e nunca logados; sem SQL cru/
  subprocess/eval/pickle; slug validado (sem path traversal); upload xlsx não usa
  filename do cliente no path e é limitado a 20MB.

### Performance das operações de campanha (mai/2026)
- **P1** — `aplicar_adicoes_skus_em_campanha` processa os itens **em paralelo**
  (cada item = uma task inflar→adicionar→descarte), com concorrência limitada por
  `asyncio.Semaphore` (`MAX_CONCORRENCIA_WRITES = 6`). Antes era sequencial.
- **P2** — snapshot do preço atual (pra reverter) é feito em **lote** via
  `GET /items?ids=...` (`_snapshot_precos`, multiget de 20), não item a item.
- **Cache do merge `GET /campaigns`** (`repositories/campaigns_list_cache.py`):
  listar campanhas re-sincroniza TODAS com o ML (centenas de requests); rajadas de
  refetch no front martelavam o ML (sintoma: "loop puxando dados do ML"). Cache em
  memória, **TTL 45s** por `(profile_id, include_archived)`, com **invalidação por
  versão** — `CampaignRepository.create/update/delete` chama `bump(profile_id)`,
  então toda escrita local invalida na hora; mudanças externas (ML/scheduler) caem
  pelo TTL. Singleton, some no restart (mesmo padrão do `FeeReportCache`).

### Sync ML do detalhe da campanha — varredura reversa lenta (mai/2026)
- Abrir uma campanha **origem ML** dispara o sync `import-from-ml`, que faz a
  **varredura reversa** (`promotions_lookup.listar_items_do_vendedor_em_promocao`):
  lista TODOS os anúncios ativos do vendedor e consulta a promoção de cada um. É
  necessária — o endpoint direto perde os legados MLB4xxx (numa loja real,
  `via_direto=5` vs `via_reverso=90`).
- **Era sequencial** (1 anúncio por vez) → ~2,5 min numa loja com ~473 anúncios; o
  banner "Atualizando dados do ML" ficava "preso" e o ML era martelado. Agora o
  loop usa `asyncio.gather` (concorrência real limitada pelo semáforo do `MLClient`,
  8) → ~15-20s.
- **Disparo único no front** (`pages/campaign-detail.tsx`): o efeito que dispara o
  sync usa `syncFiredRef` (ref síncrono), porque o `StrictMode` (dev) re-invoca o
  efeito e ambas as execuções enxergam o mesmo `syncState="idle"` capturado no
  render — sem o ref o import (= uma varredura inteira) disparava 2x.
- **Gate de re-sync** (`ULTIMO_SYNC_ML_MS`, `SYNC_ML_TTL_MS=3min`): mapa em memória
  (por sessão SPA) do último sync por `ml_promotion_id`. Re-navegar pra uma
  campanha já sincronizada há < 3min **pula a varredura** (sem banner) — antes
  voltar pra tela re-rodava os ~20s toda vez. O botão "Atualizar do ML" sempre
  força e atualiza o mapa. Some no reload (F5) — aí re-sincroniza, faz sentido.
- **Concorrência do `MLClient` 8 → 12** + `max_connections` 20→24, keepalive 10→16
  (`infrastructure/ml/client.py`): o scan reverso é limitado por latência (~472
  round-trips), então mais paralelismo encurta direto (~19s → ~13s). **Subir
  concorrência SÓ é seguro com retry-on-429**: o `get()` tem `max_retries=0` por
  default e o reverse scan engole exceção → um 429 descartaria o SKU **silenciosamente**
  (campanha mostraria menos itens). Por isso o reverse scan agora chama
  `ml.get(..., max_retries=3)` (`promotions_lookup.listar_items_do_vendedor_em_promocao`).
- **Auto-sync direcionado vs completo** (`ml_import_use_case`, `full_scan` no
  `ImportMLCampaignRequest`): a varredura reversa varre TODO o catálogo (~472
  anúncios) só pra descobrir quais estão na campanha. Agora:
  - **Auto-sync ao abrir** (campanha já espelhada, `full_scan=False`) →
    `listar_items_da_promocao_direcionado`: endpoint **direto** (MLB6 novos) +
    `confirmar_items_na_promocao` dos **SKUs já conhecidos** (`skus_selecionados`).
    Custo ~`1 + len(skus)` ≈ 90, não 472. **Não descobre** adições LEGADAS
    (MLB4xxx) feitas no painel ML — pra isso, o botão.
  - **Botão "Atualizar do ML"** (`fullScan: true`) e **primeira importação**
    (sem `existing`) e **force-resync** → varredura **completa**
    (`listar_items_da_promocao_completo`), descobre tudo.
  - Helper por-item compartilhado: `_item_esta_na_promocao` (usado pela completa e
    pela direcionada). Decisão alinhada com o user (mai/2026): aceitou que adições
    legadas no painel ML só aparecem via botão.
- **Scan duplicado no load do detalhe** (medido no log: 944 requests pra 472
  itens = cada item 2x, num único sync): o `SkuSelectorSemSimulacao` (Dialog do
  "Adicionar/editar SKUs") fica **montado mesmo fechado**, e o `useSkusComPromocoes`
  disparava a varredura de TODOS os anúncios (~14s) no load da tela, em paralelo
  com o reverse scan do import. Fix: `useSkusComPromocoes(..., { enabled: open })`
  — só busca quando o modal abre (staleTime 5min cobre reabrir). Cortou o load do
  detalhe pela metade (944 → 472 requests ML).
- **Descartado por medição** (mai/2026): HTTP/2 e cliente HTTP persistente. Medido
  no log: dentro de um scan o keepalive já reusa conexões, então persistente é
  marginal e estruturalmente arriscado (refresh de token). HTTP/2 só compensa em
  concorrência muito alta (usamos 12 de um pool de 24 HTTP/1.1) e exigiria a dep
  `h2`. Reavaliar só se empurrarmos a concorrência bem mais pra cima.
