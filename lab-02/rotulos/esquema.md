# Esquema de rótulos — Korvian (Lab 2)

Cada foto recebe **três rótulos**, na ordem em que o rotulador decide no app (`rotulador/`):

1. **Cena** — uma classe só (exclusiva).
2. **Defeitos** — zero, um ou vários (multirrótulo).
3. **Leitura legível?** — sim ou não.

A unidade rotulada é **a foto** (não a leitura nem o medidor). Fotos `_001`…`_008` da mesma leitura são rotuladas uma a uma.

## Regras de consistência (aplicadas no app e de novo na consolidação)

| Se a cena for… | Então |
|---|---|
| `nao_medidor` | defeitos = todos 0 e legível = 0 (não há medidor para ter defeito ou ser lido) |
| `ocorrencia` | legível = 0 (o medidor não está visível); defeitos continuam marcáveis (ex.: reflexo na tampa fechada) |
| `digital` ou `eletromecanico` | legível é escolha obrigatória do rotulador; o valor imposto por uma cena anterior é apagado ao trocar de cena |

---

## 1. Cena (exclusiva — softmax)

| Classe | Tecla | Definição operacional | Por que é exclusiva |
|---|:--:|---|---|
| `digital` | 1 | Aparece um **medidor eletrônico com display** (LCD/segmentos), mesmo que o display esteja ilegível, apagado ou parcialmente fora do quadro | Um medidor é eletrônico **ou** eletromecânico; a foto mostra um medidor de cada vez |
| `eletromecanico` | 2 | Aparece um **medidor eletromecânico**: registrador de roletes (ciclométrico) ou ponteiros/disco giratório | Idem |
| `ocorrencia` | 3 | A foto mostra o **contexto da leitura** (caixa de medição, padrão de entrada, fachada, portão, poste), **mas nenhum medidor está visível** (tampa fechada/opaca, foto de longe, acesso impedido) | Se o medidor aparece, a cena é 1 ou 2; se não aparece mas há contexto, é 3 |
| `nao_medidor` | 4 | Nada relacionado à medição: chão, pessoa, documento, tela preta, objeto aleatório | Ausência de contexto de medição |

### Exemplos-limite

> Os exemplos-limite de cena abaixo são **casos reais em que dois integrantes discordaram** na dupla rotulagem (os exemplos vêm do par Davi × Elizabete, 83 fotos em comum; detalhes no `README.md`). Por regra de dados do cliente, as fotos **não** são reproduzidas: cada caso é descrito em texto.

| Classe | Quase é (fica dentro) | Quase não é (fica fora) |
|---|---|---|
| `digital` | **Caso real:** medidor Elster em carcaça antiga, redonda e escura, com **display LCD de duas linhas**. Um rotulador marcou `eletromecanico` pela carcaça; o certo é `digital`, porque quem define é o mostrador, não a carcaça | **Caso real:** medidor de aparência moderna (etiqueta, código de barras, visor retangular) cujos dígitos ficam em **roletes pretos com números brancos**. Um rotulador marcou `digital`; o certo é `eletromecanico` |
| `eletromecanico` | O caso dos roletes acima: roletes mecânicos atrás de um visor retangular continuam sendo `eletromecanico` | O caso do Elster acima: segmentos de LCD em carcaça antiga são `digital` |
| `ocorrencia` | **Caso real:** caixa de medição escondida atrás de plantas; dá para ver a caixa, mas não o medidor. Um rotulador marcou `eletromecanico` e o outro `ocorrencia`. O certo é `ocorrencia` | Mesma caixa com o medidor visível pelo visor, ainda que embaçado → `digital`/`eletromecanico` + `tampa_suja` |
| `nao_medidor` | Foto do chão, do céu ou de um objeto, sem nenhum elemento da residência ou da medição | **Caso real:** portão de grade com um aviso de papel preso. Um rotulador marcou `nao_medidor` e o outro `ocorrencia`. O certo é `ocorrencia`, porque o portão é o contexto da visita (acesso impedido) |

---

## 2. Defeitos (multirrótulo — uma sigmoide por defeito)

Os defeitos **coexistem**: uma foto pode ter reflexo **e** estar fora de foco. Por isso não podem ser uma softmax.

| Defeito | Tecla | Definição operacional | Exemplo-limite: quase é | Exemplo-limite: quase não é |
|---|:--:|---|---|---|
| `reflexo` | Q | Brilho de céu, sol ou lâmpada **sobre o vidro/tampa** que cobre parte do display ou da etiqueta | Reflexo de árvore/céu atravessando o display, dígitos ainda visíveis → marcar | Brilho só na borda da caixa, longe do display → não marcar |
| `fora_de_foco` | W | A imagem está **borrada** (desfoque ou tremido) a ponto de as bordas dos dígitos não serem nítidas | Dígitos legíveis com esforço, bordas "fantasma" → marcar | Leve granulação/ruído de baixa luz com bordas definidas → não marcar |
| `tampa_suja` | E | Tampa/visor **opaco, sujo, embaçado ou riscado** que reduz o contraste do que está atrás | Visor amarelado com manchas, display visível mas lavado → marcar | Tampa limpa com reflexo → é `reflexo`, não `tampa_suja` |
| `enquadramento` | R | O display está **cortado, muito longe ou muito inclinado** para ser lido, embora o medidor apareça | Medidor ocupa menos de ~1/4 da foto e o display tem poucos pixels → marcar | Medidor um pouco torto, display inteiro e grande → não marcar |
| `display_apagado` | T | O display do medidor eletrônico **não mostra dígitos** (sem energia, apagado) | Segmentos muito fracos, quase invisíveis mesmo sem reflexo → marcar | Display aceso mas encoberto por reflexo → é `reflexo` |

---

## 3. Leitura legível? (binário — sigmoide)

| Valor | Tecla | Definição operacional |
|---|:--:|---|
| `1` (sim) | S | O rotulador **consegue ler todos os dígitos** do registro de consumo, sem chutar nenhum |
| `0` (não) | N | Pelo menos um dígito é ilegível, ou não há registro visível |

Este é o rótulo que alimenta a decisão do sistema (ACEITA / REJEITADA / DÚVIDA). Os defeitos explicam **por que** uma foto não é legível; o legível diz **se** ela serve.

- Quase é legível (fica `1`): todos os dígitos legíveis apesar de reflexo parcial.
- Quase não é legível (fica `0`): cinco dígitos claros e o sexto encoberto. Um dígito errado é uma fatura errada, então na dúvida o rótulo é `0`.

---

## 4. Kappa da rodada 1 e o que mudou no esquema (v1 → v2)

Kappa de Cohen entre os **três integrantes**, nas fotos de controle e nas demais fotos em comum: média ponderada dos três pares (Davi × Elizabete 83 fotos, Davi × Ivan 56, Elizabete × Ivan 86; 225 pares-foto):

| Classe | Kappa | Situação |
|---|--:|---|
| Cena | **0,74** | ✅ ≥ 0,6 |
| Legível | **0,82** | ✅ ≥ 0,6 |
| Reflexo | 0,41 | ❌ ambígua |
| Fora de foco | 0,35 | ❌ ambígua |
| Tampa suja | 0,15 | ❌ ambígua |
| Enquadramento | 0,35 | ❌ ambígua |
| Display apagado | 0,18 | ❌ ambígua (19 positivos no total; só 3 fotos com positivo entre as 83 em comum de Davi × Elizabete) |

Por par: Davi × Elizabete tem cena 0,78 e legível 0,81; Davi × Ivan, cena 0,71 e legível 0,82; Elizabete × Ivan, cena 0,73 e legível 0,82. A tabela completa por par está no `README.md`.

Os cinco defeitos ficaram abaixo de 0,6. Na análise das 83 fotos em comum entre Davi e Elizabete, as discordâncias têm causas repetidas, e cada uma virou uma regra na **v2** do esquema:

| Classe | O que causou a discordância (casos reais) | Mudança na v2 |
|---|---|---|
| `reflexo` | Um brilho pequeno no canto do display, com todos os dígitos visíveis, foi marcado por um rotulador e não pelo outro | Só marcar se o reflexo **cobre pelo menos um dígito ou o número de série**. Brilho fora dessas áreas não conta |
| `fora_de_foco` | O desfoque leve é contínuo e cada um tem um ponto de corte diferente | Teste operacional: **dá para contar os segmentos de cada dígito?** Se não dá, marcar |
| `tampa_suja` | Confundida com reflexo e com névoa da foto | Só marcar se a **opacidade está na tampa** (manchas, amarelado, riscos fixos). Brilho que muda com o ângulo é `reflexo` |
| `enquadramento` | Medidor visto de longe ou por uma janela redonda pequena: um achou "pequeno mas inteiro" e o outro "longe demais" | Marcar se o **display ocupa menos de 1/10 da largura da foto**, ou está cortado, ou inclinado a ponto de não dar para ler em linha |
| `display_apagado` | Foto muito embaçada em que não se vê o display: um marcou apagado, o outro não | Só marcar se o **display está visível e sem nenhum segmento aceso**. Se não dá para ver o display, a causa é `tampa_suja`/`fora_de_foco`, não `display_apagado` |
| `legivel` | Um caso com `display_apagado` = 1 e legível = 1 | Nova regra de consistência: `display_apagado = 1` implica `legível = 0` |
| Cena: `digital` × `eletromecanico` | 5 fotos: carcaça antiga com LCD, ou roletes atrás de visor moderno | **O mostrador define a cena**: segmentos LCD → `digital`; roletes ou ponteiros → `eletromecanico`. A carcaça não importa |
| Cena: `ocorrencia` × `nao_medidor` | Portão com aviso de papel | Elemento da residência visitada (portão, fachada, muro, caixa) → `ocorrencia` |

**Consequência para a arquitetura (ver `ARQUITETURA.md`, seção 9):**
- A decisão ACEITA/REJEITADA/DÚVIDA usa **só a cabeça de legível** (κ = 0,82). Os defeitos (κ < 0,6) entram apenas como **motivo** exibido ao analista.
- Treinar e decidir com um rótulo de κ = 0,15 seria aprender o ruído entre rotuladores.

**Pendente (antes do Marco 2):** uma rodada 2 de rotulagem com a v2, sobre as mesmas 50 fotos de controle, para medir de novo o kappa dos defeitos. Neste laboratório, os rótulos usados são os da rodada 1, rotulada com a v1.

## 5. Quem rotulou

| Rotulador | Fotos | Observação |
|---|--:|---|
| Davi Melo (integrante) | 100 | Viu as 50 fotos de controle |
| Elizabete (integrante) | 300 | Viu as 50 fotos de controle. A cópia do app dela tinha uma coluna extra `outros` (14 marcações), que não faz parte do esquema e foi ignorada na consolidação |
| Ivan (integrante) | 100 | Viu as 50 fotos de controle |

Total: 500 rótulos válidos sobre **329 fotos distintas** (163 de treino, 82 de validação e 84 de teste).

## 6. Como os rótulos foram coletados

- **App local** (`rotulador/`), um CSV por pessoa, sem acesso aos rótulos dos outros.
- **Pool:** 400 fotos estratificadas, 100 por lote; em cada lote, metade com nota de ocorrência e metade sem.
- **Dupla rotulagem às cegas:** 50 fotos de controle, 12 a 13 por lote, aparecem a todos os rotuladores na mesma ordem e intercaladas, 1 a cada 2. O rotulador não sabe quais são. É sobre elas que se mede o **kappa de Cohen** por classe.
- **Consolidação** (`rotulador/consolidar.py`):
  - rótulo por maioria;
  - empate vira `NA` e a foto fica fora do treino daquela cabeça;
  - as regras de consistência acima são reaplicadas.
