# Uso de IA generativa neste laboratório

| Integrante | Ferramenta | O que pedi | O que aceitei | O que descartei e por quê |
|---|---|---|---|---|
| Davi Melo | Claude Code (Claude Opus 5.5) | Leitura do enunciado e do guia, e um mapa do que o grupo Korvian precisava entregar | O mapeamento do lab para os marcos M5/M6/M7 do nosso plano, e a decisão de pôr o código em `ml/src/mlinsp/` para reaproveitar no Marco 2 | A sugestão inicial de ligar o modelo no sistema: o nosso gate do M7 exige vencer a heurística antes |
| Davi Melo | Claude Code | Um app local de rotulagem (`rotulador/`) com dupla rotulagem às cegas e kappa | O app só com a biblioteca padrão do Python, os controles em ordem fixa (para maximizar os pares de kappa) e a consolidação por maioria | **Pré-rótulo sugerido por modelo:** recusado, porque ancora o rotulador e infla o kappa. Rótulos gerados por IA: proibidos; **todos os rótulos são humanos** |
| Davi Melo | Claude Code | O esquema de classes a partir do nosso CRISP-DM (doc. 02) | As 4 cenas, os 5 defeitos e a pergunta "leitura legível?" como rótulo de decisão | Os exemplos-limite que a IA escreveu antes da rotulagem: trocados pelos **casos reais de discordância** entre integrantes |
| Davi Melo | Claude Code | O notebook e os módulos (partição, heurísticas V0, degradação, modelo, calibração, cobertura × risco) | A estrutura do notebook na ordem do enunciado e a partição por lote com prova numérica | No K2, a primeira versão completava a origem "limpa" com fotos **não rotuladas** de alta nitidez: descartada, porque rotulava como limpas fotos não verificadas. Ficou só com fotos rotuladas |
| Davi Melo | Claude Code | Revisão independente (um agente separado, só leitura) do app, do notebook e das penalizações do enunciado | As correções apontadas: legível automático que persistia ao trocar de cena, controles visíveis no manifesto, `log(0)` na baseline, testes da perda mascarada | Nenhuma correção foi aceita sem execução que a confirmasse |
| Davi Melo | Claude Code | A redação da `ARQUITETURA.md` e deste `README.md` a partir das saídas do notebook | O texto, depois de conferir cada número contra `resultados/resumo.csv` | Números de parâmetros por modelo separado que a IA escreveu sem que tivessem sido medidos: substituídos pelo único valor medido no K4 (soma de 1.859.786) |

## O que a IA errou e nós corrigimos

- **Dado do cliente vazando por caminhos indiretos:** valores reais em comentários de código e em testes, e prints de tela com uma foto de cliente. Uma revisão cruzando os números do repositório com a base real pegou esses casos. Os valores foram trocados por fictícios.
- **Afirmação sem medida:** a IA escreveu números de parâmetros por modelo separado (K4) que o notebook não mede. Ficou só a soma medida (1.859.786), conferida com o `resumo.csv`.
- **Rótulo fraco otimista:** a primeira versão do K2 usava fotos não verificadas como "limpas". Isso inflaria a queda medida no rótulo fraco.

## Declaração

**Os rótulos são 100% humanos:** Davi, Elizabete e um colaborador externo declarado (Ícaro). Nenhum número deste laboratório foi estimado pela IA: todos saem da execução do notebook. Cada integrante é capaz de explicar qualquer linha do código entregue.

| Integrante | Uso de IA |
|---|---|
| Elizabete | Não usou IA generativa neste laboratório (participou da rotulagem e da dupla rotulagem às cegas) |
| Ivan | Não usou IA generativa neste laboratório |
