"""Fila de jobs no Postgres: claim, backoff, orfaos e o teste de concorrencia do
aceite do M3 (2 workers, 500 jobs, cada job exatamente uma vez)."""

import threading
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from inspecao.enums import StatusJob, TipoJob
from inspecao.jobs import fila
from inspecao.jobs.handlers import Contexto
from inspecao.jobs.worker import processar_um
from inspecao.models import Job


def test_enfileirar_e_reivindicar_em_ordem(db_session):
    a = fila.enfileirar(db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": 1})
    b = fila.enfileirar(db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": 2})
    futuro = fila.enfileirar(
        db_session,
        TipoJob.TRIAGEM_QUALIDADE,
        {"leitura_id": 3},
        agendado_para=datetime.now(UTC) + timedelta(hours=1),
    )
    pegos = fila.reivindicar(db_session, "w1", lote=10)
    assert [j.id for j in pegos] == [a.id, b.id]  # o futuro fica
    assert all(j.status == StatusJob.PROCESSANDO and j.worker_id == "w1" for j in pegos)
    assert all(j.tentativas == 1 for j in pegos)
    assert db_session.get(Job, futuro.id).status == StatusJob.PENDENTE
    assert fila.reivindicar(db_session, "w2", lote=10) == []


def test_falhar_reagenda_com_backoff_e_depois_marca_erro(db_session):
    job = fila.enfileirar(
        db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": 1}, max_tentativas=2
    )
    (job,) = fila.reivindicar(db_session, "w1", 1)
    antes = datetime.now(UTC)
    fila.falhar(db_session, job, RuntimeError("boom"))
    assert job.status == StatusJob.PENDENTE
    assert job.worker_id is None
    assert job.agendado_para >= antes + timedelta(seconds=2)  # 2**1
    assert "boom" in job.erro
    # Nao e reivindicavel agora: esta agendada para o futuro.
    assert fila.reivindicar(db_session, "w1", 1) == []

    # Relogio do banco, nao o do host: o claim compara com clock_timestamp() do
    # Postgres, e o container pode estar alguns ms atras do host.
    job.agendado_para = func.now()
    db_session.flush()
    (job,) = fila.reivindicar(db_session, "w1", 1)
    assert job.tentativas == 2
    fila.falhar(db_session, job, RuntimeError("boom 2"))
    assert job.status == StatusJob.ERRO
    assert job.concluido_em is not None


def test_recuperar_orfaos(db_session):
    job = fila.enfileirar(db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": 1})
    (job,) = fila.reivindicar(db_session, "morto", 1)
    assert fila.recuperar_orfaos(db_session) == 0  # acabou de comecar
    job.iniciado_em = datetime.now(UTC) - timedelta(minutes=11)
    db_session.flush()
    assert fila.recuperar_orfaos(db_session) == 1
    db_session.expire_all()
    assert job.status == StatusJob.PENDENTE and job.worker_id is None


def test_processar_um_com_handler_que_falha(db_session, monkeypatch):
    from inspecao.jobs import handlers

    def explode(ctx, sessao, payload):
        raise ValueError("handler quebrou")

    monkeypatch.setitem(handlers.REGISTRY, TipoJob.TRIAGEM_QUALIDADE.value, explode)
    monkeypatch.setattr("inspecao.jobs.worker.REGISTRY", handlers.REGISTRY)
    job = fila.enfileirar(
        db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": 1}, max_tentativas=1
    )
    (job,) = fila.reivindicar(db_session, "w1", 1)
    ctx = Contexto(settings=None, detector=None, storage=None)  # handler nao usa
    assert processar_um(ctx, db_session, job) is False
    db_session.expire_all()
    assert job.status == StatusJob.ERRO
    assert "handler quebrou" in job.erro


def test_tipo_desconhecido_vira_erro(db_session):
    job = Job(tipo="inexistente", payload={}, max_tentativas=1)
    db_session.add(job)
    db_session.flush()
    (job,) = fila.reivindicar(db_session, "w1", 1)
    ctx = Contexto(settings=None, detector=None, storage=None)
    assert processar_um(ctx, db_session, job) is False
    db_session.expire_all()
    assert job.status == StatusJob.ERRO and "desconhecido" in job.erro


# --------------------------------------------------------------- concorrencia


@pytest.fixture
def engine_teste():
    from tests.conftest import SERVIDOR_OK

    if not SERVIDOR_OK:
        pytest.skip("servidor postgres indisponivel")
    from inspecao.db import engine

    # Este teste precisa de commits reais em varias conexoes: nao cabe na
    # transacao revertida do db_session. Limpa a tabela antes e depois.
    with engine.begin() as c:
        c.execute(text("TRUNCATE jobs RESTART IDENTITY"))
    yield engine
    with engine.begin() as c:
        c.execute(text("TRUNCATE jobs RESTART IDENTITY"))


def test_dois_workers_processam_cada_job_exatamente_uma_vez(engine_teste, monkeypatch):
    """Aceite do M3: 2 workers concorrentes sobre 500 jobs, zero duplicata, zero perda."""
    from inspecao.jobs import handlers, worker

    N = 500
    fabrica = sessionmaker(bind=engine_teste, expire_on_commit=False)
    with fabrica() as s:
        fila.enfileirar_em_lote(s, TipoJob.TRIAGEM_QUALIDADE, ({"leitura_id": i} for i in range(N)))
        s.commit()

    executados: list[int] = []
    trava = threading.Lock()

    def registrar(ctx, sessao: Session, payload: dict) -> None:
        with trava:
            executados.append(payload["leitura_id"])

    monkeypatch.setitem(handlers.REGISTRY, TipoJob.TRIAGEM_QUALIDADE.value, registrar)
    monkeypatch.setattr(worker, "REGISTRY", handlers.REGISTRY)
    ctx = Contexto(settings=None, detector=None, storage=None)

    def rodar(worker_id: str) -> None:
        while True:
            if worker.uma_volta(ctx, fabrica, worker_id, lote=7) == 0:
                return

    t1 = threading.Thread(target=rodar, args=("w1",))
    t2 = threading.Thread(target=rodar, args=("w2",))
    t1.start()
    t2.start()
    t1.join(timeout=120)
    t2.join(timeout=120)
    assert not t1.is_alive() and not t2.is_alive()

    contagem = Counter(executados)
    assert len(executados) == N, f"{len(executados)} execucoes para {N} jobs"
    assert set(contagem.values()) == {1}, "algum job rodou mais de uma vez"
    assert set(contagem) == set(range(N))

    with fabrica() as s:
        por_status = dict(s.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        assert por_status == {StatusJob.CONCLUIDO.value: N}
        por_worker = dict(
            s.execute(select(Job.worker_id, func.count()).group_by(Job.worker_id)).all()
        )
        assert set(por_worker) == {"w1", "w2"}, "os dois workers precisam ter trabalhado"
