"""Contrato dos endpoints de operacao: fila humana, leituras, fotos, quarentena,
metricas, usuarios e ingestao assincrona."""

import pytest
from sqlalchemy import func, select

from inspecao.config import get_settings
from inspecao.enums import StatusJob, StatusLeitura, TipoJob
from inspecao.inference.detector import DetectorNulo
from inspecao.jobs import fila
from inspecao.jobs.handlers import Contexto
from inspecao.jobs.worker import processar_um
from inspecao.models import Job, Leitura, RevisaoHumana
from tests import lote_sintetico as ls
from tests.usuarios_fixture import login


@pytest.fixture(scope="module")
def staging(tmp_path_factory):
    return ls.criar_staging(tmp_path_factory.mktemp("staging"))


@pytest.fixture
def na_fila(db_session, staging, tmp_path, monkeypatch):
    """Lote sintetico ingerido e triado: 5 leituras em fila_humana."""
    resultado, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    ctx = Contexto(settings=get_settings(), detector=DetectorNulo(), storage=storage)
    while jobs := fila.reivindicar(db_session, "t", 10):
        for j in jobs:
            processar_um(ctx, db_session, j)
    # A rota de arquivo abre o storage padrao; aponta para o do teste.
    monkeypatch.setattr("inspecao.routers.operacao.LocalFileStorage", lambda *a, **k: storage)
    return lote


def ids_na_fila(db_session, lote):
    return db_session.scalars(
        select(Leitura.id)
        .where(Leitura.lote_id == lote.id, Leitura.status_atual == StatusLeitura.FILA_HUMANA)
        .order_by(Leitura.id)
    ).all()


# ------------------------------------------------------------- fila humana


def test_listar_fila_e_detalhe(client, contas, na_fila, db_session):
    cab = login(client, contas["auditor"])
    r = client.get("/fila-humana", headers=cab)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["total"] == 5
    assert [i["status_atual"] for i in corpo["itens"]] == ["fila_humana"] * 5
    item = corpo["itens"][0]
    assert item["foto_principal"]["url"].startswith("/api/fotos/")

    r = client.get(f"/leituras/{item['id']}", headers=cab)
    assert r.status_code == 200
    det = r.json()
    assert [h["status"] for h in det["historico"]] == [
        "recebida",
        "triagem_qualidade",
        "fila_humana",
    ]
    assert det["inferencias"][0]["modelo"] == "nulo@v0"
    assert det["lote"] == ls.LOTE

    r = client.get("/fila-humana", headers=cab, params={"lote": "PSP_EXTRATLEITIMPL_999999_9999"})
    assert r.json()["total"] == 0
    assert client.get("/leituras/999999", headers=cab).status_code == 404


def test_arquivo_da_foto(client, contas, na_fila, db_session):
    cab = login(client, contas["auditor"])
    item = client.get("/fila-humana", headers=cab).json()["itens"][0]
    r = client.get(item["foto_principal"]["url"].removeprefix("/api"), headers=cab)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    assert client.get("/fotos/999999/arquivo", headers=cab).status_code == 404


def test_proxima_reserva_e_nao_entrega_o_mesmo_item_a_dois_auditores(
    client, contas, na_fila, db_session
):
    a1 = login(client, contas["auditor"])
    a2 = login(client, contas["auditor2"])

    r1 = client.post("/fila-humana/proxima", headers=a1)
    assert r1.status_code == 200
    r2 = client.post("/fila-humana/proxima", headers=a2)
    assert r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["reservado_por"] == contas["auditor"].id

    # Chamar de novo devolve o MESMO item para o mesmo auditor (reserva propria).
    assert client.post("/fila-humana/proxima", headers=a1).json()["id"] == r1.json()["id"]

    # A listagem de cada um esconde o item reservado pelo outro.
    lista1 = {i["id"] for i in client.get("/fila-humana", headers=a1).json()["itens"]}
    assert r2.json()["id"] not in lista1 and r1.json()["id"] in lista1

    # O outro auditor nao consegue decidir o item reservado.
    r = client.post(
        f"/fila-humana/{r1.json()['id']}/decisao", headers=a2, json={"decisao": "aprovada"}
    )
    assert r.status_code == 409


def test_fila_vazia_da_404(client, contas, db_session):
    assert (
        client.post("/fila-humana/proxima", headers=login(client, contas["auditor"])).status_code
        == 404
    )


def test_decisao_aprova_e_reprova(client, contas, na_fila, db_session):
    cab = login(client, contas["auditor"])
    ids = ids_na_fila(db_session, na_fila)

    r = client.post(f"/fila-humana/{ids[0]}/decisao", headers=cab, json={"decisao": "aprovada"})
    assert r.status_code == 201
    assert r.json()["decisao"] == "aprovada" and r.json()["motivo"] is None

    r = client.post(f"/fila-humana/{ids[1]}/decisao", headers=cab, json={"decisao": "reprovada"})
    assert r.status_code == 422  # reprovacao exige motivo
    r = client.post(
        f"/fila-humana/{ids[1]}/decisao",
        headers=cab,
        json={"decisao": "reprovada", "motivo": "glare", "observacao": "reflexo no visor"},
    )
    assert r.status_code == 201
    r = client.post(
        f"/fila-humana/{ids[2]}/decisao",
        headers=cab,
        json={"decisao": "reprovada", "motivo": "inventado"},
    )
    assert r.status_code == 422  # fora da lista fechada
    r = client.post(
        f"/fila-humana/{ids[2]}/decisao",
        headers=cab,
        json={"decisao": "aprovada", "motivo": "glare"},
    )
    assert r.status_code == 422  # aprovacao nao leva motivo

    # Decidir de novo o que ja foi decidido: 409.
    assert (
        client.post(
            f"/fila-humana/{ids[0]}/decisao", headers=cab, json={"decisao": "aprovada"}
        ).status_code
        == 409
    )

    db_session.expire_all()
    aprovada = db_session.get(Leitura, ids[0])
    reprovada = db_session.get(Leitura, ids[1])
    assert aprovada.status_atual == StatusLeitura.APROVADA_HUMANO
    assert reprovada.status_atual == StatusLeitura.REPROVADA_HUMANO
    assert aprovada.historico[-1].ator == f"usuario:{contas['auditor'].id}"
    assert reprovada.historico[-1].detalhe == {"motivo": "glare"}
    assert db_session.scalar(select(func.count()).select_from(RevisaoHumana)) == 2
    assert client.get("/fila-humana", headers=cab).json()["total"] == 3


# ---------------------------------------------------------------- quarentena


def test_quarentena_lista_resumo_e_resolucao(client, contas, na_fila, db_session):
    cab = login(client, contas["supervisor"])
    r = client.get("/quarentena", headers=cab)
    assert r.status_code == 200 and r.json()["total"] == 10
    r = client.get("/quarentena", headers=cab, params={"severidade": "bloqueante"})
    assert r.json()["total"] == 3
    r = client.get("/quarentena", headers=cab, params={"tipo": "foto_orfa"})
    assert r.json()["total"] == 1 and r.json()["itens"][0]["leitura_id"] is None

    resumo = client.get("/quarentena/resumo", headers=cab).json()
    assert resumo["nota_exige_sem_foto"] == {"pendentes": 1, "resolvidos": 0}

    item = r.json()["itens"][0]
    r = client.post(
        f"/quarentena/{item['id']}/resolver",
        headers=cab,
        json={"observacao": "explicado pelo cliente"},
    )
    assert r.status_code == 200
    assert r.json()["resolvido"] is True
    assert r.json()["resolvido_por"] == f"usuario:{contas['supervisor'].id}"
    assert r.json()["detalhe"]["resolucao"] == "explicado pelo cliente"
    assert (
        client.post(f"/quarentena/{item['id']}/resolver", headers=cab, json={}).status_code == 409
    )
    assert client.get("/quarentena", headers=cab).json()["total"] == 9  # padrao: so pendentes
    assert client.get("/quarentena", headers=cab, params={"resolvido": True}).json()["total"] == 1


# ------------------------------------------------------------------ metricas


def test_metricas_qualidade_e_drift(client, contas, na_fila, db_session):
    cab = login(client, contas["supervisor"])
    ids = ids_na_fila(db_session, na_fila)
    aud = login(client, contas["auditor"])
    client.post(f"/fila-humana/{ids[0]}/decisao", headers=aud, json={"decisao": "aprovada"})
    client.post(
        f"/fila-humana/{ids[1]}/decisao",
        headers=aud,
        json={"decisao": "reprovada", "motivo": "glare"},
    )

    q = client.get("/metricas/qualidade", headers=cab)
    assert q.status_code == 200
    m = q.json()
    assert m["total_leituras"] == 10 and m["com_foto"] == 5 and m["sem_foto"] == 5
    assert m["fila_humana"] == {"pendentes": 3, "decididas": 2}
    assert m["auto_decididas"]["quantidade"] == 0
    assert m["quarentena_por_tipo"]["foto_orfa"] == 1
    assert m["defeitos_qualidade"] == {"glare": 1}
    assert m["revisoes_por_decisao"] == {"aprovada": 1, "reprovada": 1}
    assert m["tempo_medio_decisao_segundos"] is not None and m["tempo_medio_decisao_segundos"] >= 0
    assert m["jobs"]["triagem_qualidade"] == {"concluido": 5}

    d = client.get("/metricas/drift", headers=cab)
    assert d.status_code == 200
    serie = d.json()["lotes"]
    assert len(serie) == 1
    l0 = serie[0]
    assert l0["lote"] == ls.LOTE and l0["com_foto"] == 5
    assert l0["taxa_auto_rejeicao"] == 0.0
    assert l0["taxa_reprovacao_humana"] == 0.5
    assert l0["modelos"] == ["nulo@v0"]
    assert l0["confianca_media"] is None  # detector nulo nao produz score


# ------------------------------------------------------------------ usuarios


def test_admin_gerencia_usuarios(client, contas, db_session):
    cab = login(client, contas["admin"])
    r = client.post(
        "/usuarios",
        headers=cab,
        json={"nome": "Novo", "email": "Novo@Teste.Com.Br", "senha_inicial": "senha-inicial-123"},
    )
    assert r.status_code == 201
    novo = r.json()
    assert novo["email"] == "novo@teste.com.br" and novo["papel"] == "auditor"
    assert novo["precisa_trocar_senha"] is True
    assert (
        client.post(
            "/usuarios",
            headers=cab,
            json={
                "nome": "Dup",
                "email": "novo@teste.com.br",
                "senha_inicial": "senha-inicial-123",
            },
        ).status_code
        == 409
    )

    # o novo entra e ve que precisa trocar a senha
    r = client.post(
        "/auth/login", json={"email": "novo@teste.com.br", "senha": "senha-inicial-123"}
    )
    assert r.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["precisa_trocar_senha"] is True

    r = client.patch(
        f"/usuarios/{novo['id']}", headers=cab, json={"papel": "supervisor", "ativo": False}
    )
    assert r.status_code == 200 and r.json()["papel"] == "supervisor" and r.json()["ativo"] is False
    assert (
        client.post(
            "/auth/login", json={"email": "novo@teste.com.br", "senha": "senha-inicial-123"}
        ).status_code
        == 401
    )

    assert (
        client.patch(
            f"/usuarios/{contas['admin'].id}", headers=cab, json={"ativo": False}
        ).status_code
        == 409
    )
    assert len(client.get("/usuarios", headers=cab).json()) == 5


# ------------------------------------------------------------------- ingestao


def test_ingest_batch_enfileira_job_e_lotes_lista(client, contas, db_session):
    cab = login(client, contas["admin"])
    r = client.post("/ingest/batch", headers=cab, json={"lote": ls.LOTE, "reprocessar": True})
    assert r.status_code == 202
    job = r.json()
    assert job["tipo"] == TipoJob.INGERIR_LOTE and job["status"] == StatusJob.PENDENTE
    assert job["payload"] == {"lote": ls.LOTE, "reprocessar": True}
    assert db_session.get(Job, job["id"]).max_tentativas == 1

    assert client.post("/ingest/batch", headers=cab, json={"lote": "../etc"}).status_code == 422
    assert client.post("/ingest/webhook", headers=cab).status_code == 501
    assert client.get("/lotes", headers=login(client, contas["auditor"])).json() == []
