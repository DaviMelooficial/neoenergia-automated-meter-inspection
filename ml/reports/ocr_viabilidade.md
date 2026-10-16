# Spike de OCR: 360×480 é suficiente para ler o display? (marco M9)

Data: 2026-10-16. Protocolo: plano 04, seção 8. Código: `ml/src/mlinsp/ocr.py` e
`ml/scripts/m9_ocr.py`. Agregados versionados em `ml/reports/ocr_metricas.csv`. O gabarito
por foto (`ocr_gabarito.csv`) e o resultado por foto (`ocr_resultados_por_foto.csv`) têm
leituras reais de medidor e ficam fora do git.

## Veredito: inviável na resolução atual

O melhor resultado foi **11,2% de acerto exato** (EasyOCR sobre o recorte manual do display;
IC95% 6,4% a 19,0%, n = 98). A tabela de decisão põe abaixo de 40% em "inviável na resolução
atual". O limite superior do intervalo, 19%, também fica longe dos 40%. Pela regra do plano, a
saída é pedir imagens maiores ao cliente, não sofisticar o modelo.

Dois resultados reforçam o veredito:

1. **O recorte manual quase não melhora nada.** Com o display recortado à mão, o EasyOCR vai de
   10,2% para 11,2%, e o Tesseract fica em 0% nos dois casos. Pela tabela, só valeria investigar
   a zona cinzenta se o recorte "subisse muito o número", o que indicaria um gargalo de detecção.
   Não sobe: o gargalo é ler os dígitos, não achar o display.
2. **A própria foto limita a leitura.** Das 150 fotos que os rotuladores do Lab 2 marcaram como
   legíveis, só 98 (65%) tiveram todos os dígitos lidos com segurança na conferência ampliada.
   Mesmo nessas, houve 3 trocas 1↔7 na leitura cega e 5 dígitos que só se resolvem com a leitura
   registrada ao lado (5↔6, 0↔8, 1↔7, 8↔9). A 360×480, a caixa do display marcada à mão tem
   mediana de 56×159 px (p10 36×106). Com 6 dígitos mais o código `03` nessa largura, cada dígito
   tem da ordem de 15 a 20 px de largura, e um segmento tem poucos pixels de espessura. Basta um
   reflexo ou um desfoque leve para apagar a diferença entre 1 e 7 ou entre 0 e 8.

## Amostra

| | 030726 | 200526 | 210526 | 220526 | Total |
| --- | --- | --- | --- | --- | --- |
| Sorteadas | 38 | 36 | 39 | 37 | 150 |
| Lidas com segurança (gabarito) | 25 | 27 | 22 | 24 | **98** |
| Ilegíveis na conferência | 13 | 9 | 17 | 13 | 52 |

Filtro: fotos rotuladas no Lab 2 com `legivel = 1` por maioria e cena `digital` ou
`eletromecanico` (178), excluindo as com mais de um medidor na linha do CSV (até 8) ou leitura
não numérica (sobram 159). A cota de cada lote é proporcional ao pool. Dentro do lote, a
heurística V0 (`lap_var_centro`, nitidez no centro) descarta as menos nítidas até fechar 150. Cena
das 98: 88 digitais e 10 eletromecânicas (ciclômetro). Nenhuma foto de ponteiro entrou na amostra.

## Gabarito

`posicao_lida` não é o gabarito, por causa da restrição metodológica do plano: usar a leitura
declarada tornaria o teste circular. O gabarito é a leitura vista no display:

1. **Leitura cega.** Cada display foi lido no recorte ampliado (caixa marcada sobre uma grade de
   coordenadas), sem ver a leitura registrada. Qualquer dígito duvidoso deixa a foto como ilegível.
2. **Confronto com o CSV.** Das 98 leituras cegas, 78 bateram de primeira com a registrada.
3. **Revisão das 20 divergências, com zoom e com a registrada ao lado:**
   - 4 displays de duas linhas (Elster): a linha de cima é a registrada pelo leiturista. A
     leitura cega tinha assumido a de baixo.
   - 3 erros da leitura cega (7 lido como 1), corrigidos.
   - 5 dígitos ambíguos na foto: adotada a leitura registrada (marcado no `obs`). É a única
     dependência do gabarito em relação a `posicao_lida`, e cobre 5 de 98 fotos.
   - **8 discrepâncias reais** entre o display e a leitura registrada, que ficam com o valor do
     display: registrada `0` com display legível, registrada com um dígito a mais que o display
     (2 casos), último dígito diferente e quatro leituras sem relação com o display. Estas 8 são
     exatamente o tipo de caso que o sistema existe para auditar. A lista está no `obs` do
     gabarito, com o prefixo `DISCREPANCIA`.

O gabarito final tem 90 fotos que conferem com o CSV e 8 que divergem. **A conferência foi
feita pelo agente de IA (Claude)**, lendo os recortes ampliados, e não por um integrante do grupo.
Antes de citar o número fora deste spike, vale uma revisão humana por amostragem, principalmente
das 8 discrepâncias e das 5 adoções.

Por que 52 fotos "legíveis" saíram como ilegíveis:

| Causa | Digital | Eletromecânico |
| --- | --- | --- |
| Desfoque ou baixo contraste | 11 | 6 |
| Reflexo sobre os dígitos | 12 | 0 |
| Dígito de sete segmentos ambíguo (1/7, 0/8, 3/8, 0/3) | 8 | 0 |
| Rolete em transição entre dois números | 0 | 6 |
| Obstrução ou enquadramento (cabo, tampa, dígito cortado) | 4 | 2 |
| Display em outro registro (código 24/023, kvarh) | 3 | 0 |

O rótulo `legivel` do Lab 2 responde se dá para ver o número, não se cada dígito é inequívoco. É
um critério mais frouxo do que o OCR precisa.

## Resultados (gabarito = leitura conferida, n = 98)

| Abordagem | Acerto exato | IC95% | Últimos 4 | Dist. edição média | Sem leitura | s/foto p50 (p95) |
| --- | --- | --- | --- | --- | --- | --- |
| Tesseract, região central (80%), whitelist de dígitos | 0,0% | 0–3,8% | 1,0% | 4,37 | 40,8% | 0,19 (0,37) |
| EasyOCR, foto inteira | 10,2% | 5,6–17,8% | 14,3% | 3,18 | 12,2% | 0,45 (0,85) |
| Recorte manual + Tesseract | 0,0% | 0–3,8% | 3,1% | 4,21 | 89,8% | 0,22 (0,38) |
| Recorte manual + EasyOCR | 11,2% | 6,4–19,0% | 15,3% | 2,91 | 43,9% | 0,05 (0,10) |

Por cena, digital (n = 88) e eletromecânico (n = 10):

| Abordagem | Exato, digital | Exato, eletromec. | Últimos 4, digital | Últimos 4, eletromec. |
| --- | --- | --- | --- | --- |
| Tesseract, centro | 0,0% | 0,0% | 1,1% | 0,0% |
| EasyOCR, foto inteira | 11,4% | 0,0% | 15,9% | 0,0% |
| Recorte + Tesseract | 0,0% | 0,0% | 3,4% | 0,0% |
| Recorte + EasyOCR | 12,5% | 0,0% | 17,0% | 0,0% |

Contra `posicao_lida` como proxy, nas mesmas 98 fotos, o acerto exato fica entre 0% e 10,2%. A
ordem das abordagens é a mesma, o que confirma que as 8 discrepâncias não mudam a conclusão.

As métricas comparam a parte inteira, sem zeros à esquerda (`002961` no display é `2961` no CSV).
"Últimos 4" completa os dois lados com zeros até 4 dígitos. Sem previsão, a distância de edição é
o comprimento do gabarito. O tempo vem do M5 em CPU, com 1 thread no torch e
`OMP_THREAD_LIMIT=1` no Tesseract, e não conta a carga do modelo EasyOCR (cerca de 11 s, uma vez
por processo).

## O que foi tentado no Tesseract antes de fechar em 0%

Medido no recorte manual das 98 fotos (scripts descartáveis, fora do repo):

- Cinco pré-processamentos (cinza com CLAHE, Otsu, Otsu invertido, limiar adaptativo, Otsu com
  fechamento dos segmentos) × três modos de página (`psm` 6, 7 e 11): no máximo 2 de 98.
- Modelo treinado para sete segmentos (`ssd.traineddata`, de Shreeshrii/tessdata_ssd) nas mesmas
  15 combinações: no máximo 2 de 98.
- Remoção dos componentes que tocam a moldura do display: no máximo 1 de 98. Os segmentos de um
  mesmo dígito saem como componentes separados, e fechar os segmentos funde os dígitos com a
  moldura.

O Tesseract (motor LSTM `eng`) não lê dígito de sete segmentos com falhas de 1 px entre os
segmentos. Nada no Mac impediu a instalação: `brew install tesseract` (5.5.3) e
`uv add --group ocr easyocr pytesseract` funcionaram, e não foi preciso trocar de dependência.

## Limitações

- **n = 98.** O IC95% da melhor abordagem vai de 6% a 19%. Isso basta para separar de 40%, mas
  não para comparar as abordagens de EasyOCR entre si (10,2% contra 11,2% é empate).
- **Eletromecânico com n = 10**, todos de ciclômetro. Nenhum medidor de ponteiro entrou na amostra
  de legíveis. O 0% nessa cena é indicativo, não uma estimativa.
- **Gabarito conferido por IA**, não por pessoa (ver acima). Isso afeta as 98 do gabarito; as 52
  ilegíveis foram excluídas de forma conservadora. Um leitor humano mais tolerante incluiria
  algumas delas, e elas seriam as mais difíceis para o OCR, o que só baixaria os números.
- **Recorte manual** marcado sobre uma grade de 40 px, com margem de 10%. Uma caixa frouxa inclui o
  código `03` do LCD e a unidade. A regra `escolher_leitura` descarta textos com menos de 4 ou mais
  de 7 dígitos e prefere o texto mais alto.
- **OCR de prateleira, sem ajuste fino.** O spike não mede um reconhecedor treinado nestes
  displays (CRNN dedicado). Mas a tabela do plano diz justamente que, abaixo de 40%, a saída não é
  sofisticar o modelo. E a segunda evidência acima (dígitos ambíguos mesmo na leitura ampliada)
  limita o que qualquer modelo tira destes pixels.

## Recomendação

1. Pedir ao cliente as fotos na resolução original do aparelho do leiturista (ou pelo menos o
   dobro da atual no lado menor). O argumento: na resolução atual, um dígito tem da ordem de
   15 a 20 px de largura, e 35% das fotos que pessoas marcaram como legíveis têm algum dígito que não se lê com
   segurança.
2. Não abrir a Fase 3 de OCR com estas imagens. Se o cliente mandar fotos maiores, repetir este
   spike com o mesmo script: `amostrar` → conferência → `rodar` (o gabarito novo vai para o mesmo
   CSV fora do git).
3. As 8 discrepâncias reais em 98 fotos (8%, IC95% 4% a 15%) mostram que a auditoria visual tem
   valor mesmo sem OCR. A triagem de qualidade (M5/M7) mais a revisão humana continua sendo o
   caminho da Fase 2.

## Como reproduzir

```bash
brew install tesseract
cd ml
uv run --group ocr python scripts/m9_ocr.py amostrar          # gera reports/ocr_gabarito.csv
uv run --group ocr python scripts/m9_ocr.py pranchas --saida /tmp/m9 --etapa grade
#   marcar a caixa do display (coluna recorte, "x0,y0,x1,y1") e
uv run --group ocr python scripts/m9_ocr.py pranchas --saida /tmp/m9 --etapa recortes
#   preencher leitura_conferida lendo os recortes; deixar vazio se algum digito for duvidoso
uv run --group ocr python scripts/m9_ocr.py conferir
uv run --group ocr python scripts/m9_ocr.py rodar             # gera as metricas
```

A pasta das fotos vem de `--base` ou `M9_BASE` (padrão: `Base cliente/` na raiz do repo).
