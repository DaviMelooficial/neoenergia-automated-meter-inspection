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
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from inspecao.config import get_settings
from inspecao.enums import StatusLeitura, TipoJob
from inspecao.estados import transicionar
from inspecao.inference.detector import DetectorHeuristico, DetectorNulo
from inspecao.inference.politica import POLITICA_V0, POLITICA_V1
from inspecao.jobs import fila, handlers
from inspecao.jobs.handlers import (
    Contexto,
    recalcular_decisao,
    reprocessar_lote,
    triagem_qualidade,
)
from inspecao.jobs.worker import processar_um
from inspecao.models import Foto, HistoricoStatus, Job, Leitura, ResultadoInferencia
from inspecao.routers.fila_humana import consulta_proxima
from inspecao.storage.local import LocalFileStorage
from tests import lote_sintetico as ls
from tests.usuarios_fixture import criar_contas

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


def test_voltar_para_detector_ja_usado_nao_reabre_e_avisa(
    db_session, staging, tmp_path, caplog, monkeypatch
):
    _, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    nulo, heuristico = (
        ctx_com(DetectorNulo(), storage, POLITICA_V0),
        ctx_com(DetectorHeuristico(), storage),
    )
    esvaziar_fila(heuristico, db_session)
    reprocessar_lote(nulo, db_session, {"lote_id": lote.id})
    esvaziar_fila(nulo, db_session)  # agora o resultado mais recente e do nulo

    # O fileConfig do alembic (migracao do banco de teste) desliga os loggers existentes.
    monkeypatch.setattr(handlers.log, "disabled", False)
    with caplog.at_level("WARNING", logger="inspecao.jobs.handlers"):
        reprocessar_lote(heuristico, db_session, {"lote_id": lote.id})
    assert esvaziar_fila(heuristico, db_session) == 0
    assert "use recalcular_decisao" in caplog.text.lower()
    assert f"{len(ls.LINHAS_COM_FOTO)} leituras ja tem resultado de heuristica@v1" in caplog.text


# ------------------------------------------------- concorrencia com a fila humana
# Estes testes precisam de duas conexoes de verdade: dados commitados no banco de
# teste e limpos ao final, como em test_revisao_m3.base_comprometida.


@pytest.fixture
def base_na_fila(staging, tmp_path):
    """Lote commitado, triado pela heuristica e devolvido inteiro para a fila (v0)."""
    from tests.conftest import SERVIDOR_OK

    if not SERVIDOR_OK:
        pytest.skip("servidor postgres indisponivel")
    from inspecao.db import engine

    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    with fabrica() as s:
        contas = criar_contas(s)
        _, lote, storage = ls.ingerir_sintetico(s, staging, tmp_path / "storage")
        esvaziar_fila(ctx_com(DetectorHeuristico(), storage), s)
        recalcular_decisao(
            ctx_com(DetectorHeuristico(), storage),
            s,
            {"lote_id": lote.id, "politica": "politica@v0"},
        )
        s.commit()
        lote_id = lote.id
    yield fabrica, contas, lote_id, storage
    with engine.begin() as c:
        c.execute(text("DELETE FROM lotes"))
        c.execute(text("DELETE FROM notas_ocorrencia WHERE codigo IN ('Z901','Z902','T111')"))
        c.execute(text("TRUNCATE jobs, log_acesso, refresh_tokens RESTART IDENTITY"))
        c.execute(text("DELETE FROM usuarios"))


def _status(fabrica, lote_id) -> dict[int, tuple[str, int | None]]:
    with fabrica() as s:
        return {
            linha: (st, por)
            for linha, st, por in s.execute(
                select(Leitura.linha_csv, Leitura.status_atual, Leitura.reservado_por).where(
                    Leitura.lote_id == lote_id, Leitura.linha_csv.in_(ls.LINHAS_COM_FOTO)
                )
            )
        }


def _reservar(sessao, usuario_id: int, leitura_id: int) -> None:
    leitura = sessao.get(Leitura, leitura_id)
    leitura.reservado_por = usuario_id
    leitura.reservado_ate = datetime.now(UTC) + timedelta(minutes=10)


def test_recalcular_pula_item_que_o_auditor_esta_pegando_sem_esperar(
    base_na_fila, caplog, monkeypatch
):
    """Auditor com o item travado em /proxima: o job pula a linha (nao espera o lock),
    avisa no log, e a reserva que o auditor grava em seguida continua valendo."""
    monkeypatch.setattr(handlers.log, "disabled", False)  # fileConfig do alembic
    fabrica, contas, lote_id, storage = base_na_fila
    auditor = contas["auditor"].id
    s_aud, s_job = fabrica(), fabrica()
    try:
        pego = s_aud.scalar(consulta_proxima(auditor, ls.LOTE))  # lock aberto
        assert pego is not None
        s_job.execute(text("SET LOCAL lock_timeout = '2s'"))  # sem o SKIP LOCKED, estoura
        with caplog.at_level("INFO", logger="inspecao.jobs.handlers"):
            recalcular_decisao(
                ctx_com(DetectorHeuristico(), StorageProibido(raiz=storage.raiz)),
                s_job,
                {"lote_id": lote_id, "politica": "politica@v1"},
            )
        s_job.commit()
        n = len(ls.LINHAS_COM_FOTO)
        assert f"~{n} candidatas, {n - 1} travadas pelo job" in caplog.text
        avisos = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(avisos) == 1 and "~1 leituras puladas" in avisos[0]
        _reservar(s_aud, auditor, pego)
        s_aud.commit()
    finally:
        s_aud.close()
        s_job.close()

    with fabrica() as s:
        linha_pega = s.get(Leitura, pego).linha_csv
    status = _status(fabrica, lote_id)
    assert status[linha_pega] == (StatusLeitura.FILA_HUMANA.value, auditor)
    for linha in LINHAS_PRETAS:
        if linha != linha_pega:
            assert status[linha] == (StatusLeitura.REJEITADA_QUALIDADE.value, None)


def test_reserva_ou_decisao_entre_o_select_e_o_loop_nao_pega_item_travado(
    base_na_fila, monkeypatch
):
    """O auditor tenta pegar e decidir itens no meio do recalculo: os candidatos ja
    estao travados, entao /proxima nao os entrega e a decisao teria de esperar."""
    fabrica, contas, lote_id, storage = base_na_fila
    auditor = contas["auditor"].id
    tentativas: dict[str, object] = {}
    decidir_original = handlers._decidir

    def decidir_com_auditor_no_meio(*args, **kwargs):
        if not tentativas:
            with fabrica() as s_aud:
                tentativas["proxima"] = s_aud.scalar(consulta_proxima(auditor, ls.LOTE))
                if tentativas["proxima"] is not None:
                    _reservar(s_aud, auditor, tentativas["proxima"])
                    s_aud.commit()
            with fabrica() as s_aud:
                alvo = s_aud.scalar(
                    select(Leitura.id).where(
                        Leitura.lote_id == lote_id, Leitura.linha_csv == LINHAS_PRETAS[0]
                    )
                )
                try:
                    s_aud.execute(
                        select(Leitura).where(Leitura.id == alvo).with_for_update(nowait=True)
                    )
                    tentativas["lock_decisao"] = "conseguiu"
                except OperationalError:
                    tentativas["lock_decisao"] = "travado"
                s_aud.rollback()
        return decidir_original(*args, **kwargs)

    monkeypatch.setattr(handlers, "_decidir", decidir_com_auditor_no_meio)
    with fabrica() as s_job:
        recalcular_decisao(
            ctx_com(DetectorHeuristico(), StorageProibido(raiz=storage.raiz)),
            s_job,
            {"lote_id": lote_id, "politica": "politica@v1"},
        )
        s_job.commit()

    # Todas as leituras com foto do lote eram candidatas e estavam travadas.
    assert tentativas == {"proxima": None, "lock_decisao": "travado"}
    status = _status(fabrica, lote_id)
    assert {linha: status[linha] for linha in LINHAS_PRETAS} == {
        linha: (StatusLeitura.REJEITADA_QUALIDADE.value, None) for linha in LINHAS_PRETAS
    }


@pytest.mark.parametrize("acao", ["reserva", "decisao_humana"])
def test_troca_de_detector_relê_a_leitura_antes_de_reabrir(base_na_fila, acao):
    """O job carregou a leitura em fila_humana; antes da reabertura um auditor reservou
    ou decidiu (outra conexao, commitada). A triagem reconfere com lock e nao reabre."""
    fabrica, contas, lote_id, storage = base_na_fila
    auditor = contas["auditor"].id
    with fabrica() as s:
        alvo = s.scalar(
            select(Leitura.id).where(
                Leitura.lote_id == lote_id, Leitura.linha_csv == LINHAS_PRETAS[0]
            )
        )
        # Resultado de heuristica@v1 apagado: sem isso a triagem pularia por "ja inferida".
        s.execute(
            ResultadoInferencia.__table__.delete().where(
                ResultadoInferencia.foto_id.in_(select(Foto.id).where(Foto.leitura_id == alvo))
            )
        )
        s.commit()

    s_job = fabrica()
    try:
        # Referencia mantida: o identity map e fraco e soltaria o objeto velho.
        lida = s_job.get(Leitura, alvo)
        assert lida.status_atual == StatusLeitura.FILA_HUMANA.value
        with fabrica() as s_aud:
            if acao == "reserva":
                _reservar(s_aud, auditor, alvo)
            else:
                transicionar(
                    s_aud,
                    s_aud.get(Leitura, alvo),
                    StatusLeitura.APROVADA_HUMANO,
                    f"usuario:{auditor}",
                )
            s_aud.commit()
        triagem_qualidade(ctx_com(DetectorHeuristico(), storage), s_job, {"leitura_id": alvo})
        s_job.commit()
    finally:
        s_job.close()

    esperado = {
        "reserva": (StatusLeitura.FILA_HUMANA.value, auditor),
        "decisao_humana": (StatusLeitura.APROVADA_HUMANO.value, None),
    }[acao]
    assert _status(fabrica, lote_id)[LINHAS_PRETAS[0]] == esperado
    with fabrica() as s:
        assert not s.scalar(
            select(func.count())
            .select_from(ResultadoInferencia)
            .join(Foto)
            .where(Foto.leitura_id == alvo)
        )


def test_reserva_gravada_entre_o_select_e_o_loop_nao_e_reaberta(
    db_session, contas, staging, tmp_path, monkeypatch, caplog
):
    """Reproducao do gate: a reserva do ultimo item chega quando o job ja selecionou
    os candidatos e esta transicionando o primeiro. Cada linha e reconferida antes de
    reabrir, entao a reservada fica na fila."""
    lote, storage = triar_com_heuristica(db_session, staging, tmp_path)
    ctx = ctx_com(DetectorHeuristico(), StorageProibido(raiz=storage.raiz))
    recalcular_decisao(ctx, db_session, {"lote_id": lote.id, "politica": "politica@v0"})
    alvo_id = leitura_da_linha(db_session, lote, LINHAS_PRETAS[-1]).id
    original = handlers.transicionar
    feito = []

    def transicionar_com_corrida(sessao, leitura, *a, **k):
        if not feito:
            feito.append(True)
            sessao.execute(
                update(Leitura)
                .where(Leitura.id == alvo_id)
                .values(
                    reservado_por=contas["auditor"].id,
                    reservado_ate=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
        return original(sessao, leitura, *a, **k)

    monkeypatch.setattr(handlers, "transicionar", transicionar_com_corrida)
    monkeypatch.setattr(handlers.log, "disabled", False)  # fileConfig do alembic
    with caplog.at_level("WARNING", logger="inspecao.jobs.handlers"):
        recalcular_decisao(ctx, db_session, {"lote_id": lote.id, "politica": "politica@v1"})
    # Contada como reconferencia, nao como travada por outra transacao.
    avisos = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(avisos) == 1 and "1 leituras nao reabertas" in avisos[0]
    status = status_por_linha(db_session, lote)
    assert status[LINHAS_PRETAS[-1]] == StatusLeitura.FILA_HUMANA.value
    assert [status[n] for n in LINHAS_PRETAS[:-1]] == [StatusLeitura.REJEITADA_QUALIDADE.value] * (
        len(LINHAS_PRETAS) - 1
    )


def test_triagem_avisa_quando_a_leitura_esta_travada_por_outra_transacao(
    base_na_fila, caplog, monkeypatch
):
    monkeypatch.setattr(handlers.log, "disabled", False)  # fileConfig do alembic
    fabrica, _, lote_id, storage = base_na_fila
    with fabrica() as s:
        alvo = s.scalar(
            select(Leitura.id).where(
                Leitura.lote_id == lote_id, Leitura.linha_csv == LINHAS_PRETAS[0]
            )
        )
        s.execute(
            ResultadoInferencia.__table__.delete().where(
                ResultadoInferencia.foto_id.in_(select(Foto.id).where(Foto.leitura_id == alvo))
            )
        )
        s.commit()

    s_outra, s_job = fabrica(), fabrica()
    try:
        s_outra.execute(select(Leitura).where(Leitura.id == alvo).with_for_update())
        with caplog.at_level("WARNING", logger="inspecao.jobs.handlers"):
            triagem_qualidade(ctx_com(DetectorHeuristico(), storage), s_job, {"leitura_id": alvo})
        s_job.commit()
    finally:
        s_outra.rollback()
        s_outra.close()
        s_job.close()

    assert f"leitura {alvo} travada por outra transacao" in caplog.text
    assert _status(fabrica, lote_id)[LINHAS_PRETAS[0]] == (StatusLeitura.FILA_HUMANA.value, None)
