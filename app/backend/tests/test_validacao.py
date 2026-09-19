"""Integracao da validacao (M2) com o banco de teste, usando o lote sintetico."""

import pytest
from sqlalchemy import func, select

from inspecao.enums import Ator, Severidade, StatusLeitura, TipoInconsistencia, TipoJob
from inspecao.estados import TransicaoInvalida, transicionar, transicionar_em_lote
from inspecao.ingest.batch import BatchFolderSource, ingerir
from inspecao.models import Foto, HistoricoStatus, Job, Leitura, Lote, Quarentena
from inspecao.storage.local import LocalFileStorage
from tests import lote_sintetico as ls


@pytest.fixture(scope="module")
def staging(tmp_path_factory):
    return ls.criar_staging(tmp_path_factory.mktemp("staging"))


@pytest.fixture
def ingerido(db_session, staging, tmp_path):
    resultado, lote, _ = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    return resultado, lote


def contar_quarentena(sessao, lote_id) -> dict[str, int]:
    linhas = sessao.execute(
        select(Quarentena.tipo, func.count())
        .where(Quarentena.lote_id == lote_id)
        .group_by(Quarentena.tipo)
    ).all()
    return dict(linhas)


def leitura_da_linha(sessao, lote_id, linha_csv) -> Leitura:
    return sessao.scalar(
        select(Leitura).where(Leitura.lote_id == lote_id, Leitura.linha_csv == linha_csv)
    )


def test_resultado_da_ingestao(ingerido):
    resultado, _ = ingerido
    assert resultado.leituras == 10
    assert resultado.fotos == 6
    assert resultado.fotos_orfas == 1
    assert resultado.fotos_ausentes == [ls.foto(7)]
    assert resultado.validacao.sem_foto == 5  # linhas 3, 5, 7, 9, 11
    assert resultado.validacao.seguem_para_triagem == 5  # linhas 2, 4, 6, 8, 10
    assert resultado.validacao.bloqueantes == 3  # linhas 3, 7 e 11
    assert resultado.jobs_enfileirados == 5


def test_jobs_de_triagem_enfileirados_para_quem_tem_foto(ingerido, db_session):
    _, lote = ingerido
    com_foto = {leitura_da_linha(db_session, lote.id, n).id for n in ls.LINHAS_COM_FOTO}
    jobs = db_session.scalars(select(Job).where(Job.tipo == TipoJob.TRIAGEM_QUALIDADE)).all()
    assert {j.payload["leitura_id"] for j in jobs} == com_foto
    assert all(j.status == "pendente" for j in jobs)


def test_quarentena_por_tipo(ingerido, db_session):
    _, lote = ingerido
    assert contar_quarentena(db_session, lote.id) == {
        TipoInconsistencia.NOTA_EXIGE_SEM_FOTO.value: 1,
        TipoInconsistencia.FOTO_SEM_EXIGENCIA.value: 2,  # linhas 4 e 10
        TipoInconsistencia.CODIGO_NAO_MAPEADO.value: 1,
        TipoInconsistencia.MEDIDOR_MULTIPLO.value: 1,
        TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value: 2,  # linhas 7 e 11
        TipoInconsistencia.POSICAO_INVALIDA.value: 1,
        TipoInconsistencia.POSICAO_AUSENTE.value: 1,
        TipoInconsistencia.FOTO_ORFA.value: 1,
    }


def test_severidades(ingerido, db_session):
    _, lote = ingerido
    bloqueantes = db_session.scalars(
        select(Quarentena.tipo).where(
            Quarentena.lote_id == lote.id,
            Quarentena.severidade == Severidade.BLOQUEANTE.value,
        )
    ).all()
    assert sorted(bloqueantes) == sorted(
        [
            TipoInconsistencia.NOTA_EXIGE_SEM_FOTO.value,
            TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value,
            TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value,
        ]
    )


def test_foto_ausente_guarda_o_nome_prometido(ingerido, db_session):
    _, lote = ingerido
    por_leitura = {
        q.leitura_id: q
        for q in db_session.scalars(
            select(Quarentena).where(
                Quarentena.lote_id == lote.id,
                Quarentena.tipo == TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value,
            )
        )
    }
    ausente = por_leitura[leitura_da_linha(db_session, lote.id, 7).id]
    assert ausente.detalhe == {"foto_nome": ls.foto(7)}

    duplicada = por_leitura[leitura_da_linha(db_session, lote.id, 11).id]
    assert duplicada.detalhe["foto_nome"] == ls.foto(1)
    assert "outra linha" in duplicada.detalhe["motivo"]
    assert db_session.scalar(select(Foto.leitura_id).where(Foto.nome_arquivo == ls.foto(1))) == (
        leitura_da_linha(db_session, lote.id, 2).id
    )


def test_orfa_aponta_para_a_foto_e_nao_para_leitura(ingerido, db_session):
    _, lote = ingerido
    q = db_session.scalar(
        select(Quarentena).where(
            Quarentena.lote_id == lote.id, Quarentena.tipo == TipoInconsistencia.FOTO_ORFA.value
        )
    )
    assert q.leitura_id is None
    assert q.foto_id == db_session.scalar(
        select(Foto.id).where(Foto.nome_arquivo == ls.foto(999, 2))
    )
    assert q.detalhe["sequencia"] == 2
    assert q.detalhe["leitura_provavel_id"] is None


def test_status_por_linha(ingerido, db_session):
    _, lote = ingerido
    for linha in ls.LINHAS_COM_FOTO:
        assert leitura_da_linha(db_session, lote.id, linha).status_atual == "recebida", linha
    for linha in ls.LINHAS_SEM_FOTO:
        assert leitura_da_linha(db_session, lote.id, linha).status_atual == "sem_foto", linha


def test_historico_tem_nascimento_e_transicao(ingerido, db_session):
    _, lote = ingerido
    sem_foto = leitura_da_linha(db_session, lote.id, 3)
    hist = db_session.scalars(
        select(HistoricoStatus)
        .where(HistoricoStatus.leitura_id == sem_foto.id)
        .order_by(HistoricoStatus.id)
    ).all()
    assert [h.status for h in hist] == ["recebida", "sem_foto"]
    assert {h.ator for h in hist} == {Ator.INGESTAO.value}
    assert hist[1].detalhe == {"origem": "consistencia_ingestao"}

    normal = leitura_da_linha(db_session, lote.id, 2)
    assert [h.status for h in normal.historico] == ["recebida"]


def test_reingestao_e_idempotente(ingerido, db_session, staging, tmp_path):
    _, lote = ingerido
    antes = contar_quarentena(db_session, lote.id)
    r = ingerir(
        db_session, BatchFolderSource(staging), ls.LOTE, LocalFileStorage(raiz=tmp_path / "s2")
    )
    assert r.pulado
    assert contar_quarentena(db_session, lote.id) == antes
    assert (
        db_session.scalar(select(func.count()).select_from(Lote).where(Lote.nome == ls.LOTE)) == 1
    )


def test_transicionar_grava_historico_e_recusa_aresta_invalida(ingerido, db_session):
    _, lote = ingerido
    leitura = leitura_da_linha(db_session, lote.id, 2)

    registro = transicionar(
        db_session, leitura, StatusLeitura.TRIAGEM_QUALIDADE, ator="heuristica", confianca=0.5
    )
    assert leitura.status_atual == StatusLeitura.TRIAGEM_QUALIDADE.value
    assert registro.confianca == 0.5
    assert [h.status for h in leitura.historico] == ["recebida", "triagem_qualidade"]

    with pytest.raises(TransicaoInvalida):
        transicionar(db_session, leitura, StatusLeitura.APROVADA_HUMANO, ator="usuario:1")
    assert leitura.status_atual == StatusLeitura.TRIAGEM_QUALIDADE.value

    final = leitura_da_linha(db_session, lote.id, 3)  # ja esta em sem_foto
    with pytest.raises(TransicaoInvalida):
        transicionar(db_session, final, StatusLeitura.TRIAGEM_QUALIDADE, ator="heuristica")


def test_transicionar_em_lote_exige_que_todas_estejam_na_origem(ingerido, db_session):
    _, lote = ingerido
    recebida = leitura_da_linha(db_session, lote.id, 2)
    sem_foto = leitura_da_linha(db_session, lote.id, 3)

    with pytest.raises(TransicaoInvalida, match="1 leituras nao estavam em recebida"):
        transicionar_em_lote(
            db_session,
            [recebida.id, sem_foto.id],
            de=StatusLeitura.RECEBIDA,
            para=StatusLeitura.TRIAGEM_QUALIDADE,
            ator="heuristica",
        )

    # Atomicidade: a leitura que casava com a origem NAO pode ter mudado, e a
    # transacao continua utilizavel (savepoint, nao rollback total).
    db_session.expire_all()
    assert recebida.status_atual == StatusLeitura.RECEBIDA.value
    assert [h.status for h in recebida.historico] == ["recebida"]

    assert transicionar_em_lote(db_session, [], StatusLeitura.RECEBIDA, "sem_foto", "x") == 0
    with pytest.raises(TransicaoInvalida):
        transicionar_em_lote(db_session, [], StatusLeitura.SEM_FOTO, "recebida", "x")


def test_transicionar_em_lote_grava_confianca(ingerido, db_session):
    _, lote = ingerido
    leitura = leitura_da_linha(db_session, lote.id, 2)
    n = transicionar_em_lote(
        db_session,
        [leitura.id],
        de=StatusLeitura.RECEBIDA,
        para=StatusLeitura.TRIAGEM_QUALIDADE,
        ator="modelo:qualidade@v1",
        confianca=0.87,
    )
    assert n == 1
    db_session.expire_all()
    assert leitura.status_atual == StatusLeitura.TRIAGEM_QUALIDADE.value
    assert leitura.historico[-1].confianca == 0.87
