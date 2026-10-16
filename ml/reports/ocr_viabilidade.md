# Spike de OCR: 360×480 é suficiente para ler o display? (marco M9)

Data: 2026-10-16. Protocolo: plano 04, seção 8. Código: `ml/src/mlinsp/ocr.py` e
`ml/scripts/m9_ocr.py`. Agregados versionados em `ml/reports/ocr_metricas.csv`. O gabarito
por foto (`ocr_gabarito.csv`) e o resultado por foto (`ocr_resultados_por_foto.csv`) têm
leituras reais de medidor e ficam fora do git (desvio de protocolo, ver adiante).

**Os números deste relatório só devem ser citados fora do spike depois de uma revisão humana por
amostragem do gabarito.** A conferência foi feita pelo agente de IA, e não por um integrante do
grupo (seção Gabarito).

## Veredito: inviável na resolução atual

O melhor resultado foi **12,0% de acerto exato** (EasyOCR sobre o recorte manual do display;
IC95% 6,8% a 20,2%). O denominador são as **92 fotos cujo display foi lido com segurança**, e não
as 150 sorteadas. Sobre as 150, as mesmas 11 leituras certas são 7,3% (IC95% 4,1% a 12,7%). A
tabela de decisão põe abaixo de 40% em "inviável na resolução atual", e o limite superior do
intervalo, 20%, também fica longe dos 40%. Pela regra do plano, a saída é pedir imagens maiores
ao cliente, não sofisticar o modelo.

Dois resultados reforçam o veredito:

1. **O recorte manual quase não melhora nada.** Com o display recortado à mão, o EasyOCR vai de
   10,9% para 12,0% (n = 92), e o Tesseract fica em 0% nos dois casos. Pela tabela, só valeria
   investigar a zona cinzenta se o recorte "subisse muito o número", o que indicaria um gargalo de
   detecção. Não sobe: o gargalo é ler os dígitos, não achar o display.
2. **A própria foto limita a leitura.** Das 150 fotos que os rotuladores do Lab 2 marcaram como
   legíveis, só 92 (61%) tiveram todos os dígitos lidos com segurança na conferência ampliada. Na
   leitura cega houve 3 trocas de 7 por 1, e outras 6 fotos têm um dígito que só se resolve com a
   leitura registrada ao lado (5/6, 0/8, 1/7, 8/9, 5/9). Essas 6 saíram do gabarito como
   ilegíveis. A 360×480, a caixa do display marcada à mão tem mediana de 56×159 px (p10 36×106).
   Com 6 dígitos mais o código `03` nessa largura, cada dígito tem da ordem de 15 a 20 px de
   largura, e um segmento tem poucos pixels de espessura. Basta um reflexo ou um desfoque leve
   para apagar a diferença entre 1 e 7 ou entre 0 e 8.

## Amostra

| | 030726 | 200526 | 210526 | 220526 | Total |
| --- | --- | --- | --- | --- | --- |
| Sorteadas | 38 | 36 | 39 | 37 | 150 |
| Lidas com segurança (gabarito) | 24 | 26 | 21 | 21 | **92** |
| Ilegíveis na conferência | 14 | 10 | 18 | 16 | 58 |

Filtro:

1. Fotos rotuladas no Lab 2 com `legivel = 1` por maioria e cena `digital` ou `eletromecanico`:
   178.
2. Fora as que têm mais de um medidor na linha do CSV (até 8) ou leitura não numérica: sobram 159.
3. A cota de cada lote é proporcional ao pool, pelo método dos maiores restos.
4. Dentro do lote, a heurística V0 (`lap_var_centro`, nitidez no centro) descarta as menos nítidas
   até fechar 150.

Cena das 92 do gabarito: 83 digitais e 9 eletromecânicas (ciclômetro). Nenhuma foto de ponteiro
entrou na amostra.

## Gabarito

`posicao_lida` não é o gabarito, por causa da restrição metodológica do plano: usar a leitura
declarada tornaria o teste circular. O gabarito é a leitura vista no display:

1. **Leitura cega.** Cada display foi lido no recorte ampliado (caixa marcada sobre uma grade de
   coordenadas), sem ver a leitura registrada. Qualquer dígito duvidoso deixa a foto como ilegível.
2. **Confronto com o CSV.** Das 98 leituras cegas, 78 bateram de primeira com a registrada.
3. **Revisão das 20 divergências, com zoom e com a registrada ao lado:**
   - 4 displays de duas linhas (Elster): a linha de cima é a registrada pelo leiturista. A
     leitura cega tinha assumido a de baixo.
   - 3 erros da leitura cega (7 lido como 1), corrigidos porque o dígito é inequívoco no zoom.
   - 6 dígitos ambíguos na foto, que só se resolviam com a registrada ao lado: marcados como
     **ilegíveis**. O gabarito não usa `posicao_lida` em nenhuma foto.
   - **7 discrepâncias entre o display e a leitura registrada**, que ficam com o valor do display.

O gabarito final tem 92 fotos: 85 conferem com o CSV e 7 divergem. As discrepâncias se dividem em
duas categorias (prefixo `DISCREPANCIA` no `obs` do gabarito):

| Categoria | Casos | Fração das 92 | IC95% |
| --- | --- | --- | --- |
| Erro de formato do registro (casa decimal colada: o registro tem um dígito a mais que o display) | 2 | 2,2% | 0,6% a 7,6% |
| Divergência substantiva (registrada `0` com display legível; 4 leituras sem relação com o display) | 5 | 5,4% | 2,3% a 12,1% |
| Total | 7 | 7,6% | 3,7% a 14,9% |

**A conferência foi feita pelo agente de IA (Claude)**, lendo os recortes ampliados, e não por um
integrante do grupo. Antes de citar qualquer número fora deste spike, o grupo precisa revisar o
gabarito por amostragem, a começar pelas 7 discrepâncias e pelas 6 fotos ambíguas.

Por que 58 fotos "legíveis" saíram como ilegíveis:

| Causa | Digital | Eletromecânico |
| --- | --- | --- |
| Desfoque ou baixo contraste | 11 | 6 |
| Reflexo sobre os dígitos | 12 | 0 |
| Dígito de sete segmentos ambíguo (1/7, 0/8, 3/8, 0/3, 5/6, 5/9) ou rolete ambíguo | 13 | 1 |
| Rolete em transição entre dois números | 0 | 6 |
| Obstrução ou enquadramento (cabo, tampa, dígito cortado) | 4 | 2 |
| Display em outro registro (código 24/023, kvarh) | 3 | 0 |

O rótulo `legivel` do Lab 2 responde se dá para ver o número, não se cada dígito é inequívoco. É
um critério mais frouxo do que o OCR precisa.

## Resultados (gabarito = leitura conferida, denominador = 92 fotos lidas com segurança)

| Abordagem | Acerto exato | IC95% | Últimos 4 | Dist. edição média | Sem leitura | s/foto p50 (p95) |
| --- | --- | --- | --- | --- | --- | --- |
| Tesseract, região central (80%), whitelist de dígitos | 0,0% | 0–4,0% | 1,1% | 4,36 | 38,0% | 0,18 (0,27) |
| EasyOCR, foto inteira | 10,9% | 6,0–18,9% | 15,2% | 3,15 | 10,9% | 0,41 (0,61) |
| Recorte manual + Tesseract | 0,0% | 0–4,0% | 3,3% | 4,20 | 89,1% | 0,20 (0,27) |
| Recorte manual + EasyOCR | 12,0% | 6,8–20,2% | 15,2% | 2,86 | 41,3% | 0,04 (0,09) |

Por cena, digital (n = 83) e eletromecânico (n = 9):

| Abordagem | Exato, digital | Exato, eletromec. | Últimos 4, digital | Últimos 4, eletromec. |
| --- | --- | --- | --- | --- |
| Tesseract, centro | 0,0% | 0,0% | 1,2% | 0,0% |
| EasyOCR, foto inteira | 12,0% | 0,0% | 16,9% | 0,0% |
| Recorte + Tesseract | 0,0% | 0,0% | 3,6% | 0,0% |
| Recorte + EasyOCR | 13,3% | 0,0% | 16,9% | 0,0% |

Contra `posicao_lida` como proxy, nas mesmas 92 fotos, o acerto exato fica entre 0% e 10,9%. A
ordem das abordagens é a mesma, o que confirma que as 7 discrepâncias não mudam a conclusão.

Como a métrica é calculada:

- Compara-se a parte inteira, sem zeros à esquerda: `001234.5` no display vira `1234`, igual ao
  formato do CSV. Isso relaxa o "acerto exato da string completa" do plano (ver Desvios).
- "Últimos 4" completa os dois lados com zeros até 4 dígitos.
- Sem previsão, a distância de edição é o comprimento do gabarito.

O tempo foi medido em CPU (Apple M5), com 1 thread no torch e `OMP_THREAD_LIMIT=1` no
Tesseract. Não conta a carga do modelo EasyOCR, cerca de 11 s, uma vez por processo.

## O que foi tentado no Tesseract antes de fechar em 0%

Medido no recorte manual das 98 fotos da primeira versão do gabarito, antes de as 6 ambíguas
saírem (scripts descartáveis, fora do repo):

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

## Limitações e desvios de protocolo

Desvios em relação ao plano (doc 04, seção 8, e doc 06, M9):

- **A amostra saiu dos rótulos humanos, não da heurística.** O plano manda amostrar entre as fotos
  que a heurística considerou legíveis. Aqui o filtro de legibilidade foi o rótulo `legivel` do Lab
  2, por maioria. A heurística V0 só desempatou dentro do lote. O efeito provável é uma amostra
  mais legível do que a da heurística, ou seja, a favor do OCR.
- **A métrica é mais frouxa que a string completa.** O plano pede o acerto exato da string
  completa. A métrica usada compara a parte inteira, sem zeros à esquerda e sem a casa decimal.
  Isso relaxa a régua a favor do OCR: zeros à esquerda e casa decimal perdidos não contam como
  erro. Mesmo assim, o resultado fica abaixo de 40%.
- **O denominador é o das fotos lidas com segurança (92), não as 150.** As 58 ilegíveis não têm
  gabarito e saem da conta. Contadas como erro, o melhor acerto cai para 7,3%.
- **O gabarito não está versionado.** O T-M9-02 pede o gabarito em `ml/reports/ocr_gabarito.csv`.
  O arquivo existe nesse caminho, mas fica fora do git (`.gitignore`) porque traz leitura real por
  medidor. Só as métricas agregadas e este relatório estão versionados. Há uma cópia em
  `Base cliente/_m9_ocr/`, também fora do git.
- **O critério de escolha do candidato foi escolhido no conjunto de avaliação.** A regra
  `escolher_leitura` prefere o texto mais alto, e não o de maior confiança, porque a altura acertou
  mais nestas mesmas fotos. Saída de `m9_ocr.py criterios` (n = 92):

  | EasyOCR | Altura | Confiança |
  | --- | --- | --- |
  | Foto inteira | 10/92 | 6/92 |
  | Recorte manual | 11/92 | 11/92 |

  Escolher a regra no próprio conjunto de teste deixa o número da foto inteira otimista. Com a
  regra de confiança, a foto inteira faria 6,5%. No recorte, as duas regras empatam. O veredito
  não muda: as duas variantes ficam abaixo de 40%.

Limitações:

- **n = 92.** O IC95% da melhor abordagem vai de 7% a 20%. Isso basta para separar de 40%, mas
  não para comparar as abordagens de EasyOCR entre si (10,9% contra 12,0% é empate).
- **Eletromecânico com n = 9**, todos de ciclômetro. Nenhum medidor de ponteiro entrou na amostra
  de legíveis. O 0% nessa cena é indicativo, não uma estimativa.
- **Gabarito conferido por IA**, não por pessoa (ver acima). As 58 ilegíveis foram excluídas de
  forma conservadora. Um leitor humano mais tolerante incluiria algumas delas, e elas seriam as
  mais difíceis para o OCR, o que só baixaria os números.
- **Recorte manual** marcado sobre uma grade de 40 px, com margem de 10%. Uma caixa frouxa inclui o
  código `03` do LCD e a unidade. A regra `escolher_leitura` descarta textos com menos de 4 ou mais
  de 7 dígitos.
- **OCR de prateleira, sem ajuste fino.** O spike não mede um reconhecedor treinado nestes
  displays (CRNN dedicado). Mas a tabela do plano diz justamente que, abaixo de 40%, a saída não é
  sofisticar o modelo. E os dígitos ambíguos mesmo na leitura ampliada limitam o que qualquer
  modelo tira destes pixels.

## Recomendação

1. Pedir ao cliente as fotos na resolução original do aparelho do leiturista, ou pelo menos o
   dobro da atual no lado menor. O argumento: na resolução atual, um dígito tem da ordem de 15 a
   20 px de largura, e 39% das fotos que pessoas marcaram como legíveis têm algum dígito que não se
   lê com segurança.
2. Não abrir a Fase 3 de OCR com estas imagens. Se o cliente mandar fotos maiores, repetir este
   spike com o mesmo script: `amostrar` → conferência → `rodar`. O gabarito novo vai para o mesmo
   CSV, fora do git.
3. As 5 divergências substantivas em 92 fotos (5,4%, IC95% 2,3% a 12,1%) indicam que a auditoria
   visual tem valor mesmo sem OCR. Esse número também depende da revisão humana do gabarito. A
   triagem de qualidade (marcos M5 e M7) mais a revisão humana continua sendo o caminho da Fase 2.

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
uv run --group ocr python scripts/m9_ocr.py criterios         # altura x confianca no EasyOCR
```

A pasta das fotos vem de `--base` ou `M9_BASE` (padrão: `Base cliente/` na raiz do repo).
