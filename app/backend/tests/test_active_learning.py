"""T-M8-06: decisao de auditor vira rotulo `producao` na mesma transacao."""

from datetime import date

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from inspecao.enums import DecisaoHumana, MotivoReprovacao
from inspecao.enums import StatusLeitura as S
from inspecao.models import HistoricoStatus, Leitura, RevisaoHumana, Rotulo
from inspecao.rotulagem import producao
from tests import m8_fixture as fx
from tests.usuarios_fixture import login


@pytest.fixture
def na_fila(db_session):
    lt = fx.lote(db_session, "0126_0002", date(2026, 1, 1))
    itens = [fx.leitura(db_session, lt, S.FILA_HUMANA, fx.scores(0.1)) for _ in range(3)]
    db_session.commit()
    return itens


def _rotulos(db_session, foto_id):
    return db_session.scalars(
        select(Rotulo).where(Rotulo.foto_id == foto_id, Rotulo.rodada == "producao")
    ).all()


@pytest.mark.parametrize(
    ("decisao", "motivo", "esperado"),
    [
        ("aprovada", None, {"legivel": True}),
        ("reprovada", "glare", {"legivel": False, "reflexo": True}),
        ("reprovada", "fora_de_foco", {"legivel": False, "fora_de_foco": True}),
        ("reprovada", "tampa_suja", {"legivel": False, "tampa_suja": True}),
        ("reprovada", "enquadramento_incorreto", {"legivel": False, "enquadramento": True}),
        ("reprovada", "display_apagado", {"legivel": False, "display_apagado": True}),
        ("reprovada", "outro", {}),
    ],
)
def test_mapeamento_da_decisao_para_o_esquema_v2(decisao, motivo, esperado):
    campos = producao.campos_da_decisao(
        DecisaoHumana(decisao), MotivoReprovacao(motivo) if motivo else None
    )
    colunas = ("cena", "legivel", *producao.COLUNAS_DEFEITO)
    assert set(campos) == set(colunas)
    assert campos == dict.fromkeys(colunas) | esperado


def test_decisao_na_fila_grava_rotulo_producao(client, db_session, contas, na_fila):
    lei, foto = na_fila[0]
    cab = login(client, contas["auditor"])
    r = client.post(
        f"/fila-humana/{lei.id}/decisao",
        json={"decisao": "reprovada", "motivo": "glare"},
        headers=cab,
    )
    assert r.status_code == 201, r.text
    (rotulo,) = _rotulos(db_session, foto.id)
    assert rotulo.revisao_id == r.json()["id"] and rotulo.auditoria_id is None
    assert rotulo.usuario_id == contas["auditor"].id
    assert (rotulo.legivel, rotulo.reflexo, rotulo.cena, rotulo.fora_de_foco) == (
        False,
        True,
        None,
        None,
    )


def test_rotulo_e_decisao_na_mesma_transacao(client, db_session, contas, na_fila):
    """Se o rotulo nao entra (indice unico por foto, rodada e usuario), a decisao
    tambem nao: 409, leitura continua na fila e nenhuma revisao nova."""
    lei, foto = na_fila[1]
    uid = contas["auditor"].id
    antiga = RevisaoHumana(leitura_id=lei.id, usuario_id=uid, decisao="aprovada")
    db_session.add(antiga)
    db_session.flush()
    db_session.add(Rotulo(foto_id=foto.id, usuario_id=uid, rodada="producao", revisao_id=antiga.id))
    db_session.commit()

    r = client.post(
        f"/fila-humana/{lei.id}/decisao",
        json={"decisao": "aprovada"},
        headers=login(client, contas["auditor"]),
    )
    assert r.status_code == 409, r.text
    db_session.expire_all()
    assert db_session.get(Leitura, lei.id).status_atual == S.FILA_HUMANA.value
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(RevisaoHumana)
            .where(RevisaoHumana.leitura_id == lei.id)
        )
        == 1
    )
    assert not db_session.scalar(
        select(func.count())
        .select_from(HistoricoStatus)
        .where(HistoricoStatus.leitura_id == lei.id, HistoricoStatus.status == "aprovada_humano")
    )


def test_rotulo_incompleto_so_na_rodada_producao(db_session, na_fila, contas):
    _, foto = na_fila[2]
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(Rotulo(foto_id=foto.id, usuario_id=contas["supervisor"].id, rodada="a"))
        db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        # producao sem a decisao de origem
        db_session.add(Rotulo(foto_id=foto.id, usuario_id=contas["auditor"].id, rodada="producao"))
        db_session.flush()


def test_rodadas_cegas_nao_sao_afetadas(client, db_session, contas, na_fila):
    """Rotulo de producao nao entra no pool, na escrita nem na consolidacao das
    rodadas cegas, e a consolidacao recusa `producao` (campos nulos)."""
    lei, foto = na_fila[0]
    sup = contas["supervisor"]
    # supervisor decide na fila (vira producao) e ainda pode rotular a foto na rodada a
    fx.decisao(db_session, lei, sup.id, "aprovada")
    db_session.add(
        Rotulo(
            foto_id=foto.id,
            rotulador_externo="ext_x",
            rodada="lab02",
            cena="digital",
            legivel=True,
            **dict.fromkeys(producao.COLUNAS_DEFEITO, False),
        )
    )
    db_session.commit()
    cab = login(client, sup)
    r = client.post("/rotulagem/proxima", params={"rodada": "a"}, headers=cab)
    assert r.status_code == 200 and r.json()["foto_id"] == foto.id
    corpo = {"rodada": "a", "cena": "digital", "legivel": True, "defeitos": []}
    assert client.post(f"/rotulagem/{foto.id}", json=corpo, headers=cab).status_code == 201

    adm = login(client, contas["admin"])
    r = client.get("/rotulagem/consolidacao", params={"rodada": ["producao"]}, headers=adm)
    assert r.status_code == 422 and "producao" in r.text
    r = client.get("/rotulagem/consolidacao", params={"rodada": ["lab02", "a"]}, headers=adm)
    assert r.status_code == 200 and r.json()["rotulos"] == 2


def test_migracao_reversivel_com_backfill():
    """Downgrade apaga os rotulos de producao; o upgrade recria um por revisao, com o
    mesmo mapeamento do codigo. Dados commitados de verdade (fora da transacao de
    teste) e apagados no fim."""
    from alembic import command
    from alembic.config import Config

    from inspecao.db import engine
    from tests.conftest import RAIZ_BACKEND
    from tests.usuarios_fixture import criar_usuario

    cfg = Config(str(RAIZ_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ_BACKEND / "migrations"))
    from sqlalchemy.orm import Session

    from inspecao.enums import Papel

    with Session(engine) as s:
        lt = fx.lote(s, "0126_0003", date(2026, 1, 2))
        conta = criar_usuario(s, "backfill@teste.com.br", Papel.AUDITOR)
        aprovada, _ = fx.leitura(s, lt, S.APROVADA_HUMANO, fx.scores(0.1))
        reprovada, foto_r = fx.leitura(s, lt, S.REPROVADA_HUMANO, fx.scores(0.1))
        fx.decisao(s, aprovada, conta.id, "aprovada")
        fx.decisao(s, reprovada, conta.id, "reprovada", "tampa_suja")
        s.commit()
        lote_id, uid, foto_r_id = lt.id, conta.id, foto_r.id
    try:
        engine.dispose()
        command.downgrade(cfg, "c3e1a7d9f204")
        insp = inspect(engine)
        assert "auditorias_amostrais" not in insp.get_table_names()
        assert not {c["name"]: c for c in insp.get_columns("rotulos")}["legivel"]["nullable"]
        with engine.connect() as c:
            assert not c.scalar(text("SELECT count(*) FROM rotulos WHERE rodada = 'producao'"))
        command.upgrade(cfg, "head")
        with Session(engine) as s:
            rotulos = s.scalars(
                select(Rotulo).where(Rotulo.rodada == "producao", Rotulo.usuario_id == uid)
            ).all()
            assert len(rotulos) == 2 and all(r.revisao_id for r in rotulos)
            r = next(r for r in rotulos if r.foto_id == foto_r_id)
            assert (r.legivel, r.tampa_suja, r.reflexo, r.cena) == (False, True, None, None)
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM lotes WHERE id = :i"), {"i": lote_id})
            c.execute(text("DELETE FROM revisoes_humanas WHERE usuario_id = :u"), {"u": uid})
            c.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": uid})
