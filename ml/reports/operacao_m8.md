# M8: operação, limiares e drift

Medido em 2026-10-17 no banco `inspecao_m8` (cópia do `inspecao_m6`, migrado até `e4b7c2a9d815`),
política ativa `politica@v1` sobre `heuristica@v1`. Só agregados: nenhuma leitura ou número de medidor.
O código está em `app/backend/src/inspecao/monitoramento/`, `auditoria.py` e `contas_teste.py`.

## Decisão pendente de confirmação do Davi

**Só o papel `auditor` faz auditoria amostral.** Supervisor e admin recebem 403 em `/auditoria/*`. Motivo: eles
leem as métricas, e qualquer agregado que separe decisões (por lote, por status, por par) entregaria a quem audita
a decisão anterior. Com quem audita fora das métricas, essa classe de vazamento fecha inteira, em vez de cada
agregado ser remendado. É reversível: troca de uma condição em `routers/auditoria.py` e no `test_rbac.py`. Decisão
tomada pelo coordenador no re-gate.

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
0,8%, com erro-padrão de ~0,17 p.p. por lote; ela teria de passar de 11% para alertar.

**phi (sobredispersão), escolha declarada:** phi = qui-quadrado de Pearson / (k − 1) entre os k lotes da baseline,
mínimo 1, e **só é aplicado com k ≥ 5 lotes** (`MIN_LOTES_PHI`). Com os 3 lotes de maio o phi estimado da
auto-rejeição é 4,95 (qui-quadrado ≈ 9,9 com 2 g.l., p ≈ 0,007), mas com 2 g.l. o intervalo de 95% dele vai de
~1,3 a ~195: aplicá-lo deixaria o limite de alerta oscilando a cada lote novo (~1,87% de auto-rejeição contra
~1,27% com phi = 1). Por isso hoje a carta usa phi = 1 e o endpoint publica o estimado à parte
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

- Taxa `AUDITORIA_TAXA` = 0,07 (aceita 0,05 a 0,10). Sorteio determinístico por leitura (sha256 com o salt
  secreto do servidor); decisão de conta de teste não é sorteada.
- Três origens, mesma taxa: `fila` (decisão de auditor, sorteada na transação da decisão), `auto_aprovada` e
  `auto_rejeitada` (decisões da máquina, sorteadas por `make auditoria-sortear`). As auto-rejeitadas
  (`rejeitada_qualidade`) são a única decisão automática ligada hoje: o segundo revisor aprovar uma delas é um falso
  positivo, e o bloco `auto_rejeitadas` de `/metricas/auditoria` é a medida, em operação, da precisão da
  auto-rejeição (alvo ≥ 0,99, doc 04 §5). Auto-aprovadas: hoje 0.
- No `inspecao_m8`: o sorteio pegou 5 das 87 auto-rejeitadas, nenhuma decidida; 0 da fila (não há decisão de conta
  real).
- Quem audita: só o papel `auditor` (decisão pendente acima). Nunca o autor da decisão (POST e banco).
- Resposta única: toda recusa em `/auditoria/{id}/decisao` e `/auditoria/{id}/liberar` é 404
  `{"detail":"auditoria indisponivel"}`, byte a byte igual para "não existe", "é sua", "pendente sem reserva sua",
  "reservada por outro", "já decidida" e falha de gravação. O 403 de papel depende só de quem chama.
- Cegueira:
  - o item é o mesmo para as três origens e não traz status, histórico, revisão anterior, scores da máquina nem
    id de leitura (nem na quarentena);
  - `GET /leituras/{id}`: para quem não decidiu nem é admin, toda leitura com decisão final (aprovada/reprovada por
    humano, auto-aprovada, auto-rejeitada) vem com status `decidida` e sem revisões, histórico e inferências, em
    auditoria ou não;
  - `GET /lotes`: para quem não é admin, esses mesmos status viram uma chave `decidida`;
  - `POST /fila-humana/{id}/decisao`: recusa de texto constante.
- Imutabilidade: o gatilho `trg_auditorias_decisao_imutavel` recusa UPDATE de revisor, decisão, motivo, observação,
  data, leitura, revisão, revisor original e origem depois de decidida. DELETE continua permitido: a auditoria só
  some junto com a leitura (cascata da reingestão com `--reprocessar`), como a revisão humana que ela confere.
- Locks do sorteio retroativo: cada leitura é travada com SKIP LOCKED num savepoint; o lock só cai no commit, e a
  CLI (`make auditoria-sortear`) faz commit por item.
- `/metricas/auditoria`: supervisor vê só os totais, e cada um só com ao menos 10 pares; contagens, detalhe por
  lote e série por dia (America/Recife, UTC−3) são só de admin.

## Desvios do plano

1. **T-M8-01 com um limiar só.** A política tem só `limiar_rejeita`; o `limiar_aprova` vem calibrado do M7
   (`politica@v2`). O M8 expõe o estado dos limiares, não os escolhe.
2. **Contador "não aplicável".** O aceite pede o número de rótulos contra os 600; o M8 mostra "0 de 600" com o
   motivo, mas declara o contador não aplicável à heurística, que não tem zona de aprovação com erro baixo.
3. **Só o papel auditor audita** (decisão pendente acima).
4. Drift (plano 04, §7.1):
   - a referência é a baseline fixa, não a média móvel dos últimos 5 lotes: só existem 4 lotes, e a média móvel
     misturaria o lote de teste de julho na própria referência;
   - a baseline contém os próprios lotes de maio: o desvio deles é contra um agregado que os inclui (por isso
     ficam perto de zero por construção);
   - "confiança média (p_ruim)" foi definida como a média de max(p_ruim, 1 − p_ruim), e "queda sustentada" como
     cair mais de 0,05 contra a baseline em 2 lotes seguidos; o plano não fixava a medida nem o limiar;
   - carta p como complemento, com phi só a partir de 5 lotes;
   - quando o detector for o do M7, a baseline tem de ser trocada junto: o modelo foi treinado e calibrado em maio,
     e a confiança dele nesses lotes é inflada pelo treino.

As auto-rejeitadas entraram no sorteio; esse desvio não existe mais.

## Para depois do merge com o M7 (registro; ajuste feito na integração)

- `sinais.politica_ativa()` deve usar `politica_para(detector configurado)`, e não `POLITICA_ATUAL`.
- `sinais.classes_ativas` (distribuição das classes no drift) deve usar `politica.chaves`.
- `jobs/cli.py`, `--politica default=POLITICA_ATUAL`, fica resolvido pelo M7.
- `qualidade.LOTES_DE_TESTE` recebe os lotes de teste do detector do M7.
- A baseline do drift troca junto com o detector.

## Fora do escopo do M8

A alternância de tema claro/escuro no cabeçalho não é tarefa do M8: entrou para verificar os gráficos nos dois
temas e pode ficar ou sair sem afetar o resto.
