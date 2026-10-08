# Ficha de arquitetura — Korvian

> Triagem de fotos de leitura (Projeto 4). Bloco profundo deste laboratório: **triagem V1**, uma MobileNetV3-Small pré-treinada com cabeças de cena, defeitos e legibilidade.
> Os números vêm de `lab02.ipynb`, executado do zero com os rótulos humanos (329 fotos). O resumo está em `resultados/resumo.csv`.

## 1. Decisão apoiada

| Pergunta | Resposta |
|---|---|
| Decisão | Por foto: **ACEITA** (a foto comprova a leitura), **REJEITADA** (não comprova: vai para revisita) ou **DÚVIDA** (fila humana) |
| Unidade de análise | Uma foto ligada a uma leitura (`id_externo` + sequência). Não é o medidor nem o cliente |
| Quem age | Os analistas conferem as DÚVIDAS e uma amostra das ACEITAS (auditoria) |
| Erro mais caro | **Aceitar foto que não comprova a leitura**: fatura sem evidência e risco regulatório. Rejeitar foto boa custa uma revisita, que é cara mas recuperável |
| Onde roda | Lote pós-coleta, CPU, servidor local (worker do sistema) |

## 2. Sub-tarefas

| Sub-tarefa | Aprendida ou regra? | Tipo | Saída | Ativação | Perda | Rótulo |
|---|---|---|---|---|---|---|
| Tipo de cena | **Aprendida** | Classificação exclusiva (4) | 1 classe | softmax | `CrossEntropyLoss` (peso ∝ 1/freq) | cena por foto |
| Defeitos de qualidade | **Aprendida** (só como motivo) | Multirrótulo (5) | 0 a 5 defeitos | sigmoide por defeito | `BCEWithLogitsLoss` (`pos_weight`) | vetor binário |
| Leitura legível | **Aprendida** (decide) | Binária | P(legível) | sigmoide | `BCEWithLogitsLoss` | S/N por foto |
| Nota exige foto / foto órfã | **Regra** | — | — | — | — | pareamento por `id_externo` (M2) |
| Valor lido × valor digitado | **Regra** (fase 3) | comparação | — | — | — | — |
| ACEITA/REJEITADA/DÚVIDA | **Regra sobre saída calibrada** | limiar | — | — | — | limiar da validação |

A comparação entre valor extraído e valor digitado é determinística, e pôr uma rede ali só pagaria incerteza. A leitura de dígitos (detecção + CRNN/CTC) fica para a fase 3: é outro contrato de entrada (recorte em resolução nativa) e outro rótulo (transcrição).

## 3. Pipeline

```
foto ──► [Regras M2: nota exige foto? órfã?] ──► [V1: cena · defeitos · legível] ──► [temperatura T] ──► [limiar] ──► ACEITA / REJEITADA / DÚVIDA
            (determinístico)                     (MobileNetV3-Small, 1 passada)       (validação)        (validação)     motivo = defeitos > 0,5
```

| Estágio | Taxa | Fonte |
|---|--:|---|
| Pareamento foto ↔ leitura (regra) | 1,00 | Determinístico. As 866 órfãs e as 296 notas sem foto saem antes (M2, contagem exata) |
| Legível correto, entre as fotos decididas | **0,92** (11/12) | Teste, lote 03/07, com o limiar da validação |
| **Ponta a ponta, nas decididas** | 1,00 × 0,92 = **0,92** | Produto, que só vale para os 14,5% decididos; o resto vai para DÚVIDA |
| *Se a cena fosse um estágio obrigatório* | 1,00 × 0,58 × 0,92 ≈ **0,53** (aproximação) | Usa o macro-F1 de cena (0,58, validação) como proxy da taxa de acerto do estágio. É por isso que a cena **não** é um portão da decisão |

Escolhemos **estágios** e não ponta a ponta porque não há rótulo de decisão final: nenhuma decisão dos analistas foi registrada. A cascata ficou com um único estágio aprendido na decisão. A conta acima mostra o motivo: pôr a cena como portão derrubaria o acerto ponta a ponta para cerca de 53%.

## 4. Contrato de entrada

| Modelo | Tamanho | Proporção | Normalização | Canais |
|---|---|---|---|---|
| V1 triagem (decisão) | **224 com letterbox** (decisão K1) | 3:4 preservada, com faixas; sem distorcer | ImageNet `[0,485; 0,456; 0,406]` / `[0,229; 0,224; 0,225]` | RGB (reflexo e névoa têm cor) |
| V0 heurísticas (baseline) | nativo 480×360 | — | — | cinza e HSV |
| Leitor de dígitos (fase 3, fora do lab) | recorte nativo | — | — | cinza |

**K1, medido:**
- Em 224, a cabeça de legível **melhora**: AUROC 0,908 contra 0,873 na nativa.
- Os defeitos empatam: Δ macro-F1 = −0,005 ± 0,006.
- A latência em CPU cai 55%: 19,4 → 8,7 ms p50.
- A cena perde 0,058 de macro-F1 em 224, mas não decide nada (seção 9).
- **Conclusão:** a triagem de qualidade **não** precisa da resolução nativa. Só a leitura de dígitos (fase 3) precisa, e ela lê um recorte.

Cada foto é uma amostra. As fotos `_001`… da mesma leitura ficam no mesmo conjunto, porque a partição é por lote (seção 11).

## 5. Espinha dorsal

**MobileNetV3-Small, ImageNet:** 932.778 parâmetros com as 3 cabeças, e 5.770 treináveis com o extrator congelado.

| Alternativa | Parâmetros | Por que foi descartada |
|---|--:|---|
| ResNet-18 | 11.689.512 | 12,5× mais parâmetros para 329 fotos rotuladas e implantação em CPU |
| EfficientNet-B0 | 5,3 M (docs do torchvision; não medida aqui) | 5,7× mais parâmetros sem rótulos que justifiquem a capacidade |
| ConvNeXt-Tiny | 28,6 M (docs do torchvision; não medida aqui) | Pressupõe GPU na implantação |

Latência medida (CPU, 1 thread, lote 1): **8,7 ms p50 / 9,0 ms p95** em 224, e 19,4 / 19,7 ms na nativa. Decodificar o JPEG e normalizar custa mais 1,3 ms.

## 6. Cabeças

| Cabeça | Saídas | Ativação | Perda | Exclusivas? Prova nos rótulos |
|---|--:|---|---|---|
| Cena | 4 | softmax | CE com peso ∝ 1/freq | **Sim**: todas as 500 linhas foto × rotulador têm exatamente uma cena |
| Defeitos | 5 | sigmoide | BCE + `pos_weight` = neg/pos (teto 20) | **Não**: **94 de 329 fotos** têm 2 ou mais defeitos ao mesmo tempo (ex.: 59 com fora de foco **e** enquadramento) |
| Legível | 1 | sigmoide | BCE | Binária. É separada dos defeitos porque **91 fotos** têm defeito marcado e continuam legíveis |

Perda total: `CE(cena) + BCE(defeitos) + BCE(legível)`. A perda é mascarada: rótulo `NA` (empate na consolidação) não contribui. Nenhuma ativação é aplicada antes da perda, que recebe logits (`modelo.py`).

**K4, multitarefa ou modelos separados:** **multitarefa**. Nenhuma cabeça perde mais que a tolerância de 0,02 ao compartilhar a espinha dorsal. A cena fica **no limite**:
- Δ cena = −0,020 ± 0,014;
- Δ defeitos = +0,017 ± 0,045;
- Δ legível AUROC = +0,036 ± 0,013.

O custo cai pela metade: 932.778 parâmetros contra 1.859.786, e 19,9 contra 39,5 ms p50 em CPU. A legível até ganha com o compartilhamento: a cena funciona como regularizador (Caruana, 1997).

## 7. Desbalanceamento

Distribuição nos 500 rótulos (todos os rotuladores):

| Cena | digital 367 · eletromecânico 88 · ocorrência 29 · não é medidor 16 |
|---|---|
| Defeitos (positivos) | reflexo 213 · fora de foco 156 · enquadramento 111 · tampa suja 84 · display apagado **19** |
| Legível | sim 295 · não 205 |

- **Técnica:** peso ∝ 1/frequência na CE de cena e `pos_weight` por defeito, com teto 20 para o defeito raro não dominar o gradiente.
- **`display_apagado`:** com 19 positivos, ficou **n/d** em todas as métricas, porque não tem exemplos suficientes por conjunto.
- **Efeito colateral:** as duas técnicas descalibram a probabilidade, e a seção 9 corrige isso.

## 8. Capacidade × rótulos

| Item | Valor |
|---|---|
| Rótulos | **329 fotos** consolidadas (163 treino · 82 validação · 84 teste); 500 rótulos de 3 rotuladores |
| Estratégia | **Extração de características** (extrator congelado): a maioria das classes tem menos de 100 exemplos no treino |
| Treináveis / totais | 5.770 / 932.778 |
| Ajuste fino (K4) | Taxas diferenciadas: extrator 1e-4, cabeças 1e-3, 6 épocas |

Curva de dados (bônus, 3 sementes):

| n de treino | V1 cena macro-F1 | V1 legível AUROC | V0 legível AUROC |
|--:|--:|--:|--:|
| 50 | 0,504 ± 0,118 | 0,873 ± 0,008 | 0,793 ± 0,011 |
| 100 | 0,552 ± 0,035 | 0,858 ± 0,008 | 0,803 ± 0,023 |
| 163 | 0,580 ± 0,009 | 0,873 ± 0,003 | 0,816 ± 0,000 |

Desvio populacional (ddof = 0) nas colunas de legível; amostral (ddof = 1) na de cena.

**Leitura:**
- A **legível já está no platô** com 50 rótulos: rotular mais não melhora a decisão.
- A **cena ainda sobe** (0,50 → 0,58) e o desvio cai muito, então vale rotular mais para ela.
- Rotular mais **defeitos** só vale **depois** da rodada 2 do esquema (kappa < 0,6).

## 9. Confiança e recusa

- **Regra de decisão:**
  - confiança = max(p, 1 − p) sobre P(legível) **calibrada**;
  - se a confiança ≥ limiar: **ACEITA** quando p ≥ 0,5, **REJEITADA** quando p < 0,5;
  - abaixo do limiar: **DÚVIDA**;
  - defeitos com P > 0,5 viram o **motivo** mostrado ao analista.
- **Por que só a legível decide:** é a única cabeça com rótulo confiável (κ = 0,81 entre integrantes). Os defeitos têm κ entre 0,19 e 0,49 (`rotulos/esquema.md`, seção 4).
- **Calibração**, com T ajustado **só na validação** (lote 22/05):

  | Saída | ECE antes | ECE depois | T |
  |---|--:|--:|--:|
  | Legível | 0,106 | **0,068** | 1,98 |
  | Cena (top-1) | 0,156 | 0,143 | 1,90 |

  No teste, com o T da validação, o ECE da legível ficou em 0,058.
- **Meta do grupo:** "decidir 30 a 40% do volume" vira **cobertura ≥ 30% com risco ≤ 2%**. Os 2% são o menor risco que cerca de 150 fotos decididas conseguem sustentar pela regra dos três (3/150).
- **Resultado (V1 em 480×360; a curva em 224 não foi medida): a meta não cabe na curva.**
  - Na validação, com risco ≤ 2%, a cobertura máxima é **15,9%** (limiar 0,944, risco 0%).
  - No teste (03/07), aplicado **uma única vez**: cobertura **14,5%** (12/83), risco **8,3%** (1 ACEITA errada), limite superior de 95% **33,9%**. Nenhuma REJEITADA errada.
  - Com 12 fotos decididas, o risco no teste não é estimável com precisão: o intervalo vai de 0 a 34%.

## 10. Aumento de dados

| Transformação | Modelo | Muda o rótulo? |
|---|---|---|
| Rotação ±10° | V1 (cena) | Não |
| Brilho, contraste e cor moderados | V1 (cena) | Não |
| Desfoque gaussiano ou tremido | V1 (qualidade) | **Sim**: vira `fora_de_foco` |
| Reflexo sintético | V1 (qualidade) | **Sim**: vira `reflexo` |
| Névoa ou opacidade | V1 (qualidade) | **Sim**: vira `tampa_suja` |
| Recorte que tira o display | V1 (qualidade) | **Sim**: vira `enquadramento` |
| Escurecer o display | V1 (qualidade) | **Sim**: vira `display_apagado` |
| Espelhamento horizontal | — | **Proibido**: inverte texto e dígitos |
| Recompressão JPEG | todas | Não (aplicada também à variante limpa, para não virar atalho) |

**K2, o rótulo fraco se sustenta?**

Treinamos só com degradação sintética de **37 fotos rotuladas como limpas** (185 exemplos) e avaliamos só em fotos reais da validação. AUROC:

| Defeito | Rótulo real | Só sintético | Queda |
|---|--:|--:|--:|
| fora de foco | 0,738 | 0,735 | **≈ 0** ✅ |
| tampa suja | 0,781 | 0,625 | −0,16 |
| reflexo | 0,684 | 0,486 | −0,20 (abaixo do acaso) |
| enquadramento | 0,682 | 0,450 | −0,23 (abaixo do acaso) |
| legível | 0,875 | 0,611 | −0,26 |
| display apagado | n/d | n/d | — |

**Leitura:**
- O **borrão sintético se sustenta**: a rede aprende o defeito e não o artefato.
- **Reflexo e enquadramento sintéticos não se sustentam**: o reflexo real (céu e árvore espelhados no vidro) e o enquadramento real (foto de longe) não se parecem com o que geramos.
- **Plano:** manter o sintético só para `fora_de_foco` e rotular os outros defeitos à mão.
- **Regra da foto já degradada:** uma foto real que já tem o defeito X não serve de origem "limpa". O rótulo do exemplo sintético é o OU do rótulo original com o defeito aplicado.

## 11. Partição

| Conjunto | Lote | Fotos na base | Fotos rotuladas |
|---|---|--:|--:|
| Treino | 20/05 e 21/05 | 6.285 | 163 |
| Validação | 22/05 | 3.063 | 82 |
| Teste | 03/07 | 2.992 | 84 |

**Prova numérica (seção 1 do notebook):** zero `numero_medidor` e zero `id_externo` em comum entre os três pares de conjuntos, na base inteira e no subconjunto rotulado. Os `assert` passam.
- O zero de `id_externo` é **por construção**: o id é único por foto, inclusive nas sequências `_001`.
- A garantia real vem do **lote + medidor**.
- Para as 866 órfãs sem medidor no CSV, a garantia é só o lote.

**Achado:** o guia aponta 4, 3 e 3 medidores repetidos entre lotes, mas eles aparecem só em linhas do CSV **sem foto**. Entre fotos, a interseção é zero, e a remoção por vazamento tirou 0 fotos.

## 12. Sanidade

| Verificação | Resultado |
|---|---|
| Formas | (1,3,480,360) → extrator (1,576,15,12) → pool (1,576,1,1) → cabeças (1,4), (1,5), (1,1) |
| Perda inicial | CE **1,386** (ln 4 = 1,386); BCE **0,693** (ln 2 = 0,693) |
| Sobreajuste de 16 fotos **reais** | 2,770 → **0,0099** em 42 passos |
| Baseline (D3, validação 22/05, 3 sementes) | ver abaixo |

| Métrica | V1 extração | V0 + logística | V0 + floresta |
|---|--:|--:|--:|
| Cena macro-F1 | **0,576 ± 0,010** | 0,335 | 0,365 ± 0,015 |
| Legível AUROC | **0,873 ± 0,004** | 0,816 | 0,782 ± 0,006 |
| Defeitos macro-F1 | 0,491 ± 0,008 | **0,498** | 0,404 ± 0,004 |
| F1 fora de foco | 0,552 ± 0,010 | **0,656** | 0,645 ± 0,040 |
| F1 enquadramento | 0,400 ± 0,007 | **0,458** | 0,321 ± 0,035 |

**Leitura:**
- A V1 ganha onde a tarefa é **semântica**: cena e legível.
- A V0 empata ou ganha em **foco e enquadramento**, que são exatamente o que o Laplaciano e as estatísticas de borda medem. Com 163 fotos de treino e rótulo de defeito ruidoso (κ < 0,5), a feature feita à mão é tão boa quanto a aprendida.

**K3, V0 contra V1 na decisão** (cobertura com risco ≤ 2%, limiar escolhido na validação):

| Modelo | Cobertura val | Cobertura teste | Risco teste |
|---|--:|--:|--:|
| V0 Laplaciano puro | 2,4% | 0,0% | — |
| V0 logística | 4,9% | 0,0% | — |
| V0 floresta | **19,5%** | **16,9%** | 0% (sup. 95%: 19%) |
| V1 calibrada | 15,9% | 14,5% | 8,3% (sup. 95%: 34%) |

Na faixa de alta confiança, a **V0 floresta decide mais e erra menos que a V1**, apesar do AUROC global menor. Os erros (0/14 contra 1/12) não diferem com significância, porque os limites superiores de 95% se sobrepõem. Mesmo assim, pelo gate do M7 do projeto ("o modelo só entra se vencer a heurística"), **a V1 ainda não entra em produção**.

## 13. Implantação

| Item | Valor |
|---|---|
| Volume adotado | **3.100 fotos por extração** (o que os lotes recebidos mostram). Os 30 a 50 mil por dia do documento de entendimento são pergunta em aberto para o cliente |
| Latência p50 / p95 (CPU, 1 thread, 224) | 8,7 / 9,0 ms, mais 1,3 ms de JPEG e normalização |
| Tempo do lote | 3.100 × ~10 ms ≈ **31 s**; com 50 mil, ≈ **8,5 min**. Cabe com folga numa janela noturna, mesmo no volume alto |
| Onde roda | Worker do sistema (fila no Postgres, M3) |
| Exportação | Planejada (M7, não feita neste lab): ONNX + ONNX Runtime, com teste de paridade < 1e-4 |
| Licenças | MobileNetV3/torchvision: BSD-3; OpenCV: Apache-2.0; ONNX Runtime: MIT |

## 14. O que decidimos não fazer

| Não fizemos | Por quê |
|---|---|
| Usar os defeitos para decidir | κ entre 0,19 e 0,49: a rede aprenderia o desacordo entre rotuladores. Os defeitos ficam só como motivo, até a rodada 2 |
| Ler os dígitos neste laboratório | Exige detecção, recorte nativo e transcrição (fase 3) |
| Treinar do zero | Com 163 fotos de treino, 932 mil parâmetros sobreajustam (o sobreajuste de 16 fotos prova a capacidade, não a generalização). O ajuste fino com taxas diferenciadas **ajuda** a legível (K4: 0,909 contra 0,873 na extração), e é o próximo passo, junto com os 224 px do K1 (0,908) |
| Rede ponta a ponta até ACEITA/REJEITADA | Não existe rótulo de decisão final |
| Sintético para reflexo e enquadramento | O K2 mostrou AUROC abaixo de 0,5 em fotos reais |
| YOLO | Não há caixas desenhadas, e as versões comuns são AGPL-3.0 |
| Ligar a V1 em produção | Não venceu a V0 floresta na decisão (K3), e a auto-aprovação (M8) exige 600 rótulos |
