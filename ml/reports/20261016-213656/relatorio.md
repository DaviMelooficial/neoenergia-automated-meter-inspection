# Modelo de qualidade `qualidade@v1` (marco M7), rodada 20261016-213656

Data: 2026-10-16. MobileNetV3-Small (ImageNet) com cabeças de cena, defeitos e legível
(`ml/src/mlinsp/modelo.py`), treinado em dois estágios por `mlinsp.train_quality`, calibrado
por `mlinsp.calibrate`, avaliado por `mlinsp.evaluate` e exportado por `mlinsp.export_onnx`.
Reprodutível com `make m7-treinar` e depois `make m7-calibrar|m7-avaliar|m7-exportar|m7-paridade
RODADA=20261016-213656`. Os números abaixo saem de `treino.json`, `calibracao.json`,
`avaliacao.json`, `qualidade.json` e `paridade_backend.json`, todos nesta pasta.

## Resumo

| Item | Resultado |
| --- | --- |
| Critério de entrada (E1 + E2 + E3) | **Atendido pela semente escolhida (0)**. Ver a ressalva da seção 6 antes de ativar |
| E1: macro-F1 de legível no teste (03/07, 83 fotos) | modelo **0,766** × heurística 0,651 (média das 3 sementes: 0,785 ± 0,018) |
| E2: falso positivo no teste no limiar calibrado | **0** em 3 rejeitadas (IC 95% de Wilson da precisão: 0,44 a 1,00) |
| E3: cobertura de rejeição na base inteira (11.419 leituras) | **3,72%** (425) × 0,76% (87) da heurística |
| Limiar (`politica@v2`) | `ilegivel >= 0,976`; `limiar_aprova` = None |
| Reprodutibilidade | semente 0 treinada duas vezes: logits de validação e teste **idênticos** (diferença 0,0) |
| Paridade torch × onnxruntime (100 fotos do teste) | logit de legível 3,9e-5, cena 3,1e-5, probabilidades < 1e-5 (critério < 1e-4) |
| Paridade de ponta a ponta (backend × torch, 11.419 fotos) | máx. 6,0e-6 em `ilegivel`; **0 decisões diferentes** (425 × 425) |
| Tempo de treino | 12,4 min para 3 sementes + 1 repetição (MPS, Apple M5); cerca de 2,5 a 3,5 min por semente |
| Latência no backend | 7,2 ms p50, 9,1 ms p95 por foto (onnxruntime CPU, 1 thread por chamada, 8 em paralelo) |
| `.onnx` no git | **Não** (ver seção 5). sha256 `ab653b59e90e…` registrado em `qualidade.json` |

## 1. Dados e rótulos

- Partição por lote (`particao.py`): treino 20 e 21/05, validação 22/05, teste 03/07. Nenhuma foto
  ou medidor em comum entre conjuntos (prova do Lab 2).
- Rótulos humanos do Lab 2 (`lab-02/rotulos/rotulos.csv`), consolidados por maioria com a regra do
  app (`rotulador/consolidar.py`): 163 fotos de treino (90 legíveis, 68 ilegíveis, 5 empates),
  82 de validação (43 e 39) e 84 de teste (46, 37 e 1 empate). Os empates ficam fora da perda e da
  métrica de legível.
- **Só cena (kappa 0,74) e legível (kappa 0,82) entram na perda.** O rótulo humano de defeito
  (kappa 0,15 a 0,41) não é alvo em nenhum estágio, conforme a regra do plano de não treinar
  decisão sobre rótulo ambíguo.
- Falso positivo = foto auto-rejeitada que **algum** rotulador leu (`legivel_algum`), o mesmo
  critério do M5.

## 2. Treino

**Estágio 1, pré-treino sintético.** As fontes são 2.500 fotos do treino que a heurística do M5
considerou nítidas (`p_ruim <= 0,05`). Cada uma gera uma variante limpa (só recompressão JPEG) e uma
degradada com 1 ou 2 defeitos (`degradacao.py`), o que dá 5.000 exemplos com cerca de 750 positivos
por defeito. O único alvo é a cabeça de defeitos ("qual degradação foi aplicada"). Legível e cena
ficam sem alvo, porque a foto limpa não é necessariamente legível. AdamW com lr 3e-4 e cosseno,
6 épocas, flip horizontal e jitter de ±10% de brilho e contraste.

**Estágio 2, ajuste fino.** Os 163 rótulos humanos do treino (cena e legível), com o extrator em
lr 5e-5 e as cabeças em 1e-3. Cada lote humano vai intercalado com um lote sintético (peso 0,5) para
a cabeça de defeitos não perder o significado. O early stopping usa a macro-F1 de legível na
validação, com mínimo de 10 épocas, paciência 5 e teto de 30.

Rodada anterior descartada (`20261016-212320`, que só tem `treino.json` e curvas): com paciência 3,
como no plano, duas sementes pararam na 1ª e na 5ª época, com macro-F1 de validação de 0,61 e 0,65.
O ruído da macro-F1 com 82 fotos é de ±0,03 entre épocas. A configuração foi ajustada olhando só a
validação. O teste só foi aberto na rodada final.

| Semente | Época escolhida | Macro-F1 validação | Tempo |
| --- | --- | --- | --- |
| 0 | 19 de 24 | 0,829 | 157 s |
| 1 | 19 de 24 | 0,804 | 193 s |
| 2 | 21 de 26 | 0,801 | 213 s |
| 0 (repetição) | 19 de 24 | 0,829 | 177 s |

![curvas](curvas_treino.png)

## 3. Calibração (validação)

A temperatura de legível minimiza a NLL na validação. O score de decisão é
`p_ilegivel = 1 - sigmoid(z / T)`. O limiar é a maior cobertura de rejeição com **zero** foto lida
na validação: é o menor `p_ilegivel` rejeitado corretamente acima da foto lida mais alta,
**arredondado para cima em 3 casas**. Sem o arredondamento, o limiar seria o `p` exato de uma foto
da validação, e o ruído numérico entre MPS, CPU e onnxruntime (~1e-5) decidia para que lado ela
caía: na primeira exportação, backend e torch divergiram em 1 foto de 11.419. Arredondar para cima
é o lado conservador.

| Semente | T | Foto lida mais alta | Limiar | Rejeitadas na validação | ECE antes → depois |
| --- | --- | --- | --- | --- | --- |
| **0 (escolhida)** | 1,023 | 0,9613 | **0,976** | 3 de 82 | 0,036 → 0,035 |
| 1 | 1,226 | 0,9874 | nenhum (nada rejeitável sem FP) | 0 | 0,080 → 0,073 |
| 2 | 1,330 | 0,8666 | 0,889 | 8 | 0,110 → 0,145 |

Critério de escolha: maior macro-F1 de legível na validação.

## 4. Avaliação no teste (03/07) e comparação com a heurística

A V0 é a `heuristica@v1`, com o `p_ruim` que o worker gravou no banco (exportação de
`make relatorio-heuristicas`). O limiar de macro-F1 dela foi escolhido na validação (0,070) e a
rejeição usa a `politica@v1` (0,85). Conjunto: as 84 fotos rotuladas do teste, todas no banco;
83 têm legível sem empate.

| Métrica (teste) | qualidade@v1, semente 0 | média ± desvio, 3 sementes | heurística V0 |
| --- | --- | --- | --- |
| macro-F1 legível | **0,766** | 0,785 ± 0,018 | 0,651 |
| F1 legível / F1 ilegível | 0,800 / 0,732 | | 0,714 / 0,588 |
| PR-AUC (positivo = ilegível) | 0,867 | 0,858 ± 0,008 | 0,624 |
| AUROC | 0,872 | 0,866 ± 0,013 | 0,670 |
| acurácia (IC 95% Wilson) | 0,771 (0,670 a 0,848) | | 0,663 (0,556 a 0,755) |
| matriz [real legível: prev. leg, prev. ileg] | [38, 8] | | [35, 11] |
| matriz [real ilegível: prev. leg, prev. ileg] | [11, 26] | | [17, 20] |
| rejeitadas no limiar / falsos positivos | 3 / **0** | FP por semente: 0, 0, 2 | 1 / 0 (política v1) |
| cobertura máxima com zero FP (oráculo) | 16,7% | | 4,8% |
| macro-F1 de cena (informativo) | 0,386 | 0,355 ± 0,033 | n/d |

![cobertura x risco](cobertura_risco_teste.png)

**Cena.** F1 de 0,90 em digital (70 fotos), 0,14 em eletromecânico (9), 0,50 em ocorrência (3) e
0,00 em não-medidor (1). Com essas contagens, cena fica só como informação.

**Defeitos (informativo, nunca no `p_ruim`).** A cabeça aprendeu com degradação sintética e foi
medida contra a maioria humana de kappa baixo:

| Defeito | positivos / n | F1 | AUROC |
| --- | --- | --- | --- |
| reflexo | 39 / 80 | 0,14 | 0,56 |
| fora_de_foco | 29 / 77 | 0,63 | 0,75 |
| tampa_suja | 14 / 80 | 0,24 | 0,67 |
| enquadramento | 20 / 79 | 0,33 | 0,64 |
| display_apagado | 4 / 84 | 0,00 | 0,62 |

Só `fora_de_foco` carrega sinal. O resto confirma a decisão de mantê-los fora da política.

**Base inteira, mesmas 11.419 leituras do M5.** O modelo rejeita 425 (3,72%), contra 87 (0,76%) da
heurística. Por lote:

| Lote | 200526 | 210526 | 220526 | 030726 |
| --- | --- | --- | --- | --- |
| rejeitadas (de) | 117 (2.922) | 98 (2.881) | 107 (2.821) | 103 (2.795) |

Concordância com a V0: 70 rejeitadas pelos dois, 355 só pelo modelo e 17 só pela heurística.

## 5. Exportação e o `.onnx` fora do git

- `ml/artifacts/qualidade.onnx` (3,7 MB, opset 17, lote dinâmico, entrada `float32[N,3,224,224]`
  normalizada, saídas em logit) e `ml/artifacts/qualidade.json`, com o contrato: pré-processamento,
  temperaturas, limiar, mapeamento defeito → classe e sha256. Uma cópia do json está nesta pasta.
- **Decisão: o `.onnx` não vai para o git.** `ml/artifacts/` já é ignorado. É binário, muda a cada
  treino, deriva de pesos ImageNet de terceiros e foi ajustado com fotos do cliente. O hash
  versionado aqui (`ab653b59e90e5e8e16637bc5ead99f1beac988df693291f724459b8e53304b72`) é o elo: o
  `DetectorOnnx` recusa subir com um `.onnx` cujo sha256 não bate com o json.
- Paridade na exportação: diferença máxima de logit de 3,9e-5 em legível, 3,1e-5 em cena e 1,1e-4
  em defeitos. Os logits de defeito chegam a |12|, e o ruído de float32 do onnxruntime neles passa
  de 1e-4 mesmo contra o torch em float64. Em probabilidade, que é o que o backend grava, a
  diferença fica abaixo de 1e-5 em todas as saídas. O critério (< 1e-4) vale para os logits de
  legível e cena e para as probabilidades de todas as saídas.
- Ponta a ponta (`make m7-paridade`): o caminho real do worker (JPEG, `decodificar`,
  `DetectorOnnx`) contra o torch nas 11.419 fotos dá diferença máxima de 6,0e-6 em `ilegivel` e
  nenhuma decisão diferente.
- O campo `commit` do json aponta para `a00f1e1`, o HEAD no momento da exportação. O código do M7
  ainda não estava commitado; ele entra nos commits `feat(M7)` desta branch.

## 6. Decisão de entrada e a ressalva

Os três critérios combinados com o Davi passam na semente escolhida. Pela regra, o `qualidade@v1`
entra. Três fatos limitam o quanto o zero falso positivo vale:

1. **O zero FP do teste repousa em 3 rejeições.** O limite superior de 95% do erro é 63%.
2. **Entre sementes, o zero FP não se mantém.** A semente 2, calibrada do mesmo jeito, rejeita 12 no
   teste com 2 falsos positivos. A semente 1 não acha limiar nenhum. A fronteira da rejeição sem FP
   é estreita: na semente 0, a foto lida mais alta da validação tem 0,961 e o limiar é 0,976.
3. **As 355 rejeições que só o modelo faz na base nunca foram vistas por um humano.**

Recomendação: o código fica pronto e a ativação é uma linha de configuração
(`DETECTOR_QUALIDADE=onnx`). O worker passa a usar a `politica@v2` sozinho, e a `heuristica@v1`
continua intacta. Antes de ligar a auto-rejeição do modelo, vale a mesma inspeção humana pedida no
M5, aplicada às 425 rejeitadas, ou a uma amostra delas que inclua as 355 que a heurística não pega.
O padrão do código continua `heuristica`. A troca é decisão do Davi.

## 7. Reprocessamento dos 4 lotes no banco (2026-10-17)

Banco de dev restaurado, revisão `1840bea5fdb9`, dump prévio em scratchpad. Comando:
`DETECTOR_QUALIDADE=onnx`, `make triagem-todos` e 4 workers no host, que subiram com
`detector=qualidade@v1, politica=politica@v2`. Em seguida, `make recalcular-todos POLITICA=politica@v1`
para devolver a operação à heurística, que continua sendo o detector padrão. Os resultados
`qualidade@v1` ficam gravados em `resultados_inferencia` ao lado dos `heuristica@v1`.

| | qualidade@v1 + politica@v2 | heuristica@v1 + politica@v1 |
| --- | --- | --- |
| tempo (4 workers, 11.419 leituras) | 41,4 s do primeiro claim ao último job | 41,5 s (M5) |
| latência gravada por foto (decodificação + inferência) | média 5,4 ms, p95 6 ms | média 3,6 ms, p95 5 ms |
| rejeitada_qualidade 200526 / 210526 / 220526 / 030726 | 117 / 98 / 107 / 103 | 12 / 22 / 32 / 21 |
| rejeitada_qualidade, total | **425 (3,72%)** | 87 (0,76%) |
| fila_humana | 10.994 | 11.332 |

As contagens por lote do banco batem uma a uma com as calculadas offline na seção 4. Cruzamento no
banco: 70 rejeitadas pelos dois, 355 só pelo modelo, 17 só pela heurística, 10.977 por nenhum.
O `recalcular_decisao` de volta para a v1 levou cada lote ao estado do M5 sem abrir imagem
(87 rejeitadas, 11.332 na fila), e as 55 decisões humanas não foram tocadas.

## 8. Pendências

- Inspeção humana das rejeições do modelo antes de ligar `DETECTOR_QUALIDADE=onnx` (seção 6).
- Os testes do M3 que falhavam sob carga (`test_reserva_expirada...` e
  `test_falhar_reagenda...`) foram corrigidos no commit `37273e3`: os dois comparavam o relógio do
  host com o `now()` do banco. Também apareceu uma vez, ao sair do pytest, um abort
  `libc++abi: recursive_mutex lock failed`, com todos os testes passando. Ele não se repetiu em 5
  execuções seguidas e fica em observação.
