"""Amostra da inspecao humana que decide ligar o modelo do M7: as fotos que so ele rejeita.

As fotos da base com `ilegivel` >= limiar da `politica@v2` (o `limiar_rejeita` do contrato
`reports/<rodada>/qualidade.json`) e p_ruim da heuristica abaixo do limiar da `politica@v1`
(0,85). Na rodada 20261016-213656 sao 355 (relatorio do M7, secao 5). Nenhum humano as viu.

Fonte: `artifacts/m7/<rodada>/p_ilegivel_base.csv` (`make m7-avaliar`, fora do git). Saida:
`reports/<rodada>/inspecao_355.csv`, versionada como a amostra do M5
(`heuristicas_amostra_200.csv`): nome de arquivo e scores, sem foto nem leitura. As colunas
`legivel_inspecao` (1/0) e `inspetor` sao preenchidas na inspecao; regerar a amostra
preserva o que ja foi preenchido. O backend importa o CSV como rodada de calibracao
`inspecao_m7` (`make rotulos-importar-inspecao-m7`), antes de ligar `DETECTOR_QUALIDADE=onnx`.

Uso: python -m mlinsp.amostra_inspecao_m7 --rodada 20261016-213656
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

# politica@v1 (app/backend/src/inspecao/inference/politica.py), o mesmo de mlinsp.evaluate,
# que nao e importado aqui para nao carregar o torch.
LIMIAR_V1 = 0.85
log = logging.getLogger(__name__)
RAIZ_ML = Path(__file__).resolve().parents[2]
COLUNAS = ["arquivo", "lote", "p_ilegivel", "p_ruim_heuristica", "legivel_inspecao", "inspetor"]
PREENCHIDAS = ["legivel_inspecao", "inspetor"]


def so_o_modelo_rejeita(
    base: pd.DataFrame, limiar_modelo: float, limiar_v1: float = LIMIAR_V1
) -> pd.DataFrame:
    """Fotos rejeitadas pelo modelo (p_ilegivel >= limiar_modelo) e nao pela v1
    (p_ruim_heuristica < limiar_v1), em ordem de lote e arquivo, com as colunas da
    inspecao vazias."""
    sel = (base["p_ilegivel"] >= limiar_modelo) & (base["p_ruim_heuristica"] < limiar_v1)
    amostra = base.loc[sel, ["arquivo", "lote", "p_ilegivel", "p_ruim_heuristica"]].copy()
    for c in PREENCHIDAS:
        amostra[c] = ""
    return amostra.sort_values(["lote", "arquivo"]).reset_index(drop=True)[COLUNAS]


def preservar_inspecao(nova: pd.DataFrame, anterior: pd.DataFrame | None) -> pd.DataFrame:
    """Copia `legivel_inspecao` e `inspetor` ja preenchidos na amostra anterior (mesmo
    arquivo). Arquivo que saiu da amostra nova nao volta; se ele ja tinha veredito, avisa
    (WARNING): o rotulo dele continua no banco ate a rodada ser apagada e reimportada."""
    if anterior is None:
        return nova
    feito = anterior.set_index("arquivo")[PREENCHIDAS].fillna("").astype(str)
    perdidos = sorted(
        a for a in feito.index[feito["legivel_inspecao"] != ""] if a not in set(nova["arquivo"])
    )
    if perdidos:
        log.warning(
            "%d fotos ja inspecionadas sairam da amostra regerada (o veredito fica so no CSV "
            "antigo e no banco): %s",
            len(perdidos),
            ", ".join(perdidos),
        )
    saida = nova.copy()
    for c in PREENCHIDAS:
        saida[c] = saida["arquivo"].map(feito[c]).fillna(saida[c])
    return saida


def gerar(rodada: str, raiz: Path = RAIZ_ML) -> tuple[Path, int]:
    contrato = json.loads((raiz / "reports" / rodada / "qualidade.json").read_text("utf-8"))
    base = pd.read_csv(raiz / "artifacts" / "m7" / rodada / "p_ilegivel_base.csv")
    nova = so_o_modelo_rejeita(base, float(contrato["limiar_rejeita"]))
    destino = raiz / "reports" / rodada / "inspecao_355.csv"
    anterior = pd.read_csv(destino, dtype=str, keep_default_na=False) if destino.is_file() else None
    preservar_inspecao(nova, anterior).to_csv(destino, index=False)
    return destino, len(nova)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rodada", required=True)
    args = ap.parse_args(argv)
    destino, n = gerar(args.rodada)
    print(f"{n} fotos so o modelo rejeita -> {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
