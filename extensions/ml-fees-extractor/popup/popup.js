// Popup — orquestra setup/captura/exportação enviando mensagens pro content
// script. Estado vive em chrome.storage.local pra sobreviver entre re-aberturas.

const $ = (id) => document.getElementById(id);

const STORAGE_KEYS = {
  ROWS: 'mlx_rows',
  LAST_CAPTURE: 'mlx_last_capture',
  AUTO_PAGINATE: 'mlx_auto_paginate',
  DELAY: 'mlx_delay',
  PAGES_COUNT: 'mlx_pages_count',
};

const log = (msg, kind = 'info') => {
  const div = document.createElement('div');
  div.className = `line ${kind}`;
  div.textContent = `[${new Date().toLocaleTimeString('pt-BR')}] ${msg}`;
  $('log-content').appendChild(div);
  $('log-content').scrollTop = $('log-content').scrollHeight;
};

const refreshStatus = async () => {
  const data = await chrome.storage.local.get(Object.values(STORAGE_KEYS));
  const rows = data[STORAGE_KEYS.ROWS] || {};
  const lastCapture = data[STORAGE_KEYS.LAST_CAPTURE];
  const pages = data[STORAGE_KEYS.PAGES_COUNT] || 0;
  const total = Object.keys(rows).length;

  const rowsEl = $('rows-count');
  rowsEl.textContent = total.toString();
  rowsEl.classList.toggle('has-data', total > 0);
  $('pages-count').textContent = pages.toString();
  $('last-capture').textContent = lastCapture
    ? new Date(lastCapture).toLocaleString('pt-BR')
    : '—';

  const tab = await getCurrentTab();
  $('page-url').textContent = tab?.url ? new URL(tab.url).pathname : '—';
  $('containers-count').textContent = '—';  // atualizado pelo Diagnóstico
  $('btn-export').disabled = total === 0;
  $('btn-export-label').textContent = total > 0
    ? `Exportar XLSX (${total} linhas)`
    : 'Exportar XLSX';
};

const getCurrentTab = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
};

const isMLPage = (url) => {
  if (!url) return false;
  return /mercadolivre\.com\.br|mercadolibre\.com\.br/.test(url);
};

const sendToContent = async (msg) => {
  const tab = await getCurrentTab();
  if (!tab?.id) throw new Error('Sem aba ativa');
  if (!isMLPage(tab.url)) throw new Error('Abra o painel do ML antes (mercadolivre.com.br).');
  return chrome.tabs.sendMessage(tab.id, msg);
};

// ─── Captura ─────────────────────────────────────────────────────────
$('btn-capture').addEventListener('click', async () => {
  try {
    const autoPaginate = $('auto-paginate').checked;
    const delaySec = parseInt($('delay').value, 10) || 2;
    log(`Capturando${autoPaginate ? ' + auto-paginação' : ''}…`, 'info');
    const resp = await sendToContent({
      type: 'START_CAPTURE',
      autoPaginate,
      delayMs: delaySec * 1000,
    });
    if (typeof resp?.expandidos === 'number' && resp.expandidos > 0) {
      log(`expandiu ${resp.expandidos} anúncios de família (variações)`, 'info');
    }
    if (typeof resp?.pulados_pai_familia === 'number' && resp.pulados_pai_familia > 0) {
      log(`pulou ${resp.pulados_pai_familia} pai(s) de família (dados são guarda-chuva)`, 'info');
    }
    if (typeof resp?.pulados_zerados === 'number' && resp.pulados_zerados > 0) {
      log(`descartou ${resp.pulados_zerados} linha(s) com frete=0 e cf=0 (sem dado útil)`, 'info');
    }
    log(`OK: ${resp?.added ?? 0} linhas adicionadas (buffer agora: ${resp?.total ?? 0})`, 'ok');
    // Conta páginas processadas — incrementa baseado em "auto-paginar":
    // sem auto, soma 1 (uma página visível); com auto, o resposta `pages`
    // virá embutida; senão fallback +1.
    const cur = (await chrome.storage.local.get(STORAGE_KEYS.PAGES_COUNT))[STORAGE_KEYS.PAGES_COUNT] || 0;
    const inc = autoPaginate ? Math.max(1, resp?.pages || 1) : 1;
    await chrome.storage.local.set({ [STORAGE_KEYS.PAGES_COUNT]: cur + inc });
    await refreshStatus();
  } catch (e) {
    log(`Erro: ${e.message}`, 'error');
  }
});

// ─── Expandir famílias (manual) ──────────────────────────────────────
$('btn-expand').addEventListener('click', async () => {
  try {
    log('Expandindo famílias…', 'info');
    const resp = await sendToContent({ type: 'EXPAND' });
    log(`Expandiu ${resp?.expandidos ?? 0} elementos. Aguarde 1-2s e clique em "Capturar".`,
      resp?.expandidos > 0 ? 'ok' : 'error');
  } catch (e) {
    log(`Erro: ${e.message}`, 'error');
  }
});

// ─── Diagnóstico ─────────────────────────────────────────────────────
$('btn-diagnose').addEventListener('click', async () => {
  try {
    log('Rodando diagnóstico…', 'info');
    const resp = await sendToContent({ type: 'DIAGNOSE' });
    if (!resp?.ok) {
      log(`Erro: ${resp?.error || 'desconhecido'}`, 'error');
      return;
    }
    const d = resp.diag;
    log(`anúncios na página: ${d.containerCount}`, d.containerCount > 0 ? 'ok' : 'error');
    $('containers-count').textContent = d.containerCount.toString();
    log(`botão "Próxima" detectado: ${d.nextBtnFound ? 'sim' : 'não'}`,
      d.nextBtnFound ? 'ok' : 'info');
    d.samples.forEach((s, i) => {
      log(`#${i + 1}: ${s.mlbs.length} MLB(s) [${s.mlbs.join(', ')}], ` +
          `${s.purchaseCellsCount} purchase-cell(s), ${s.priceCellsCount} price-cell(s)`, 'info');
      for (const r of s.linhas) {
        log(`    ${r.item_id}: frete=${r.frete.toFixed(2)} cf=${r.custo_fixo.toFixed(2)} preço=${r.preco ?? '?'}`, 'ok');
      }
    });
  } catch (e) {
    log(`Erro: ${e.message}`, 'error');
  }
});

// ─── Mapear página (dump dev) ────────────────────────────────────────
$('btn-map').addEventListener('click', async () => {
  try {
    log('Gerando mapa da página…', 'info');
    const resp = await sendToContent({ type: 'MAP_PAGE' });
    if (!resp?.ok) {
      log(`Erro: ${resp?.error || '?'}`, 'error');
      return;
    }
    const blob = new Blob([resp.txt], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    const filename = `ml-dom-map-${stamp}.txt`;
    await chrome.downloads.download({ url, filename, saveAs: true });
    log(`OK: ${resp.bytes} bytes salvos em ${filename}`, 'ok');
  } catch (e) {
    log(`Erro: ${e.message}`, 'error');
  }
});

// ─── XLSX writer inline (ZIP store-only, sem deps) ───────────────────
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

const crc32 = (bytes) => {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) {
    crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ bytes[i]) & 0xFF];
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
};

const utf8 = (s) => new TextEncoder().encode(s);

// Monta um ZIP store-only (sem compressão) — formato suficiente pra XLSX.
const buildZip = (files) => {
  const localChunks = [];
  const centralChunks = [];
  let offset = 0;
  let totalLocal = 0;
  let totalCentral = 0;

  for (const f of files) {
    const nameBytes = utf8(f.name);
    const crc = crc32(f.data);
    const size = f.data.length;

    // Local file header (30 bytes + name + data)
    const local = new Uint8Array(30 + nameBytes.length + size);
    const dv = new DataView(local.buffer);
    dv.setUint32(0, 0x04034b50, true);
    dv.setUint16(4, 20, true);     // version needed
    dv.setUint16(6, 0, true);      // flags
    dv.setUint16(8, 0, true);      // method = stored
    dv.setUint16(10, 0, true);     // time
    dv.setUint16(12, 0x21, true);  // date (1/1/1980)
    dv.setUint32(14, crc, true);
    dv.setUint32(18, size, true);  // compressed size
    dv.setUint32(22, size, true);  // uncompressed size
    dv.setUint16(26, nameBytes.length, true);
    dv.setUint16(28, 0, true);     // extra length
    local.set(nameBytes, 30);
    local.set(f.data, 30 + nameBytes.length);
    localChunks.push(local);
    totalLocal += local.length;

    // Central directory record (46 + name)
    const central = new Uint8Array(46 + nameBytes.length);
    const dvc = new DataView(central.buffer);
    dvc.setUint32(0, 0x02014b50, true);
    dvc.setUint16(4, 20, true);
    dvc.setUint16(6, 20, true);
    dvc.setUint16(8, 0, true);
    dvc.setUint16(10, 0, true);
    dvc.setUint16(12, 0, true);
    dvc.setUint16(14, 0x21, true);
    dvc.setUint32(16, crc, true);
    dvc.setUint32(20, size, true);
    dvc.setUint32(24, size, true);
    dvc.setUint16(28, nameBytes.length, true);
    dvc.setUint16(30, 0, true);
    dvc.setUint16(32, 0, true);
    dvc.setUint16(34, 0, true);
    dvc.setUint16(36, 0, true);
    dvc.setUint32(38, 0, true);
    dvc.setUint32(42, offset, true);
    central.set(nameBytes, 46);
    centralChunks.push(central);
    totalCentral += central.length;

    offset += local.length;
  }

  // End of Central Directory Record (22 bytes)
  const eocd = new Uint8Array(22);
  const dvE = new DataView(eocd.buffer);
  dvE.setUint32(0, 0x06054b50, true);
  dvE.setUint16(4, 0, true);
  dvE.setUint16(6, 0, true);
  dvE.setUint16(8, files.length, true);
  dvE.setUint16(10, files.length, true);
  dvE.setUint32(12, totalCentral, true);
  dvE.setUint32(16, totalLocal, true);
  dvE.setUint16(20, 0, true);

  const result = new Uint8Array(totalLocal + totalCentral + 22);
  let pos = 0;
  for (const c of localChunks)   { result.set(c, pos); pos += c.length; }
  for (const c of centralChunks) { result.set(c, pos); pos += c.length; }
  result.set(eocd, pos);
  return result;
};

const colLetter = (n) => {
  let s = '';
  let v = n;
  while (v >= 0) {
    s = String.fromCharCode((v % 26) + 65) + s;
    v = Math.floor(v / 26) - 1;
  }
  return s;
};

const escapeXml = (s) =>
  String(s).replace(/[<>&'"]/g, (c) =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' })[c]);

// rows: lista de listas. Cada célula pode ser string ou number.
const buildXlsx = (rows) => {
  const cellsForRow = (row, rIdx) =>
    row.map((val, cIdx) => {
      const ref = colLetter(cIdx) + (rIdx + 1);
      if (typeof val === 'number' && Number.isFinite(val)) {
        return `<c r="${ref}" t="n"><v>${val}</v></c>`;
      }
      return `<c r="${ref}" t="inlineStr"><is><t>${escapeXml(val)}</t></is></c>`;
    }).join('');

  const rowsXml = rows
    .map((r, i) => `<row r="${i + 1}">${cellsForRow(r, i)}</row>`)
    .join('');

  const sheetXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rowsXml}</sheetData></worksheet>`;

  const workbookXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="TarifasML" sheetId="1" r:id="rId1"/></sheets></workbook>`;

  const workbookRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`;

  const rootRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`;

  const contentTypes = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`;

  return buildZip([
    { name: '[Content_Types].xml',         data: utf8(contentTypes) },
    { name: '_rels/.rels',                 data: utf8(rootRels) },
    { name: 'xl/workbook.xml',             data: utf8(workbookXml) },
    { name: 'xl/_rels/workbook.xml.rels',  data: utf8(workbookRels) },
    { name: 'xl/worksheets/sheet1.xml',    data: utf8(sheetXml) },
  ]);
};

// ─── Exportar XLSX ───────────────────────────────────────────────────
$('btn-export').addEventListener('click', async () => {
  try {
    const { [STORAGE_KEYS.ROWS]: rows = {} } = await chrome.storage.local.get(STORAGE_KEYS.ROWS);
    const entries = Object.values(rows);
    if (entries.length === 0) {
      log('Nada pra exportar.', 'error');
      return;
    }
    // Ordem das colunas pedida: MLB | Frete | Custo fixo
    const data = [['MLB', 'Frete', 'Custo fixo']];
    for (const r of entries) {
      data.push([
        r.item_id,
        Number((r.frete ?? 0).toFixed(2)),
        Number((r.custo_fixo ?? 0).toFixed(2)),
      ]);
    }
    const xlsxBytes = buildXlsx(data);
    const blob = new Blob([xlsxBytes], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    const filename = `ml-tarifas-${stamp}.xlsx`;
    await chrome.downloads.download({ url, filename, saveAs: true });
    log(`Exportado: ${filename} (${entries.length} linhas)`, 'ok');
  } catch (e) {
    log(`Erro ao exportar: ${e.message}`, 'error');
  }
});

// ─── Reset ───────────────────────────────────────────────────────────
$('btn-reset').addEventListener('click', async () => {
  const data = await chrome.storage.local.get(STORAGE_KEYS.ROWS);
  const total = Object.keys(data[STORAGE_KEYS.ROWS] || {}).length;
  if (total === 0) {
    log('Buffer já está vazio.', 'info');
    return;
  }
  if (!confirm(
    `Apagar ${total} linha(s) acumulada(s) no buffer?\n\n` +
    `Você vai perder o que capturou até agora.\n` +
    `(seletores hardcoded continuam.)`,
  )) return;
  await chrome.storage.local.remove([
    STORAGE_KEYS.ROWS, STORAGE_KEYS.LAST_CAPTURE, STORAGE_KEYS.PAGES_COUNT,
  ]);
  log('Buffer limpo.', 'info');
  await refreshStatus();
});

// ─── Persistência das opções ─────────────────────────────────────────
$('auto-paginate').addEventListener('change', async (e) => {
  await chrome.storage.local.set({ [STORAGE_KEYS.AUTO_PAGINATE]: e.target.checked });
});
$('delay').addEventListener('change', async (e) => {
  await chrome.storage.local.set({ [STORAGE_KEYS.DELAY]: parseInt(e.target.value, 10) });
});

// ─── Init ────────────────────────────────────────────────────────────
(async () => {
  const data = await chrome.storage.local.get([STORAGE_KEYS.AUTO_PAGINATE, STORAGE_KEYS.DELAY]);
  if (data[STORAGE_KEYS.AUTO_PAGINATE]) $('auto-paginate').checked = true;
  if (data[STORAGE_KEYS.DELAY]) $('delay').value = data[STORAGE_KEYS.DELAY];
  await refreshStatus();
})();

// Permissão pra downloads.download
// (precisamos pedir no manifest se já não está — vamos checar)
