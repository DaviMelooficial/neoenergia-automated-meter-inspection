"""Aceite do M3: com DetectorNulo, ingerir um lote e ver 100% das leituras com
foto chegarem a `fila_humana`, com historico e resultado de inferencia."""

import pytest
from sqlalchemy import func, select

from inspecao.config import get_settings
from inspecao.enums import StatusJob, StatusLeitura, TipoJob
from inspecao.inference.detector import DetectorNulo
from inspecao.jobs import fila
from inspecao.jobs.handlers import Contexto, reprocessar_lote
from inspecao.jobs.worker import processar_um
from inspecao.models import Foto, HistoricoStatus, Job, Leitura, ResultadoInferencia
from tests import lote_sintetico as ls


@pytest.fixture(scope="module")
def staging(tmp_path_factory):
    return ls.criar_staging(tmp_path_factory.mktemp("staging"))


def esvaziar_fila(ctx, sessao) -> int:
    total = 0
    while jobs := fila.reivindicar(sessao, "teste", lote=3):
        for job in jobs:
            processar_um(ctx, sessao, job)
        total += len(jobs)
    return total


def test_fluxo_completo_com_detector_nulo(db_session, staging, tmp_path):
    resultado, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    ctx = Contexto(settings=get_settings(), detector=DetectorNulo(), storage=storage)

    processados = esvaziar_fila(ctx, db_session)
    assert processados == resultado.jobs_enfileirados == 5

    db_session.expire_all()
    por_status = dict(
        db_session.execute(
            select(Leitura.status_atual, func.count())
            .where(Leitura.lote_id == lote.id)
            .group_by(Leitura.status_atual)
        ).all()
    )
    assert por_status == {
        StatusLeitura.FILA_HUMANA.value: len(ls.LINHAS_COM_FOTO),
        StatusLeitura.SEM_FOTO.value: len(ls.LINHAS_SEM_FOTO),
    }

    # Historico completo de uma leitura com foto: recebida -> triagem -> fila
    leitura = db_session.scalar(
        select(Leitura).where(Leitura.lote_id == lote.id, Leitura.linha_csv == 2)
    )
    assert [h.status for h in leitura.historico] == ["recebida", "triagem_qualidade", "fila_humana"]
    assert leitura.historico[1].ator == "modelo:nulo@v0"
    assert leitura.historico[2].detalhe == {"politica": "politica@v0", "decisao": "humano"}
    assert leitura.historico[2].confianca is None

    # Uma inferencia por foto principal, com saida vazia do detector nulo.
    inferencias = db_session.scalars(
        select(ResultadoInferencia).join(Foto).where(Foto.lote_id == lote.id)
    ).all()
    assert len(inferencias) == len(ls.LINHAS_COM_FOTO)
    assert all(i.modelo == "nulo@v0" and i.saida == {} for i in inferencias)
    assert all(i.latencia_ms is not None for i in inferencias)

    # Todos os jobs concluidos, nenhum erro.
    por_job = dict(db_session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    assert por_job == {StatusJob.CONCLUIDO.value: 5}


def test_triagem_repetida_e_inofensiva(db_session, staging, tmp_path):
    """Um job duplicado para uma leitura ja triada conclui sem transicionar de novo."""
    resultado, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    ctx = Contexto(settings=get_settings(), detector=DetectorNulo(), storage=storage)
    esvaziar_fila(ctx, db_session)

    leitura = db_session.scalar(
        select(Leitura).where(Leitura.lote_id == lote.id, Leitura.linha_csv == 2)
    )
    fila.enfileirar(db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": leitura.id})
    assert esvaziar_fila(ctx, db_session) == 1
    db_session.expire_all()
    assert leitura.status_atual == StatusLeitura.FILA_HUMANA.value
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(HistoricoStatus)
            .where(HistoricoStatus.leitura_id == leitura.id)
        )
        == 3
    )


def test_reprocessar_lote_so_enfileira_quem_esta_em_recebida(db_session, staging, tmp_path):
    resultado, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    ctx = Contexto(settings=get_settings(), detector=DetectorNulo(), storage=storage)
    # Descarta os jobs da ingestao para simular um lote antigo sem fila.
    for job in fila.reivindicar(db_session, "limpeza", lote=100):
        fila.concluir(db_session, job)
    db_session.flush()

    reprocessar_lote(ctx, db_session, {"lote_id": lote.id})
    pendentes = db_session.scalars(select(Job).where(Job.status == StatusJob.PENDENTE.value)).all()
    assert len(pendentes) == len(ls.LINHAS_COM_FOTO)
    assert esvaziar_fila(ctx, db_session) == len(ls.LINHAS_COM_FOTO)
    db_session.expire_all()
    assert db_session.scalar(
        select(func.count())
        .select_from(Leitura)
        .where(Leitura.lote_id == lote.id, Leitura.status_atual == StatusLeitura.FILA_HUMANA)
    ) == len(ls.LINHAS_COM_FOTO)
