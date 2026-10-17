"""Importa a inspecao humana das 355 fotos que so a `politica@v2` rejeita (M7), rodada
`inspecao_m7`.

Fonte: `ml/reports/20261016-213656/inspecao_355.csv` (`make m7-amostra-inspecao`): as fotos
dos 4 lotes com `ilegivel` >= 0,976 no modelo `qualidade@v1` e p_ruim < 0,85 na
heuristica, que nenhum humano viu. Ligar o modelo depende do veredito delas (relatorio do
M7, secao 7), entao elas sao calibracao da v2 e ficam fora da medida de falso positivo
(`auditoria.RODADAS_DE_CALIBRACAO`). Mesmo formato e mesmas regras da `inspecao_m5`:
todas entram, inspecionadas ou pendentes (`legivel` nulo, inspetor `pendente`).
"""

from pathlib import Path

from sqlalchemy.orm import Session

from inspecao.enums import RodadaRotulagem
from inspecao.rotulagem import inspecao_m5
from inspecao.rotulagem.inspecao_m5 import CsvInvalido, Resumo

RODADA = RodadaRotulagem.INSPECAO_M7.value

__all__ = ["RODADA", "CsvInvalido", "Resumo", "importar"]


def importar(sessao: Session, caminho: Path) -> Resumo:
    """Grava (substituindo) um rotulo `inspecao_m7` por foto do CSV. Sem commit."""
    return inspecao_m5.importar(sessao, caminho, rodada=RODADA)
