# M8: operação, limiares e drift

Medido em 2026-10-17 no banco `inspecao_m8` (cópia do `inspecao_m6`, migrado até `e4b7c2a9d815`),
política ativa `politica@v1` sobre `heuristica@v1`. Só agregados: nenhuma leitura ou número de medidor.
O código está em `app/backend/src/inspecao/monitoramento/`, `auditoria.py` e `contas_teste.py`.

## Contas de teste ficam fora de toda medida humana

As 55 revisões do banco local são de contas de E2E e demo (`auditor.e2e@exemplo.com.br`, 40;
`demo@exemplo.com.br`, 15), **não são decisões de auditor**. `inspecao.contas_teste.DOMINIOS_DE_TESTE`
(`@exemplo.com.br`, `@teste.com.br`, a mesma lista de `ml/src/mlinsp/relatorio_heuristicas.py`) tira essas
contas do backfill de rótulos `producao`, do veredito humano, da concordância da auditoria, das contagens de
aprovada/reprovada por humano (viram o bucket `decidida_conta_teste`) e do sorteio da auditoria. No
`inspecao_m8`, portanto, há 0 rótulos `producao` e 0 decisões de auditor.

Numa primeira medição (antes desta correção) o sorteio retroativo criou 2 auditorias no `inspecao_m8`; as duas
vinham dessas decisões de demo. A migração foi refeita (downgrade e upgrade) e elas não existem mais.

## Definições (iguais em todos os endpoints)

| termo | definição |
| --- | --- |
| p_ruim | o que a política calcula sobre `resultados_inferencia.saida` (`Politica.decidir`); na v1, o maior score das 5 classes |
| máquina diz "ilegível" | p_ruim ≥ `limiar_rejeita` da política |
| classe ativa | score da classe ≥ 0,5 |
| zona cinzenta | 0,5 ≤ p_ruim < `limiar_rejeita` |
| zona de aprovação | p_ruim ≤ `limiar_aprova` **da política**; sem ele, não existe |
| veredito humano de produção | por foto, só contas reais: ilegível se algum rótulo `producao` diz `legivel = false`; legível se todos os definidos dizem `true` |

Resultado usado: o mais recente do detector da política, por foto principal. Nada reabre imagem.

## `/metricas/qualidade`: baseline dos 4 lotes reais

Taxa sobre as leituras com foto (11.474). Auto-rejeitada = `rejeitada_qualidade` + `auto_rejeitada`.

| lote | com foto | auto-rejeitada | fila humana | aprovada (humano) | reprovada (humano) | decidida por conta de teste |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 200526 | 2.977 | 0,40% | 97,75% | 0% | 0% | 1,85% |
| 210526 | 2.881 | 0,76% | 99,24% | 0% | 0% | 0% |
| 220526 | 2.821 | 1,13% | 98,87% | 0% | 0% | 0% |
| 030726 | 2.795 | 0,75% | 99,25% | 0% | 0% | 0% |
| **baseline** | **11.474** | **0,76%** (87) | **98,76%** (11.332) | **0%** | **0%** | 0,48% (55) |

Por detector e política (última saída de triagem no histórico): `heuristica@v1`/`politica@v1`, 11.419 leituras;
`nulo@v0`/`politica@v0`, 55 (as 55 decididas pelas contas de E2E e demo antes do M5, sem score heurístico).

Concordância máquina × humano onde há os dois:

| fonte humana | escopo | pares | rejeita × ilegível | rejeita × legível | passa × ilegível | passa × legível | concordância | kappa |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| produção (auditores reais) | fora da amostra | 0 | | | | | | |
| Lab 2, conjunto de calibração do M5 | **dentro da amostra** | 322 | 3 | 0 | 140 | 179 | 56,5% | 0,023 |

O Lab 2 foi o conjunto em que o limiar 0,85 do M5 foi escolhido com zero falso positivo: o "0 rejeita ×
legível" vale por construção e não é evidência fora da amostra. É pelo mesmo motivo que o Lab 2 não entra no
contador da auto-aprovação. Para detector treinado (M7) a linha do Lab 2 se restringe sozinha ao lote de teste
(fora de `DRIFT_LOTES_BASELINE`). O que o Lab 2 mostra de útil: a heurística deixa passar 140 das 143 ilegíveis.

## `/metricas/drift`: 5 sinais contra a baseline de maio

Baseline = lotes 200526, 210526 e 220526 (treino e validação do split, `DRIFT_LOTES_BASELINE`), 8.624 fotos
com score, frações somadas sobre as fotos dos 3 lotes.

| lote | fotos | auto-rejeição (desvio, z) | confiança | zona cinzenta (desvio, z) | fora de foco | display apagado | discordância | alertas |
| --- | ---: | --- | ---: | --- | ---: | ---: | --- | --- |
| baseline | 8.624 | 0,77% | 0,902 | 3,87% | 4,64% | 1,24% | sem pares | |
| 200526 | 2.922 | 0,41% (−0,4 p.p., z −0,99) | 0,899 | 3,87% (0,0, z −0,02) | 4,28% | 1,10% | sem pares | nenhum |
| 210526 | 2.881 | 0,76% (0,0, z 0,00) | 0,901 | 4,06% (+0,2, z +0,52) | 4,82% | 1,18% | sem pares | nenhum |
| 220526 | 2.821 | 1,13% (+0,4, z +1,01) | 0,907 | 3,69% (−0,2, z −0,51) | 4,82% | 1,45% | sem pares | nenhum |
| 030726 (teste, julho) | 2.795 | 0,75% (0,0, z −0,04) | 0,906 | 3,40% (−0,5, z −1,30) | 4,15% | 0,72% | sem pares | nenhum |

Sobredispersão da auto-rejeição entre os lotes de maio: phi = 4,95 (qui-quadrado 9,9 com 2 g.l., p ≈ 0,007). Nas
outras frações, phi = 1. Julho não desvia de maio em nenhum sinal, nem pelo critério absoluto nem pela carta p.

### Critério de alerta

Desvio = valor do lote − valor da baseline.

| sinal | alerta quando |
| --- | --- |
| distribuição das 5 classes (fração com classe ativa) | \|desvio\| > 10 p.p. em qualquer classe, ou \|z\| > 3 na carta p |
| taxa de auto-rejeição | \|desvio\| > 10 p.p. em 3 lotes seguidos (este e os 2 anteriores), ou \|z\| > 3 na carta p |
| confiança média | desvio < −0,05 em 2 lotes seguidos (queda sustentada) |
| discordância humano × modelo (pares com rótulo `producao`) | desvio > +10 p.p., com ≥ 30 pares no lote e na baseline |
| zona cinzenta (proxy de fabricante ou aparência nova) | desvio > +10 p.p., ou \|z\| > 3 na carta p |

Carta p com sobredispersão: z = (p_lote − p_base) / √(phi · p_base · (1 − p_base) / n_lote), com phi = qui-quadrado
de Pearson / (k − 1) entre os k lotes da baseline (mínimo 1). Lote com menos de 100 fotos com score não alerta
(`amostra_insuficiente`) e quebra a sequência de lotes seguidos.

Por que a carta p existe: **o limiar absoluto de 10 p.p. é cego para taxas de 1% a 5%.** A auto-rejeição fica em
0,8%, com erro-padrão de ~0,17 p.p. por lote; ela teria de passar de 11% para alertar. E os lotes de maio já
diferem entre si além do binomial (o phi de 4,95 acima), que a carta absorve para não alarmar pela variação normal
entre lotes.

### Desvios do plano 04, §7.1

1. A referência é a baseline fixa, não a média móvel dos últimos 5 lotes: só existem 4 lotes, e a média móvel
   misturaria o lote de teste de julho na própria referência.
2. A baseline contém os próprios lotes de maio: o desvio deles é contra um agregado que os inclui (por isso
   ficam perto de zero por construção).
3. "Confiança média (p_ruim)" foi definida como a média de max(p_ruim, 1 − p_ruim), e "queda sustentada" como
   cair mais de 0,05 contra a baseline em 2 lotes seguidos. O plano não fixava nem a medida nem o limiar.
4. Carta p como complemento, pelo motivo acima.
5. Quando o detector for o do M7, a baseline tem de ser trocada junto: o modelo foi treinado e calibrado em maio,
   e a confiança dele nesses lotes é inflada pelo treino.

## `/metricas/auto-aprovacao`: o contador dos 600

`politica@v1` não define `limiar_aprova`: auto-aprovação desligada, e **o contador não se aplica** a ela. Não é só
falta de limiar: a heurística não tem zona de aprovação com erro baixo. No Lab 2, **70 das 194 fotos com p_ruim ≤
0,05 são ilegíveis (36,1%; ao menos 30,3% com 95%, Clopper-Pearson unilateral)**. Esse número aparece no endpoint
e na tela como diagnóstico, não como candidato a limiar. O contador só faz sentido para um detector com
`limiar_aprova` próprio, calibrado (M7).

Regra do contador, quando a política tiver `limiar_aprova`: fotos principais distintas com (1) resultado do
detector da política, (2) p_ruim ≤ `limiar_aprova`, (3) veredito humano de produção de conta real. Erro = uma
delas julgada ilegível. Zero erro em n sustenta erro < 1 − 0,05^(1/n) ≈ 3/n com 95% (o endpoint e a tela mostram
esse limite superior de Clopper-Pearson ao lado do contador); erro < 0,5% exige 600. **Depois de um erro, o
contador fica em 0 para aquele par (detector, limiar) até surgir um limiar ou detector novo.** Só rótulos
`producao` contam: Lab 2 e rodadas cegas calibraram (M5) e treinaram (M7) e não são evidência fora da amostra.

Hoje: contador **0 de 600**, não aplicável.

## Active learning: mapeamento da decisão para o esquema v2

| decisão | legivel | defeito | cena |
| --- | --- | --- | --- |
| aprovada | true | todos nulos | nulo |
| reprovada, motivo de defeito | false | o do motivo = true, os outros nulos | nulo |
| reprovada, `outro` | nulo | todos nulos | nulo |

Motivo → coluna: `fora_de_foco` → fora_de_foco, `glare` → reflexo, `tampa_suja` → tampa_suja,
`enquadramento_incorreto` → enquadramento, `display_apagado` → display_apagado. O backfill da migração
`e4b7c2a9d815` aplica o mesmo mapeamento às revisões de contas reais; no `inspecao_m8` são 0 (as 55 são de teste).

Ordem da fila e viés de seleção: a fila entrega por data de extração do lote e id da leitura, **não por
p_ruim**. Os rótulos de produção não são escolhidos pelo score, mas os primeiros vêm do lote mais antigo
(200526): uma medida por zona de p_ruim feita cedo representa esse lote, não os quatro.

## Auditoria amostral

- Taxa `AUDITORIA_TAXA` = 0,07 (aceita 0,05 a 0,10). Sorteio determinístico por leitura (sha256 com o salt
  secreto do servidor), na transação da decisão; decisão de conta de teste não é sorteada.
- Origens: `fila` (decisão de auditor) e `auto_aprovada` (decisão da máquina, sorteada por
  `make auditoria-sortear`; hoje 0). Quando a auto-aprovação ligar, o segundo revisor mede o erro dela em operação.
- Cegueira: o item não traz status, histórico, revisão anterior nem id de leitura (nem na quarentena). Fora dele,
  `GET /leituras/{id}` mostra a decisão final (aprovada/reprovada por humano, auto-aprovada) só ao autor e a admin;
  para os demais o status vira `decidida`, para TODA leitura decidida, em auditoria ou não, de modo que a resposta
  não serve de oráculo de "foi sorteada". A recusa do `POST /fila-humana/{id}/decisao` tem texto constante.
  Admin vê o detalhe da concordância e, por isso, não audita (como na rotulagem cega).
- Imutabilidade: o gatilho `trg_auditorias_decisao_imutavel` recusa UPDATE de revisor, decisão, motivo,
  observação e data depois de decidida. DELETE continua permitido: a auditoria só some junto com a leitura
  (cascata da reingestão com `--reprocessar`), como a revisão humana que ela confere.
- `/metricas/auditoria`: supervisor vê só os totais, e só com ao menos 10 pares; contagens (sorteadas, pendentes),
  detalhe por lote e a série por dia (America/Recife, UTC−3) são só de admin.
- No `inspecao_m8`: 0 decisões de auditor real, 0 auditorias.

## Para depois do merge com o M7 (registro; ajuste feito na integração)

- `sinais.politica_ativa()` deve usar `politica_para(detector configurado)`, e não `POLITICA_ATUAL`.
- `sinais.classes_ativas` (distribuição das classes no drift) deve usar `politica.chaves`.
- `jobs/cli.py`, `--politica default=POLITICA_ATUAL`, fica resolvido pelo M7.
- A baseline do drift troca junto com o detector (desvio 5 acima).

## Fora do escopo do M8

A alternância de tema claro/escuro no cabeçalho não é tarefa do M8: entrou para verificar os gráficos nos dois
temas e pode ficar ou sair sem afetar o resto.
