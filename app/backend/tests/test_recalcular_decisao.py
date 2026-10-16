"""Aceite do M5 nos jobs: triagem com `heuristica@v1`, `recalcular_decisao` sem
reabrir imagem e troca de detector via `reprocessar_lote`.

O lote sintetico tem fotos pretas (lixo obvio para a heuristica); aqui duas delas
sao trocadas por um xadrez nitido antes da ingestao, para que a politica tenha
os dois lados para decidir.
"""

from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest
from sqlalchemy import func, select

from inspecao.config import get_settings
from inspecao.enums import StatusLeitura, TipoJob
from inspecao.estados import transicionar
from inspecao.inference.detector import DetectorHeuristico, DetectorNulo
from inspecao.inference.politica import POLITICA_V0, POLITICA_V1
from inspecao.jobs import fila
from inspecao.jobs.handlers import Contexto, recalcular_decisao, reprocessar_lote
from inspecao.jobs.worker import processar_um
from inspecao.models import Foto, HistoricoStatus, Job, Leitura, ResultadoInferencia
from inspecao.storage.local import LocalFileStorage
from tests import lote_sintetico as ls

LINHAS_NITIDAS = {4: ls.foto(3), 6: ls.foto(6)}
LINHAS_PRETAS = sorted(set(ls.LINHAS_COM_FOTO) - set(LINHAS_NITIDAS))


class StorageProibido(LocalFileStorage):
    """Falha em qualquer leitura de imagem: prova que o job nao abriu arquivo."""

    def abrir(self, chave: str) -> bytes:
        raise AssertionError(f"recalcular_decisao abriu imagem: {chave}")


@pytest.fixture(scope="module")
def staging(tmp_path_factory):
    raiz = ls.criar_staging(tmp_path_factory.mktemp("staging"))
    y, x = np.mgrid[0:480, 0:360]
    xadrez = cv2.cvtColor((((x // 24) + (y // 24)) % 2 * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    ok, buf = cv2.imencode(".jpg", xadrez)
    assert ok
    for nome in LINHAS_NITIDAS.values():
        (raiz / ls.LOTE / nome).write_bytes(buf.tobytes())
    return raiz


def esvaziar_fila(ctx, sessao) -> int:
    total = 0
    while jobs := fila.reivindicar(sessao, "teste", lote=10):
        for job in jobs:
            assert processar_um(ctx, sessao, job), job.erro
        total += len(jobs)
    return total


def ctx_com(detector, storage, politica=POLITICA_V1) -> Contexto:
    return Contexto(settings=get_settings(), detector=detector, storage=storage, politica=politica)


def status_por_linha(sessao, lote) -> dict[int, str]:
    sessao.expire_all()
    return dict(
        sessao.execute(
            select(Leitura.linha_csv, Leitura.status_atual).where(
                Leitura.lote_id == lote.id, Leitura.linha_csv.in_(ls.LINHAS_COM_FOTO)
            )
        ).all()
    )


def leitura_da_linha(sessao, lote, linha: int) -> Leitura:
    return sessao.scalar(
        select(Leitura).where(Leitura.lote_id == lote.id, Leitura.linha_csv == linha)
    )


def n_historico(sessao, leitura: Leitura) -> int:
    return sessao.scalar(
        select(func.count())
        .select_from(HistoricoStatus)
        .where(HistoricoStatus.leitura_id == leitura.id)
    )


def triar_com_heuristica(sessao, staging, tmp_path):
    _, lote, storage = ls.ingerir_sintetico(sessao, staging, tmp_path / "storage")
    esvaziar_fila(ctx_com(DetectorHeuristico(), storage), sessao)
    return lote, storage


def test_triagem_heuristica_rejeita_o_preto_e_manda_o_nitido_para_humano(
    db_session, staging, tmp_path
):
    lote, _ = triar_com_heuristica(db_session, staging, tmp_path)

    assert status_por_linha(db_session, lote) == {
        **{linha: StatusLeitura.REJEITADA_QUALIDADE.value for linha in LINHAS_PRETAS},
        **{linha: StatusLeitura.FILA_HUMANA.value for linha in LINHAS_NITIDAS},
    }

    preta = leitura_da_linha(db_session, lote, LINHAS_PRETAS[0])
    assert [h.status for h in preta.historico] == [
        "recebida",
        "triagem_qualidade",
        "rejeitada_qualidade",
    ]
    decisao = preta.historico[-1]
    assert decisao.ator == "modelo:heuristica@v1"
    assert decisao.detalhe == {"politica": "politica@v1", "decisao": "rejeitar"}
    assert decisao.confianca == 1.0

    nitida = leitura_da_linha(db_session, lote, 4)
    assert nitida.historico[-1].detalhe == {"politica": "politica@v1", "decisao": "humano"}
    assert nitida.historico[-1].confianca == 0.0

    inferencias = db_session.scalars(
        select(ResultadoInferencia).join(Foto).where(Foto.lote_id == lote.id)
    ).all()
    assert len(inferencias) == len(ls.LINHAS_COM_FOTO)
    assert all(i.modelo == "heuristica@v1" and "sinais" in i.saida for i in inferencias)


def test_recalcular_decisao_muda_status_sem_abrir_imagem(db_session, staging, tmp_path):
    lote, storage = triar_com_heuristica(db_session, staging, tmp_path)
    proibido = ctx_com(DetectorHeuristico(), StorageProibido(raiz=storage.raiz))
    nitida = leitura_da_linha(db_session, lote, 4)
    historico_nitida = n_historico(db_session, nitida)

    # v0 nunca rejeita: as pretas voltam para humano, passando pela triagem.
    fila.enfileirar(
        db_session, TipoJob.RECALCULAR_DECISAO, {"lote_id": lote.id, "politica": "politica@v0"}
    )
    assert esvaziar_fila(proibido, db_session) == 1
    assert set(status_por_linha(db_session, lote).values()) == {StatusLeitura.FILA_HUMANA.value}

    preta = leitura_da_linha(db_session, lote, LINHAS_PRETAS[0])
    assert [h.status for h in preta.historico][-3:] == [
        "rejeitada_qualidade",
        "triagem_qualidade",
        "fila_humana",
    ]
    reabertura, decisao = preta.historico[-2:]
    assert reabertura.detalhe == {
        "motivo": "recalcular_decisao",
        "de": "rejeitada_qualidade",
        "politica": "politica@v0",
    }
    assert decisao.detalhe == {"politica": "politica@v0", "decisao": "humano", "recalculo": True}
    # Decisao que nao muda nao ganha historico.
    assert n_historico(db_session, nitida) == historico_nitida

    # E de volta para v1, sem payload de politica: usa a do worker.
    fila.enfileirar(db_session, TipoJob.RECALCULAR_DECISAO, {"lote_id": lote.id})
    assert esvaziar_fila(proibido, db_session) == 1
    assert status_por_linha(db_session, lote)[LINHAS_PRETAS[0]] == (
        StatusLeitura.REJEITADA_QUALIDADE.value
    )
    # Nenhum resultado novo: so os da triagem original.
    assert db_session.scalar(
        select(func.count())
        .select_from(ResultadoInferencia)
        .join(Foto)
        .where(Foto.lote_id == lote.id)
    ) == len(ls.LINHAS_COM_FOTO)


def test_recalcular_nao_reabre_reservada_nem_decisao_humana(db_session, contas, staging, tmp_path):
    lote, storage = triar_com_heuristica(db_session, staging, tmp_path)
    ctx = ctx_com(DetectorHeuristico(), StorageProibido(raiz=storage.raiz))
    recalcular_decisao(ctx, db_session, {"lote_id": lote.id, "politica": "politica@v0"})

    reservada, decidida, livre = (leitura_da_linha(db_session, lote, n) for n in LINHAS_PRETAS)
    reservada.reservado_por = contas["auditor"].id
    reservada.reservado_ate = datetime.now(UTC) + timedelta(minutes=10)
    transicionar(
        db_session, decidida, StatusLeitura.APROVADA_HUMANO, f"usuario:{contas['auditor'].id}"
    )
    db_session.flush()

    recalcular_decisao(ctx, db_session, {"lote_id": lote.id, "politica": "politica@v1"})
    status = status_por_linha(db_session, lote)
    assert status[reservada.linha_csv] == StatusLeitura.FILA_HUMANA.value
    assert status[decidida.linha_csv] == StatusLeitura.APROVADA_HUMANO.value
    assert status[livre.linha_csv] == StatusLeitura.REJEITADA_QUALIDADE.value


def test_recalcular_rejeita_lote_ou_politica_inexistente(db_session, staging, tmp_path):
    lote, storage = triar_com_heuristica(db_session, staging, tmp_path)
    ctx = ctx_com(DetectorHeuristico(), storage)
    with pytest.raises(LookupError):
        recalcular_decisao(ctx, db_session, {"lote_id": lote.id, "politica": "politica@v9"})
    with pytest.raises(LookupError):
        recalcular_decisao(ctx, db_session, {"lote_id": 10**9})


def test_politica_de_outro_detector_nao_decide(db_session, staging, tmp_path):
    """Limiar calibrado para heuristica@v1 nao se aplica a saida de outro detector."""
    _, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    esvaziar_fila(ctx_com(DetectorNulo(), storage, POLITICA_V1), db_session)
    assert set(status_por_linha(db_session, lote).values()) == {StatusLeitura.FILA_HUMANA.value}
    leitura = leitura_da_linha(db_session, lote, LINHAS_PRETAS[0])
    assert leitura.historico[-1].detalhe == {
        "politica": "politica@v1",
        "decisao": "humano",
        "motivo": "politica_de_outro_detector",
    }


def test_troca_de_detector_reabre_o_lote_e_guarda_o_resultado_antigo(db_session, staging, tmp_path):
    _, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    esvaziar_fila(ctx_com(DetectorNulo(), storage, POLITICA_V0), db_session)
    assert set(status_por_linha(db_session, lote).values()) == {StatusLeitura.FILA_HUMANA.value}

    heuristico = ctx_com(DetectorHeuristico(), storage)
    reprocessar_lote(heuristico, db_session, {"lote_id": lote.id})
    pendentes = db_session.scalar(
        select(func.count()).select_from(Job).where(Job.status == "pendente")
    )
    assert pendentes == len(ls.LINHAS_COM_FOTO)
    assert esvaziar_fila(heuristico, db_session) == len(ls.LINHAS_COM_FOTO)

    assert status_por_linha(db_session, lote) == {
        **{linha: StatusLeitura.REJEITADA_QUALIDADE.value for linha in LINHAS_PRETAS},
        **{linha: StatusLeitura.FILA_HUMANA.value for linha in LINHAS_NITIDAS},
    }
    preta = leitura_da_linha(db_session, lote, LINHAS_PRETAS[0])
    assert preta.historico[-2].detalhe == {"motivo": "troca_de_detector", "de": "fila_humana"}
    modelos = db_session.scalars(
        select(ResultadoInferencia.modelo).join(Foto).where(Foto.leitura_id == preta.id)
    ).all()
    assert sorted(modelos) == ["heuristica@v1", "nulo@v0"]

    # Segunda passada: todo mundo ja tem resultado da heuristica, nada a enfileirar.
    reprocessar_lote(heuristico, db_session, {"lote_id": lote.id})
    assert esvaziar_fila(heuristico, db_session) == 0
    # Job de triagem duplicado para leitura ja inferida tambem nao reabre.
    fila.enfileirar(db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": preta.id})
    historico = n_historico(db_session, preta)
    assert esvaziar_fila(heuristico, db_session) == 1
    assert n_historico(db_session, preta) == historico
