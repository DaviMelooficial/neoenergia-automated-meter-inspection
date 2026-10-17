"""Linha de comando da rotulagem.

python -m inspecao.rotulagem.cli importar-lab02 [--csv lab-02/rotulos/rotulos.csv]
python -m inspecao.rotulagem.cli kappa [--rodada lab02 ...]
python -m inspecao.rotulagem.cli exportar [--saida ml/artifacts/rotulos.csv] [--rodada lab02 ...]

Caminhos relativos sao resolvidos contra a raiz do repositorio.
"""

import argparse
import logging
import sys
from pathlib import Path

from inspecao.config import get_settings
from inspecao.db import SessionLocal
from inspecao.enums import RodadaRotulagem
from inspecao.rotulagem import importador, servico
from inspecao.rotulagem.consolidacao import CLASSES, LIMIAR_KAPPA, kappa_medio, kappa_por_par

log = logging.getLogger(__name__)

CSV_LAB02 = Path("lab-02/rotulos/rotulos.csv")
SAIDA_PADRAO = Path("ml/artifacts/rotulos.csv")


def _fmt(k: float | None) -> str:
    return "n/d" if k is None else f"{k:.3f}"


def importar_lab02(caminho: Path) -> int:
    caminho = get_settings().caminho_absoluto(caminho)
    with SessionLocal() as sessao:
        try:
            resumo = importador.importar(sessao, caminho)
        except importador.CsvInvalido as erro:
            log.error("csv invalido: %s", erro)
            return 1
        sessao.commit()
    print(f"linhas lidas        {resumo.linhas} (puladas {resumo.puladas})")
    print(f"fotos no csv        {resumo.fotos_csv}")
    print(f"fotos mapeadas      {resumo.fotos_mapeadas}")
    print(f"fotos nao mapeadas  {len(resumo.nao_mapeadas)}")
    for nome in resumo.nao_mapeadas:
        print(f"  {nome}")
    print(
        f"rotulos lab02       {resumo.rotulos_no_banco} (inseridos {resumo.inseridos}, "
        f"atualizados {resumo.atualizados}, inalterados {resumo.inalterados})"
    )
    return 0


def kappa(rodadas: list[RodadaRotulagem]) -> int:
    with SessionLocal() as sessao:
        pares = kappa_por_par(servico.votos(sessao, rodadas))
    medio = kappa_medio(pares)
    print(f"{'classe':16s} {'kappa':>7s} {'n':>5s}  gate {LIMIAR_KAPPA}")
    for c in CLASSES:
        k = medio[c]
        print(f"{c:16s} {_fmt(k.kappa):>7s} {k.n:5d}  {'passa' if k.passa else 'NAO passa'}")
    for p in pares:
        print(
            f"\n{p.a} x {p.b} ({p.n} fotos): "
            + ", ".join(f"{c}={_fmt(p.kappa[c])}" for c in CLASSES)
        )
    return 0


def exportar(saida: Path, rodadas: list[RodadaRotulagem]) -> int:
    saida = get_settings().caminho_absoluto(saida)
    with SessionLocal() as sessao:
        texto, n = servico.exportar_csv(sessao, rodadas)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(texto, encoding="utf-8")
    print(f"{n} fotos consolidadas em {saida}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Rotulagem (M6)")
    sub = ap.add_subparsers(dest="comando", required=True)
    imp = sub.add_parser("importar-lab02", help="importa os rotulos do Lab 2 (rodada lab02)")
    imp.add_argument("--csv", type=Path, default=CSV_LAB02)
    for nome, ajuda in (("kappa", "kappa por classe"), ("exportar", "csv consolidado para o ml/")):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument(
            "--rodada",
            action="append",
            type=RodadaRotulagem,
            choices=list(RodadaRotulagem),
            help="pode repetir; padrao: lab02",
        )
        if nome == "exportar":
            p.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    args = ap.parse_args(argv)

    if args.comando == "importar-lab02":
        return importar_lab02(args.csv)
    rodadas = args.rodada or [RodadaRotulagem.LAB02]
    try:
        if args.comando == "kappa":
            return kappa(rodadas)
        return exportar(args.saida, rodadas)
    except servico.ColisaoDeRotulador as erro:
        log.error("%s", erro)
        return 1


if __name__ == "__main__":
    sys.exit(main())
