# Kappa de Cohen por classe (M6)

Gerado por `make rotulos-kappa` (`python -m inspecao.rotulagem.cli kappa`). Só agregados: nenhum rótulo por foto.

Rodadas consolidadas: lab02. Rótulos: 500; fotos: 329; com dupla rotulagem: 117; com empate na maioria: 39.
Fotos de controle com 2 ou mais rótulos: 50.

## Kappa médio ponderado por n (gate: κ ≥ 0,6)

| classe | κ | n (pares-foto) | gate |
|---|--:|--:|---|
| cena | 0,744 | 225 | passa |
| reflexo | 0,410 | 225 | **não passa** (< 0,6) |
| fora_de_foco | 0,352 | 225 | **não passa** (< 0,6) |
| tampa_suja | 0,148 | 225 | **não passa** (< 0,6) |
| enquadramento | 0,355 | 225 | **não passa** (< 0,6) |
| display_apagado | 0,181 | 225 | **não passa** (< 0,6) |
| legivel | 0,817 | 225 | passa |

## Por par de rotuladores

| par | fotos | cena | reflexo | fora_de_foco | tampa_suja | enquadramento | display_apagado | legivel |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| davi_melo × elizabete_barbosa | 83 | 0,780 | 0,395 | 0,193 | 0,444 | 0,276 | 0,491 | 0,807 |
| davi_melo × ivan | 56 | 0,711 | 0,391 | 0,341 | 0,000 | 0,732 | 0,000 | 0,819 |
| elizabete_barbosa × ivan | 86 | 0,730 | 0,437 | 0,513 | -0,042 | 0,186 | 0,000 | 0,825 |

n/d = kappa indefinido (os dois usaram uma única categoria em todas as fotos).

## Aceite do M6: o que ainda não está cumprido

| rodada | rótulos no banco |
|---|--:|
| lab02 | 500 |
| a | 0 |
| b | 0 |
| desempate | 0 |
| producao | 0 |

- As rodadas A e B com o esquema v2 ainda não rodaram.
- A amostra estratificada de 300 fotos (T-M6-02) não existe.
- 5 de 7 classes ficam abaixo de 0,6 nestas rodadas: reflexo, fora_de_foco, tampa_suja, enquadramento, display_apagado.
  Pelo gate do M6, essas classes não seguem para o treino até uma nova rodada passar.

## Desvios em relação ao plano

1. **Amostra das rodadas A e B = as 329 fotos do Lab 2** (rodada `lab02`). A amostra
   estratificada de 300 fotos do T-M6-02 (lote, nota, faixa do score heurístico) não foi
   construída. As rodadas A e B servem hoje para remedir o esquema v2 sobre o que o Lab 2
   já rotulou, como pede `lab-02/rotulos/esquema.md`.
2. **Desempate cego.** O terceiro rotulador recebe a foto divergente sem ver as duas
   versões, o que contraria a regra 4 do doc 05, seção 4 ("a rodada de desempate é a única
   que mostra as duas versões"). Escolhido para manter a cegueira estrita no servidor; a
   maioria de 3 votos resolve o empate. **Decisão pendente do Davi.**
3. **Sem reserva de foto na rotulagem.** Dois supervisores na mesma rodada podem receber
   a mesma foto ao mesmo tempo; a foto pode ganhar 3 votos ou mais e a dupla deixa de ser
   dupla. A maioria e o kappa por par toleram isso, mas o n de cada par passa a não ser o
   desenho do plano.
4. **Export em CSV, não parquet** (T-M6-08): `ml/artifacts/rotulos.csv` e
   `GET /rotulagem/export` (só admin, porque traz o consolidado por foto). O backend não
   tem pyarrow; o `ml/` converte se precisar. A coluna `lote` é o nome completo do lote
   no banco, não o "220526" do CSV do Lab 2.
5. **Consolidação com as regras v1 de `rotulador/consolidar.py`**, para que o kappa do
   `lab02` bata com `lab-02/resultados/resumo.json`. A regra v2 "display apagado implica
   não legível" vale para os rótulos novos (o servidor recusa com 422); o `lab02` tem 1
   rótulo que a fere.
6. **`POST /rotulagem/proxima`**, e não o `GET` do doc 05, seção 4: segue o doc 03
   (seção 4.2) e a fila humana, e a rota não é cacheável por proxy.
7. **Cegueira também nos agregados: o kappa das rodadas cegas é só para admin, e admin
   não rotula nelas.** Qualquer consulta de consolidação, kappa ou export que inclua `a`,
   `b` ou `desempate` (sozinha ou com `lab02`) é só de admin; supervisor vê só o `lab02`,
   que já fechou. Admin recebe 403 no `/rotulagem/proxima` e no `POST /rotulagem/{id}`
   dessas rodadas. Decidir pelo voto passado não bastava: com n pequeno, o kappa por par
   antes e depois do próprio voto revela o voto do outro, e o terceiro do desempate, que
   nunca votou em a/b, leria os votos antes de receber a foto divergente. A ordem do
   `/proxima` é própria de cada rotulador (md5 de "foto:usuário"), para que um não saiba
   qual foto o outro vai receber.
8. **A migração `c3e1a7d9f204` foi editada no lugar** antes de sair do branch (coluna
   `controle` e índice `uq_rotulos_foto_usuario_dupla_cega`). Banco que já aplicou uma
   versão anterior dela precisa de `alembic downgrade 1840bea5fdb9` e `upgrade head`
   (os rótulos da tabela se perdem; reimportar com `make rotulos-importar`).

## Risco residual

A cegueira é garantida nos endpoints de rotulagem, não no papel: o supervisor também é
auditor e acessa `/leituras` e `/fila-humana`, onde vê posição lida, nota do leiturista,
score e decisões. Um rotulador que abrir a mesma foto por lá deixa de ser cego. Mitigação
possível: rotuladores sem papel de auditor no período das rodadas, ou separar o papel de
rotulador.
