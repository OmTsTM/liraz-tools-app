// LiraZ ML Fees Extractor — content script (v0.2, sem Setup).
//
// Seletores HARDCODED baseados no DOM mapeado do painel ML (mai/2026).
// Painel: https://www.mercadolivre.com.br/anuncios?...
// Estrutura confirmada via debug_dump:
//
//   div.sll-list-grid-row.sll-list-grid-row--item               (= 1 anúncio)
//     .sll-list-cell-product__copyable                          (= cabeçalho compartilhado)
//       .sc-copyable-element__text "#6590363324"               (= 1 MLB por modalidade)
//       .sc-copyable-element__text "#6590363326"               (= MLB da 2ª modalidade)
//     .sll-list-cell-price                                      (= preço — 1 por modalidade)
//     .sll-list-cell-purchase-options                           (= 1 por modalidade)
//       text-line: "Clássico"/"Premium"
//       text-line: "A pagar R$ X,XX"   ← comissão (ignorar)
//       text-line: "Envio por conta do comprador" / "Você oferece frete grátis"
//       text-line: "A pagar R$ X,XX"   ← TARIFA FIXA ou FRETE (queremos = ÚLTIMO "A pagar")
//       text-line: "Você receberá até R$ Y,YY por usar o Flex"   ← bônus (IGNORAR)
//
// Empareçamento: 1º MLB ↔ 1ª purchase-cell ↔ 1ª price-cell, etc.

const STORAGE = {
  ROWS: 'mlx_rows',
  LAST_CAPTURE: 'mlx_last_capture',
};

const SELECTORS = {
  // Captura DOIS tipos de container:
  // - --item:   anúncio normal (1 ou N modalidades; sem variações OU com sub-família expandida)
  // - --family: UMA variação de uma família (Branco, Cinza, Preto, ...)
  // Os pais-família (--item que contém --family dentro) são pulados — seus
  // dados são só faixa/guarda-chuva das variações; pra cadastrar tarifa, só as
  // variações importam.
  container: 'div.sll-list-grid-row.sll-list-grid-row--item, div.sll-list-grid-row.sll-list-grid-row--family',
  // Seletor pra checar se um --item tem variação expandida dentro (= pai-família).
  family_child: '.sll-list-grid-row--family',
  mlb_text: '.sll-list-copyable-id.sll-list-cell-product__copyable .sc-copyable-element__text',
  purchase_cell: '.sll-list-cell-purchase-options',
  price_cell: '.sll-list-cell-price',
  price_main: '.andes-typography--weight-semibold',  // preço base destacado
  next_button_candidates: 'a.andes-pagination__link, button.andes-pagination__link',
  // Botão "Expandir anúncios" do painel ML — IDENTIFICADO pelo dump:
  //   <button aria-expanded="false" aria-label="Expandir anúncios"
  //           class="sll-list-chevron-button__icon ... sc-chevron--down">
  // O botão fica FORA do `sll-list-grid-row--item`, na coluna esquerda do
  // anúncio-pai. Por isso filtro `closest(container)` não pode bloquear.
  //
  // O `sc-chevron--down` identifica que está colapsado (apontando pra baixo);
  // quando expandido vira `sc-chevron--up`, então o seletor não casa mais — ✓.
  expand_button_candidates: 'button.sll-list-chevron-button__icon.sc-chevron--down[aria-expanded="false"]',
  // Defense in depth: containers a EXCLUIR (menus/dropdowns)
  expand_exclude_inside: '[role="menu"], [class*="andes-dropdown"], [class*="andes-tooltip"], [class*="andes-menu"]',
};

// ─── Parsers ─────────────────────────────────────────────────────────
const parseBrl = (raw) => {
  if (!raw) return null;
  const m = String(raw).match(/R\$\s*([\d.,]+)/);
  if (!m) return null;
  const n = parseFloat(m[1].replace(/\./g, '').replace(',', '.'));
  return Number.isFinite(n) ? n : null;
};

const parseMlbText = (raw) => {
  if (!raw) return null;
  const s = String(raw).trim();
  const m = s.match(/MLB(\d{8,})/) || s.match(/#?(\d{8,})/);
  if (!m) return null;
  return 'MLB' + (m[1] || m[0].replace(/^#/, ''));
};

// Extrai TODOS os valores R$ X,XX presentes dentro de um elemento, na ordem do DOM
const extrairValoresReais = (el) => {
  if (!el) return [];
  const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
  const matches = [...txt.matchAll(/R\$\s*([\d.,]+)/g)];
  const out = [];
  for (const m of matches) {
    const n = parseFloat(m[1].replace(/\./g, '').replace(',', '.'));
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
};

// Extrai só valores rotulados como "A pagar R$ X,XX". Estrutura atual do card:
//   "A pagar R$ 23,23"   ← comissão
//   "Você oferece frete grátis"
//   "A pagar R$ 23,65"   ← FRETE REAL
//   "Você receberá até R$ 0,89 por usar o Flex"   ← bônus opcional (IGNORAR)
// O último R$ da cell agora pode ser o bônus Flex, então `extrairValoresReais`
// pega o valor errado. Este filtro pega só "A pagar", ignora "receberá".
const extrairValoresAPagar = (el) => {
  if (!el) return [];
  const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
  const matches = [...txt.matchAll(/A\s*pagar\s*R\$\s*([\d.,]+)/gi)];
  const out = [];
  for (const m of matches) {
    const n = parseFloat(m[1].replace(/\./g, '').replace(',', '.'));
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
};

// Verifica se um descendente pertence a um sub-container aninhado (família ou
// item dentro do alvo). Usado pra não duplicar MLBs/cells quando o container
// pai contém sub-containers (caso família expandida).
const _CONTAINER_SUB_MATCH = '.sll-list-grid-row--family, .sll-list-grid-row--item';
const isInNestedContainer = (el, root) => {
  let cur = el.parentElement;
  while (cur && cur !== root) {
    if (cur.matches?.(_CONTAINER_SUB_MATCH)) return true;
    cur = cur.parentElement;
  }
  return false;
};

const queryDireto = (container, selector) => {
  const all = Array.from(container.querySelectorAll(selector));
  return all.filter((el) => !isInNestedContainer(el, container));
};

// ─── Extração de UM container ────────────────────────────────────────
const extrairDeContainer = (container) => {
  // 1) MLBs do cabeçalho — só os que pertencem DIRETO a este container
  //    (não os que estão dentro de sub-containers --family).
  const mlbEls = queryDireto(container, SELECTORS.mlb_text);
  const mlbs = [];
  const seen = new Set();
  for (const el of mlbEls) {
    const mlb = parseMlbText(el.textContent);
    if (mlb && !seen.has(mlb)) {
      seen.add(mlb);
      mlbs.push(mlb);
    }
  }

  // 2) Cells de purchase-options (1 por modalidade) — também restritas
  const purchaseCells = queryDireto(container, SELECTORS.purchase_cell);

  // 3) Cells de price restritas
  const priceCells = queryDireto(container, SELECTORS.price_cell);

  const linhas = [];
  const N = Math.min(mlbs.length, Math.max(1, purchaseCells.length));

  for (let i = 0; i < N; i++) {
    const item_id = mlbs[i];
    const pCell = purchaseCells[i] || purchaseCells[0];
    const prCell = priceCells[i] || priceCells[0];

    // Detecta "frete grátis" no texto da purchase-cell
    const cellText = (pCell?.textContent || '').toLowerCase();
    const freteGratis = /frete\s*gr[áa]tis|voc[êe]\s*oferece/i.test(cellText);

    // Pega os valores "A pagar R$ X,XX" na cell (comissão + tarifa/frete).
    // Usamos o ÚLTIMO "A pagar" — que é a tarifa fixa ou o frete.
    // FIX: ignora "Você receberá até R$ 0,89 por usar o Flex" que o ML passou
    // a inserir depois do frete real (fazia o pega-tudo antigo capturar o
    // bônus Flex como se fosse o frete).
    // Fallback: se o ML mudar o rótulo, cai no comportamento antigo (último
    // R$ da cell) pra não quebrar completamente.
    let valores = extrairValoresAPagar(pCell);
    if (valores.length === 0) {
      valores = extrairValoresReais(pCell);
    }
    const lastValor = valores.length > 0 ? valores[valores.length - 1] : null;

    // Preço base destacado (semibold) da cell de price
    let preco = null;
    if (prCell) {
      const semibold = prCell.querySelector(SELECTORS.price_main);
      if (semibold) preco = parseBrl(semibold.textContent);
      if (preco == null) {
        // fallback: primeiro R$ X da cell inteira
        const vs = extrairValoresReais(prCell);
        if (vs.length > 0) preco = vs[0];
      }
    }

    // Regra mutuamente exclusiva
    let custo_fixo, frete;
    if (freteGratis) {
      custo_fixo = 0;
      frete = lastValor != null ? lastValor : 0;
    } else {
      custo_fixo = lastValor != null ? lastValor : 0;
      frete = 0;
    }

    if (item_id) {
      linhas.push({ item_id, custo_fixo, frete, preco });
    }
  }

  return linhas;
};

// ─── Auto-expand de anúncios com variações ───────────────────────────
// Anúncios "família" (ex.: Branco/Cinza/Preto) vêm colapsados no painel ML —
// é preciso clicar na setinha pra cada variação aparecer como linha própria,
// com MLB e tarifa distintos. Esse helper varre TODOS os botões de expansão
// candidatos antes de cada captura.
const expandirAnunciosFamilia = async () => {
  // O botão de expandir família FICA FORA do `sll-list-grid-row--item` (no painel
  // ML ele está na coluna esquerda do anúncio-pai, num wrapper irmão). Só filtra
  // contra menus/dropdowns como defense in depth.
  const cands = Array.from(document.querySelectorAll(SELECTORS.expand_button_candidates));
  const valid = cands.filter((el) => !el.closest(SELECTORS.expand_exclude_inside));
  let clicados = 0;
  for (const btn of valid) {
    try {
      btn.click();
      clicados++;
    } catch { /* ignora */ }
  }
  if (clicados > 0) {
    // Aguarda DOM atualizar — variações expandidas viram novos
    // `sll-list-grid-row--item`. Dá 1s + 100ms por expansão.
    await new Promise((r) => setTimeout(r, Math.min(3000, 1000 + clicados * 100)));
  }
  return clicados;
};

// ─── Captura: itera todos os containers da página ────────────────────
const captureOnce = async () => {
  const { [STORAGE.ROWS]: existing = {} } = await chrome.storage.local.get(STORAGE.ROWS);

  // Antes de tudo: expande famílias colapsadas (variações vão virar containers
  // top-level na lista).
  const expandidos = await expandirAnunciosFamilia();

  const containers = Array.from(document.querySelectorAll(SELECTORS.container));
  if (containers.length === 0) {
    throw new Error(
      `Nenhum anúncio encontrado. Esperava elementos "${SELECTORS.container}". ` +
      `Você está no painel certo (mercadolivre.com.br/anuncios)?`
    );
  }

  let added = 0;
  let pulados_pai_familia = 0;
  let pulados_zerados = 0;
  const merged = { ...existing };
  for (const container of containers) {
    // Pula anúncios-pai família. Duas heurísticas (qualquer uma detecta):
    //   1. Tem --family expandido dentro (caso pós-expansão DOM)
    //   2. Tem o botão de expandir família dentro (sll-list-chevron-button)
    //      — pega ainda quando colapsado, antes do click
    const isPaiFamilia = container.matches('.sll-list-grid-row--item') && (
      container.querySelector(SELECTORS.family_child)
      || container.querySelector('button.sll-list-chevron-button__icon')
    );
    if (isPaiFamilia) {
      pulados_pai_familia++;
      continue;
    }
    const linhas = extrairDeContainer(container);
    for (const r of linhas) {
      // Filtro final: descarta linhas inúteis (sem nem tarifa nem frete).
      // Geralmente anúncios pausados, sem cálculo de tarifa, ou pai-família
      // que escapou do filtro acima.
      if ((r.frete ?? 0) <= 0 && (r.custo_fixo ?? 0) <= 0) {
        pulados_zerados++;
        continue;
      }
      merged[r.item_id] = { ...r, capturado_em: new Date().toISOString() };
      added++;
    }
  }

  await chrome.storage.local.set({
    [STORAGE.ROWS]: merged,
    [STORAGE.LAST_CAPTURE]: Date.now(),
  });
  return {
    added, total: Object.keys(merged).length,
    expandidos, pulados_pai_familia, pulados_zerados,
  };
};

// ─── Botão "Próxima página" ──────────────────────────────────────────
const findNextButton = () => {
  const cands = document.querySelectorAll(SELECTORS.next_button_candidates);
  for (const el of cands) {
    const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
    const aria = el.getAttribute('aria-label') || '';
    if (/seguinte|pr[óo]xima|next/i.test(txt + ' ' + aria)) {
      const disabled = el.disabled
        || el.getAttribute('aria-disabled') === 'true'
        || el.classList.contains('disabled')
        || el.classList.contains('andes-pagination__link--disabled');
      if (disabled) return null;
      return el;
    }
  }
  return null;
};

// ─── Toast de progresso ──────────────────────────────────────────────
const showToast = (title, detail = '') => {
  let toast = document.getElementById('mlx-capture-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'mlx-capture-toast';
    toast.className = 'mlx-capture-toast';
    document.body.appendChild(toast);
  }
  toast.innerHTML = `<div class="mlx-toast-title">${title}</div><div class="mlx-toast-detail">${detail}</div>`;
};
const hideToast = () => document.getElementById('mlx-capture-toast')?.remove();

// ─── Auto-paginação ──────────────────────────────────────────────────
const waitForRowsChange = (oldCount, timeoutMs = 15000) => new Promise((resolve) => {
  const t0 = Date.now();
  const check = () => {
    const n = document.querySelectorAll(SELECTORS.container).length;
    if (n > 0 && n !== oldCount) return resolve(true);
    if (Date.now() - t0 > timeoutMs) return resolve(false);
    setTimeout(check, 200);
  };
  check();
});

const captureWithPagination = async (delayMs) => {
  let totalAdded = 0;
  let pages = 0;

  for (;;) {
    const before = (await chrome.storage.local.get(STORAGE.ROWS))[STORAGE.ROWS] || {};
    showToast(`Capturando página ${pages + 1}…`, `Buffer: ${Object.keys(before).length}`);
    const r = await captureOnce();
    totalAdded += r.added;
    pages++;

    const nextBtn = findNextButton();
    if (!nextBtn) {
      showToast('Fim', `Sem botão "Próxima" ativo. Buffer: ${r.total}.`);
      setTimeout(hideToast, 4000);
      break;
    }

    const oldCount = document.querySelectorAll(SELECTORS.container).length;
    nextBtn.click();
    const wait = delayMs + Math.random() * (delayMs * 0.5);
    await new Promise((res) => setTimeout(res, wait));
    const changed = await waitForRowsChange(oldCount);
    if (!changed) {
      showToast('Timeout', `Página não mudou. Buffer: ${r.total}.`);
      setTimeout(hideToast, 4000);
      break;
    }
  }

  const final = (await chrome.storage.local.get(STORAGE.ROWS))[STORAGE.ROWS] || {};
  return { added: totalAdded, total: Object.keys(final).length, pages };
};

// ─── Diagnóstico ─────────────────────────────────────────────────────
const diagnose = async () => {
  const containers = Array.from(document.querySelectorAll(SELECTORS.container));
  const expandCands = Array.from(document.querySelectorAll(SELECTORS.expand_button_candidates))
    .filter((el) => el.closest(SELECTORS.container));
  const result = {
    containerCount: containers.length,
    expandButtonsCount: expandCands.length,
    nextBtnFound: !!findNextButton(),
    samples: [],
  };

  // Amostra dos 3 primeiros containers
  for (const c of containers.slice(0, 3)) {
    const mlbsRaw = c.querySelectorAll(SELECTORS.mlb_text);
    const mlbs = [];
    const seen = new Set();
    for (const el of mlbsRaw) {
      const m = parseMlbText(el.textContent);
      if (m && !seen.has(m)) { seen.add(m); mlbs.push(m); }
    }
    const purchaseCells = c.querySelectorAll(SELECTORS.purchase_cell);
    const priceCells = c.querySelectorAll(SELECTORS.price_cell);
    const linhas = extrairDeContainer(c);
    result.samples.push({
      mlbs, purchaseCellsCount: purchaseCells.length,
      priceCellsCount: priceCells.length, linhas,
    });
  }
  return { ok: true, diag: result };
};

// ─── Mapear página (debug rico — pra identificar botões de expansão) ─
const mapPage = async () => {
  const lines = [];
  const p = (s = '') => lines.push(s);

  p('═══════════════════════════════════════════════════════════════════════');
  p('LiraZ ML Fees Extractor — DOM MAP (dev-only)');
  p(`Gerado em: ${new Date().toLocaleString('pt-BR')}`);
  p(`URL: ${location.href}`);
  p(`Title: ${document.title}`);
  p('═══════════════════════════════════════════════════════════════════════');

  // [A] Seletores atuais
  p('\n┌─ [A] Seletores hardcoded em uso ─');
  for (const [k, v] of Object.entries(SELECTORS)) {
    let n = 0;
    try { n = document.querySelectorAll(v).length; } catch { n = -1; }
    p(`│  ${k.padEnd(28)} → ${n === -1 ? 'erro' : n} matches`);
    p(`│  ${' '.repeat(28)}   ${v.slice(0, 120)}${v.length > 120 ? '…' : ''}`);
  }

  const containers = Array.from(document.querySelectorAll(SELECTORS.container));
  p(`\n  containers detectados: ${containers.length}`);

  // [B] CAÇADA do botão de EXPANDIR — pra cada container, lista TODOS os
  // botões/links/ícones clicáveis pra identificar qual é a "setinha de expandir"
  p('\n┌─ [B] Elementos CLICÁVEIS dentro dos 3 primeiros containers ─');
  p('│  (procurando candidatos a "Expandir anúncios")');
  containers.slice(0, 3).forEach((c, idx) => {
    p('│');
    p(`├── Anúncio #${idx + 1} ─`);
    // Pega todos os elementos potencialmente clicáveis
    const clickables = c.querySelectorAll(
      'button, a, [role="button"], [aria-expanded], [class*="expand"], ' +
      '[class*="collapse"], [class*="toggle"], [class*="chevron"], [class*="arrow"]',
    );
    p(`│  total clicáveis: ${clickables.length}`);
    Array.from(clickables).slice(0, 25).forEach((el, i) => {
      const tag = el.tagName.toLowerCase();
      const klass = Array.from(el.classList).filter((c) => !c.startsWith('mlx-')).join(' ');
      const aria = el.getAttribute('aria-label') || '';
      const expanded = el.getAttribute('aria-expanded');
      const role = el.getAttribute('role') || '';
      const text = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
      const hasSvg = el.querySelector('svg, [class*="icon"]') ? 'SVG' : '';
      p(`│  ${(i + 1).toString().padStart(2)}. <${tag}${role ? ` role="${role}"` : ''}${expanded != null ? ` aria-expanded="${expanded}"` : ''}> ${hasSvg}`);
      p(`│      class="${klass}"`);
      if (aria) p(`│      aria-label="${aria}"`);
      if (text) p(`│      text="${text}"`);
    });
  });

  // [C] Inventário GERAL da página: elementos com aria-expanded
  p('\n┌─ [C] Todos elementos da página com aria-expanded ─');
  const exp = document.querySelectorAll('[aria-expanded]');
  p(`│  total: ${exp.length}`);
  Array.from(exp).slice(0, 20).forEach((el, i) => {
    const dentroAnuncio = el.closest(SELECTORS.container) ? '  [DENTRO de anúncio]' : '';
    const tag = el.tagName.toLowerCase();
    const klass = Array.from(el.classList).filter((c) => !c.startsWith('mlx-')).join(' ');
    const value = el.getAttribute('aria-expanded');
    const aria = el.getAttribute('aria-label') || '';
    p(`│  ${(i + 1).toString().padStart(2)}. <${tag} aria-expanded="${value}"> aria="${aria}"${dentroAnuncio}`);
    p(`│      class="${klass}"`);
  });

  // [D] Elementos com classes "expand/collapse/toggle"
  p('\n┌─ [D] Elementos com classes "expand/collapse/toggle" ─');
  const togCands = document.querySelectorAll(
    '[class*="expand"], [class*="collapse"], [class*="toggle"]',
  );
  p(`│  total: ${togCands.length}`);
  Array.from(togCands).slice(0, 20).forEach((el, i) => {
    const dentroAnuncio = el.closest(SELECTORS.container) ? '  [DENTRO de anúncio]' : '';
    const tag = el.tagName.toLowerCase();
    const klass = Array.from(el.classList).filter((c) => !c.startsWith('mlx-')).join(' ');
    const aria = el.getAttribute('aria-label') || '';
    p(`│  ${(i + 1).toString().padStart(2)}. <${tag}> aria="${aria}"${dentroAnuncio}`);
    p(`│      class="${klass}"`);
  });

  // [E] Botões de paginação
  p('\n┌─ [E] Paginação ─');
  document.querySelectorAll('a.andes-pagination__link, button.andes-pagination__link, [class*="pagination"]')
    .forEach((el, i) => {
      const tag = el.tagName.toLowerCase();
      const text = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30);
      const aria = el.getAttribute('aria-label') || '';
      const dis = el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('disabled');
      p(`│  ${(i + 1).toString().padStart(2)}. <${tag}> text="${text}" aria="${aria}" disabled=${dis}`);
    });

  // [F] Captura simulada — mostra o que o algoritmo extrairia AGORA
  p('\n┌─ [F] Simulação de captura (5 primeiros containers) ─');
  containers.slice(0, 5).forEach((c, i) => {
    const linhas = extrairDeContainer(c);
    p(`│  Anúncio #${i + 1}: ${linhas.length} linha(s) extraídas`);
    for (const l of linhas) {
      p(`│    ${l.item_id}  frete=${l.frete.toFixed(2)}  cf=${l.custo_fixo.toFixed(2)}  preço=${l.preco ?? '?'}`);
    }
  });

  p('\n═══════════════════════════════════════════════════════════════════════');
  p('FIM');

  const txt = lines.join('\n');
  return { ok: true, txt, bytes: txt.length };
};

// ─── Listener ────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === 'START_SETUP') {
        // Setup foi removido — seletores são hardcoded.
        sendResponse({
          ok: true,
          info: 'Setup não é mais necessário. Use "Capturar página atual" direto.',
        });
      } else if (msg.type === 'DIAGNOSE') {
        sendResponse(await diagnose());
      } else if (msg.type === 'MAP_PAGE') {
        sendResponse(await mapPage());
      } else if (msg.type === 'EXPAND') {
        const n = await expandirAnunciosFamilia();
        sendResponse({ ok: true, expandidos: n });
      } else if (msg.type === 'START_CAPTURE') {
        if (msg.autoPaginate) {
          const r = await captureWithPagination(msg.delayMs || 2000);
          sendResponse({ ok: true, ...r });
        } else {
          const r = await captureOnce();
          showToast(`Capturado: ${r.added} novos`, `Total: ${r.total}`);
          setTimeout(hideToast, 3500);
          sendResponse({ ok: true, ...r });
        }
      } else {
        sendResponse({ ok: false, error: 'msg desconhecida' });
      }
    } catch (e) {
      sendResponse({ ok: false, error: e.message });
    }
  })();
  return true;
});
