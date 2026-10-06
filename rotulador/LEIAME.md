# Rotulador de medidores (Korvian, Lab 2)

App local para rotular fotos de medidor. Roda no seu computador, sem internet e sem instalar nada além do Python.

## 1. Abrir

- **Mac:** dê duplo clique em `ABRIR_MAC.command`. Se o Mac bloquear ("desenvolvedor não identificado"), clique com o botão direito no arquivo → **Abrir** → **Abrir**.
- **Windows:** dê duplo clique em `ABRIR_WINDOWS.bat`. Se aparecer "O Windows protegeu o computador", clique em **Mais informações** → **Executar assim mesmo**.

Uma janela preta abre e o navegador abre sozinho em `http://localhost:8765`. **Deixe a janela preta aberta** enquanto rotula; fechar ela encerra o app. Se o navegador não abrir, copie o endereço que aparece na janela preta.

Sem Python? Instale em <https://www.python.org/downloads/> (no Windows, marque **Add python.exe to PATH**) e abra de novo.

## 2. Rotular

1. Digite seu nome (sempre o mesmo, para continuar de onde parou) e quantas fotos quer fazer agora.
2. Para cada foto, marque a **cena**, os **defeitos** que houver e se a **leitura é legível**. Depois `Enter`.
3. Tudo é salvo a cada foto. Pode fechar e voltar outro dia: as fotos já feitas não aparecem de novo.

| tecla | o quê |
|---|---|
| `1` `2` `3` `4` | cena: digital · eletromecânico · cena de ocorrência · não é medidor |
| `Q` `W` `E` `R` `T` | defeitos (liga/desliga): reflexo · fora de foco · tampa suja · enquadramento ruim · display apagado |
| `S` / `N` | leitura legível: sim / não |
| `Enter` | salvar e ir para a próxima |
| `Backspace` (Mac: `delete`) | voltar e corrigir a anterior |
| `X` | pular a foto |
| `Z` ou clique na foto | ampliar (clique de novo na foto ampliada para aproximar) |
| `?` | mostrar as definições |

## 3. Definições

**Cena** (escolha uma):

- **Digital:** medidor eletrônico, a leitura aparece num display (LCD/LED).
- **Eletromecânico:** roletes numerados que giram (ciclométrico) ou relógios de ponteiro; costuma ter um disco metálico giratório.
- **Cena de ocorrência:** aparece a caixa, o padrão de entrada, a fachada, o portão ou um cadeado, mas **o medidor não está visível**.
- **Não é medidor:** nada relacionado (chão, pessoa, documento, foto preta ou totalmente borrada).

**Defeitos** (opcional, marque todos que atrapalham):

- **Reflexo:** brilho de luz ou flash sobre o vidro cobrindo o display ou os números.
- **Fora de foco:** foto tremida ou desfocada.
- **Tampa suja/embaçada:** vidro ou tampa sujos, riscados, opacos ou úmidos.
- **Enquadramento ruim:** display cortado, medidor muito longe ou foto muito torta.
- **Display apagado:** medidor digital com o display sem nada aceso.

**Leitura legível:** **Sim** só se você consegue ler com segurança **todos** os dígitos. Em "cena de ocorrência" e "não é medidor" fica **Não** automaticamente.

Em dúvida, escolha o que melhor descreve a foto e siga. Use `X` só se for impossível decidir.

## 4. No fim

1. Feche a janela preta.
2. Compacte (zip) a pasta `rotulos/<seu_nome>` (o caminho aparece na tela "Obrigado").
3. Envie o zip **só ao Davi**, por mensagem privada.

## Regras

- **Às cegas:** algumas fotos são vistas por mais de uma pessoa, para medirmos a concordância; por isso rotule sozinho e não converse sobre as fotos nem sobre seus rótulos com colegas até todos terminarem.
- **Dados do cliente (LGPD):** as fotos são da Neoenergia. Não compartilhe, não poste e não envie para ninguém fora da equipe. **Apague esta pasta inteira ao fim do semestre.**

---

### Para o Davi

```bash
python3 preparar.py      # uma vez: sorteia 400 fotos (100 por lote, 50 de controle) para imagens/ + manifesto.csv
# controles: mesma ordem para todos, 1 a cada 2 fotos (100 fotos cobrem os 50); controle pulado volta uma vez
python3 rotulador.py     # testar local (porta opcional: python3 rotulador.py 9000)
# colegas devolvem rotulos/<nome>/ -> copiar para rotulos/
python3 consolidar.py    # saida/: consolidado por maioria, todos os rótulos e kappa.md
```

`imagens/`, `manifesto.csv`, `rotulos/` e `saida/` ficam fora do git. Para distribuir, zipe a pasta `rotulador/` inteira (com `imagens/` e `manifesto.csv`, sem `rotulos/` e `saida/`).
