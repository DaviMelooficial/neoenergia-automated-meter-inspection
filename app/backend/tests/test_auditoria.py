"""T-M8-07: auditoria amostral com segundo revisor diferente, cego, e concordancia."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from inspecao import auditoria
from inspecao.config import Settings
from inspecao.enums import StatusLeitura as S
from inspecao.models import AuditoriaAmostral, Leitura, RevisaoHumana, Rotulo
from tests import m8_fixture as fx
from tests.usuarios_fixture import login

CAMPOS_ITEM = {
    "auditoria_id",
    "lote",
    "linha_csv",
    "medidores",
    "posicao_lida",
    "nota_codigo",
    "nota",
    "foto_principal",
    "fotos",
    "inferencias",
    "quarentena",
    "reservado_ate",
    "restantes",
}


@pytest.fixture
def lote_aud(db_session):
    return fx.lote(db_session, "0126_0004", date(2026, 1, 3))


@pytest.fixture
def sempre(monkeypatch):
    monkeypatch.setattr(auditoria, "sorteada", lambda *_: True)


@pytest.fixture
def decidida(client, db_session, contas, lote_aud, sempre):
    """Item decidido pelo `auditor` (reprovada, glare) e sorteado para auditoria."""
    lei, foto = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    db_session.commit()
    r = client.post(
        f"/fila-humana/{lei.id}/decisao",
        json={"decisao": "reprovada", "motivo": "glare", "observacao": "nota secreta"},
        headers=login(client, contas["auditor"]),
    )
    assert r.status_code == 201, r.text
    aud = db_session.scalar(select(AuditoriaAmostral).where(AuditoriaAmostral.leitura_id == lei.id))
    return lei, foto, aud


def test_taxa_configuravel_entre_5_e_10_porcento():
    assert Settings(auditoria_taxa=0.05).auditoria_taxa == 0.05
    for fora in (0.04, 0.11):
        with pytest.raises(ValidationError):
            Settings(auditoria_taxa=fora)


@pytest.mark.parametrize("taxa", [0.05, 0.07, 0.10])
def test_sorteio_e_deterministico_e_respeita_a_taxa(taxa):
    ids = range(1, 20_001)
    sorteados = [i for i in ids if auditoria.sorteada(i, taxa, "salt")]
    assert abs(len(sorteados) / len(ids) - taxa) < 0.01
    assert sorteados == [i for i in ids if auditoria.sorteada(i, taxa, "salt")]
    assert sorteados != [i for i in ids if auditoria.sorteada(i, taxa, "outro-salt")]


def test_decisao_sorteada_cria_auditoria_na_transacao(db_session, decidida, contas):
    lei, _, aud = decidida
    assert aud is not None and aud.revisor_original_id == contas["auditor"].id
    assert aud.decisao is None
    assert db_session.get(RevisaoHumana, aud.revisao_id).redistribuida is True


def test_decisao_nao_sorteada_nao_cria_auditoria(client, db_session, contas, lote_aud, monkeypatch):
    monkeypatch.setattr(auditoria, "sorteada", lambda *_: False)
    lei, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    db_session.commit()
    cab = login(client, contas["auditor"])
    assert (
        client.post(
            f"/fila-humana/{lei.id}/decisao", json={"decisao": "aprovada"}, headers=cab
        ).status_code
        == 201
    )
    assert db_session.scalar(select(AuditoriaAmostral)) is None


def test_primeiro_revisor_nao_recebe_nem_decide(client, contas, decidida):
    _, _, aud = decidida
    cab = login(client, contas["auditor"])
    assert client.post("/auditoria/proxima", headers=cab).status_code == 404
    r = client.post(f"/auditoria/{aud.id}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 403


def test_segundo_revisor_diferente_tambem_no_banco(db_session, contas, decidida):
    _, _, aud = decidida
    with pytest.raises(IntegrityError), db_session.begin_nested():
        aud.revisor_id = contas["auditor"].id
        aud.decisao = "aprovada"
        aud.decidida_em = datetime.now(UTC)
        db_session.flush()
    db_session.expire(aud)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        aud.reservado_por = contas["auditor"].id
        db_session.flush()


def test_item_cego_lista_fechada(client, contas, decidida):
    """Nada de status, historico, revisao, observacao nem id da leitura."""
    lei, foto, aud = decidida
    r = client.post("/auditoria/proxima", headers=login(client, contas["auditor2"]))
    assert r.status_code == 200, r.text
    item = r.json()
    assert set(item) == CAMPOS_ITEM
    assert item["auditoria_id"] == aud.id and item["foto_principal"]["id"] == foto.id
    assert item["restantes"] == 1
    for vazamento in ("reprovada", "reprovada_humano", "nota secreta", "historico", "revisoes"):
        assert vazamento not in r.text
    assert "leitura_id" not in r.text


def test_leitura_em_auditoria_pendente_so_para_o_revisor_original(client, contas, decidida):
    lei, _, aud = decidida
    outro = login(client, contas["auditor2"])
    assert client.get(f"/leituras/{lei.id}", headers=outro).status_code == 409
    sup = login(client, contas["supervisor"])
    assert client.get(f"/leituras/{lei.id}", headers=sup).status_code == 409
    assert (
        client.get(f"/leituras/{lei.id}", headers=login(client, contas["auditor"])).status_code
        == 200
    )
    r = client.post(f"/auditoria/{aud.id}/decisao", json={"decisao": "aprovada"}, headers=outro)
    assert r.status_code == 201, r.text
    assert client.get(f"/leituras/{lei.id}", headers=outro).status_code == 200


def test_decisao_do_segundo_revisor(client, db_session, contas, decidida):
    lei, foto, aud = decidida
    cab = login(client, contas["auditor2"])
    r = client.post(
        f"/auditoria/{aud.id}/decisao",
        json={"decisao": "reprovada", "motivo": "fora_de_foco"},
        headers=cab,
    )
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"auditoria_id", "decisao", "motivo", "observacao", "decidida_em"}
    db_session.expire_all()
    aud = db_session.get(AuditoriaAmostral, aud.id)
    assert (aud.revisor_id, aud.decisao, aud.motivo) == (
        contas["auditor2"].id,
        "reprovada",
        "fora_de_foco",
    )
    # status da leitura nao muda; o segundo voto vira rotulo de producao
    assert db_session.get(Leitura, lei.id).status_atual == S.REPROVADA_HUMANO.value
    rotulo = db_session.scalar(select(Rotulo).where(Rotulo.auditoria_id == aud.id))
    assert rotulo.usuario_id == contas["auditor2"].id and rotulo.fora_de_foco is True
    # decidida uma vez so
    outra = login(client, contas["supervisor"])
    r = client.post(f"/auditoria/{aud.id}/decisao", json={"decisao": "aprovada"}, headers=outra)
    assert r.status_code == 409
    assert client.post("/auditoria/proxima", headers=outra).status_code == 404


def test_reserva_exclusiva_no_post(client, db_session, contas, decidida):
    _, _, aud = decidida
    assert (
        client.post("/auditoria/proxima", headers=login(client, contas["auditor2"])).status_code
        == 200
    )
    sup = login(client, contas["supervisor"])
    assert client.post("/auditoria/proxima", headers=sup).status_code == 404
    r = client.post(f"/auditoria/{aud.id}/decisao", json={"decisao": "aprovada"}, headers=sup)
    assert r.status_code == 409 and "reservada" in r.text
    # liberar so vale para o dono da reserva
    assert client.post(f"/auditoria/{aud.id}/liberar", headers=sup).status_code == 204
    assert client.post("/auditoria/proxima", headers=sup).status_code == 404
    aud2 = login(client, contas["auditor2"])
    assert client.post(f"/auditoria/{aud.id}/liberar", headers=aud2).status_code == 204
    assert client.post("/auditoria/proxima", headers=sup).status_code == 200


def test_reserva_vencida_libera(client, db_session, contas, decidida):
    _, _, aud = decidida
    aud.reservado_por = contas["auditor2"].id
    aud.reservado_ate = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    sup = login(client, contas["supervisor"])
    r = client.post(f"/auditoria/{aud.id}/decisao", json={"decisao": "aprovada"}, headers=sup)
    assert r.status_code == 201


def test_decisao_invalida(client, contas, decidida):
    _, _, aud = decidida
    cab = login(client, contas["auditor2"])
    assert (
        client.post(
            f"/auditoria/{aud.id}/decisao", json={"decisao": "reprovada"}, headers=cab
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/auditoria/{aud.id}/decisao",
            json={"decisao": "aprovada", "motivo": "glare"},
            headers=cab,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/auditoria/999999/decisao", json={"decisao": "aprovada"}, headers=cab
        ).status_code
        == 404
    )


def test_concordancia_continua(client, db_session, contas, lote_aud):
    """Pares (1o, 2o): (a, a), (glare, glare), (a, fora_de_foco), (glare, tampa_suja).
    Decisao: 3/4 e kappa 0,5. Motivo: 2/4 e kappa 1/3."""
    pares = [
        (("aprovada", None), ("aprovada", None)),
        (("reprovada", "glare"), ("reprovada", "glare")),
        (("aprovada", None), ("reprovada", "fora_de_foco")),
        (("reprovada", "glare"), ("reprovada", "tampa_suja")),
    ]
    agora = datetime.now(UTC)
    for i, ((d1, m1), (d2, m2)) in enumerate(pares):
        status = S.APROVADA_HUMANO if d1 == "aprovada" else S.REPROVADA_HUMANO
        lei, _ = fx.leitura(db_session, lote_aud, status, fx.scores(0.2))
        rev = fx.decisao(db_session, lei, contas["auditor"].id, d1, m1)
        db_session.add(
            AuditoriaAmostral(
                leitura_id=lei.id,
                revisao_id=rev.id,
                revisor_original_id=contas["auditor"].id,
                revisor_id=contas["auditor2"].id,
                decisao=d2,
                motivo=m2,
                decidida_em=agora - timedelta(days=3 - i),
            )
        )
    pendente, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
    rev = fx.decisao(db_session, pendente, contas["auditor"].id, "aprovada")
    db_session.add(
        AuditoriaAmostral(
            leitura_id=pendente.id, revisao_id=rev.id, revisor_original_id=contas["auditor"].id
        )
    )
    db_session.commit()

    r = client.get("/metricas/auditoria", headers=login(client, contas["supervisor"]))
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["sorteadas"], c["decididas"], c["pendentes"], c["decisoes_na_fila"]) == (5, 4, 1, 5)
    assert c["taxa_configurada"] == 0.07
    t = c["total"]
    assert t["n"] == 4
    assert t["concordancia_decisao"] == 0.75 and t["kappa_decisao"] == pytest.approx(0.5)
    assert t["concordancia_motivo"] == 0.5 and t["kappa_motivo"] == pytest.approx(1 / 3)
    assert [p["n"] for p in c["serie"]] == [1, 2, 3, 4]
    assert c["serie"][2]["concordancia_decisao"] == pytest.approx(2 / 3)
    assert c["por_lote"] == [{"lote": lote_aud.nome, **t}]
    # supervisor ve so agregados; auditor nem isso
    assert (
        client.get("/metricas/auditoria", headers=login(client, contas["auditor"])).status_code
        == 403
    )


def test_sortear_decididas_e_idempotente(db_session, contas, lote_aud, sempre):
    lei, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
    fx.decisao(db_session, lei, contas["auditor"].id, "aprovada")
    assert auditoria.sortear_decididas(db_session) == 1
    assert auditoria.sortear_decididas(db_session) == 0
