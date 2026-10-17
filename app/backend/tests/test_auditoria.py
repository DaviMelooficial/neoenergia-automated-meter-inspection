"""T-M8-07: auditoria amostral com segundo revisor diferente, cego, e concordancia.

As decisoes medidas sao de contas reais (`m8_fixture.contas_reais`); as de
`usuarios_fixture` (@teste.com.br) contam como E2E e nao sao sorteadas. So o papel
`auditor` audita; toda recusa em `/auditoria/{id}/*` e a mesma resposta.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from inspecao import auditoria
from inspecao.config import Settings
from inspecao.enums import StatusLeitura as S
from inspecao.models import AuditoriaAmostral, Leitura, Quarentena, RevisaoHumana, Rotulo
from inspecao.routers.auditoria import AUDITORIA_INDISPONIVEL
from inspecao.routers.fila_humana import ITEM_INDISPONIVEL
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
    "quarentena",
    "reservado_ate",
    "restantes",
}


@pytest.fixture
def lote_aud(db_session):
    return fx.lote(db_session, "0126_0004", date(2026, 1, 3))


@pytest.fixture
def reais(db_session):
    return fx.contas_reais(db_session)


@pytest.fixture
def sempre(monkeypatch):
    monkeypatch.setattr(auditoria, "sorteada", lambda *_: True)


def _com_quarentena(db_session, lote_aud, status=S.FILA_HUMANA, p=0.2):
    lei, foto = fx.leitura(db_session, lote_aud, status, fx.scores(p))
    db_session.add(
        Quarentena(
            lote_id=lote_aud.id,
            leitura_id=lei.id,
            foto_id=foto.id,
            tipo="medidor_multiplo",
            severidade="informativo",
            detalhe={"quantidade": 2},
        )
    )
    db_session.commit()
    return lei, foto


@pytest.fixture
def decidida(client, db_session, reais, lote_aud, sempre):
    """Item COM quarentena, decidido pela auditora real (reprovada, glare) e sorteado."""
    lei, foto = _com_quarentena(db_session, lote_aud)
    r = client.post(
        f"/fila-humana/{lei.id}/decisao",
        json={"decisao": "reprovada", "motivo": "glare", "observacao": "nota secreta"},
        headers=login(client, reais["auditor"]),
    )
    assert r.status_code == 201, r.text
    aud = db_session.scalar(select(AuditoriaAmostral).where(AuditoriaAmostral.leitura_id == lei.id))
    return lei, foto, aud


# ------------------------------------------------------------------ sorteio


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


def test_decisao_sorteada_cria_auditoria_na_transacao(db_session, decidida, reais):
    _, _, aud = decidida
    assert aud is not None and aud.origem == "fila"
    assert aud.revisor_original_id == reais["auditor"].id and aud.decisao is None
    assert db_session.get(RevisaoHumana, aud.revisao_id).redistribuida is True


def test_decisao_de_conta_de_teste_nao_e_sorteada(client, db_session, contas, lote_aud, sempre):
    lei, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    db_session.commit()
    r = client.post(
        f"/fila-humana/{lei.id}/decisao",
        json={"decisao": "aprovada"},
        headers=login(client, contas["auditor"]),  # @teste.com.br
    )
    assert r.status_code == 201
    assert db_session.scalar(select(AuditoriaAmostral)) is None


def test_decisao_nao_sorteada_nao_cria_auditoria(client, db_session, reais, lote_aud, monkeypatch):
    monkeypatch.setattr(auditoria, "sorteada", lambda *_: False)
    lei, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    db_session.commit()
    cab = login(client, reais["auditor"])
    r = client.post(f"/fila-humana/{lei.id}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 201
    assert db_session.scalar(select(AuditoriaAmostral)) is None


def test_integrity_error_do_sorteio_desfaz_a_decisao(client, db_session, reais, lote_aud, sempre):
    """Leitura que ja tem auditoria (corrida com o sorteio retroativo): o INSERT da
    auditoria viola `uq_auditorias_leitura` e a decisao inteira volta, com 409."""
    lei, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    antiga = RevisaoHumana(leitura_id=lei.id, usuario_id=reais["auditor2"].id, decisao="aprovada")
    db_session.add(antiga)
    db_session.flush()
    db_session.add(
        AuditoriaAmostral(
            leitura_id=lei.id, revisao_id=antiga.id, revisor_original_id=reais["auditor2"].id
        )
    )
    db_session.commit()
    cab = login(client, reais["auditor"])
    r = client.post(f"/fila-humana/{lei.id}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 409
    db_session.expire_all()
    assert db_session.get(Leitura, lei.id).status_atual == S.FILA_HUMANA.value
    revisoes = db_session.scalars(select(RevisaoHumana).where(RevisaoHumana.leitura_id == lei.id))
    assert [r.usuario_id for r in revisoes] == [reais["auditor2"].id]
    assert not db_session.scalars(select(Rotulo).where(Rotulo.rodada == "producao")).all()


def test_sortear_decididas_idempotente_e_sem_contas_de_teste(
    db_session, reais, contas, lote_aud, sempre
):
    real, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
    fx.decisao(db_session, real, reais["auditor"].id, "aprovada")
    teste, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
    fx.decisao(db_session, teste, contas["auditor"].id, "aprovada")
    assert auditoria.sortear_decididas(db_session) == 1
    assert auditoria.sortear_decididas(db_session) == 0
    (aud,) = db_session.scalars(select(AuditoriaAmostral)).all()
    assert aud.leitura_id == real.id


def test_sortear_decididas_nao_perde_o_lote_numa_colisao(
    db_session, reais, lote_aud, sempre, monkeypatch
):
    """Colisao num item (IntegrityError) fica no savepoint dele; os outros entram."""
    for _ in range(3):
        lei, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
        fx.decisao(db_session, lei, reais["auditor"].id, "aprovada")
    original = auditoria.sortear
    chamadas = {"n": 0}

    def sortear_com_colisao(db, revisao):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            for _ in range(2):
                db.add(
                    AuditoriaAmostral(
                        leitura_id=revisao.leitura_id,
                        revisao_id=revisao.id,
                        revisor_original_id=revisao.usuario_id,
                    )
                )
            db.flush()
        return original(db, revisao)

    monkeypatch.setattr(auditoria, "sortear", sortear_com_colisao)
    assert auditoria.sortear_decididas(db_session) == 2
    assert len(db_session.scalars(select(AuditoriaAmostral)).all()) == 2


# ------------------------------------------------------------------ quem audita


def test_so_o_papel_auditor_audita(client, reais, decidida):
    _, _, aud = decidida
    for papel in ("supervisor", "admin"):
        cab = login(client, reais[papel])
        respostas = [
            client.post("/auditoria/proxima", headers=cab),
            client.post(f"/auditoria/{aud.id}/decisao", json={"decisao": "aprovada"}, headers=cab),
            client.post(f"/auditoria/{aud.id}/liberar", headers=cab),
            client.post("/auditoria/999999/liberar", headers=cab),
        ]
        assert {(r.status_code, r.content) for r in respostas} == {(403, respostas[0].content)}


def test_recusa_unica_sem_oraculo(client, db_session, reais, decidida):
    """Mesmo status e o mesmo corpo, byte a byte, para: nao existe, a decisao e sua,
    reservada por outro e ja decidida, no `/decisao` e no `/liberar`."""
    _, _, aud = decidida
    original = login(client, reais["auditor"])
    outro = login(client, reais["auditor2"])
    terceiro = login(client, reais["auditor3"])
    corpo = {"decisao": "aprovada"}

    def decisao(cab, i):
        return client.post(f"/auditoria/{i}/decisao", json=corpo, headers=cab)

    def liberar(cab, i):
        return client.post(f"/auditoria/{i}/liberar", headers=cab)

    respostas = [
        decisao(outro, 999999),  # nao existe
        liberar(outro, 999999),
        decisao(original, aud.id),  # a decisao e sua
        liberar(original, aud.id),
        liberar(outro, aud.id),  # pendente, sem reserva dele
    ]
    assert client.post("/auditoria/proxima", headers=outro).status_code == 200
    respostas += [decisao(terceiro, aud.id), liberar(terceiro, aud.id)]  # reservada por outro
    assert decisao(outro, aud.id).status_code == 201
    respostas += [decisao(terceiro, aud.id), liberar(terceiro, aud.id)]  # ja decidida
    respostas.append(client.post("/auditoria/proxima", headers=original))  # nada para ele
    assert {(r.status_code, r.content) for r in respostas} == {
        (404, b'{"detail":"' + AUDITORIA_INDISPONIVEL.encode() + b'"}')
    }


def test_segundo_revisor_diferente_tambem_no_banco(db_session, reais, decidida):
    _, _, aud = decidida
    with pytest.raises(IntegrityError), db_session.begin_nested():
        aud.revisor_id = reais["auditor"].id
        aud.decisao = "aprovada"
        aud.decidida_em = datetime.now(UTC)
        db_session.flush()
    db_session.expire(aud)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        aud.reservado_por = reais["auditor"].id
        db_session.flush()


# ------------------------------------------------------------------ cegueira


def test_item_cego_lista_fechada_inclusive_quarentena(client, reais, decidida):
    """Nada de status, historico, revisao, observacao, scores da maquina nem id de
    leitura, inclusive na quarentena (a fixture tem inconsistencia ligada a leitura)."""
    lei, foto, aud = decidida
    r = client.post("/auditoria/proxima", headers=login(client, reais["auditor2"]))
    assert r.status_code == 200, r.text
    item = r.json()
    assert set(item) == CAMPOS_ITEM
    assert item["auditoria_id"] == aud.id and item["foto_principal"]["id"] == foto.id
    assert item["quarentena"] == [
        {"tipo": "medidor_multiplo", "severidade": "informativo", "detalhe": {"quantidade": 2}}
    ]
    assert item["restantes"] == 1
    for vazamento in ("reprovada", "nota secreta", "historico", "revisoes", "leitura_id", "p_ruim"):
        assert vazamento not in r.text
    assert "inferencias" not in r.text and "fora_de_foco" not in r.text


def test_item_igual_para_as_tres_origens(client, db_session, reais, lote_aud, decidida, sempre):
    """Fila, auto-aprovada e auto-rejeitada: mesmos campos, nenhum sinal da origem."""
    _com_quarentena(db_session, lote_aud, S.AUTO_APROVADA, 0.01)
    _com_quarentena(db_session, lote_aud, S.REJEITADA_QUALIDADE, 0.95)
    assert auditoria.sortear_automaticas(db_session) == {"auto_aprovada": 1, "auto_rejeitada": 1}
    db_session.commit()
    cab = login(client, reais["auditor2"])
    itens = []
    for _ in range(3):
        r = client.post("/auditoria/proxima", headers=cab)
        assert r.status_code == 200
        itens.append(r.json())
        client.post(
            f"/auditoria/{itens[-1]['auditoria_id']}/decisao",
            json={"decisao": "aprovada"},
            headers=cab,
        )
    neutros = [
        {k: v for k, v in i.items() if k not in ("auditoria_id", "linha_csv", "medidores")}
        | {"posicao_lida": None, "foto_principal": None, "fotos": None, "reservado_ate": None}
        | {"restantes": None}
        for i in itens
    ]
    assert all(set(i) == CAMPOS_ITEM for i in itens)
    assert neutros[0] == neutros[1] == neutros[2]


def test_decisao_na_fila_nao_ecoa_status(client, db_session, reais, lote_aud, decidida):
    """Caminho (a): com o id da leitura auditada, o POST da fila responde 409 com o mesmo
    texto que da para um item reservado por outro auditor; nada de status."""
    lei, _, _ = decidida
    cab = login(client, reais["auditor2"])
    r = client.post(f"/fila-humana/{lei.id}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 409 and r.json()["detail"] == ITEM_INDISPONIVEL
    assert "reprovada" not in r.text and "status" not in r.text

    reservada, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    reservada.reservado_por = reais["auditor3"].id
    reservada.reservado_ate = datetime.now(UTC) + timedelta(minutes=5)
    db_session.commit()
    r2 = client.post(
        f"/fila-humana/{reservada.id}/decisao", json={"decisao": "aprovada"}, headers=cab
    )
    assert (r2.status_code, r2.content) == (r.status_code, r.content)


def test_leitura_decidida_nao_e_oraculo(client, db_session, reais, lote_aud, decidida):
    """Caminho (b): GET /leituras responde igual (200, decisao oculta, sem historico nem
    scores) para leitura decidida em auditoria e fora dela, humana ou da maquina; so o
    autor da decisao e admin a veem."""
    lei, _, _ = decidida
    fora, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
    fx.decisao(db_session, fora, reais["auditor3"].id, "aprovada")
    rejeitada, _ = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    db_session.commit()
    cab = login(client, reais["auditor2"])
    for i in (lei.id, fora.id, rejeitada.id):
        r = client.get(f"/leituras/{i}", headers=cab)
        assert r.status_code == 200
        c = r.json()
        assert c["status_atual"] == "decidida"
        assert c["revisoes"] == c["historico"] == c["inferencias"] == []
    autora = client.get(f"/leituras/{lei.id}", headers=login(client, reais["auditor"])).json()
    assert autora["status_atual"] == "reprovada_humano" and len(autora["revisoes"]) == 1
    admin = client.get(f"/leituras/{lei.id}", headers=login(client, reais["admin"])).json()
    assert admin["status_atual"] == "reprovada_humano"
    na_fila, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    db_session.commit()
    r = client.get(f"/leituras/{na_fila.id}", headers=cab)
    assert r.json()["status_atual"] == "fila_humana" and r.json()["inferencias"]


def test_lotes_nao_separam_decisoes_para_quem_nao_e_admin(client, db_session, reais, decidida):
    """Bloqueante do re-gate: lote de uma leitura so + `lote` do item de auditoria = a
    decisao anterior. Para auditor e supervisor, os status finais viram `decidida`."""
    lei, _, _ = decidida
    nome = db_session.get(Leitura, lei.id).lote.nome
    for papel in ("auditor2", "supervisor"):
        lotes = client.get("/lotes", headers=login(client, reais[papel])).json()
        (lote,) = [lt for lt in lotes if lt["nome"] == nome]
        assert lote["por_status"] == {"decidida": 1}
    lotes = client.get("/lotes", headers=login(client, reais["admin"])).json()
    (lote,) = [lt for lt in lotes if lt["nome"] == nome]
    assert lote["por_status"] == {"reprovada_humano": 1}


# ------------------------------------------------------------------ decisao


def test_decisao_do_segundo_revisor(client, db_session, reais, decidida):
    lei, _, aud = decidida
    cab = login(client, reais["auditor2"])
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
        reais["auditor2"].id,
        "reprovada",
        "fora_de_foco",
    )
    assert db_session.get(Leitura, lei.id).status_atual == S.REPROVADA_HUMANO.value
    rotulo = db_session.scalar(select(Rotulo).where(Rotulo.auditoria_id == aud.id))
    assert rotulo.usuario_id == reais["auditor2"].id and rotulo.fora_de_foco is True


def test_decisao_gravada_e_imutavel_no_banco(client, db_session, reais, lote_aud, decidida):
    _, _, aud = decidida
    r = client.post(
        f"/auditoria/{aud.id}/decisao",
        json={"decisao": "aprovada"},
        headers=login(client, reais["auditor2"]),
    )
    assert r.status_code == 201
    outra, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    db_session.commit()
    mudancas = [
        ("decisao", "'reprovada'"),
        ("observacao", "'editada'"),
        ("leitura_id", str(outra.id)),
        ("revisao_id", "NULL"),
        ("revisor_original_id", str(reais["auditor3"].id)),
        ("origem", "'auto_aprovada'"),
    ]
    for coluna, valor in mudancas:
        with pytest.raises(DBAPIError, match="imutavel"), db_session.begin_nested():
            db_session.execute(
                text(f"UPDATE auditorias_amostrais SET {coluna} = {valor} WHERE id = :i"),
                {"i": aud.id},
            )


def test_rotulo_que_falha_desfaz_a_decisao_da_auditoria(client, db_session, reais, decidida):
    """`producao.registrar` viola o indice unico (foto, producao, usuario): recusa unica e
    a auditoria continua pendente, sem rotulo novo."""
    lei, foto, aud = decidida
    uid = reais["auditor2"].id
    antiga = RevisaoHumana(leitura_id=lei.id, usuario_id=uid, decisao="aprovada")
    db_session.add(antiga)
    db_session.flush()
    db_session.add(Rotulo(foto_id=foto.id, usuario_id=uid, rodada="producao", revisao_id=antiga.id))
    db_session.commit()
    r = client.post(
        f"/auditoria/{aud.id}/decisao",
        json={"decisao": "aprovada"},
        headers=login(client, reais["auditor2"]),
    )
    assert r.status_code == 404 and r.json()["detail"] == AUDITORIA_INDISPONIVEL
    db_session.expire_all()
    aud = db_session.get(AuditoriaAmostral, aud.id)
    assert (aud.decisao, aud.revisor_id, aud.decidida_em) == (None, None, None)
    assert db_session.scalar(select(Rotulo).where(Rotulo.auditoria_id == aud.id)) is None


def test_reserva_exclusiva(client, reais, decidida):
    _, _, aud = decidida
    aud2 = login(client, reais["auditor2"])
    aud3 = login(client, reais["auditor3"])
    assert client.post("/auditoria/proxima", headers=aud2).status_code == 200
    assert client.post("/auditoria/proxima", headers=aud3).status_code == 404
    assert client.post(f"/auditoria/{aud.id}/liberar", headers=aud2).status_code == 204
    assert client.post("/auditoria/proxima", headers=aud3).status_code == 200


def test_reserva_vencida_libera(client, db_session, reais, decidida):
    _, _, aud = decidida
    aud.reservado_por = reais["auditor2"].id
    aud.reservado_ate = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    cab = login(client, reais["auditor3"])
    r = client.post(f"/auditoria/{aud.id}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 201


def test_decisao_invalida(client, reais, decidida):
    _, _, aud = decidida
    cab = login(client, reais["auditor2"])
    url = f"/auditoria/{aud.id}/decisao"
    assert client.post(url, json={"decisao": "reprovada"}, headers=cab).status_code == 422
    corpo = {"decisao": "aprovada", "motivo": "glare"}
    assert client.post(url, json=corpo, headers=cab).status_code == 422


def test_corrida_entre_dois_revisores_no_proxima():
    """Duas transacoes reais ao mesmo tempo: SKIP LOCKED entrega itens diferentes, e uma
    terceira nao recebe nada enquanto as duas seguram os locks."""
    from inspecao.db import engine
    from inspecao.enums import Papel
    from tests.usuarios_fixture import criar_usuario

    with Session(engine) as s:
        lt = fx.lote(s, "0126_0005", date(2026, 1, 4))
        dono = criar_usuario(s, f"dono.corrida{fx.DOMINIO_REAL}", Papel.AUDITOR)
        r1 = criar_usuario(s, f"r1.corrida{fx.DOMINIO_REAL}", Papel.AUDITOR)
        r2 = criar_usuario(s, f"r2.corrida{fx.DOMINIO_REAL}", Papel.AUDITOR)
        r3 = criar_usuario(s, f"r3.corrida{fx.DOMINIO_REAL}", Papel.AUDITOR)
        for _ in range(2):
            lei, _ = fx.leitura(s, lt, S.APROVADA_HUMANO, fx.scores(0.2))
            rev = fx.decisao(s, lei, dono.id, "aprovada")
            s.add(
                AuditoriaAmostral(leitura_id=lei.id, revisao_id=rev.id, revisor_original_id=dono.id)
            )
        s.commit()
        lote_id, uids = lt.id, [dono.id, r1.id, r2.id, r3.id]
    a, b, c = Session(engine), Session(engine), Session(engine)
    try:
        primeira = auditoria.reservar_proxima(a, r1.id)
        segunda = auditoria.reservar_proxima(b, r2.id)
        assert primeira is not None and segunda is not None
        assert primeira.id != segunda.id
        assert auditoria.reservar_proxima(c, r3.id) is None
    finally:
        for sessao in (a, b, c):
            sessao.rollback()
            sessao.close()
        with engine.begin() as conexao:
            conexao.execute(text("DELETE FROM lotes WHERE id = :i"), {"i": lote_id})
            conexao.execute(text("DELETE FROM usuarios WHERE id = ANY(:u)"), {"u": uids})


# ------------------------------------------------------------------ decisoes da maquina


@pytest.mark.parametrize(
    ("status", "origem", "decisao_revisor", "bloco"),
    [
        (S.AUTO_APROVADA, "auto_aprovada", "reprovada", "auto_aprovadas"),
        (S.REJEITADA_QUALIDADE, "auto_rejeitada", "aprovada", "auto_rejeitadas"),
    ],
)
def test_decisoes_da_maquina_tambem_sao_auditadas(
    client, db_session, reais, lote_aud, sempre, status, origem, decisao_revisor, bloco
):
    """Auto-aprovada reprovada pelo revisor e auto-rejeitada aprovada pelo revisor sao
    erros da maquina; cada origem tem bloco proprio na concordancia."""
    lei, _ = fx.leitura(db_session, lote_aud, status, fx.scores(0.5))
    contagem = auditoria.sortear_automaticas(db_session)
    assert contagem[origem] == 1 and sum(contagem.values()) == 1
    assert sum(auditoria.sortear_automaticas(db_session).values()) == 0
    db_session.commit()
    (aud,) = db_session.scalars(select(AuditoriaAmostral)).all()
    assert (aud.origem, aud.revisao_id, aud.revisor_original_id) == (origem, None, None)
    cab = login(client, reais["auditor"])
    item = client.post("/auditoria/proxima", headers=cab).json()
    assert item["auditoria_id"] == aud.id
    assert client.get(f"/leituras/{lei.id}", headers=cab).json()["status_atual"] == "decidida"
    corpo = {"decisao": decisao_revisor}
    if decisao_revisor == "reprovada":
        corpo["motivo"] = "tampa_suja"
    r = client.post(f"/auditoria/{aud.id}/decisao", json=corpo, headers=cab)
    assert r.status_code == 201, r.text
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    assert admin[bloco] == {"n": 1, "erros": 1, "taxa_erro": 1.0, "suprimido": False}
    sup = client.get("/metricas/auditoria", headers=login(client, reais["supervisor"])).json()
    assert sup[bloco]["suprimido"] is True and sup[bloco]["n"] is None


def test_sortear_automaticas_commit_por_item_solta_cada_lock(db_session, lote_aud, sempre):
    """Com commit por item (a CLI), cada leitura sorteada e commitada na hora."""
    for _ in range(2):
        fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    commits = []
    original = db_session.commit

    def contar():
        commits.append(1)
        original()

    db_session.commit = contar
    try:
        resultado = auditoria.sortear_automaticas(db_session, commit_por_item=True)
    finally:
        db_session.commit = original
    assert resultado["auto_rejeitada"] == 2 and len(commits) == 2


# ------------------------------------------------------------------ concordancia


def _pares(db_session, reais, lote_aud, pares, quando):
    for i, ((d1, m1), (d2, m2)) in enumerate(pares):
        status = S.APROVADA_HUMANO if d1 == "aprovada" else S.REPROVADA_HUMANO
        lei, _ = fx.leitura(db_session, lote_aud, status, fx.scores(0.2))
        rev = fx.decisao(db_session, lei, reais["auditor"].id, d1, m1)
        db_session.add(
            AuditoriaAmostral(
                leitura_id=lei.id,
                revisao_id=rev.id,
                revisor_original_id=reais["auditor"].id,
                revisor_id=reais["auditor2"].id,
                decisao=d2,
                motivo=m2,
                decidida_em=quando[i],
            )
        )


PARES = [
    (("aprovada", None), ("aprovada", None)),
    (("reprovada", "glare"), ("reprovada", "glare")),
    (("aprovada", None), ("reprovada", "fora_de_foco")),
    (("reprovada", "glare"), ("reprovada", "tampa_suja")),
]


def test_concordancia_admin_ve_detalhe_e_dia_de_recife(client, db_session, reais, lote_aud):
    """Decisao: 3/4 e kappa 0,5. Motivo: 2/4 e kappa 1/3. Duas decisoes as 01:00 UTC de
    16/10 sao 22:00 de 15/10 em Recife: o dia da serie e 15/10."""
    quando = [
        datetime(2026, 10, 16, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 16, 1, 30, tzinfo=UTC),
        datetime(2026, 10, 16, 14, 0, tzinfo=UTC),
        datetime(2026, 10, 17, 14, 0, tzinfo=UTC),
    ]
    _pares(db_session, reais, lote_aud, PARES, quando)
    pendente, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
    rev = fx.decisao(db_session, pendente, reais["auditor"].id, "aprovada")
    db_session.add(
        AuditoriaAmostral(
            leitura_id=pendente.id, revisao_id=rev.id, revisor_original_id=reais["auditor"].id
        )
    )
    db_session.commit()

    c = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    assert c["detalhe"] is True
    assert (c["sorteadas"], c["decididas"], c["pendentes"], c["decisoes_na_fila"]) == (5, 4, 1, 5)
    t = c["total"]
    assert t["n"] == 4 and t["suprimido"] is False
    assert t["concordancia_decisao"] == 0.75 and t["kappa_decisao"] == pytest.approx(0.5)
    assert t["concordancia_motivo"] == 0.5 and t["kappa_motivo"] == pytest.approx(1 / 3)
    assert [(p["dia"], p["n"]) for p in c["serie"]] == [
        ("2026-10-15", 2),
        ("2026-10-16", 3),
        ("2026-10-17", 4),
    ]
    assert c["por_lote"][0]["lote"] == lote_aud.nome and c["por_lote"][0]["n"] == 4


def test_concordancia_supervisor_so_agregado_acima_do_minimo(client, db_session, reais, lote_aud):
    agora = datetime.now(UTC)
    _pares(db_session, reais, lote_aud, PARES, [agora] * 4)
    db_session.commit()
    sup = login(client, reais["supervisor"])
    c = client.get("/metricas/auditoria", headers=sup).json()
    assert c["detalhe"] is False and c["por_lote"] == [] and c["serie"] == []
    assert (c["sorteadas"], c["pendentes"], c["decididas"], c["decisoes_na_fila"]) == (None,) * 4
    assert c["total"]["suprimido"] is True and c["total"]["concordancia_decisao"] is None

    concordantes = [(("aprovada", None), ("aprovada", None))] * 6
    _pares(db_session, reais, lote_aud, concordantes, [agora] * 6)
    db_session.commit()
    c = client.get("/metricas/auditoria", headers=sup).json()
    assert c["total"]["suprimido"] is False and c["total"]["n"] == 10
    assert c["total"]["concordancia_decisao"] == pytest.approx(0.9)
    auditor = login(client, reais["auditor"])
    assert client.get("/metricas/auditoria", headers=auditor).status_code == 403


def test_concordancia_ignora_contas_de_teste(client, db_session, reais, contas, lote_aud):
    lei, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
    rev = RevisaoHumana(leitura_id=lei.id, usuario_id=contas["auditor"].id, decisao="aprovada")
    db_session.add(rev)
    db_session.flush()
    db_session.add(
        AuditoriaAmostral(
            leitura_id=lei.id,
            revisao_id=rev.id,
            revisor_original_id=contas["auditor"].id,
            revisor_id=reais["auditor2"].id,
            decisao="reprovada",
            motivo="glare",
            decidida_em=datetime.now(UTC),
        )
    )
    db_session.commit()
    c = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    assert c["decididas"] == 0 and c["total"]["n"] == 0
