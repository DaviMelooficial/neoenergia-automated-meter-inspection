"""Importa a inspecao humana das 200 fotos dos extremos do M5 na rodada `inspecao_m5`.

Fonte: `ml/reports/heuristicas_amostra_200.csv` (grupos `pior` e `limitrofe`). E sobre
essas fotos que o limiar 0,85 da `politica@v1` foi escolhido: elas sao CALIBRACAO, e a
medida de falso positivo da auto-rejeicao (`auditoria._estratos`) precisa enxerga-las
para deixa-las de fora. Por isso TODAS as 200 entram, inspecionadas ou nao:
- `legivel_inspecao` preenchido (1/0, sim/nao, true/false) -> `legivel` com o veredito;
- em branco (inspecao ainda pendente) -> `legivel` nulo e inspetor `pendente`.
Cena e defeitos nao sao declarados pela inspecao e ficam nulos.

Idempotente: cada foto tem no maximo um rotulo `inspecao_m5`; rodar de novo depois de
preencher o CSV troca o rotulo pendente pelo veredito. A foto e achada pelo nome do
arquivo; nome que nao existe no banco e contado como nao mapeado e fica de fora.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from inspecao.enums import RodadaRotulagem
from inspecao.models import Foto, Rotulo

RODADA = RodadaRotulagem.INSPECAO_M5.value
PENDENTE = "pendente"
COLUNAS = {"arquivo", "legivel_inspecao", "inspetor"}
VERDADEIRO = {"1", "sim", "s", "true", "legivel"}
FALSO = {"0", "nao", "não", "n", "false", "ilegivel"}


class CsvInvalido(ValueError):
    pass


@dataclass
class Resumo:
    linhas: int = 0
    gravados: int = 0
    inspecionados: int = 0
    pendentes: int = 0
    nao_mapeadas: list[str] = field(default_factory=list)


def _legivel(valor: str) -> bool | None:
    v = (valor or "").strip().lower()
    if not v:
        return None
    if v in VERDADEIRO:
        return True
    if v in FALSO:
        return False
    raise CsvInvalido(f"legivel_inspecao invalido: {valor!r} (use 1/0, sim/nao)")


def importar(sessao: Session, caminho: Path) -> Resumo:
    """Grava (substituindo) um rotulo `inspecao_m5` por foto do CSV. Sem commit."""
    with caminho.open(encoding="utf-8", newline="") as f:
        leitor = csv.DictReader(f)
        faltam = COLUNAS - set(leitor.fieldnames or [])
        if faltam:
            raise CsvInvalido(f"colunas ausentes: {sorted(faltam)}")
        linhas = list(leitor)
    resumo = Resumo(linhas=len(linhas))
    nomes = [linha["arquivo"] for linha in linhas]
    fotos = dict(
        sessao.execute(select(Foto.nome_arquivo, Foto.id).where(Foto.nome_arquivo.in_(nomes)))
        .tuples()
        .all()
    )
    for linha in linhas:
        foto_id = fotos.get(linha["arquivo"])
        if foto_id is None:
            resumo.nao_mapeadas.append(linha["arquivo"])
            continue
        legivel = _legivel(linha["legivel_inspecao"])
        inspetor = (linha["inspetor"] or "").strip() or PENDENTE
        sessao.execute(delete(Rotulo).where(Rotulo.foto_id == foto_id, Rotulo.rodada == RODADA))
        sessao.add(
            Rotulo(foto_id=foto_id, rodada=RODADA, rotulador_externo=inspetor, legivel=legivel)
        )
        resumo.gravados += 1
        if legivel is None:
            resumo.pendentes += 1
        else:
            resumo.inspecionados += 1
    sessao.flush()
    return resumo
