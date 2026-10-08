# Lab 2 — A arquitetura profunda do Korvian

**Grupo Korvian** · Projeto 4 · Deep Learning · entrega 08/10/2026

| Arquivo | O que é |
|---|---|
| [`ARQUITETURA.md`](ARQUITETURA.md) | A ficha de arquitetura (14 seções do guia), com os números medidos |
| [`lab02.ipynb`](lab02.ipynb) | Notebook executado do zero, com as saídas visíveis (1,7 min com o cache de features pronto; ~4 min na primeira execução) |
| [`rotulos/esquema.md`](rotulos/esquema.md) | Esquema de rótulos, exemplos-limite reais, kappa e o que mudou da v1 para a v2 |
| [`resultados/resumo.csv`](resultados/resumo.csv) | Todos os números-chave em uma tabela (só agregados) |
| [`USO-DE-IA.md`](USO-DE-IA.md) | Registro obrigatório do uso de IA |

O código reaproveitável fica em [`../ml/src/mlinsp/`](../ml/src/mlinsp/), coberto por 23 testes em [`../ml/tests/`](../ml/tests/), e adianta o M7 do plano do projeto. O app de rotulagem fica em [`../rotulador/`](../rotulador/).

## Como reproduzir

As fotos do cliente **não** estão no repositório. O notebook lê de um caminho configurável no topo (`BASE`, por padrão `../Base cliente`).

```bash
make lab02            # ou: bash lab-02/executar.sh
```

O script parte de [`rotulos/rotulos.csv`](rotulos/rotulos.csv), que traz todos os rótulos humanos, uma linha por foto e rotulador:
1. remonta as pastas por rotulador (`rotulador/rotulos/`, que fica fora do git);
2. consolida por maioria e calcula o kappa com `rotulador/consolidar.py`;
3. executa o notebook no lugar (`nbconvert --execute`).

Quem clonar o repositório só precisa da pasta de fotos `Base cliente/` ao lado. Ambiente: `ml/pyproject.toml` + `uv.lock` (Python 3.12, torch 2.13). Os pesos ImageNet da MobileNetV3 são baixados uma vez para `~/.cache/torch`.

---

## 0. A leitura do docente sobre o Korvian

> *"Arquitetura de sistema mais completa da turma (…). Nenhum modelo treinado. Risco: chegar ao Marco 2 com a plataforma pronta e sem rede."*

**Concordamos.** Até este laboratório, a rede estava empurrada para a Fase 2. Este lab tira a rede de lá, com o primeiro bloco treinado, calibrado e comparado com a heurística V0. A conclusão também é a que o nosso próprio plano exige: **com 329 rótulos, a V1 ainda não vence a V0 na decisão**, então ela não entra em produção (gate do M7). O lab mostra **onde** ela perde e **o que** rotular para virar o jogo.

---

## C. Rótulos e partição

**C1.** Esquema e exemplos-limite em [`rotulos/esquema.md`](rotulos/esquema.md). São 4 cenas exclusivas, 5 defeitos multirrótulo e o binário "leitura legível". Os exemplos-limite de cena são casos reais de discordância entre os rotuladores.

**C2.** **329 fotos distintas** rotuladas, a partir de 500 rótulos: 100 do Davi, 300 da Elizabete e 100 do Ícaro, colaborador externo, declarado. O pool é estratificado por lote (100 por lote) e, dentro de cada lote, por "com nota / sem nota".

| Conjunto | Lote | Fotos rotuladas |
|---|---|--:|
| Treino | 20/05 + 21/05 | 163 |
| Validação | 22/05 | 82 |
| Teste | 03/07 | 84 |

**C3. Dupla rotulagem às cegas.**
- **50 fotos de controle** aparecem para todos os rotuladores, na mesma ordem, intercaladas e sem identificação.
- O kappa oficial é **entre integrantes** (Davi × Elizabete, 83 fotos em comum).

| Classe | κ integrantes (Davi × Elizabete, 83 fotos) | κ médio ponderado (3 pares, 225 pares-foto) | ≥ 0,6? |
|---|--:|--:|:--:|
| Cena | **0,78** | 0,74 | ✅ |
| Legível | **0,81** | 0,82 | ✅ |
| Reflexo | 0,39 | 0,41 | ❌ |
| Fora de foco | 0,19 | 0,35 | ❌ |
| Tampa suja | 0,44 | 0,15 | ❌ |
| Enquadramento | 0,28 | 0,35 | ❌ |
| Display apagado | 0,49 | 0,18 | ❌ |

**Leitura:**
- As duas saídas de que a decisão precisa (cena e legível) são confiáveis.
- Os cinco defeitos estão ambíguos. As causas e a reescrita do esquema (v2) estão em [`esquema.md`, seção 4](rotulos/esquema.md).
- **Consequência de arquitetura:** só a cabeça de legível decide, e os defeitos viram motivo.

**C4. Partição por lote, com prova numérica:**
- Zero `numero_medidor` e zero `id_externo` em comum entre treino, validação e teste, tanto na base inteira (6.285 / 3.063 / 2.992 fotos) quanto no subconjunto rotulado.
- O zero de `id_externo` é por construção, porque o id é único por foto. A garantia real é lote + medidor.
- Os 4, 3 e 3 medidores repetidos entre lotes que o guia aponta só existem em linhas do CSV sem foto.

---

## B. O modelo em código

**B1.** A tabela de formas, de (1,3,480,360) até as cabeças (1,4), (1,5) e (1,1), está no notebook, seção 2.

**B2.**

| Configuração | Totais | Treináveis |
|---|--:|--:|
| Extração (extrator congelado) | 932.778 | **5.770** |
| Ajuste fino completo | 932.778 | 932.778 |
| 2 modelos separados (K4) | 1.859.786 | — |

**B3.** Ativação e perda de cada cabeça, com a prova nos rótulos:

| Cabeça | Ativação | Perda | Prova |
|---|---|---|---|
| Cena | softmax | `CrossEntropyLoss` (peso ∝ 1/freq) | As 500 linhas têm exatamente 1 cena: classes exclusivas |
| Defeitos | sigmoide por defeito | `BCEWithLogitsLoss` (`pos_weight`) | **94 de 329 fotos** têm 2 ou mais defeitos (ex.: 59 com fora de foco **e** enquadramento): não são exclusivos |
| Legível | sigmoide | `BCEWithLogitsLoss` | **91 fotos** com defeito continuam legíveis: não é função dos defeitos |

**Exemplos reais da base** (descritos em texto, sem reproduzir a foto do cliente):
- **Defeitos não são exclusivos:** um medidor digital atrás de uma tampa embaçada, com o céu refletido no vidro. **Os dois integrantes**, cada um às cegas, marcaram `reflexo`, `fora_de_foco` e `tampa_suja` na mesma foto. Uma softmax obrigaria a escolher só um.
- **Legível não é função dos defeitos:** um medidor digital nítido com um brilho no canto do visor. Um integrante marcou `reflexo`, e os dois marcaram a leitura como legível. A foto tem defeito e serve como comprovação.

As perdas recebem **logits**. Nenhuma softmax ou sigmoide é aplicada antes delas.

---

## D. Sanidade e baseline

| | Esperado | Medido |
|---|--:|--:|
| D1, CE inicial | ln 4 = 1,386 | **1,386** |
| D1, BCE inicial | ln 2 = 0,693 | **0,693** |
| D2, sobreajuste de 16 fotos reais | ~0 | 2,770 → **0,0099** em 42 passos |

**D3.** Validação (lote 22/05), 3 sementes, média ± desvio:

| Métrica | V1 extração (MobileNetV3) | V0 + logística | V0 + floresta | Quem ganha |
|---|--:|--:|--:|---|
| Cena macro-F1 | **0,576 ± 0,010** | 0,335 | 0,365 ± 0,015 | V1 |
| Legível AUROC | **0,873 ± 0,004** | 0,816 | 0,782 ± 0,006 | V1 |
| Defeitos macro-F1 | 0,491 ± 0,008 | **0,498** | 0,404 ± 0,004 | V0, por 0,007 (dentro de 1 desvio) |
| F1 fora de foco | 0,552 ± 0,010 | **0,656** | 0,645 ± 0,040 | **V0** |
| F1 enquadramento | 0,400 ± 0,007 | **0,458** | 0,321 ± 0,035 | **V0** |

**Onde a rede perde, e por quê achamos que perdeu:**
1. **Foco e enquadramento** são exatamente o que as features V0 medem: a variância do Laplaciano mede foco, e as bordas do centro medem se o display está no quadro.
2. **O rótulo desses defeitos é ruidoso** (κ = 0,19 e 0,28). Nenhum modelo passa do teto de concordância humana, e um modelo com 5.770 parâmetros treináveis sobre 163 fotos não tem dado para aprender uma feature melhor que a que já foi desenhada para isso.
3. Onde a tarefa é semântica (que cena é esta? dá para ler?), a feature pré-treinada no ImageNet ganha com folga.

---

## E. Confiança e recusa

**E1. Calibração na validação** (T ajustado só ali):

| Saída | ECE antes | ECE depois | T |
|---|--:|--:|--:|
| Legível | 0,106 | **0,068** | 1,98 |
| Cena | 0,156 | 0,143 | 1,90 |

No teste, com o T da validação, o ECE da legível ficou em 0,058. Os diagramas de confiabilidade estão no notebook, seção 4.

**E2. Cobertura × risco** da decisão (P(legível) calibrada). A meta do grupo, "decidir 30 a 40% do volume", vira **cobertura ≥ 30% com risco ≤ 2%**.

| | Cobertura | Risco | Limite sup. 95% |
|---|--:|--:|--:|
| Validação (limiar 0,944) | 15,9% | 0% | — |
| **Teste 03/07** (aplicado uma vez) | **14,5%** (12/83) | **8,3%** (1 ACEITA errada) | 33,9% |

**A meta não cabe na curva:** com risco ≤ 2%, a V1 decide no máximo cerca de 16%. No teste, das 12 fotos decididas, 11 estão certas: 5 ACEITAS legíveis, 6 REJEITADAS ilegíveis e 1 ilegível auto-aprovada. Com 12 decisões, o risco não é estimável com precisão (IC até 34%).

---

## Parte específica — Korvian

### K1. Resolução de entrada

| | Nativa 480×360 | 224 letterbox | Δ (nativa − 224) |
|---|--:|--:|--:|
| Legível AUROC | 0,873 ± 0,004 | **0,908 ± 0,001** | −0,035 |
| Defeitos macro-F1 | 0,491 ± 0,008 | 0,496 ± 0,013 | −0,005 |
| F1 fora de foco | 0,552 ± 0,010 | 0,566 ± 0,009 | −0,013 |
| Cena macro-F1 | **0,576 ± 0,010** | 0,518 ± 0,015 | +0,058 |
| Latência CPU p50 / p95 | 19,4 / 19,7 ms | **8,7 / 9,0 ms** | −55% |

**Decisão** (regra declarada antes, com tolerância de 0,02): a triagem de **qualidade usa 224**. Nenhuma métrica de qualidade melhora mais que 0,02 na nativa, e a latência cai 55%. A cena ganha com a nativa, mas não decide nada. **Só a leitura de dígitos precisa de resolução nativa**, e ela lê um recorte (fase 3).

### K2. O rótulo fraco se sustenta?

Treino **só com degradação sintética** de 37 fotos rotuladas como limpas (185 exemplos), avaliação **só em fotos reais** da validação. AUROC:

| Defeito | Treino real | Só sintético | Queda |
|---|--:|--:|--:|
| Fora de foco | 0,738 | 0,735 | **0,00** |
| Tampa suja | 0,781 | 0,625 | −0,16 |
| Reflexo | 0,684 | 0,486 | −0,20 |
| Enquadramento | 0,682 | 0,450 | −0,23 |
| Legível | 0,875 | 0,611 | −0,26 |
| Display apagado | n/d | n/d | 19 positivos no total |

**Leitura:**
- O **borrão sintético se sustenta**: queda zero. A rede aprende o defeito e não o artefato.
- **Reflexo e enquadramento sintéticos caem abaixo do acaso.** O reflexo real é céu e árvore espelhados no vidro; o nosso era um gradiente de brilho. O enquadramento real é foto de longe; o nosso era um recorte.
- **O rótulo fraco só vale para `fora_de_foco`.**

**Regra para a foto real já borrada que recebe borrão sintético:**
- uma foto real que já tem o defeito X **não** serve de origem "limpa";
- só fotos rotuladas com medidor, sem defeito e legíveis geram sintéticos;
- o rótulo do sintético é o OU do rótulo original com o defeito aplicado.

### K3. Heurística V0 como baseline oficial

Mesma partição, mesma métrica. As curvas cobertura × risco de V0 e V1 estão no mesmo gráfico (notebook, seção 7).

| Modelo (risco ≤ 2%, limiar da validação) | Cobertura val | Cobertura teste | Risco teste (sup. 95%) |
|---|--:|--:|--:|
| V0 Laplaciano puro | 2,4% | 0,0% | — |
| V0 logística (12 features) | 4,9% | 0,0% | — |
| **V0 floresta (12 features)** | **19,5%** | **16,9%** | **0% (19%)** |
| V1 MobileNetV3 calibrada | 15,9% | 14,5% | 8,3% (34%) |

**Leitura:**
- A V1 tem o maior AUROC global (D3), mas, **na faixa de alta confiança que a decisão usa, a V0 floresta decide mais** (16,9% contra 14,5% no teste), com 0/14 erros contra 1/12. Os limites superiores de 95% (19% e 34%) se sobrepõem, então essa diferença de erro não é significativa. A decisão conservadora vale assim mesmo.
- Pelo gate do M7 do nosso plano ("o modelo só entra se vencer a heurística"), **a V1 não entra em produção agora**.
- Nenhum dos dois atinge os 30%.

### K4. Multitarefa ou modelo separado?

Ajuste fino completo, 3 sementes:

| | Cena macro-F1 | Defeitos macro-F1 | Legível AUROC | Parâmetros | CPU p50 |
|---|--:|--:|--:|--:|--:|
| **Multitarefa** (1 espinha, 3 cabeças) | 0,570 ± 0,015 | 0,460 ± 0,054 | **0,909 ± 0,003** | **932.778** | **19,9 ms** |
| Só cena | 0,590 ± 0,024 | — | — | | |
| Só qualidade | — | 0,443 ± 0,013 | 0,873 ± 0,012 | | |
| 2 modelos separados (soma) | | | | 1.859.786 | 39,5 ms |

**Decisão: multitarefa.** Nenhuma cabeça perde mais que a tolerância de 0,02: a cena fica **exatamente no limite** (Δ = −0,020 ± 0,014), e a legível **ganha** 0,036. O custo cai pela metade em parâmetros e em latência.

---

## Bônus — curva de dados

| n de treino | V1 cena | V1 legível AUROC | V0 legível AUROC |
|--:|--:|--:|--:|
| 50 | 0,504 ± 0,118 | 0,873 ± 0,008 | 0,793 ± 0,011 |
| 100 | 0,552 ± 0,035 | 0,858 ± 0,008 | 0,803 ± 0,023 |
| 163 | 0,580 ± 0,009 | 0,873 ± 0,003 | 0,816 ± 0,000 |

Desvio populacional (ddof = 0), igual ao resto do documento, nas colunas de legível. Na coluna de cena, o desvio é amostral (ddof = 1), da tabela da seção 9 do notebook.

**Vale a pena rotular mais?**
- **Para a legível, não.** Ela está no platô desde 50 rótulos.
- **Para a cena, sim.** Ela sobe e o desvio cai muito.
- **Para os defeitos, só depois da rodada 2 do esquema.** Com κ < 0,5, mais rótulos trazem mais ruído.

---

## O que levamos para o Marco 2 (29/10)

1. **Rodada 2 de rotulagem** com o esquema v2, sobre as mesmas 50 fotos de controle, para reavaliar o kappa dos defeitos.
2. **Mais rótulos de cena** (eletromecânico e ocorrência são raros).
3. **Juntar V0 e V1:** as features V0 entram como entrada extra da cabeça de legível. No K3 a V0 floresta ganha na alta confiança, e a V1 ganha no AUROC global.
4. Configuração final: **multitarefa em 224**. Essa combinação ainda não foi medida junta: o K4 rodou na nativa, e a cobertura × risco do E2/K3 foi medida com a V1 em **480×360**. A curva em 224 (onde o AUROC de legível é 0,908) é a primeira medida do Marco 2.

## Observações sobre os rótulos

- O **Ícaro** não é integrante. Colaborou como rotulador externo, e os rótulos dele entram na maioria, mas o kappa oficial é só entre integrantes.
- O arquivo recebido do **Ivan** era uma cópia idêntica do arquivo do Ícaro e foi descartado. O Ivan não tem rótulos nesta rodada.
- A cópia do app da Elizabete tinha uma coluna extra `outros` (14 marcações). Ela não pertence ao esquema e foi ignorada na consolidação.
