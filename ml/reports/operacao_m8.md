# M8: operação, limiares e drift

Medido em 2026-10-17 no banco `inspecao_m8` (cópia do `inspecao_m6`, migrado até `e4b7c2a9d815`),
política ativa `politica@v1` sobre `heuristica@v1`. Só agregados: nenhuma leitura ou número de medidor.
O código que calcula tudo está em `app/backend/src/inspecao/monitoramento/` e `auditoria.py`.

## Definições (iguais em todos os endpoints)

| termo | definição |
| --- | --- |
| p_ruim | o que a política calcula sobre `resultados_inferencia.saida` (`Politica.decidir`); na v1, o maior score das 5 classes |
| máquina diz "ilegível" | p_ruim ≥ `limiar_rejeita` da política |
| classe ativa | score da classe ≥ 0,5 |
| zona cinzenta | 0,5 ≤ p_ruim < `limiar_rejeita` |
| zona de aprovação | p_ruim ≤ `limiar_aprova`; sem limiar na política, o candidato do M8, 0,05, que só conta evidência |
| veredito humano de produção | por foto: ilegível se algum rótulo `producao` diz `legivel = false`; legível se todos os definidos dizem `true` |

Resultado usado: o mais recente do detector da política, por foto principal. Nada reabre imagem.

## `/metricas/qualidade`: baseline dos 4 lotes reais

Taxa sobre as leituras com foto (11.474). Buckets: auto-rejeitada = `rejeitada_qualidade` + `auto_rejeitada`.

| lote | com foto | auto-rejeitada | fila humana | aprovada (humano) | reprovada (humano) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 200526 | 2.977 | 0,40% | 97,75% | 0,91% | 0,94% |
| 210526 | 2.881 | 0,76% | 99,24% | 0,00% | 0,00% |
| 220526 | 2.821 | 1,13% | 98,87% | 0,00% | 0,00% |
| 030726 | 2.795 | 0,75% | 99,25% | 0,00% | 0,00% |
| **baseline** | **11.474** | **0,76%** (87) | **98,76%** (11.332) | **0,24%** (27) | **0,24%** (28) |

Por detector e política (última saída de triagem no histórico): `heuristica@v1`/`politica@v1`, 11.419 leituras;
`nulo@v0`/`politica@v0`, 55 (as 55 decididas por auditor antes do M5, que nunca ganharam score heurístico).

Concordância máquina × humano onde há os dois:

| fonte humana | pares | rejeita × ilegível | rejeita × legível | passa × ilegível | passa × legível | concordância | kappa |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| produção (auditores) | 0 | | | | | | |
| Lab 2 (maioria, amostra fechada) | 322 | 3 | 0 | 140 | 179 | 56,5% | 0,023 |

Leitura: a heurística não erra quando rejeita (0 falso positivo no Lab 2), mas deixa passar 140 das 143
ilegíveis. É o comportamento pedido no M5 (só lixo óbvio); kappa baixo é a consequência, não um defeito novo.
Produção tem 0 pares porque as 55 decisões de auditor não têm score do `heuristica@v1`.

## `/metricas/drift`: 5 sinais contra a baseline de maio

Baseline = lotes 200526, 210526 e 220526 (treino e validação do split, `DRIFT_LOTES_BASELINE`), 8.624 fotos
com score, frações somadas sobre as fotos dos 3 lotes.

| lote | fotos | auto-rejeição | confiança média | zona cinzenta | fora de foco ativo | display apagado ativo | discordância | alertas |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| baseline | 8.624 | 0,77% | 0,902 | 3,87% | 4,64% | 1,24% | sem pares | |
| 200526 | 2.922 | 0,41% (−0,4 p.p.) | 0,899 | 3,87% | 4,28% | 1,10% | sem pares | nenhum |
| 210526 | 2.881 | 0,76% (0,0) | 0,901 | 4,06% | 4,82% | 1,18% | sem pares | nenhum |
| 220526 | 2.821 | 1,13% (+0,4) | 0,907 | 3,69% | 4,82% | 1,45% | sem pares | nenhum |
| 030726 (teste, julho) | 2.795 | 0,75% (−0,0) | 0,906 | 3,40% (−0,5) | 4,15% (−0,5) | 0,72% (−0,5) | sem pares | nenhum |

Julho não desvia de maio em nenhum sinal: o maior desvio é −0,5 p.p., vinte vezes abaixo do limiar de alerta.
`glare` ativo é 0% em todos os lotes (a heurística quase nunca dá score ≥ 0,5 para reflexo).

### Critério de alerta

Desvio = valor do lote − valor da baseline.

| sinal | alerta quando |
| --- | --- |
| distribuição das 5 classes (fração com classe ativa) | \|desvio\| > 10 p.p. em qualquer classe |
| taxa de auto-rejeição | \|desvio\| > 10 p.p. em 3 lotes seguidos (este e os 2 anteriores) |
| confiança média, média de max(p_ruim, 1 − p_ruim) | desvio < −0,05 em 2 lotes seguidos (queda sustentada) |
| discordância humano × modelo (pares com rótulo `producao`) | desvio > +10 p.p., com ≥ 30 pares no lote e na baseline |
| zona cinzenta (proxy de fabricante ou aparência nova) | desvio > +10 p.p. |

Lote com menos de 100 fotos com score não alerta (`amostra_insuficiente`) e quebra a sequência de lotes seguidos.
Desvio consciente do plano 04, §7.1: a referência é a baseline fixa, não a média móvel dos últimos 5 lotes, porque
só há 4 lotes e a média móvel misturaria o lote de teste na própria referência.

## `/metricas/auto-aprovacao`: o contador dos 600

`politica@v1` não define `limiar_aprova`: auto-aprovação desligada. Zero erro em n amostras sustenta erro
< 1 − 0,05^(1/n) ≈ 3/n com 95% de confiança; afirmar < 0,5% exige 600.

O que conta: fotos principais distintas com (1) resultado do detector da política, (2) p_ruim na zona de
aprovação, (3) veredito humano de produção. Erro = uma delas com veredito "ilegível"; com qualquer erro o
contador volta a 0. Só rótulos `producao` contam: Lab 2 e rodadas cegas treinaram e calibraram o modelo e não
são evidência fora da amostra.

Hoje: 6.574 fotos na zona (p_ruim ≤ 0,05), **0 de 600** rotuladas. O contador começa a andar com as decisões
de auditor a partir do M8 sobre itens já triados pela heurística.

## Active learning: mapeamento da decisão para o esquema v2

| decisão | legivel | defeito | cena |
| --- | --- | --- | --- |
| aprovada | true | todos nulos | nulo |
| reprovada, motivo de defeito | false | o do motivo = true, os outros nulos | nulo |
| reprovada, `outro` | nulo | todos nulos | nulo |

Motivo → coluna: `fora_de_foco` → fora_de_foco, `glare` → reflexo, `tampa_suja` → tampa_suja,
`enquadramento_incorreto` → enquadramento, `display_apagado` → display_apagado. A migração `e4b7c2a9d815`
aplicou o mesmo mapeamento às 55 revisões existentes: 27 legível, 24 ilegível, 4 nulos (`outro`).

## Auditoria amostral

Taxa `AUDITORIA_TAXA` = 0,07 (aceita 0,05 a 0,10). Sorteio determinístico por leitura (sha256 com o salt
secreto do servidor), na transação da decisão. `make auditoria-sortear` aplicou o sorteio às 55 decisões
anteriores ao M8: 2 sorteadas (3,6%, amostra pequena), 0 decididas, então concordância e kappa ainda nulos.
