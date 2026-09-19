"""Utilitarios de linha de comando da fila.

python -m inspecao.jobs.cli reprocessar --todos
python -m inspecao.jobs.cli reprocessar --lote PSP_EXTRATLEITIMPL_200526_0352
python -m inspecao.jobs.cli resumo
"""

import argparse
import logging
import sys

from sqlalchemy import select

from inspecao.db import SessionLocal
from inspecao.enums import TipoJob
from inspecao.jobs import fila
from inspecao.models import Lote

log = logging.getLogger(__name__)


def reprocessar(nomes: list[str] | None) -> int:
    with SessionLocal() as sessao:
        consulta = select(Lote).order_by(Lote.nome)
        if nomes:
            consulta = consulta.where(Lote.nome.in_(nomes))
        lotes = sessao.scalars(consulta).all()
        if not lotes:
            log.error("nenhum lote encontrado")
            return 1
        for lote in lotes:
            fila.enfileirar(sessao, TipoJob.REPROCESSAR_LOTE, {"lote_id": lote.id})
            log.info("reprocessar_lote enfileirado para %s", lote.nome)
        sessao.commit()
    return 0


def resumo() -> int:
    with SessionLocal() as sessao:
        for tipo, por_status in sorted(fila.resumo(sessao).items()):
            print(f"{tipo:24s} {por_status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Fila de jobs")
    sub = ap.add_subparsers(dest="comando", required=True)
    rp = sub.add_parser("reprocessar", help="enfileira triagem para as leituras em recebida")
    rp.add_argument("--lote", action="append", help="nome do lote (repetivel)")
    rp.add_argument("--todos", action="store_true")
    sub.add_parser("resumo", help="contagem de jobs por tipo e status")
    args = ap.parse_args(argv)

    if args.comando == "reprocessar":
        if not args.lote and not args.todos:
            ap.error("informe --lote NOME ou --todos")
        return reprocessar(None if args.todos else args.lote)
    return resumo()


if __name__ == "__main__":
    sys.exit(main())
