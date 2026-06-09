# LiraZ — ML Fees Extractor

Extensão do Chrome que extrai **tarifa fixa** ("A pagar R$ X") e **MLB** do painel de anúncios do Mercado Livre, paginando automaticamente, e exporta um CSV pra usar no LiraZ Tools como override de custos.

## Por que existe

A API pública do ML **não expõe** a tarifa fixa real cobrada pelo vendedor (R$ 6,75 / 7,95 / 8,55 / etc.) — o valor depende de peso + dimensões + categoria + preço a partir de mar/2026, e nenhum endpoint retorna esse cálculo. Só o painel renderiza. Essa extensão lê do DOM diretamente.

## Instalação

1. Abra Chrome (ou Edge/Brave/Opera) e vá em `chrome://extensions/`
2. Liga o **"Modo desenvolvedor"** (canto superior direito)
3. Clica **"Carregar sem compactação"** (Load unpacked)
4. Aponta pra esta pasta: `extensions/ml-fees-extractor/`
5. Pronto — o ícone da extensão aparece na barra do Chrome (clique no quebra-cabeça pra fixar)

## Uso

Os seletores são **hardcoded** baseados na estrutura atual do painel ML (mapeada em mai/2026). **Não precisa de Setup.**

### 1) Captura

1. Vá para o **painel de anúncios**: https://www.mercadolivre.com.br/anuncios?page=1
2. Clique no ícone da extensão → opcionalmente **"Diagnóstico"** pra verificar que os anúncios são detectados (vai mostrar quantos MLBs por anúncio)
3. **"Capturar página atual"** ou marque **"Auto-paginar"** + delay e clica capturar

### 2) Exportar

**"Exportar XLSX"** — baixa um arquivo `ml-tarifas-YYYY-MM-DD-HH-MM-SS.xlsx` com 3 colunas:

| MLB | Frete | Custo fixo |
|---|---:|---:|
| MLB6731404730 | 0,00 | 6,75 |
| MLB4592946051 | 0,00 | 7,95 |
| MLB6590363324 | 0,00 | 7,95 |
| MLB6590363326 | 13,85 | 0,00 |
| MLB5135724194 | 12,50 | 0,00 |

**Regra do `custo_fixo` vs `frete`** (mutuamente exclusivos, como combinado):
- Card com "Envio por conta do comprador" → `custo_fixo > 0` (= "A pagar R$ X"), `frete = 0`
- Card com "Você oferece frete grátis" / "Frete grátis" → `custo_fixo = 0`, `frete > 0` (= "A pagar R$ X" do card, que nesse contexto é o frete que o vendedor paga)

> Anúncio com Clássico + Premium gera **2 linhas** no CSV (1 por MLB) — a extensão emparelha cada MLB com sua modalidade automaticamente.

## Integração com o LiraZ Tools

1. Abra o seu `custos.xlsx` (caminho cadastrado na config da loja)
2. Adicione uma **nova aba** chamada exatamente **`TarifasML`** (case-sensitive)
3. Cole o conteúdo do XLSX exportado (3 colunas: `MLB | Frete | Custo fixo`)
4. Salve o `custos.xlsx`
5. O backend lê essa aba automaticamente. Toda vez que o app precisar da tarifa fixa ou frete de um anúncio, vai usar o valor real cadastrado em vez do teto teórico.

## Reset

**"Resetar dados"** apaga as linhas capturadas mas mantém os seletores. Útil pra recapturar do zero sem refazer o Setup.

## Troubleshooting

- **"Nenhum anúncio encontrado"**: a página não é o painel de anúncios. Vá pra https://www.mercadolivre.com.br/anuncios?page=1
- **Diagnóstico mostra 0 anúncios**: o ML mudou layout. Use o botão **"Mapear página (dump pra dev)"** e nos envie — atualizamos os seletores
- **Auto-paginar para no meio**: o botão "Seguinte" desabilitou (última página) ou demorou >8s pra carregar
- **XLSX abre com colunas erradas**: verifique se você não editou a ordem `MLB | Frete | Custo fixo` antes de colar no `custos.xlsx`

## Privacidade

- Tudo roda local. **Nada é enviado pra servidor.**
- Os seletores capturados e as linhas ficam no `chrome.storage.local`. Some quando você desinstala a extensão.
- A extensão pede só permissões mínimas: `storage`, `scripting`, `activeTab`, `downloads`, e acesso a `mercadolivre.com.br` (pra rodar o content script).
