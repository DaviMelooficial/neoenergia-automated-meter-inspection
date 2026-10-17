"""Utilitarios de linha de comando da fila.

python -m inspecao.jobs.cli reprocessar --todos
python -m inspecao.jobs.cli reprocessar --lote PSP_EXTRATLEITIMPL_200526_0352
python -m inspecao.jobs.cli recalcular --todos --politica politica@v1
python -m inspecao.jobs.cli resumo
python -m inspecao.jobs.cli auditoria-sortear
"""

import argparse
import logging
import sys

from sqlalchemy import select

from inspecao.db import SessionLocal
from inspecao.enums import TipoJob
from inspecao.inference import politica as politica_mod
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


def payload_recalcular(versao: str | None) -> dict:
    """Sem versao explicita o payload vai sem `politica`: o worker aplica a dele, a do
    detector que ele carregou e cujo contrato validou. O CLI nao adivinha pela config, que
    pode estar apontando um modelo sem politica registrada."""
    if versao is None:
        return {}
    politica_mod.obter(versao)  # falha aqui, nao no worker, se a versao nao existe
    return {"politica": versao}


def recalcular(nomes: list[str] | None, versao: str | None) -> int:
    if versao is None:
        log.warning(
            "recalcular_decisao sem --politica: cada lote usa a politica do worker que pegar "
            "o job; com workers de detectores diferentes no ar, lotes podem receber politicas "
            "diferentes"
        )
    else:
        log.info("recalcular_decisao com %s", versao)
    return _enfileirar_por_lote(nomes, TipoJob.RECALCULAR_DECISAO, payload_recalcular(versao))


def resumo() -> int:
    with SessionLocal() as sessao:
        for tipo, por_status in sorted(fila.resumo(sessao).items()):
            print(f"{tipo:24s} {por_status}")
    return 0


def auditoria_sortear() -> int:
    """Sorteio da auditoria amostral para as decisoes de fila sem sorteio (as gravadas
    antes do M8) e para as decisoes da maquina (auto-aprovadas e auto-rejeitadas).
    Idempotente; contas de teste ficam de fora."""
    from inspecao import auditoria

    with SessionLocal() as sessao:
        # commit por item: cada leitura sorteada fica travada so ate o proprio commit
        fila_humana = auditoria.sortear_decididas(sessao, commit_por_item=True)
        auto = auditoria.sortear_automaticas(sessao, commit_por_item=True)
        sessao.commit()
    print(
        f"sorteadas para segundo revisor: {fila_humana} da fila, "
        f"{auto['auto_aprovada']} auto-aprovadas, {auto['auto_rejeitada']} auto-rejeitadas"
    )
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
    rc.add_argument(
        "--politica",
        default=None,
        help="versao (padrao: a politica do worker que pegar o job; com workers de detectores "
        "diferentes no ar, cada lote pode receber uma politica diferente)",
    )
    sub.add_parser("resumo", help="contagem de jobs por tipo e status")
    sub.add_parser(
        "auditoria-sortear", help="sorteia para auditoria amostral as decisoes sem sorteio"
    )
    args = ap.parse_args(argv)

    if args.comando == "auditoria-sortear":
        return auditoria_sortear()

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
