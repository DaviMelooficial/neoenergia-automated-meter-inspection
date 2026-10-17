"""Linha de comando da rotulagem.

python -m inspecao.rotulagem.cli importar-lab02 [--csv ...] [--controle rotulador/controle.dat]
python -m inspecao.rotulagem.cli importar-inspecao-m5 [--csv ml/reports/heuristicas_amostra_200.csv]
python -m inspecao.rotulagem.cli kappa [--rodada lab02 ...] [--relatorio ml/reports/kappa.md]
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
from inspecao.rotulagem import importador, inspecao_m5, relatorio, servico
from inspecao.rotulagem.consolidacao import CLASSES, LIMIAR_KAPPA

log = logging.getLogger(__name__)

CSV_LAB02 = Path("lab-02/rotulos/rotulos.csv")
CONTROLE_LAB02 = Path("rotulador/controle.dat")  # fora do git
SAIDA_PADRAO = Path("ml/artifacts/rotulos.csv")
CSV_INSPECAO_M5 = Path("ml/reports/heuristicas_amostra_200.csv")
RELATORIO_PADRAO = Path("ml/reports/kappa.md")  # versionado: so agregados


def _fmt(k: float | None) -> str:
    return "n/d" if k is None else f"{k:.3f}"


def importar_lab02(caminho: Path, caminho_controle: Path) -> int:
    caminho = get_settings().caminho_absoluto(caminho)
    caminho_controle = get_settings().caminho_absoluto(caminho_controle)
    with SessionLocal() as sessao:
        try:
            controle = None
            if caminho_controle.is_file():
                controle = importador.ler_controle(caminho_controle)
            else:
                log.warning("sem %s: coluna controle nao sera preenchida", caminho_controle)
            resumo = importador.importar(sessao, caminho, controle)
        except importador.CsvInvalido as erro:
            log.error("csv invalido: %s", erro)
            return 1
        sessao.commit()
    print(f"linhas lidas        {resumo.linhas} (puladas {resumo.puladas})")
    print(f"fotos no csv        {resumo.fotos_csv}")
    print(f"fotos mapeadas      {resumo.fotos_mapeadas}")
    print(f"fotos nao mapeadas  {len(resumo.nao_mapeadas)}")
    if resumo.fotos_controle is not None:
        print(f"fotos de controle   {resumo.fotos_controle}")
    for nome in resumo.nao_mapeadas:
        print(f"  {nome}")
    print(
        f"rotulos lab02       {resumo.rotulos_no_banco} (inseridos {resumo.inseridos}, "
        f"atualizados {resumo.atualizados}, inalterados {resumo.inalterados})"
    )
    return 0


def kappa(rodadas: list[RodadaRotulagem], relatorio_md: Path) -> int:
    with SessionLocal() as sessao:
        resumo = servico.resumir(sessao, rodadas)
        contagens = servico.contagem_por_rodada(sessao)
    pares, medio = resumo.pares, resumo.medio
    print(f"{'classe':16s} {'kappa':>7s} {'n':>5s}  gate {LIMIAR_KAPPA}")
    for c in CLASSES:
        k = medio[c]
        print(f"{c:16s} {_fmt(k.kappa):>7s} {k.n:5d}  {'passa' if k.passa else 'NAO passa'}")
    for p in pares:
        print(
            f"\n{p.a} x {p.b} ({p.n} fotos): "
            + ", ".join(f"{c}={_fmt(p.kappa[c])}" for c in CLASSES)
        )
    texto = relatorio.gerar(
        rodadas,
        medio,
        pares,
        contagens,
        rotulos=resumo.rotulos,
        fotos=resumo.fotos,
        fotos_dupla=resumo.fotos_dupla,
        fotos_empate=resumo.fotos_empate,
        controle_com_dupla=resumo.controle_com_dupla,
    )
    relatorio_md = get_settings().caminho_absoluto(relatorio_md)
    relatorio_md.parent.mkdir(parents=True, exist_ok=True)
    relatorio_md.write_text(texto, encoding="utf-8")
    print(f"\nrelatorio em {relatorio_md}")
    return 0


def exportar(saida: Path, rodadas: list[RodadaRotulagem]) -> int:
    saida = get_settings().caminho_absoluto(saida)
    with SessionLocal() as sessao:
        texto, n = servico.exportar_csv(sessao, rodadas)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(texto, encoding="utf-8")
    print(f"{n} fotos consolidadas em {saida}")
    return 0


def importar_inspecao_m5(caminho: Path) -> int:
    caminho = get_settings().caminho_absoluto(caminho)
    with SessionLocal() as sessao:
        try:
            resumo = inspecao_m5.importar(sessao, caminho)
        except inspecao_m5.CsvInvalido as erro:
            log.error("csv invalido: %s", erro)
            return 1
        sessao.commit()
    print(f"linhas lidas        {resumo.linhas}")
    print(
        f"rotulos inspecao_m5 {resumo.gravados} ({resumo.inspecionados} inspecionadas, "
        f"{resumo.pendentes} pendentes)"
    )
    print(f"nao mapeadas        {len(resumo.nao_mapeadas)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Rotulagem (M6)")
    sub = ap.add_subparsers(dest="comando", required=True)
    imp = sub.add_parser("importar-lab02", help="importa os rotulos do Lab 2 (rodada lab02)")
    imp.add_argument("--csv", type=Path, default=CSV_LAB02)
    imp.add_argument("--controle", type=Path, default=CONTROLE_LAB02)
    insp = sub.add_parser(
        "importar-inspecao-m5", help="importa a inspecao das 200 fotos do M5 (rodada inspecao_m5)"
    )
    insp.add_argument("--csv", type=Path, default=CSV_INSPECAO_M5)
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
        else:
            p.add_argument("--relatorio", type=Path, default=RELATORIO_PADRAO)
    args = ap.parse_args(argv)

    if args.comando == "importar-lab02":
        return importar_lab02(args.csv, args.controle)
    if args.comando == "importar-inspecao-m5":
        return importar_inspecao_m5(args.csv)
    rodadas = args.rodada or [RodadaRotulagem.LAB02]
    try:
        if args.comando == "kappa":
            return kappa(rodadas, args.relatorio)
        return exportar(args.saida, rodadas)
    except (servico.ColisaoDeRotulador, servico.RodadaSemEsquemaCompleto) as erro:
        log.error("%s", erro)
        return 1


if __name__ == "__main__":
    sys.exit(main())
