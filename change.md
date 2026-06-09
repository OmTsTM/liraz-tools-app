# Planilha Precificadora Mercado Livre — Contexto Completo da Sessão

> **Arquivo associado:** `liraz-precos-passo3-v2.xlsx`
> **Aba ativa de trabalho:** `Planilha1` (a aba `Taxas e Lucro` é referência antiga e não foi modificada)
> **Linhas de dados:** 3 a 475 (473 SKUs)

---

## 1. Objetivo geral da planilha

Precificador de produtos vendidos no Mercado Livre, com três objetivos sequenciais:

1. **Diagnóstico atual:** mostrar o lucro/margem real do preço de venda atual de cada SKU.
2. **Recomendação de preço novo (P):** calcular o preço que entrega uma **margem alvo (Q2, padrão 20%)** respeitando uma **margem mínima inviolável (R2, padrão 15%)** e as regras de tarifa do ML.
3. **Preço inflado para campanhas:** calcular um preço "inchado" (T2, padrão 20% acima de P) para o usuário cadastrar no ML, e dar desconto em campanhas que voltem ao P, obtendo a margem alvo no final.

---

## 2. Regras de negócio do Mercado Livre relevantes

- **Tarifa por categoria (% sobre o preço de venda):** Clássico ~11,5% / Premium ~16,5% (varia por categoria). Está cadastrada na **coluna G** de cada linha como percentual.
- **Tipo de anúncio:** Clássico aparece em E ou Premium aparece em F. Sempre só uma das duas tem valor; a outra fica vazia.
- **Tarifa fixa vs frete (regra dos R$ 79):**
  - Produtos com preço **< R$ 79,00** pagam **tarifa fixa** (cadastrada na coluna H, varia por preço/categoria).
  - Produtos com preço **≥ R$ 79,00** pagam **frete** ao invés de tarifa fixa (cadastrado na coluna I, varia por peso/dimensão/categoria).
  - **NUNCA cobra os dois ao mesmo tempo.**
- **Imposto:** alíquota global editável (K2, padrão 8%), aplicada sobre o preço de venda.

---

## 3. Estrutura completa das colunas

### Bloco "Estado Atual" (colunas A a O) — descreve a situação de venda **hoje** com o preço D

| Col | Cabeçalho | Conteúdo | Tipo |
|---|---|---|---|
| A | SKU | Código interno do produto | Input |
| B | Custo do Produto | Custo de aquisição em R$ (pode ser "(sem custo)" → fórmulas tratam como 0) | **Input editável** (azul-claro) |
| C | ID do Produto | MLB ID | Input |
| D | Valor de Venda Atual | Preço atualmente publicado no ML | **Input editável** (azul-claro) |
| E | Tarifa Clássico (R$) | `=D*G` se for anúncio Clássico, senão vazio | Fórmula |
| F | Tarifa Premium (R$) | `=D*G` se for anúncio Premium, senão vazio | Fórmula |
| G | % da Tarifa | % da tarifa do ML para essa categoria (input) | Input |
| H | Tarifa Fixa | Valor cadastrado da tarifa fixa (sempre input puro, mesmo se produto está em faixa de frete hoje) | **Input editável** (azul-claro) |
| I | Frete | Valor cadastrado do frete (sempre input puro, mesmo se produto está em faixa fixa hoje) | **Input editável** (azul-claro) |
| J | Valor Final a Receber do meli | `=D - E - F - IF(D<79,H,0) - IF(D>=79,I,0)` | Fórmula |
| K | Alíquota Imposto | K2 é editável (amarelo, padrão 8%) | **K2 input global** (amarelo) |
| L | % Imposto por linha | `=$K$2` | Fórmula |
| M | Imposto em R$ | `=D*$K$2` | Fórmula |
| N | Lucro Líquido (atual) | `=J - IF(ISNUMBER(B),B,0) - M` | Fórmula |
| O | % Líquida (atual) | `=IFERROR(N/D,0)` | Fórmula |

### Bloco "Preço Recomendado" (colunas P a R) — calcula o preço ideal

| Col | Cabeçalho | Conteúdo | Tipo |
|---|---|---|---|
| P | Valor Final de Venda | Preço que entrega a margem alvo Q2, respeitando regras (fórmula complexa abaixo) | Fórmula |
| Q | Margem a Obter | Q2 editável (amarelo, padrão 20%). Em cada linha: margem real atingida em P | **Q2 input global** (amarelo) |
| R | Margem Mín. p/ Forçar < 79 | R2 editável (amarelo, padrão 15%). Margem mínima inviolável | **R2 input global** (amarelo) |

### Coluna separadora

| Col | Conteúdo |
|---|---|
| S | **Vazia** (separador visual entre blocos) |

### Bloco "Preço Inflado para Campanhas" (colunas T a Z)

| Col | Cabeçalho | Conteúdo |
|---|---|---|
| T | % Inflação | **T2 editável** (amarelo, padrão 20%). Coluna T vazia nas linhas de dados; T2 é o controle global. |
| U | Preço Inflado | `=IF(NOT(ISNUMBER(P)),"",P*(1+$T$2))` |
| V | Desc. Necessário | `=IF(NOT(ISNUMBER(U)),"",1-P/U)` — % de desconto para voltar de U ao P |
| W | Margem no Inflado | Margem se vender no preço inflado SEM desconto. Respeita regra dos R$ 79. |
| X | Margem Pós-Desc. | Margem ao aplicar o desconto e voltar ao P (deve bater com Q). |
| Y | Lucro Pós-Desc. (R$) | Lucro em reais no preço P. |
| Z | Valor Final de Venda na Campanha | `=IF(NOT(ISNUMBER(P)),"",P)` — espelho de P (preço final que o cliente paga após desconto) |

### Coluna de Status

| Col | Conteúdo |
|---|---|
| AA | **Status** — texto curto + cor (formatação condicional) indicando o estado da linha |

Status possíveis e cores:
- 🟢 **OK** (verde): atinge margem alvo Q2
- 🟡 **Forçado <79 (evita frete)** (amarelo): preço ficou em R$ 78,99, margem entre R2 e Q2
- 🔴 **⚠ Subiu p/ frete (I=0)** (vermelho): preço ≥ R$ 79 mas frete não cadastrado (margem otimista — precisa cadastrar I)
- 🟠 **Subiu p/ frete (min R2)** (laranja): preço ≥ R$ 79 atingindo R2 mínimo, mas abaixo de Q2 alvo
- 🔴 **⚠ Margem abaixo do mínimo** (vermelho): margem real < R2 (não deveria acontecer com a lógica atual)
- ⚫ **Sem custo** (cinza): B vazio ou texto "(sem custo)"

---

## 4. Fórmula completa do P (Valor Final de Venda)

Esta é a fórmula mais importante e mais complexa da planilha. Implementa a **Filosofia B**: prioriza evitar frete sempre que a margem em R$ 78,99 atender o mínimo R2.

```
P = IF(NOT(ISNUMBER(B)), "",
    IF((1-G-K2-Q2)<=0, "erro",
    IF(P_fixa_alvo < 79, P_fixa_alvo,
    IF(margem_em_78.99 >= R2, 78.99,
    IF(P_frete_alvo >= 79, P_frete_alvo,
    MAX(79, P_frete_min)
    )))))
```

Onde:
- `denom_alvo = (1 - G - K2 - Q2)`
- `denom_min = (1 - G - K2 - R2)`
- `P_fixa_alvo = (B + H) / denom_alvo`
- `P_frete_alvo = (B + I) / denom_alvo`
- `P_frete_min = (B + I) / denom_min`
- `margem_em_78.99 = (78.99 - 78.99*G - H - 78.99*K2 - B) / 78.99`

### Ordem de decisão (Filosofia B)

1. **Tenta atingir Q2 na faixa fixa**: se `P_fixa_alvo < 79`, usa esse preço. Margem real = Q2 alvo. ✅
2. **Senão, testa forçar R$ 78,99**: se a margem real em R$ 78,99 ≥ R2, usa **R$ 78,99** (prioriza evitar frete, aceita margem entre R2 e Q2).
3. **Senão, tenta atingir Q2 na faixa frete**: se `P_frete_alvo ≥ 79`, usa esse preço. Margem real = Q2 alvo.
4. **Senão (zona morta total)**: sobe para faixa frete pelo mínimo: `MAX(79, P_frete_min)`. Garante pelo menos R2 de margem.

### Fórmula da margem real Q (em P)

```
Q = (P - P*G - IF(P<79, H, I) - P*K2 - B) / P
```

Sempre reavalia a faixa pelo P (não pelo D). Se P < 79, cobra H; se P ≥ 79, cobra I.

---

## 5. Decisões de design importantes (e armadilhas evitadas)

### 5.1 H e I são inputs puros, NÃO fórmulas

Numa versão anterior, H e I eram fórmulas (`=IF(D<79, Y, 0)` e `=IF(D>=79, Z, 0)`) que puxavam de colunas auxiliares ocultas Y/Z. Isso causou bug grave porque:
- O usuário editava o valor direto na coluna I (lugar visível e óbvio)
- Mas a fórmula de P puxava de Z (oculto), que continuava zerado
- Resultado: P não recalculava como deveria

**Decisão final:** H e I são **inputs puros**, sempre. O usuário pode editar livremente. As fórmulas que precisam da regra dos R$ 79 fazem o `IF(P<79, H, I)` localmente. As colunas Y e Z foram apagadas (Y é usada agora para "Lucro Pós-Desc. R$", Z para "Valor Final na Campanha").

### 5.2 Frete pode ser cadastrado mesmo em produto que está abaixo de R$ 79

Quando D < 79, o ML não cobra frete (cobra tarifa fixa H). Mas o usuário pode cadastrar o frete I antecipadamente, sabendo que **se um dia o preço subir para ≥ R$ 79, o I cadastrado entra em ação automaticamente**.

A fórmula de P **considera o I cadastrado** ao calcular `P_frete_alvo` — ou seja, ela já testa a hipótese "e se eu subisse pra faixa de frete?". Se nessa faixa der margem alvo, usa esse preço.

**Caveat útil:** se a fórmula optar por ficar abaixo de R$ 79 (Filosofia B), o frete I cadastrado fica "guardado" mas não incide. Isso é correto — o ML só cobra frete se o preço estiver ≥ 79.

### 5.3 R2 é trava INVIOLÁVEL

A regra R2 (margem mínima) **não pode ser quebrada em nenhuma circunstância**. Em todos os cenários da fórmula de P, a margem real Q sempre fica ≥ R2.

A única exceção visível seria status "⚠ Subiu p/ frete (I=0)": quando o produto sobe pra faixa de frete mas o frete está zerado, a margem mostrada é otimista. Quando o usuário cadastrar I real, a fórmula recalcula automaticamente e pode subir ainda mais o preço se necessário para garantir R2.

### 5.4 Filosofia A vs Filosofia B (decidida: B)

**Filosofia A:** sempre prioriza atingir Q2 alvo, mesmo se isso significar subir pra faixa de frete.
- Vantagem: garante margem máxima.
- Desvantagem: produto fica mais caro, menos competitivo.

**Filosofia B (escolhida):** prioriza ficar na faixa fixa (< R$ 79). Só sobe pra faixa de frete se a margem em R$ 78,99 ficar abaixo de R2.
- Vantagem: produto mais competitivo, evita frete sempre que possível.
- Desvantagem: aceita margem menor que Q2 alvo em alguns casos (mas sempre ≥ R2).

### 5.5 Linhas "(sem custo)"

43 produtos têm B = `"(sem custo)"` (texto, não número). Para essas linhas:
- N (Lucro Líquido) usa `IF(ISNUMBER(B), B, 0)` — trata texto como 0, mostra apenas o lucro bruto.
- P, Q, U, V, W, X, Y, Z ficam todos vazios (fórmulas usam `IF(NOT(ISNUMBER(B)),"",...)`).
- **Quando o usuário preencher B com um número, tudo recalcula automaticamente.** As fórmulas estão prontas, só esperando o valor.

### 5.6 Bloco "Preço Inflado" respeita regra dos R$ 79

O preço inflado U pode cruzar R$ 79 (ex.: P=75 → U=90). Nesse caso:
- A fórmula de W (Margem no Inflado) usa `IF(U<79, H, I)` — cobra frete se inflado ≥ 79.
- A fórmula de X (Margem Pós-Desc.) usa `IF(P<79, H, I)` — quando volta ao P, reavalia.
- Resultado: produtos próximos de R$ 79 podem ter margem no inflado bem reduzida, mas a margem pós-desconto volta ao alvo Q2.

---

## 6. Estatística atual do portfólio (com B/I das edições do usuário)

Distribuição de status na última versão (`liraz-precos-passo3-v2.xlsx`, parâmetros padrão Q2=20%, R2=15%, K2=8%, T2=20%):

| Status | Quantidade |
|---|---|
| OK (atinge Q2) | ~390 |
| Sem custo | 43 |
| ⚠ Subiu p/ frete (I=0) | ~28 |
| Forçado <79 (evita frete) | ~12 |

---

## 7. Fluxo de trabalho recomendado

### Para precificar tudo do zero
1. Garantir que B (custo) e D (preço atual) estão preenchidos corretamente.
2. Cadastrar tarifa fixa H e frete I (mesmo que o produto esteja em uma faixa só, cadastrar os dois ajuda a fórmula a calcular alternativas).
3. Ajustar Q2 (margem alvo) e R2 (margem mínima) se necessário.
4. Olhar a coluna P (preço recomendado) e Q (margem que vai obter).
5. Conferir a coluna AA (Status) — produtos com 🔴 ou 🟠 precisam de atenção (cadastrar frete, revisar custo, etc.).
6. Atualizar os preços no ML conforme P.

### Para produtos que vão cruzar para faixa de frete (Subiu p/ frete I=0)
1. Olhar P recomendado (margem otimista pois I=0).
2. Ir ao ML, simular o preço P para descobrir o frete que será cobrado.
3. Voltar à planilha e cadastrar o valor real em I.
4. P recalcula sozinho. Se mudou, repetir passo 2 (porque frete varia por faixa).
5. Quando estabilizar, atualizar preço no ML e o D da planilha.

### Para campanhas com desconto
1. Ajustar T2 (% inflação, padrão 20%).
2. Olhar a coluna U (preço inflado) — esse é o preço a cadastrar no ML.
3. Olhar a coluna V (% desconto necessário) — esse é o cupom/desconto a aplicar na campanha.
4. Conferir W (margem se alguém comprar sem usar cupom — você lucra mais) e Y (lucro pós-desconto em R$).

### Para alterar a alíquota de imposto
1. Mudar K2 (única célula) — todas as linhas refletem automaticamente.

---

## 8. Coisas que NÃO devem ser feitas

- ❌ Não criar colunas ocultas auxiliares para "valores base" — já foi tentado e causou bug grave (5.1).
- ❌ Não calcular margem em cima de D (preço atual) no bloco de recomendação — Q deve ser sempre calculada em P.
- ❌ Não relaxar a trava R2 — é margem mínima inviolável.
- ❌ Não usar dados da aba "Taxas e Lucro" — ela é histórica e não foi modificada nesta sessão.
- ❌ Não mexer nas colunas A até R como estrutura — apenas valores dos inputs (B, D, H, I, e os controles K2/Q2/R2).

---

## 9. Próximos passos pendentes

O usuário sinalizou que continuaremos em etapas futuras. Esta sessão completou os "Passos 1, 2 e 3". Não há próximo passo definido ainda.

---

## 10. Arquivo final entregue

**Nome:** `liraz-precos-passo3-v2.xlsx`
**Status:** validado, 11.223 fórmulas, zero erros de cálculo.
**Última verificação:** 27/05/2026
