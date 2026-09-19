"""Fila de jobs no PostgreSQL. Sem broker externo.

Claim atomico com `FOR UPDATE SKIP LOCKED`: N workers em paralelo nunca pegam o
mesmo job. Backoff exponencial em falha, recuperacao de job orfao (preso em
`processando` alem do prazo) no inicio de cada loop.
"""

import logging
import traceback
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from inspecao.enums import StatusJob, TipoJob
from inspecao.models import Job

log = logging.getLogger(__name__)

PRAZO_ORFAO = timedelta(minutes=10)
LOTE_INSERCAO = 2000


def enfileirar(
    sessao: Session,
    tipo: TipoJob | str,
    payload: dict,
    *,
    agendado_para: datetime | None = None,
    max_tentativas: int = 3,
) -> Job:
    # Sem agendamento explicito, o relogio e o do banco (server_default now()):
    # o relogio do host e o do container podem divergir alguns milissegundos.
    campos = dict(
        tipo=TipoJob(tipo).value,
        payload=payload,
        status=StatusJob.PENDENTE.value,
        max_tentativas=max_tentativas,
    )
    if agendado_para is not None:
        campos["agendado_para"] = agendado_para
    job = Job(**campos)
    sessao.add(job)
    sessao.flush()
    return job


def enfileirar_em_lote(
    sessao: Session, tipo: TipoJob | str, payloads: Iterable[dict], *, max_tentativas: int = 3
) -> int:
    tipo_valor = TipoJob(tipo).value
    linhas = [
        {
            "tipo": tipo_valor,
            "payload": p,
            "status": StatusJob.PENDENTE.value,
            "max_tentativas": max_tentativas,
            # agendado_para omitido: server_default now() do banco
        }
        for p in payloads
    ]
    for i in range(0, len(linhas), LOTE_INSERCAO):
        sessao.bulk_insert_mappings(Job, linhas[i : i + LOTE_INSERCAO])
    return len(linhas)


# clock_timestamp(), nao now(): now() e o instante de INICIO da transacao e um
# job enfileirado dentro da mesma transacao ficaria "no futuro" para sempre.
_CLAIM = text(
    """
    UPDATE jobs SET
        status      = 'processando',
        iniciado_em = clock_timestamp(),
        tentativas  = tentativas + 1,
        worker_id   = :worker_id
    WHERE id IN (
        SELECT id FROM jobs
        WHERE status = 'pendente' AND agendado_para <= clock_timestamp()
        ORDER BY agendado_para, id
        FOR UPDATE SKIP LOCKED
        LIMIT :lote
    )
    RETURNING id
    """
)


def reivindicar(sessao: Session, worker_id: str, lote: int) -> Sequence[Job]:
    """Pega ate `lote` jobs pendentes para este worker. Faz commit: o claim precisa
    ficar visivel para os outros workers imediatamente."""
    ids = sessao.execute(_CLAIM, {"worker_id": worker_id, "lote": lote}).scalars().all()
    sessao.commit()
    if not ids:
        return []
    # populate_existing: se o chamador ja tinha esses Jobs na sessao (testes,
    # enfileirou e reivindicou na mesma sessao), os atributos sao recarregados.
    return sessao.scalars(
        select(Job)
        .where(Job.id.in_(ids))
        .order_by(Job.id)
        .execution_options(populate_existing=True)
    ).all()


def concluir(sessao: Session, job: Job) -> None:
    job.status = StatusJob.CONCLUIDO.value
    job.concluido_em = datetime.now(UTC)
    job.erro = None


def falhar(sessao: Session, job: Job, erro: BaseException) -> None:
    """Reagenda com backoff exponencial ou marca erro definitivo."""
    detalhe = "".join(traceback.format_exception(erro))[-4000:]
    if job.tentativas >= job.max_tentativas:
        job.status = StatusJob.ERRO.value
        job.concluido_em = datetime.now(UTC)
        job.erro = detalhe
        log.error(
            "job %s (%s) falhou definitivamente apos %d tentativas",
            job.id,
            job.tipo,
            job.tentativas,
        )
        return
    atraso = timedelta(seconds=2**job.tentativas)
    job.status = StatusJob.PENDENTE.value
    job.agendado_para = datetime.now(UTC) + atraso
    job.erro = detalhe
    job.worker_id = None
    log.warning(
        "job %s (%s) falhou, tentativa %d, reagendado em %ss",
        job.id,
        job.tipo,
        job.tentativas,
        atraso.seconds,
    )


def recuperar_orfaos(sessao: Session, prazo: timedelta = PRAZO_ORFAO) -> int:
    """Job em `processando` alem do prazo e considerado morto.

    Volta para a fila se ainda tem tentativas; senao vira `erro`. Sem esse limite
    um job que derruba o processo (OOM, imagem que trava o decoder) voltaria
    para sempre: cada tentativa mataria o worker de novo.
    """
    limite = datetime.now(UTC) - prazo
    morto = (Job.status == StatusJob.PROCESSANDO.value) & (Job.iniciado_em < limite)
    esgotados = sessao.execute(
        update(Job)
        .where(morto, Job.tentativas >= Job.max_tentativas)
        .values(
            status=StatusJob.ERRO.value,
            concluido_em=func.now(),
            erro="orfao: worker morreu durante o processamento e as tentativas se esgotaram",
        )
    ).rowcount
    devolvidos = sessao.execute(
        update(Job)
        .where(morto)
        .values(status=StatusJob.PENDENTE.value, worker_id=None, agendado_para=func.now())
    ).rowcount
    if devolvidos or esgotados:
        log.warning("jobs orfaos: %d devolvidos, %d marcados como erro", devolvidos, esgotados)
    return devolvidos


def resumo(sessao: Session) -> dict[str, dict[str, int]]:
    """Contagem por tipo e status, para /metricas."""
    linhas = sessao.execute(
        select(Job.tipo, Job.status, func.count()).group_by(Job.tipo, Job.status)
    )
    saida: dict[str, dict[str, int]] = {}
    for tipo, status, n in linhas:
        saida.setdefault(tipo, {})[status] = n
    return saida
