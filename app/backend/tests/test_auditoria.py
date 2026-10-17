"""T-M8-07: auditoria amostral com segundo revisor diferente, cego, e concordancia.

As decisoes medidas sao de contas reais (`m8_fixture.contas_reais`); as de
`usuarios_fixture` (@teste.com.br) contam como E2E e nao sao sorteadas. So o papel
`auditor` audita; toda recusa em `/auditoria/{codigo}/*` e a mesma resposta.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from inspecao import auditoria
from inspecao.config import Settings
from inspecao.enums import StatusLeitura as S
from inspecao.models import (
    AuditoriaAmostral,
    HistoricoStatus,
    Leitura,
    Quarentena,
    RevisaoHumana,
    Rotulo,
)
from inspecao.routers.auditoria import AUDITORIA_INDISPONIVEL
from inspecao.routers.fila_humana import ITEM_INDISPONIVEL
from tests import m8_fixture as fx
from tests.usuarios_fixture import login

CAMPOS_ITEM = {
    "auditoria",
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
    "ha_mais",
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


def test_integrity_error_do_sorteio_desfaz_a_decisao(
    client, db_session, reais, lote_aud, monkeypatch
):
    """Falha de gravacao no sorteio: a decisao inteira volta, com o 409 de texto constante
    (o mesmo de "ja decidida" e "reservada por outro")."""
    lei, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    db_session.commit()

    def falha(*_):
        raise IntegrityError("INSERT auditorias_amostrais", {}, Exception("colisao"))

    monkeypatch.setattr(auditoria, "sortear", falha)
    cab = login(client, reais["auditor"])
    r = client.post(f"/fila-humana/{lei.id}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert (r.status_code, r.json()) == (409, {"detail": ITEM_INDISPONIVEL})
    db_session.expire_all()
    assert db_session.get(Leitura, lei.id).status_atual == S.FILA_HUMANA.value
    assert not db_session.scalars(
        select(RevisaoHumana).where(RevisaoHumana.leitura_id == lei.id)
    ).all()
    assert not db_session.scalars(select(Rotulo).where(Rotulo.rodada == "producao")).all()


def _reabrir_e_mandar_para_fila(db_session, lei):
    from inspecao.estados import transicionar

    transicionar(db_session, lei, S.TRIAGEM_QUALIDADE, ator="modelo:heuristica@v1")
    transicionar(db_session, lei, S.FILA_HUMANA, ator="modelo:heuristica@v1")
    db_session.commit()


def test_auto_rejeitada_reaberta_e_decidida_na_fila(client, db_session, reais, lote_aud, sempre):
    """Bloqueante do gate 3: auto-rejeitada sorteada que a maquina reabre e manda para a
    fila. A pendente da decisao antiga e invalidada; a decisao do auditor grava (201) e
    ganha auditoria propria, sem colidir com a da leitura."""
    lei, _ = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    assert auditoria.sortear_automaticas(db_session)["auto_rejeitada"] == 1
    db_session.commit()
    _reabrir_e_mandar_para_fila(db_session, lei)
    antiga = db_session.scalar(
        select(AuditoriaAmostral).where(AuditoriaAmostral.origem == "auto_rejeitada")
    )
    assert antiga.invalidada_em is not None and antiga.decisao is None
    cab = login(client, reais["auditor"])
    r = client.post(f"/fila-humana/{lei.id}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 201, r.text
    origens = sorted(
        a.origem
        for a in db_session.scalars(
            select(AuditoriaAmostral).where(AuditoriaAmostral.leitura_id == lei.id)
        )
    )
    assert origens == ["auto_rejeitada", "fila"]
    # a invalidada nao e entregue nem conta como pendente
    item = client.post("/auditoria/proxima", headers=login(client, reais["auditor2"])).json()
    nova = db_session.scalar(select(AuditoriaAmostral).where(AuditoriaAmostral.origem == "fila"))
    assert item["auditoria"] == str(nova.codigo) and item["ha_mais"] is False
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    assert (admin["pendentes"], admin["invalidadas"]) == (1, 1)


def test_auditoria_decidida_sobrevive_a_reabertura(client, db_session, reais, lote_aud, sempre):
    """A auditoria ja decidida mede a decisao antiga e continua contando."""
    lei, _ = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    auditoria.sortear_automaticas(db_session)
    db_session.commit()
    aud = db_session.scalar(select(AuditoriaAmostral))
    cab = login(client, reais["auditor2"])
    assert client.post("/auditoria/proxima", headers=cab).status_code == 200
    r = client.post(f"/auditoria/{aud.codigo}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 201
    _reabrir_e_mandar_para_fila(db_session, lei)
    db_session.expire_all()
    aud = db_session.get(AuditoriaAmostral, aud.id)
    assert aud.invalidada_em is None and aud.decisao == "aprovada"
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    assert admin["auto_rejeitadas"]["n"] == 1 and admin["auto_rejeitadas"]["erros"] == 1


def test_triagem_sorteia_a_auto_rejeicao_na_mesma_transacao(db_session, lote_aud):
    """Sem `make auditoria-sortear`: a saida da triagem para rejeitada_qualidade ja cria a
    auditoria (taxa padrao de auto-rejeicao = 100%), apontando a linha do historico."""
    from inspecao.inference.politica import POLITICA_V1
    from inspecao.jobs.handlers import _sair_da_triagem

    lei, _ = fx.leitura(db_session, lote_aud, S.TRIAGEM_QUALIDADE, fx.scores(0.95))
    decisao, p, detalhe = POLITICA_V1.decidir(fx.scores(0.95)) + ({"politica": "politica@v1"},)
    _sair_da_triagem(db_session, lei, decisao, "modelo:heuristica@v1", p, detalhe)
    aud = db_session.scalar(select(AuditoriaAmostral).where(AuditoriaAmostral.leitura_id == lei.id))
    assert aud.origem == "auto_rejeitada" and aud.historico_id is not None
    assert db_session.get(HistoricoStatus, aud.historico_id).status == S.REJEITADA_QUALIDADE.value


def test_taxa_de_auto_rejeicao_e_separada_da_fila():
    from inspecao.config import Settings

    s = Settings()
    assert (s.auditoria_taxa, s.auditoria_taxa_auto_rejeitada) == (0.07, 1.0)
    with pytest.raises(ValidationError):
        Settings(auditoria_taxa_auto_rejeitada=1.5)
    assert auditoria.sorteada("auto_rejeitada:1", 1.0, "salt") is True


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
            client.post(
                f"/auditoria/{aud.codigo}/decisao", json={"decisao": "aprovada"}, headers=cab
            ),
            client.post(f"/auditoria/{aud.codigo}/liberar", headers=cab),
            client.post(f"/auditoria/{uuid.uuid4()}/liberar", headers=cab),
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
        decisao(outro, uuid.uuid4()),  # nao existe
        liberar(outro, uuid.uuid4()),
        decisao(original, aud.codigo),  # a decisao e sua
        liberar(original, aud.codigo),
        liberar(outro, aud.codigo),  # pendente, sem reserva dele
    ]
    assert client.post("/auditoria/proxima", headers=outro).status_code == 200
    respostas += [
        decisao(terceiro, aud.codigo),
        liberar(terceiro, aud.codigo),
    ]  # reservada por outro
    assert decisao(outro, aud.codigo).status_code == 201
    respostas += [decisao(terceiro, aud.codigo), liberar(terceiro, aud.codigo)]  # ja decidida
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
    assert item["auditoria"] == str(aud.codigo) and item["foto_principal"]["id"] == foto.id
    assert item["quarentena"] == [
        {"tipo": "medidor_multiplo", "severidade": "informativo", "detalhe": {"quantidade": 2}}
    ]
    assert item["ha_mais"] is False
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
            f"/auditoria/{itens[-1]['auditoria']}/decisao",
            json={"decisao": "aprovada"},
            headers=cab,
        )
    neutros = [
        {k: v for k, v in i.items() if k not in ("auditoria", "linha_csv", "medidores")}
        | {"posicao_lida": None, "foto_principal": None, "fotos": None, "reservado_ate": None}
        | {"ha_mais": None}
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
        f"/auditoria/{aud.codigo}/decisao",
        json={"decisao": "reprovada", "motivo": "fora_de_foco"},
        headers=cab,
    )
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"auditoria", "decisao", "motivo", "observacao", "decidida_em"}
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
        f"/auditoria/{aud.codigo}/decisao",
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
        f"/auditoria/{aud.codigo}/decisao",
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
    assert client.post(f"/auditoria/{aud.codigo}/liberar", headers=aud2).status_code == 204
    assert client.post("/auditoria/proxima", headers=aud3).status_code == 200


def test_reserva_vencida_libera(client, db_session, reais, decidida):
    _, _, aud = decidida
    aud.reservado_por = reais["auditor2"].id
    aud.reservado_ate = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    cab = login(client, reais["auditor3"])
    r = client.post(f"/auditoria/{aud.codigo}/decisao", json={"decisao": "aprovada"}, headers=cab)
    assert r.status_code == 201


def test_decisao_invalida(client, reais, decidida):
    _, _, aud = decidida
    cab = login(client, reais["auditor2"])
    url = f"/auditoria/{aud.codigo}/decisao"
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
    assert item["auditoria"] == str(aud.codigo)
    assert client.get(f"/leituras/{lei.id}", headers=cab).json()["status_atual"] == "decidida"
    corpo = {"decisao": decisao_revisor}
    if decisao_revisor == "reprovada":
        corpo["motivo"] = "tampa_suja"
    r = client.post(f"/auditoria/{aud.codigo}/decisao", json=corpo, headers=cab)
    assert r.status_code == 201, r.text
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    b = admin[bloco]
    assert (b["n"], b["erros"], b["taxa_erro"], b["suprimido"]) == (1, 1, 1.0, False)
    assert b["limite_superior_erro"] == 1.0 and b["alvo_atingido"] is False
    # com 1 erro: 473 auditorias para precisao >= 0,99; 947 para erro < 0,5%
    assert b["necessarias"] == {"auto_rejeitadas": 473, "auto_aprovadas": 947}[bloco]
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


def test_ordem_de_entrega_nao_segue_o_sorteio(client, db_session, reais, lote_aud, sempre):
    """Bloqueante do gate 3: as automaticas sao sorteadas em bloco; entregar por ordem de
    sorteio diria a origem. A entrega segue md5(salt:ordem:id), igual para as origens."""
    import hashlib

    from inspecao.config import get_settings

    for _ in range(3):
        fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    auditoria.sortear_automaticas(db_session)
    for _ in range(3):
        lei, _ = fx.leitura(db_session, lote_aud, S.APROVADA_HUMANO, fx.scores(0.2))
        rev = fx.decisao(db_session, lei, reais["auditor"].id, "aprovada")
        auditoria.sortear(db_session, rev)
    db_session.commit()
    salt = get_settings().salt_da_rotulagem()
    ids = [a.id for a in db_session.scalars(select(AuditoriaAmostral))]
    esperado = sorted(ids, key=lambda i: (hashlib.md5(f"{salt}:ordem:{i}".encode()).hexdigest(), i))
    cab = login(client, reais["auditor2"])
    entregues = []
    for _ in ids:
        item = client.post("/auditoria/proxima", headers=cab).json()
        entregues.append(item["auditoria"])
        # reserva propria volta primeiro: o mesmo item ate decidir
        assert client.post("/auditoria/proxima", headers=cab).json()["auditoria"] == entregues[-1]
        client.post(
            f"/auditoria/{entregues[-1]}/decisao", json={"decisao": "aprovada"}, headers=cab
        )
    por_codigo = {str(a.codigo): a.id for a in db_session.scalars(select(AuditoriaAmostral))}
    assert [por_codigo[c] for c in entregues] == esperado


def test_falso_positivo_com_intervalo_e_sem_alvo_antes_da_hora(
    client, db_session, reais, lote_aud, sempre
):
    """Bloqueante de dados do gate 3: 10 auto-rejeicoes confirmadas e 0 erro nao sao
    "falso positivo 0%": o limite superior de 95% e 25,9%, sao precisas 299 no total para sustentar
    precisao >= 0,99, e o alvo nao esta atingido."""
    for _ in range(10):
        fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    auditoria.sortear_automaticas(db_session)
    agora = datetime.now(UTC)
    for aud in db_session.scalars(select(AuditoriaAmostral)):
        aud.revisor_id, aud.decisao, aud.motivo, aud.decidida_em = (
            reais["auditor2"].id,
            "reprovada",
            "fora_de_foco",
            agora,
        )
    db_session.commit()
    sup = client.get("/metricas/auditoria", headers=login(client, reais["supervisor"])).json()
    b = sup["auto_rejeitadas"]
    assert (b["n"], b["erros"], b["taxa_erro"]) == (10, 0, 0.0)
    assert b["limite_superior_erro"] == pytest.approx(0.2589, abs=1e-4)
    assert (b["alvo_erro"], b["necessarias"], b["alvo_atingido"]) == (0.01, 299, False)
    assert sup["taxa_auto_rejeitada"] == 1.0 and sup["taxa_configurada"] == 0.07
    assert (b["detector"], b["politica"]) == ("heuristica@v1", "politica@v1")


# ------------------------------------------------------------------ gate 4


def test_identificador_opaco_nao_ordena_pelo_sorteio(client, db_session, reais, lote_aud, sempre):
    """Bloqueante do gate 4: o id sequencial, sorteado em rajada, diria a origem. O item e
    as rotas usam um uuid aleatorio (v4); em 20 auditorias ele nao segue a ordem do id."""
    for _ in range(20):
        fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    auditoria.sortear_automaticas(db_session)
    db_session.commit()
    auds = db_session.scalars(select(AuditoriaAmostral).order_by(AuditoriaAmostral.id)).all()
    codigos = [str(a.codigo) for a in auds]
    assert all(uuid.UUID(c).version == 4 for c in codigos)
    assert codigos != sorted(codigos) and codigos != sorted(codigos, reverse=True)
    item = client.post("/auditoria/proxima", headers=login(client, reais["auditor2"])).json()
    assert item["auditoria"] in codigos
    assert all(str(a.id) not in (item["auditoria"],) for a in auds)
    assert not any(isinstance(v, int) and v in {a.id for a in auds} for v in item.values())


def _decidir_auditoria(db_session, aud, revisor_id, decisao, motivo=None, quando=None):
    aud.revisor_id, aud.decisao, aud.motivo = revisor_id, decisao, motivo
    aud.decidida_em = quando or datetime.now(UTC)


def test_falso_positivo_uma_leitura_por_estrato(db_session, client, reais, lote_aud, sempre):
    """Bloqueante de dados do gate 4: a unidade e a leitura dentro de (detector, politica).
    Mesma leitura auto-rejeitada e auditada duas vezes pela v1: conta uma vez, a auditoria
    decidida mais antiga. Uma decisao da mesma leitura por outro detector/politica vai para
    o estrato dele e nao entra no da politica ativa."""
    from inspecao.estados import transicionar

    lei, _ = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    auditoria.sortear_automaticas(db_session)
    primeira = db_session.scalar(select(AuditoriaAmostral))
    _decidir_auditoria(db_session, primeira, reais["auditor2"].id, "aprovada")  # erro
    detalhe = {"politica": "politica@v1"}
    for ator, det in (
        ("modelo:heuristica@v1", detalhe),
        ("modelo:qualidade@v1", {"politica": "politica@v2"}),
    ):
        transicionar(db_session, lei, S.TRIAGEM_QUALIDADE, ator=ator)
        transicionar(db_session, lei, S.REJEITADA_QUALIDADE, ator=ator, detalhe=det)
        nova = db_session.scalar(
            select(AuditoriaAmostral)
            .where(AuditoriaAmostral.decisao.is_(None))
            .order_by(AuditoriaAmostral.id.desc())
        )
        _decidir_auditoria(
            db_session,
            nova,
            reais["auditor3"].id,
            "reprovada",
            "fora_de_foco",
            datetime.now(UTC) + timedelta(minutes=1),
        )
    db_session.commit()
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    # o bloco principal e o estrato da ULTIMA decisao automatica (quem decide de fato)
    vigente = admin["auto_rejeitadas"]
    assert (vigente["detector"], vigente["politica"], vigente["etapa"]) == (
        "qualidade@v1",
        "politica@v2",
        "qualidade",
    )
    assert (vigente["n"], vigente["erros"]) == (1, 0)
    estratos = {
        (e["detector"], e["politica"], e["etapa"]): (e["n"], e["erros"])
        for e in admin["estratos_auto_rejeitadas"]
    }
    assert estratos == {
        ("heuristica@v1", "politica@v1", "qualidade"): (1, 1),  # a mais antiga, a aprovada
        ("qualidade@v1", "politica@v2", "qualidade"): (1, 0),
    }


@pytest.mark.parametrize(
    ("origem", "decisao", "motivo", "erro"),
    [
        ("auto_rejeitada", "aprovada", None, True),
        ("auto_rejeitada", "reprovada", "outro", True),
        ("auto_rejeitada", "reprovada", "glare", False),
        ("auto_aprovada", "reprovada", "outro", True),
        ("auto_aprovada", "aprovada", None, False),
    ],
)
def test_definicao_de_erro_da_maquina(origem, decisao, motivo, erro):
    assert auditoria.e_erro_da_maquina(origem, decisao, motivo) is erro


def test_reabertura_pelo_recalcular_real(client, db_session, reais, lote_aud, sempre):
    """Via `recalcular_decisao`: v0 manda a auto-rejeitada para a fila (pendente invalida),
    v1 rejeita de novo (auditoria nova da decisao nova), v0 reabre outra vez e o auditor
    decide na fila com 201 e auditoria propria."""
    from inspecao.inference.detector import DetectorNulo
    from inspecao.jobs.handlers import recalcular_decisao
    from tests.test_recalcular_decisao import ctx_com

    ctx = ctx_com(DetectorNulo(), None)
    lei, _ = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    auditoria.sortear_automaticas(db_session)
    db_session.commit()

    def auds():
        db_session.expire_all()
        return db_session.scalars(
            select(AuditoriaAmostral)
            .where(AuditoriaAmostral.leitura_id == lei.id)
            .order_by(AuditoriaAmostral.id)
        ).all()

    recalcular_decisao(ctx, db_session, {"lote_id": lote_aud.id, "politica": "politica@v0"})
    db_session.commit()
    assert db_session.get(Leitura, lei.id).status_atual == S.FILA_HUMANA.value
    (a1,) = auds()
    assert a1.invalidada_em is not None
    recalcular_decisao(ctx, db_session, {"lote_id": lote_aud.id, "politica": "politica@v1"})
    db_session.commit()
    assert db_session.get(Leitura, lei.id).status_atual == S.REJEITADA_QUALIDADE.value
    a1, a2 = auds()
    assert a2.origem == "auto_rejeitada" and a2.invalidada_em is None and a2.historico_id
    recalcular_decisao(ctx, db_session, {"lote_id": lote_aud.id, "politica": "politica@v0"})
    db_session.commit()
    r = client.post(
        f"/fila-humana/{lei.id}/decisao",
        json={"decisao": "aprovada"},
        headers=login(client, reais["auditor"]),
    )
    assert r.status_code == 201, r.text
    origens = [(a.origem, a.invalidada_em is not None) for a in auds()]
    assert origens == [("auto_rejeitada", True), ("auto_rejeitada", True), ("fila", False)]


def test_recalcular_nao_reabre_com_auditoria_reservada(client, db_session, reais, lote_aud, sempre):
    """O segundo revisor com a auditoria na mao nao ve o item sumir: o recalculo pula a
    leitura enquanto a reserva vale (risco residual do 404 depois de reserva valida)."""
    from inspecao.inference.detector import DetectorNulo
    from inspecao.jobs.handlers import recalcular_decisao
    from tests.test_recalcular_decisao import ctx_com

    lei, _ = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    auditoria.sortear_automaticas(db_session)
    db_session.commit()
    cab = login(client, reais["auditor2"])
    item = client.post("/auditoria/proxima", headers=cab).json()
    recalcular_decisao(
        ctx_com(DetectorNulo(), None),
        db_session,
        {"lote_id": lote_aud.id, "politica": "politica@v0"},
    )
    db_session.commit()
    assert db_session.get(Leitura, lei.id).status_atual == S.REJEITADA_QUALIDADE.value
    r = client.post(
        f"/auditoria/{item['auditoria']}/decisao", json={"decisao": "aprovada"}, headers=cab
    )
    assert r.status_code == 201


def test_violacao_inesperada_no_sorteio_propaga(db_session, lote_aud):
    """So a colisao em `uq_auditorias_historico` e tolerada; outra violacao (aqui, FK de um
    historico que nao existe) propaga."""
    fantasma = HistoricoStatus(id=10**12, leitura_id=1, status=S.REJEITADA_QUALIDADE.value)
    lei, _ = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    with pytest.raises(IntegrityError):
        auditoria.sortear_decisao_automatica(db_session, lei.id, fantasma)


# ------------------------------------------------------------------ gate 5


def test_colisao_tolerada_devolve_none_e_avisa(db_session, lote_aud, sempre, caplog, monkeypatch):
    """A mesma decisao sorteada duas vezes (corrida com o sorteio retroativo): a segunda
    colide em `uq_auditorias_historico`, devolve None e loga em WARNING."""
    import logging

    lei, _ = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    decisao = db_session.scalar(
        select(HistoricoStatus).where(
            HistoricoStatus.leitura_id == lei.id, HistoricoStatus.status == S.REJEITADA_QUALIDADE
        )
    )
    assert auditoria.sortear_decisao_automatica(db_session, lei.id, decisao) is not None
    # o fileConfig do alembic (migracao do banco de teste) desliga os loggers ja criados
    monkeypatch.setattr(logging.getLogger("inspecao.auditoria"), "disabled", False)
    with caplog.at_level(logging.WARNING, logger="inspecao.auditoria"):
        assert auditoria.sortear_decisao_automatica(db_session, lei.id, decisao) is None
    assert any(
        r.levelno == logging.WARNING and "ja tem auditoria" in r.getMessage()
        for r in caplog.records
    )
    assert len(db_session.scalars(select(AuditoriaAmostral)).all()) == 1


def test_em_lote_recusa_destino_automatico(db_session, lote_aud):
    from inspecao.estados import TransicaoInvalida, transicionar_em_lote

    lei, _ = fx.leitura(db_session, lote_aud, S.TRIAGEM_QUALIDADE, fx.scores(0.95))
    with pytest.raises(TransicaoInvalida, match="use transicionar"):
        transicionar_em_lote(
            db_session, [lei.id], S.TRIAGEM_QUALIDADE, S.REJEITADA_QUALIDADE, ator="modelo:x"
        )


def test_foto_de_calibracao_e_sorteada_mas_fica_fora_da_medida(
    client, db_session, reais, lote_aud, sempre
):
    """Gates 5 e 6: foto com rotulo de calibracao/treino e acerto por construcao. Ela e
    sorteada normalmente (excluir do sorteio diria "na fila de auditoria = veio da fila"),
    mas a auditoria decidida dela fica fora do estrato."""
    from inspecao.estados import transicionar

    cal, foto_cal = fx.leitura(db_session, lote_aud, S.TRIAGEM_QUALIDADE, fx.scores(0.95))
    _rotulo_lab02(db_session, foto_cal.id)
    db_session.flush()
    transicionar(
        db_session,
        cal,
        S.REJEITADA_QUALIDADE,
        "modelo:heuristica@v1",
        detalhe={"politica": "politica@v1"},
    )
    aud = db_session.scalar(select(AuditoriaAmostral))
    assert aud is not None and aud.leitura_id == cal.id  # sorteada como qualquer outra
    _decidir_auditoria(db_session, aud, reais["auditor2"].id, "reprovada", "fora_de_foco")
    db_session.commit()
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    assert admin["auto_rejeitadas"]["n"] == 0 and admin["estratos_auto_rejeitadas"] == []


def _rotulo_lab02(db_session, foto_id):
    db_session.add(
        Rotulo(
            foto_id=foto_id,
            rotulador_externo="ext_x",
            rodada="lab02",
            cena="digital",
            legivel=False,
            reflexo=False,
            fora_de_foco=True,
            tampa_suja=False,
            enquadramento=False,
            display_apagado=False,
        )
    )


def test_inspecao_m5_importada_e_fora_da_medida(
    client, db_session, reais, lote_aud, sempre, tmp_path
):
    """Bloqueante de dados do gate 6: a inspecao das 200 fotos do M5 calibrou o 0,85. O
    importador grava toda foto do CSV na rodada inspecao_m5 (inspecionada ou pendente), e
    a auditoria decidida dessas fotos fica fora do estrato."""
    import csv

    from inspecao.enums import RodadaRotulagem
    from inspecao.rotulagem import inspecao_m5, servico

    _, foto_i = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    _, foto_p = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))  # fora da inspecao
    caminho = tmp_path / "amostra.csv"
    with caminho.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["arquivo", "grupo", "legivel_inspecao", "inspetor"])
        w.writerow([foto_i.nome_arquivo, "pior", "0", "inspetora"])
        w.writerow([foto_p.nome_arquivo, "pior", "", ""])
        w.writerow(["PSP_EXTRATLEITIMPL_990101_0000_x.jpg", "pior", "1", "x"])
    r = inspecao_m5.importar(db_session, caminho)
    assert (r.linhas, r.gravados, r.inspecionados, r.pendentes, len(r.nao_mapeadas)) == (
        3,
        2,
        1,
        1,
        1,
    )
    assert inspecao_m5.importar(db_session, caminho).gravados == 2  # idempotente
    rotulos = {
        x.foto_id: (x.rotulador_externo, x.legivel)
        for x in db_session.scalars(select(Rotulo).where(Rotulo.rodada == "inspecao_m5"))
    }
    assert rotulos == {foto_i.id: ("inspetora", False), foto_p.id: ("pendente", None)}
    with pytest.raises(servico.RodadaSemEsquemaCompleto):
        servico.votos(db_session, [RodadaRotulagem.INSPECAO_M5])

    auditoria.sortear_automaticas(db_session)
    for aud in db_session.scalars(select(AuditoriaAmostral)):
        _decidir_auditoria(db_session, aud, reais["auditor2"].id, "aprovada")  # erro
    db_session.commit()
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    b = admin["auto_rejeitadas"]
    assert (b["n"], b["erros"]) == (1, 1)  # so a leitura fora da inspecao
    assert admin["rotulos_de_calibracao"]["inspecao_m5"] == 2


def test_precondicao_da_exclusao_aparece_na_metrica(client, db_session, reais, lote_aud):
    """Sem rotulos lab02 e inspecao_m5 importados a exclusao nao enxerga nada: a metrica
    diz quais faltam (e o backend loga WARNING)."""
    sup = login(client, reais["supervisor"])
    c = client.get("/metricas/auditoria", headers=sup).json()
    assert c["exclusao_incompleta"] == ["lab02", "inspecao_m5"]
    _, foto = fx.leitura(db_session, lote_aud, S.FILA_HUMANA, fx.scores(0.2))
    _rotulo_lab02(db_session, foto.id)
    db_session.add(Rotulo(foto_id=foto.id, rodada="inspecao_m5", rotulador_externo="pendente"))
    db_session.commit()
    c = client.get("/metricas/auditoria", headers=sup).json()
    assert c["exclusao_incompleta"] == []
    assert (c["rotulos_de_calibracao"]["lab02"], c["rotulos_de_calibracao"]["inspecao_m5"]) == (
        1,
        1,
    )


def test_troca_de_detector_adiada_com_auditoria_travada(sempre):
    """Ramo de `triagem_qualidade`: a auditoria pendente da leitura esta travada por outra
    transacao (um revisor reservando agora). A troca de detector nao reabre a leitura; ela
    fica com a decisao antiga ate um novo `reprocessar_lote`."""
    from inspecao.db import engine
    from inspecao.inference.detector import DetectorNulo
    from inspecao.jobs.handlers import triagem_qualidade
    from tests.test_recalcular_decisao import ctx_com

    s, lote, lei, _revisor, aud_id = _cenario_corrida()
    trava = Session(engine)
    job = Session(engine, expire_on_commit=False)
    # Se a guarda regredir e o job esperar o lock, o teste falha em 5 s em vez de travar.
    job.execute(text("SET lock_timeout = '5s'"))
    try:
        trava.execute(
            select(AuditoriaAmostral.id).where(AuditoriaAmostral.id == aud_id).with_for_update()
        )
        antes = s.scalar(
            select(func.count())
            .select_from(HistoricoStatus)
            .where(HistoricoStatus.leitura_id == lei.id)
        )
        triagem_qualidade(ctx_com(DetectorNulo(), None), job, {"leitura_id": lei.id})
        job.commit()
        s.expire_all()
        assert s.get(Leitura, lei.id).status_atual == S.REJEITADA_QUALIDADE.value
        depois = s.scalar(
            select(func.count())
            .select_from(HistoricoStatus)
            .where(HistoricoStatus.leitura_id == lei.id)
        )
        assert depois == antes
        assert s.get(AuditoriaAmostral, aud_id).invalidada_em is None
    finally:
        trava.rollback()
        trava.close()
        # O SET vale para a conexao, que volta ao pool: sem o RESET, o proximo teste que a
        # pegar herda o lock_timeout de 5 s.
        job.rollback()
        job.execute(text("RESET lock_timeout"))
        job.commit()
        job.close()
        _limpar_corrida(s, lote.id)


@pytest.mark.parametrize(
    ("etapa", "decisao", "motivo", "erro"),
    [
        ("qualidade", "reprovada", "outro", True),
        ("validacao", "reprovada", "outro", False),  # divergencia confirmada
        ("validacao", "aprovada", None, True),
    ],
)
def test_erro_depende_da_etapa(etapa, decisao, motivo, erro):
    assert auditoria.e_erro_da_maquina("auto_rejeitada", decisao, motivo, etapa) is erro


def test_rejeicao_por_divergencia_tem_estrato_proprio(client, db_session, reais, lote_aud, sempre):
    """AUTO_REJEITADA (validacao, ponto B) nao se soma a rejeitada_qualidade (triagem)."""
    from inspecao.estados import transicionar

    for status in (S.REJEITADA_QUALIDADE, S.AUTO_REJEITADA):
        lei, _ = fx.leitura(db_session, lote_aud, S.TRIAGEM_QUALIDADE, fx.scores(0.95))
        if status is S.AUTO_REJEITADA:
            transicionar(db_session, lei, S.VALIDACAO_LEITURA, "modelo:heuristica@v1")
        transicionar(
            db_session, lei, status, "modelo:heuristica@v1", detalhe={"politica": "politica@v1"}
        )
    for aud in db_session.scalars(select(AuditoriaAmostral)):
        _decidir_auditoria(db_session, aud, reais["auditor2"].id, "reprovada", "outro")
    db_session.commit()
    admin = client.get("/metricas/auditoria", headers=login(client, reais["admin"])).json()
    estratos = {e["etapa"]: (e["n"], e["erros"]) for e in admin["estratos_auto_rejeitadas"]}
    assert estratos == {"qualidade": (1, 1), "validacao": (1, 0)}


def _cenario_corrida():
    """Dados commitados de verdade (duas conexoes): leitura auto-rejeitada com auditoria."""
    from inspecao.db import engine

    s = Session(engine, expire_on_commit=False)
    lote = fx.lote(s, "0126_0098", date(2026, 1, 9))
    revisor = fx.contas_reais(s)["auditor2"]
    lei, _ = fx.leitura(s, lote, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    s.commit()
    auditoria.sortear_automaticas(s)
    s.commit()
    aud_id = s.scalar(select(AuditoriaAmostral.id).where(AuditoriaAmostral.leitura_id == lei.id))
    return s, lote, lei, revisor, aud_id


def _limpar_corrida(s, lote_id):
    from inspecao.db import engine

    s.close()
    with engine.begin() as c:
        c.execute(text("DELETE FROM lotes WHERE id = :i"), {"i": lote_id})
        c.execute(text("DELETE FROM usuarios WHERE email LIKE :d"), {"d": f"%{fx.DOMINIO_REAL}"})


def test_corrida_reserva_durante_o_recalcular(monkeypatch, sempre):
    """Bloqueante do gate 5, com a sonda do gate como base: o segundo revisor tenta
    reservar NO MEIO do recalcular (outra conexao, commit real). O job ja travou a
    auditoria pendente, entao o `/proxima` nao a entrega; a reabertura invalida sem
    ninguem com o item na mao, e nao ha 404 depois de reserva."""
    from inspecao.db import engine
    from inspecao.inference.detector import DetectorNulo
    from inspecao.jobs import handlers
    from tests.test_recalcular_decisao import ctx_com

    s, lote, lei, revisor, aud_id = _cenario_corrida()
    entregue = {}
    original = handlers.transicionar

    def com_corrida(sess, leitura, novo, *a, **k):
        if novo is S.TRIAGEM_QUALIDADE and "feito" not in entregue:
            entregue["feito"] = True
            with Session(engine) as outra:
                entregue["aud"] = auditoria.reservar_proxima(outra, revisor.id)
                outra.commit()
        return original(sess, leitura, novo, *a, **k)

    monkeypatch.setattr(handlers, "transicionar", com_corrida)
    job = Session(engine, expire_on_commit=False)
    try:
        handlers.recalcular_decisao(
            ctx_com(DetectorNulo(), None), job, {"lote_id": lote.id, "politica": "politica@v0"}
        )
        job.commit()
        assert entregue["aud"] is None  # travada pelo job: nao entregue
        s.expire_all()
        a = s.get(AuditoriaAmostral, aud_id)
        assert a.invalidada_em is not None and a.reservado_por is None
        assert s.get(Leitura, lei.id).status_atual == S.FILA_HUMANA.value
    finally:
        job.close()
        _limpar_corrida(s, lote.id)


def test_reserva_antes_do_recalcular_pula_a_leitura(sempre):
    """A outra ordem: a reserva foi commitada antes do job. O recalcular pula a leitura e a
    auditoria segue valida e reservada."""
    from inspecao.db import engine
    from inspecao.inference.detector import DetectorNulo
    from inspecao.jobs import handlers
    from tests.test_recalcular_decisao import ctx_com

    s, lote, lei, revisor, aud_id = _cenario_corrida()
    with Session(engine) as outra:
        assert auditoria.reservar_proxima(outra, revisor.id).id == aud_id
        outra.commit()
    job = Session(engine, expire_on_commit=False)
    try:
        handlers.recalcular_decisao(
            ctx_com(DetectorNulo(), None), job, {"lote_id": lote.id, "politica": "politica@v0"}
        )
        job.commit()
        s.expire_all()
        a = s.get(AuditoriaAmostral, aud_id)
        assert a.invalidada_em is None and a.reservado_por == revisor.id
        assert s.get(Leitura, lei.id).status_atual == S.REJEITADA_QUALIDADE.value
    finally:
        job.close()
        _limpar_corrida(s, lote.id)


# ------------------------------------------------------------------ integracao com o M7


def _auto_rejeitada_auditada(db_session, lote, politica, revisor_id, decisao="aprovada"):
    """Auto-rejeitada pela `politica` via `transicionar` (que grava a ativacao e sorteia),
    com a auditoria decidida pelo `revisor_id`."""
    from inspecao.estados import transicionar

    lei, _ = fx.leitura(db_session, lote, S.TRIAGEM_QUALIDADE, fx.scores(0.95))
    registro = transicionar(
        db_session,
        lei,
        S.REJEITADA_QUALIDADE,
        ator="modelo:qualidade@v1",
        detalhe={"politica": politica},
    )
    aud = db_session.scalar(
        select(AuditoriaAmostral).where(AuditoriaAmostral.historico_id == registro.id)
    )
    _decidir_auditoria(db_session, aud, revisor_id, decisao)
    return lei, registro


def _estrato(db_session, politica):
    return auditoria._estratos(db_session, "auto_rejeitada").get(
        ("qualidade@v1", politica, "qualidade"), []
    )


def test_primeira_decisao_automatica_grava_a_ativacao_com_os_lotes_existentes(
    db_session, reais, lote_aud, sempre
):
    """A ativacao e gravada pela primeira decisao automatica da politica, com os lotes que
    existem nesse instante (inclusive o da propria decisao); a segunda nao a muda."""
    from inspecao.models import AtivacaoPolitica

    p = "politica@teste-ativacao"
    _, primeira = _auto_rejeitada_auditada(db_session, lote_aud, p, reais["auditor2"].id)
    ativacao = db_session.get(AtivacaoPolitica, p)
    assert ativacao.historico_id == primeira.id
    assert lote_aud.nome in ativacao.lotes_anteriores
    novo = fx.lote(db_session, "0126_0005", date(2026, 1, 4))
    _auto_rejeitada_auditada(db_session, novo, p, reais["auditor2"].id)
    db_session.expire_all()
    ativacao = db_session.get(AtivacaoPolitica, p)
    assert ativacao.historico_id == primeira.id and novo.nome not in ativacao.lotes_anteriores


def test_estrato_da_politica_so_conta_lotes_ingeridos_depois_da_ativacao(
    db_session, reais, lote_aud, sempre
):
    """Regra do merge M7 x M8: a politica ativada com `lote_aud` ja no banco nunca mede
    `lote_aud` (contem a calibracao dela), nem se ele for reingerido com o mesmo nome; um
    lote ingerido depois da ativacao entra."""
    from sqlalchemy import delete

    from inspecao.models import Lote

    p = "politica@teste-posterior"
    _auto_rejeitada_auditada(db_session, lote_aud, p, reais["auditor2"].id)
    db_session.flush()
    assert _estrato(db_session, p) == []  # lote anterior a ativacao: fora
    depois = fx.lote(db_session, "0126_0006", date(2026, 1, 5))
    _auto_rejeitada_auditada(db_session, depois, p, reais["auditor2"].id)
    db_session.flush()
    assert _estrato(db_session, p) == [("aprovada", None)]
    # reingestao (--reprocessar): o lote anterior volta com outro id e o mesmo nome
    nome = lote_aud.nome
    db_session.execute(delete(Lote).where(Lote.id == lote_aud.id))
    db_session.expunge_all()
    reingerido = Lote(nome=nome, csv_nome="x.csv", checksum_csv="1" * 64)
    db_session.add(reingerido)
    db_session.flush()
    _auto_rejeitada_auditada(db_session, reingerido, p, reais["auditor3"].id)
    db_session.flush()
    assert _estrato(db_session, p) == [("aprovada", None)]


def test_politica_sem_ativacao_nao_mede(db_session, reais, lote_aud, sempre):
    """Decisao gravada no historico sem passar pela ativacao (ou sem politica registrada):
    fica fora do estrato, em vez de medir lotes que podem ser de calibracao."""
    lei, _ = fx.leitura(
        db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95), politica="politica@nunca"
    )
    auditoria.sortear_automaticas(db_session)
    aud = db_session.scalar(select(AuditoriaAmostral).where(AuditoriaAmostral.leitura_id == lei.id))
    _decidir_auditoria(db_session, aud, reais["auditor2"].id, "aprovada")
    db_session.flush()
    estratos = auditoria._estratos(db_session, "auto_rejeitada")
    assert ("heuristica@v1", "politica@nunca", "qualidade") not in estratos


def test_inspecao_m7_e_calibracao_e_obrigatoria_com_o_detector_onnx(
    db_session, lote_aud, monkeypatch
):
    """A inspecao das 355 entra como rodada de calibracao (fora da medida) e passa a ser
    obrigatoria na precondicao quando o detector configurado e o qualidade@v1."""
    from inspecao.inference.politica import POLITICA_V1, POLITICA_V2
    from inspecao.monitoramento import sinais

    assert "inspecao_m7" in auditoria.RODADAS_DE_CALIBRACAO
    monkeypatch.setattr(sinais, "politica_ativa", lambda: POLITICA_V1)
    assert "inspecao_m7" not in auditoria.precondicao_da_exclusao(db_session)[1]
    monkeypatch.setattr(sinais, "politica_ativa", lambda: POLITICA_V2)
    assert "inspecao_m7" in auditoria.precondicao_da_exclusao(db_session)[1]
    _, foto = fx.leitura(db_session, lote_aud, S.REJEITADA_QUALIDADE, fx.scores(0.95))
    db_session.add(Rotulo(foto_id=foto.id, rodada="inspecao_m7", rotulador_externo="pendente"))
    db_session.flush()
    contagem, faltando = auditoria.precondicao_da_exclusao(db_session)
    assert contagem["inspecao_m7"] == 1 and "inspecao_m7" not in faltando
