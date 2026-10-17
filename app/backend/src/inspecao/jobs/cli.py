"""Utilitarios de linha de comando da fila.

python -m inspecao.jobs.cli reprocessar --todos
python -m inspecao.jobs.cli reprocessar --lote PSP_EXTRATLEITIMPL_200526_0352
python -m inspecao.jobs.cli recalcular --todos --politica politica@v1
python -m inspecao.jobs.cli resumo
"""

import argparse
import logging
import sys

from sqlalchemy import select

from inspecao.config import Settings, get_settings
from inspecao.db import SessionLocal
from inspecao.enums import TipoJob
from inspecao.inference import politica as politica_mod
from inspecao.inference.detector import nome_do_detector_configurado
from inspecao.jobs import fila
from inspecao.models import Lote

log = logging.getLogger(__name__)


def _enfileirar_por_lote(nomes: list[str] | None, tipo: TipoJob, extra: dict) -> int:
    with SessionLocal() as sessao:
        consulta = select(Lote).order_by(Lote.nome)
        if nomes:
            consulta = consulta.where(Lote.nome.in_(nomes))
        lotes = sessao.scalars(consulta).all()
        if not lotes:
            log.error("nenhum lote encontrado")
            return 1
        for lote in lotes:
            fila.enfileirar(sessao, tipo, {"lote_id": lote.id, **extra})
            log.info("%s enfileirado para %s", tipo.value, lote.nome)
        sessao.commit()
    return 0


def reprocessar(nomes: list[str] | None) -> int:
    return _enfileirar_por_lote(nomes, TipoJob.REPROCESSAR_LOTE, {})


def politica_padrao(settings: Settings) -> str:
    """A politica do detector configurado (a mesma que o worker usaria), nao uma fixa:
    recalcular com a v1 enquanto o worker roda o modelo desfaria a v2 em silencio."""
    return politica_mod.politica_para(nome_do_detector_configurado(settings)).versao


def recalcular(nomes: list[str] | None, versao: str | None) -> int:
    versao = versao or politica_padrao(get_settings())
    politica_mod.obter(versao)  # falha aqui, nao no worker, se a versao nao existe
    log.info("recalcular_decisao com %s", versao)
    return _enfileirar_por_lote(nomes, TipoJob.RECALCULAR_DECISAO, {"politica": versao})


def resumo() -> int:
    with SessionLocal() as sessao:
        for tipo, por_status in sorted(fila.resumo(sessao).items()):
            print(f"{tipo:24s} {por_status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Fila de jobs")
    sub = ap.add_subparsers(dest="comando", required=True)
    rp = sub.add_parser(
        "reprocessar", help="enfileira triagem (recebida, ou sem resultado do detector atual)"
    )
    rc = sub.add_parser("recalcular", help="reaplica uma politica sem reabrir imagem")
    for p in (rp, rc):
        p.add_argument("--lote", action="append", help="nome do lote (repetivel)")
        p.add_argument("--todos", action="store_true")
    rc.add_argument("--politica", default=None, help="versao (padrao: a do detector configurado)")
    sub.add_parser("resumo", help="contagem de jobs por tipo e status")
    args = ap.parse_args(argv)

    if args.comando in ("reprocessar", "recalcular"):
        if not args.lote and not args.todos:
            ap.error("informe --lote NOME ou --todos")
        nomes = None if args.todos else args.lote
        if args.comando == "reprocessar":
            return reprocessar(nomes)
        return recalcular(nomes, args.politica)
    return resumo()


if __name__ == "__main__":
    sys.exit(main())
