# M8: operação, limiares e drift

Medido em 2026-10-17 no banco `inspecao_m8` (cópia do `inspecao_m6`, migrado até `e4b7c2a9d815`),
política ativa `politica@v1` sobre `heuristica@v1`. Só agregados: nenhuma leitura ou número de medidor.
O código está em `app/backend/src/inspecao/monitoramento/`, `auditoria.py` e `contas_teste.py`.

## Decisões de desenho

Decisão do Davi (2026-10-17), já confirmada: **auto-rejeitadas auditadas a 100%** (`AUDITORIA_TAXA_AUTO_REJEITADA`).

**Só o papel `auditor` faz auditoria amostral.** Supervisor e admin recebem 403 em `/auditoria/*`. Motivo: eles
leem as métricas, e qualquer agregado que separe decisões (por lote, por status, por par) entregaria a quem audita
a decisão anterior. Com quem audita fora das métricas, essa classe de vazamento fecha inteira, em vez de cada
agregado ser remendado. É reversível: troca de uma condição em `routers/auditoria.py` e no `test_rbac.py`. Decisão
tomada pelo coordenador no re-gate, **pendente de confirmação do Davi**.

## Contas de teste ficam fora de toda medida humana

As 55 revisões do banco local são de contas de E2E e demo (`auditor.e2e@exemplo.com.br`, 40;
`demo@exemplo.com.br`, 15), **não são decisões de auditor**. `inspecao.contas_teste.DOMINIOS_DE_TESTE`
(`@exemplo.com.br`, `@teste.com.br`, a mesma lista de `ml/src/mlinsp/relatorio_heuristicas.py`) tira essas
contas do backfill de rótulos `producao`, da escrita de rótulo `producao` (a decisão continua gravada, o rótulo
não), do veredito humano, da concordância da auditoria, das contagens de aprovada/reprovada por humano (viram o
bucket `decidida_conta_teste`) e do sorteio da auditoria. No `inspecao_m8`, portanto, há 0 rótulos `producao` e 0
decisões de auditor.

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
contador da auto-aprovação. O que o Lab 2 mostra de útil: a heurística deixa passar 140 das 143 ilegíveis. A medida
de falso positivo da auto-rejeição em operação vem da auditoria das auto-rejeitadas (abaixo).

Para detector treinado (M7), a linha do Lab 2 só usa os lotes de teste **do próprio detector**
(`monitoramento.qualidade.LOTES_DE_TESTE`, a preencher a partir do contrato do modelo, `qualidade.json`). Detector
treinado sem lotes de teste declarados não tem concordância contra o Lab 2 (n = 0, escopo "pendente da integração
com o M7"), em vez de um recorte adivinhado. **Pendência de integração com o M7.**

## `/metricas/drift`: 5 sinais contra a baseline de maio

Baseline = lotes 200526, 210526 e 220526 (treino e validação do split, `DRIFT_LOTES_BASELINE`), 8.624 fotos
com score, frações somadas sobre as fotos dos 3 lotes.

| lote | fotos | auto-rejeição (desvio, z) | confiança | zona cinzenta (desvio, z) | fora de foco (z) | display apagado (z) | discordância | alertas |
| --- | ---: | --- | ---: | --- | ---: | ---: | --- | --- |
| baseline | 8.624 | 0,77% | 0,902 | 3,87% | 4,64% | 1,24% | sem pares | |
| 200526 | 2.922 | 0,41% (−0,35 p.p., z −1,90) | 0,899 | 3,87% (0,0, z −0,01) | 4,28% (−0,80) | 1,10% (−0,61) | sem pares | nenhum |
| 210526 | 2.881 | 0,76% (0,0, z −0,01) | 0,901 | 4,06% (+0,19, z +0,45) | 4,82% (+0,41) | 1,18% (−0,25) | sem pares | nenhum |
| 220526 | 2.821 | 1,13% (+0,37, z +1,95) | 0,907 | 3,69% (−0,19, z −0,45) | 4,82% (+0,40) | 1,45% (+0,89) | sem pares | nenhum |
| 030726 (teste, julho) | 2.795 | 0,75% (−0,01, z −0,07) | 0,906 | 3,40% (−0,47, z −1,13) | 4,15% (−1,07) | 0,72% (−2,18) | sem pares | nenhum |

z com phi = 1 (ver abaixo) e com a variância da baseline. **Nenhum sinal passa do critério** em nenhum lote. O maior
desvio de julho é de 0,525 p.p. (display apagado, 0,72% contra 1,24%), com z = −2,18; sem o termo da baseline seria
−2,51. Ficar abaixo do critério não é o mesmo que "julho igual a maio": é só que nenhuma diferença chegou ao limiar.

### Critério de alerta

Desvio = valor do lote − valor da baseline.

| sinal | alerta quando |
| --- | --- |
| distribuição das 5 classes (fração com classe ativa) | \|desvio\| > 10 p.p. em qualquer classe, ou \|z\| > 3 na carta p |
| taxa de auto-rejeição | \|desvio\| > 10 p.p. em 3 lotes seguidos (este e os 2 anteriores), ou \|z\| > 3 na carta p |
| confiança média | desvio < −0,05 em 2 lotes seguidos (queda sustentada) |
| discordância humano × modelo (pares com rótulo `producao`) | desvio > +10 p.p., com ≥ 30 pares no lote e na baseline |
| zona cinzenta (proxy de fabricante ou aparência nova) | desvio > +10 p.p., ou \|z\| > 3 na carta p |

Carta p: z = (p_lote − p_base) / √(phi · p_base · (1 − p_base) · (1/n_lote + 1/n_base)). O termo 1/n_base conta a
incerteza da própria baseline; para um lote que está dentro da baseline os dois não são independentes, e o z dele
serve só de referência. Lote com menos de 100 fotos com score não alerta (`amostra_insuficiente`) e quebra a
sequência de lotes seguidos.

Por que a carta p existe: **o limiar absoluto de 10 p.p. é cego para taxas de 1% a 5%.** A auto-rejeição fica em
0,8%, com erro-padrão de ~0,19 p.p. por lote contra a baseline (já com o termo 1/n_base); ela teria de passar de
11% para alertar.

**phi (sobredispersão), escolha declarada:** phi = qui-quadrado de Pearson / (k − 1) entre os k lotes da baseline,
mínimo 1, e **só é aplicado com k ≥ 5 lotes** (`MIN_LOTES_PHI`). Com os 3 lotes de maio o phi estimado da
auto-rejeição é 4,95 (qui-quadrado ≈ 9,9 com 2 g.l., p ≈ 0,007), mas com 2 g.l. o intervalo de 95% dele vai de
~1,3 a ~195: aplicá-lo deixaria o limite de alerta oscilando a cada lote novo (|z| = 3 na auto-rejeição cai em
~2,03% com phi = 4,95 contra ~1,33% com phi = 1, já com o termo 1/n_base). Por isso hoje a carta usa phi = 1 e o endpoint publica o estimado à parte
(`sobredispersao_estimada`). O preço: com phi = 1 a carta pode alarmar pela variação normal entre lotes, que os
lotes de maio mostram existir; o alerta dela é um convite a olhar, não um veredito.

**Pontos cegos declarados:** `glare` tem p_base = 0 (a heurística quase nunca dá score ≥ 0,5 para reflexo), e a
carta não calcula z com p_base 0, então não enxerga glare; `fora_de_foco` e `enquadramento_incorreto` são o mesmo
sinal na heurística (os dois limitados pelo score de foco) e sobem e descem juntos: um alerta num aparece no outro e
conta uma vez só como evidência.

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
Decisão de conta de teste nunca grava rótulo `producao` (`producao.registrar` devolve None).

Ordem da fila e viés de seleção: a fila entrega por data de extração do lote e id da leitura, **não por
p_ruim**. Os rótulos de produção não são escolhidos pelo score, mas os primeiros vêm do lote mais antigo
(200526): uma medida por zona de p_ruim feita cedo representa esse lote, não os quatro.

## Auditoria amostral

- **A auditoria confere uma decisão, não uma leitura.** `fila` aponta a revisão do auditor (`revisao_id`, único);
  `auto_rejeitada` e `auto_aprovada` apontam a linha do histórico em que a máquina decidiu (`historico_id`, único).
  O sorteio é determinístico por decisão: sha256 de "salt:auditoria:<origem>:<id da decisão>" com o salt secreto.
- **Taxas:** fila e auto-aprovadas com `AUDITORIA_TAXA` = 0,07 (aceita 0,05 a 0,10); auto-rejeitadas com taxa
  própria, `AUDITORIA_TAXA_AUTO_REJEITADA` = 1,0 (decisão do Davi, 2026-10-17; aceita 0,05 a 1,0). Decisão de conta de
  teste não é sorteada.
- **Quando sorteia:** a decisão de fila, na transação da decisão; a decisão automática, dentro de
  `estados.transicionar`, no mesmo ponto em que ela é gravada, seja na triagem, no `recalcular_decisao` ou numa
  futura saída automática da `validacao_leitura` (hoje ela só manda para a fila). `transicionar_em_lote` recusa
  esses destinos, para nenhuma decisão automática escapar do sorteio. A CLI sobra para o retroativo (o que já estava
  decidido antes do M8), com commit por item. Colisão no sorteio só é tolerada em `uq_auditorias_historico` (logada
  em WARNING); qualquer outra violação propaga.
- **Identificador opaco:** o item e as rotas `/auditoria/{codigo}/*` usam o `codigo` (uuid4 aleatório) da
  auditoria, nunca o `id` sequencial: as automáticas são sorteadas em rajada na triagem, e um id baixo dentro do lote
  dava a origem (a sonda do gate 4 acertava 37 de 41). A tela não mostra número de auditoria.
- **Reabertura:** `rejeitada_qualidade` é re-decidível. Quando a máquina reabre a leitura (recalcular, troca de
  detector), `estados.transicionar` marca `invalidada_em` na auditoria **pendente** da decisão automática antiga: ela
  sai do `/proxima`, de "pendentes" e de toda medida, e `/metricas/auditoria` (admin) conta as invalidadas. A auditoria
  **decidida** fica: é a medida daquela decisão, que existiu. A decisão nova, inclusive a do auditor se a leitura for
  para a fila, sorteia por conta própria, sem colidir (antes, a unicidade por leitura dava um 409 permanente).
- **Falso positivo da auto-rejeição: unidade e erro.**
  - Unidade: a leitura, dentro de um estrato (detector, política) lido da linha do histórico da decisão (ator e
    `detalhe.politica`). A mesma foto pode ter várias auditorias decididas (reaberta e rejeitada de novo, com a
    auditoria decidida sobrevivendo à reabertura e taxa de 100%): no estrato, só a auditoria decidida **mais
    antiga** conta. No `inspecao_m8` as 87 foram auto-rejeitadas duas vezes pela v1; só a decisão vigente foi sorteada,
    e mesmo que as duas fossem auditadas, contariam uma vez. Estratos diferentes não se somam: na integração com o M7,
    as auditorias da heurística ficam no estrato `heuristica@v1 · politica@v1` e não entram no da v2.
  - O endpoint e a tela mostram o estrato da política ativa; admin vê todos (`estratos_auto_rejeitadas`).
  - Erro: qualquer veredito que não seja "reprovada por uma das 5 classes de qualidade": aprovada, ou reprovada por
    `outro`. (Na auto-aprovada, qualquer reprovada é erro.)
  - O bloco mostra a taxa sempre com o limite superior de 95% (Clopper-Pearson) e o total de leituras auditadas que
    sustentaria precisão ≥ 0,99 com os erros de hoje: **299 sem erro, 473 com 1, 628 com 2** (é o total, não o que
    falta). `alvo_atingido` quando o limite superior é **menor ou igual** a 1%; com 10 auditadas e 0 erro a tela diz
    "falso positivo 0,0% (até 25,9% com 95%)", não "0%".
  - **O estimador tende a subestimar o falso positivo**, por dois caminhos: ancoragem (a maioria das auditorias no
    começo é de auto-rejeitadas, e o revisor tende a confirmar a máquina) e a própria definição (um revisor em
    dúvida que reprova por um defeito de qualidade conta como acerto da máquina). Ler o `alvo_atingido` com isso em
    mente.
- **Tempo para o alvo com taxa de 100%:** os 4 lotes têm 87 auto-rejeições, cerca de 22 por lote. 299 sem erro são
  cerca de **14 lotes no total, contando os 4 que já existem: faltam uns 10**. Com 1 erro, 473 (~22 lotes); com 2,
  628 (~29). O limite real é a **vazão do auditor** (cada auditoria é uma decisão a mais na fila dele), não o sorteio.
- No `inspecao_m8`: 87 de 87 auto-rejeitadas sorteadas (taxa 100%), nenhuma decidida; 0 da fila (não há decisão de
  conta real).
- **Quem audita:** só o papel `auditor` (decisão pendente acima). Nunca o autor da decisão (POST e banco).
- **Resposta única:** toda recusa em `/auditoria/{id}/decisao` e `/auditoria/{id}/liberar` é 404
  `{"detail":"auditoria indisponivel"}`, byte a byte igual para "não existe", "é sua", "pendente sem reserva sua",
  "reservada por outro", "já decidida" e falha de gravação. O 403 de papel depende só de quem chama. Na fila, toda
  falha de gravação da decisão (inclusive a do sorteio) é o 409 de texto constante `item indisponivel para decisao`.
- **Cegueira:**
  - o item é o mesmo para as três origens e não traz status, histórico, revisão anterior, scores da máquina, id de
    leitura (nem na quarentena) nem a contagem exata de restantes (só `ha_mais`);
  - a ordem de entrega é md5(salt:ordem:id da auditoria), com a reserva própria primeiro, e não a ordem do sorteio
    (as automáticas são sorteadas em bloco e chegariam juntas);
  - `GET /leituras/{id}`: para quem não decidiu nem é admin, toda leitura com decisão final (aprovada/reprovada por
    humano, auto-aprovada, auto-rejeitada) vem com status `decidida` e sem revisões, histórico e inferências, em
    auditoria ou não;
  - `GET /lotes`: para quem não é admin, esses mesmos status viram uma chave `decidida`;
  - `POST /fila-humana/{id}/decisao`: recusa de texto constante.
- **Nota e quarentena no item:** nenhuma regra decide pela nota ou pela quarentena (a política só lê o score da
  foto; a consistência só manda para `sem_foto`, que não é triada), então nenhum campo força a origem e nada foi
  tirado. Mas há correlação: no `inspecao_m8`, P111 tem 620 leituras na fila e 0 rejeitadas, T181 tem 2.392 e 1, T111
  tem 16 de 341 rejeitadas (4,7%, contra 0,76% no geral). A nota fica no item porque o revisor precisa dela para
  julgar a coerência; a correlação entra no risco residual abaixo.
- **Imutabilidade:** o gatilho `trg_auditorias_decisao_imutavel` recusa UPDATE de revisor, decisão, motivo,
  observação, data, leitura, revisão, histórico, revisor original e origem depois de decidida; `invalidada_em` só vale
  para pendente (`ck_auditorias_invalidada_pendente`). DELETE continua permitido: a auditoria só some junto com a
  leitura (cascata da reingestão com `--reprocessar`), como a revisão humana que ela confere.
- `/metricas/auditoria`: supervisor vê só os totais, e cada um só com ao menos 10 pares; contagens, invalidadas,
  detalhe por lote e série por dia (America/Recife, UTC−3) são só de admin.

### Riscos residuais (declarados, não resolvidos)

- **Cegueira de origem é parcial no começo.** Hoje 100% das auditorias pendentes vêm da máquina (87 de 87), e as
  primeiras auditorias de produção serão todas de auto-rejeitadas. Em regime a proporção cai: a fila a 7% de ~2.840
  leituras por lote dá ~198 auditorias de fila, contra ~22 automáticas, uns 10%. Um revisor que sabe que as
  automáticas são maioria no começo tende a esperar "reprovada", o que puxa para concordar com a máquina e
  **subestima o falso positivo** (ver a definição de erro acima).
- **404 depois de uma reserva válida.** Se a máquina reabre a leitura enquanto o revisor está com a auditoria, ela é
  invalidada e o POST dele dá a recusa única, o que só acontece com item automático. Mitigado: `_redecidivel` não
  reabre leitura com auditoria pendente reservada (o `recalcular_decisao` e a troca de detector pulam a leitura
  enquanto a reserva vale, 10 min). Resta o caso de a reserva vencer no meio da decisão.
- **Inferência pela observação ao longo do tempo.** O revisor vê lote, linha e medidores no item. Varrendo
  `/fila-humana` ao longo do dia, ele vê quais leituras de um lote estiveram na fila; uma leitura que nunca apareceu
  lá e surge na auditoria é, com alta chance, uma auto-rejeitada. `/leituras/{id}` não ajuda (decisão oculta), mas a
  ausência na fila é informação.
- **Conluio.** Auditor com auditor (um conta ao outro o que decidiu); supervisor com auditor (o supervisor vê
  `por_status` em `/metricas/qualidade` e `/metricas/drift`, que num lote pequeno entrega a decisão de uma leitura);
  admin (vê tudo). O desenho fecha o vazamento por canal técnico para quem audita, não o canal social.
- **Ritmo de chegada.** As automáticas nascem em rajada, quando um lote é triado ou recalculado; itens novos que
  surgem de uma vez logo depois de uma triagem são, com boa chance, automáticos. O identificador opaco e a ordem por
  hash tiram o sinal de cada item, não o do momento.
- **Aparência da foto.** Uma foto obviamente ruim (escura, sem foco) foi provavelmente auto-rejeitada; uma foto boa
  veio provavelmente da fila. É o próprio objeto julgado, e não tem como ser escondido.
- **Nota e quarentena** (ver acima): correlacionadas com a origem, mantidas no item porque o revisor precisa delas.
- **Escopo do que os gates bloqueiam.** O desenho fecha o que revela a decisão ou a origem de forma direta (status,
  histórico, scores, contagens por lote, ids, mensagens de erro, ordem de entrega). O que fica nesta lista é inferência
  estatística fraca ou canal social.

## Desvios do plano

1. **T-M8-01 com um limiar só.** A política tem só `limiar_rejeita`; o `limiar_aprova` vem calibrado do M7
   (`politica@v2`). O M8 expõe o estado dos limiares, não os escolhe.
2. **Contador "não aplicável".** O aceite pede o número de rótulos contra os 600; o M8 mostra "0 de 600" com o
   motivo, mas declara o contador não aplicável à heurística, que não tem zona de aprovação com erro baixo.
3. **Só o papel auditor audita** (decisão pendente acima).
4. **Auditoria decidida de decisão reaberta continua no bloco.** A máquina pode reabrir e decidir de novo a mesma
   leitura; a auditoria já decidida da decisão antiga fica valendo, com a unidade "uma leitura por (detector,
   política), a auditoria decidida mais antiga".
5. **T-M8-07 com duas taxas.** O plano pede 5% a 10% dos itens decididos; a fila segue nessa faixa, mas as
   auto-rejeitadas são auditadas a 100% (`AUDITORIA_TAXA_AUTO_REJEITADA`, decisão do Davi) para medir o falso positivo
   da única decisão automática ligada. As decisões da máquina também entram na auditoria, o que o plano não previa.
6. Drift (plano 04, §7.1):
   - a referência é a baseline fixa, não a média móvel dos últimos 5 lotes: só existem 4 lotes, e a média móvel
     misturaria o lote de teste de julho na própria referência;
   - a baseline contém os próprios lotes de maio: o desvio deles é contra um agregado que os inclui (por isso
     ficam perto de zero por construção);
   - "confiança média (p_ruim)" foi definida como a média de max(p_ruim, 1 − p_ruim), e "queda sustentada" como
     cair mais de 0,05 contra a baseline em 2 lotes seguidos; o plano não fixava a medida nem o limiar;
   - carta p como complemento, com phi só a partir de 5 lotes;
   - quando o detector for o do M7, a baseline tem de ser trocada junto: o modelo foi treinado e calibrado em maio,
     e a confiança dele nesses lotes é inflada pelo treino.

## Migração editada no lugar

A `e4b7c2a9d815` foi editada no lugar quatro vezes durante os gates (a última acrescenta `codigo`) (nunca foi publicada). **Todo banco que aplicou uma
versão anterior dela precisa de `alembic downgrade c3e1a7d9f204` seguido de `alembic upgrade head`**; o upgrade
sozinho não faz nada, porque a revisão já consta como aplicada. O downgrade funciona nas duas versões (os índices saem
com a tabela, sem `drop_index` explícito); ele apaga os rótulos `producao` e as auditorias, e o upgrade recria os
rótulos das revisões reais pelo backfill (as auditorias não; o sorteio as recria com `make auditoria-sortear`). Feito
assim no `inspecao_m8`.

## Para depois do merge com o M7 (registro; ajuste feito na integração)

- `sinais.politica_ativa()` deve usar `politica_para(detector configurado)`, e não `POLITICA_ATUAL`.
- `sinais.classes_ativas` (distribuição das classes no drift) deve usar `politica.chaves`.
- `jobs/cli.py`, `--politica default=POLITICA_ATUAL`, fica resolvido pelo M7.
- `qualidade.LOTES_DE_TESTE` recebe os lotes de teste do detector do M7.
- `estados.transicionar` já sorteia toda decisão automática gravada; com o M7, as decisões da v2 passam por ali sem
  mudança, e caem num estrato próprio (`qualidade@v1 · politica@v2`).
- A baseline do drift troca junto com o detector.

## Fora do escopo do M8

A alternância de tema claro/escuro no cabeçalho não é tarefa do M8: entrou para verificar os gráficos nos dois
temas e pode ficar ou sair sem afetar o resto.
