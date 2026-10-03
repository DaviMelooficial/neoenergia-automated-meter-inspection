"""Loop do worker. Uso: python -m inspecao.jobs.worker

Desligamento limpo em SIGTERM/SIGINT: termina os jobs ja reivindicados e nao
faz novo claim. Jobs presos em `processando` por outro worker morto sao
recuperados no inicio de cada volta.
"""

import logging
import signal
import socket
import sys
import threading
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from inspecao.config import Settings, get_settings
from inspecao.db import SessionLocal
from inspecao.inference.detector import carregar_detector
from inspecao.jobs import fila
from inspecao.jobs.handlers import REGISTRY, Contexto
from inspecao.models import Job
from inspecao.storage.local import LocalFileStorage

log = logging.getLogger("inspecao.worker")


def processar_um(ctx: Contexto, sessao: Session, job: Job) -> bool:
    """Executa um job ja reivindicado. Devolve True se concluiu."""
    handler: Callable | None = REGISTRY.get(job.tipo)
    try:
        if handler is None:
            raise LookupError(f"tipo de job desconhecido: {job.tipo}")
        with sessao.begin_nested():
            handler(ctx, sessao, dict(job.payload))
        fila.concluir(sessao, job)
        sessao.commit()
        return True
    except Exception as erro:
        sessao.rollback()
        # O job precisa ser relido: o rollback expirou o objeto.
        job = sessao.get(Job, job.id)
        fila.falhar(sessao, job, erro)
        sessao.commit()
        return False


def uma_volta(ctx: Contexto, fabrica: sessionmaker, worker_id: str, lote: int) -> int:
    """Recupera orfaos, reivindica ate `lote` jobs e processa. Devolve quantos pegou."""
    with fabrica() as sessao:
        fila.recuperar_orfaos(sessao)
        sessao.commit()
        jobs = fila.reivindicar(sessao, worker_id, lote)
        for job in jobs:
            processar_um(ctx, sessao, job)
        return len(jobs)


class Worker:
    def __init__(self, settings: Settings | None = None, fabrica: sessionmaker = SessionLocal):
        self.settings = settings or get_settings()
        self.fabrica = fabrica
        self.worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self.parar = threading.Event()
        self.ctx = Contexto(
            settings=self.settings,
            detector=carregar_detector(self.settings),  # uma vez por worker
            storage=LocalFileStorage(),
        )

    def _sinal(self, signum, frame) -> None:
        log.info("sinal %s recebido, encerrando apos os jobs em andamento", signum)
        self.parar.set()

    def executar(self) -> None:
        signal.signal(signal.SIGTERM, self._sinal)
        signal.signal(signal.SIGINT, self._sinal)
        log.info("worker %s iniciado, detector=%s", self.worker_id, self.ctx.detector.nome)
        while not self.parar.is_set():
            try:
                n = uma_volta(
                    self.ctx, self.fabrica, self.worker_id, self.settings.worker_concurrency
                )
            except Exception:
                # Banco fora, conexao caiu: nao derruba o processo. Os jobs que
                # ficaram em `processando` voltam pela recuperacao de orfaos.
                log.exception("falha na volta do worker; nova tentativa em breve")
                n = 0
            if n == 0:
                self.parar.wait(self.settings.worker_poll_segundos)
        log.info("worker %s encerrado", self.worker_id)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    Worker().executar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
