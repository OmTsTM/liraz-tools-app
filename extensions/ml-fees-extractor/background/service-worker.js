// Service worker — MV3 exige ele declarado, mesmo se vazio.
// Hoje toda lógica vive no content script + popup, então este só registra um
// handler pra futuro caso precise (ex.: tabs.update pra abrir docs).

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === 'install') {
    console.log('[LiraZ Fees Extractor] Instalado com sucesso.');
  }
});
