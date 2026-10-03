# Fotos órfãs: investigação da hipótese (T-M2-04)

Data: 2026-09-18. Base: os 4 lotes de `Base cliente/` reingeridos com a validação do marco M2.
Reprodutível por SQL sobre `fotos` e `quarentena` após `make ingest-todos`.

## Pergunta

866 imagens em disco (7,0% de 12.340) não são referenciadas por nenhuma linha de CSV.
A hipótese registrada no plano (doc 02, seção 2.1) era: **são fotos adicionais da mesma
leitura**, com o mesmo identificador de 20 dígitos e sequência diferente da foto principal.

## Resultado: hipótese refutada

| Verificação | Resultado |
| --- | --- |
| Pares (lote, id externo) com mais de uma foto | **0**. Cada id externo aparece exatamente uma vez no lote |
| Órfãs cujo id externo é foto principal em outro lote | **0** |
| Órfãs com sha256 idêntico a alguma foto principal | **0** |
| Órfãs duplicadas entre si (mesmo sha256) | **0** |
| Registros `foto_orfa` com `leitura_provavel_id` preenchido | **0 de 866** |

Toda órfã tem um identificador único que nenhum CSV menciona. Não são retomadas da mesma
leitura; são leituras inteiras cuja linha não veio na extração.

## O que os dados dizem sobre elas

**Distribuição uniforme entre lotes.** Entre 6,5% e 7,9% das fotos de cada lote, sem lote
anômalo. É um fenômeno sistemático do processo de extração, não um incidente.

| Lote | Órfãs | Fotos | % |
| --- | --- | --- | --- |
| 200526_0352 | 208 | 3.185 | 6,5 |
| 210526_0335 | 219 | 3.100 | 7,1 |
| 220526_0408 | 242 | 3.063 | 7,9 |
| 030726_0121 | 197 | 2.992 | 6,6 |

**Mesmo espaço de identificadores.** As 866 caem dentro da faixa de ids das fotos
principais do próprio lote, e o prefixo do id (`20…` em 99,7% dos casos, `00…` no resto)
segue a mesma proporção das principais. Foram capturadas no mesmo ciclo, pelo mesmo app.

**O sinal mais forte: a sequência do arquivo.**

| Sequência do nome | Entre as principais | Entre as órfãs |
| --- | --- | --- |
| `_000` | 98,5% | 59,5% |
| `_001` ou maior | 1,5% | **40,5%** |
| `_004` a `_008` | 0 casos | 25 casos (todos) |

O sufixo de sequência parece contar tentativas de captura. Uma foto órfã tem 27 vezes mais
chance de ter sido refeita do que uma foto principal. Isso é consistente com leituras que
deram problema em campo (várias tentativas de foto) e que, por algum critério do sistema de
origem, ficaram fora do CSV de "leituras implantadas".

**O id de 20 dígitos não é o número do medidor.** Testado por sufixo e sem zeros à esquerda
contra `leitura_medidores`: zero coincidências. É um identificador opaco do sistema de
origem, como o plano já assumia.

## Interpretação e o que perguntar ao cliente

Hipótese de trabalho revisada: as órfãs são leituras **não implantadas** (rejeitadas, pendentes
ou de outra classe de status no SAP) cuja foto foi exportada junto com o lote, mas cuja linha
o extrator filtrou. Isso explica a uniformidade entre lotes e o viés para múltiplas tentativas.

Pergunta objetiva para o cliente (pendência de entrada nº 2 do plano):

> No extrator `PSP_EXTRATLEITIMPL`, qual critério seleciona as linhas do CSV? Existem leituras
> com foto que ficam fora por status (não implantada, cancelada, em revisão)? Se sim, é
> possível incluir uma coluna de status ou um segundo arquivo com essas linhas?

## Decisão de engenharia até a resposta

1. **Continuam importadas e em quarentena** (`foto_orfa`, informativa), com `leitura_id` nulo.
   Não se cria leitura sintética para elas: sem linha de CSV não há leitura declarada para
   auditar.
2. **Não entram no conjunto de treino como exemplos "bons" nem "ruins"** sem rótulo humano.
   Podem entrar na amostra estratificada da rotulagem (M6) como estrato próprio, justamente
   porque o viés de retentativa sugere concentração de defeitos de qualidade.
3. **Não entram no teste de regressão como `leitura_provavel_id`**: o teste agora afirma que a
   coluna é nula para todas as 866. Se um lote futuro trouxer pares reais, o teste avisa.
4. O campo `detalhe.leitura_provavel_id` fica no esquema: custa nada e detecta a hipótese
   original caso o comportamento do extrator mude.

## Consultas usadas

```sql
-- pares (lote, id_externo) por quantidade de fotos
select n, count(*) from (
  select lote_id, id_externo, count(*) n from fotos group by 1, 2
) t group by 1;

-- orfas x sequencia
select principal, round(100.0 * count(*) filter (where sequencia >= 1) / count(*), 1)
from fotos group by 1;

-- orfas dentro da faixa de ids das principais do lote
with p as (select lote_id, id_externo from fotos where principal),
     o as (select lote_id, id_externo from fotos where leitura_id is null)
select count(*) filter (where
  exists (select 1 from p where p.lote_id = o.lote_id and p.id_externo < o.id_externo) and
  exists (select 1 from p where p.lote_id = o.lote_id and p.id_externo > o.id_externo))
from o;
```
