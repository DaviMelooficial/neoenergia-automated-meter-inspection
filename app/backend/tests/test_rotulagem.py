"""Aceite do M6 no backend: tabela `rotulos`, importador do Lab 2, cegueira do
`/rotulagem/proxima`, regras das rodadas, kappa por classe e export.

Tudo com nomes de arquivo, leituras e notas ficticios: lote 010199 nao existe.
"""

import csv
import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from inspecao.enums import Papel
from inspecao.models import Foto, Leitura, Lote, ResultadoInferencia, RevisaoHumana, Rotulo
from inspecao.rotulagem import importador
from inspecao.rotulagem.consolidacao import DEFEITOS
from tests.usuarios_fixture import criar_usuario, login

LOTE = "PSP_EXTRATLEITIMPL_010199_0000"
CABECALHO = ["nome_arquivo", "lote", "rotulador", "cena", *DEFEITOS, "legivel", "pulada"]
CABECALHO += ["segundos_na_foto", "gravado_em"]
CAMPOS_PROXIMA = {"foto_id", "url", "largura", "altura", "rodada", "restantes"}


def nome(i: int) -> str:
    return f"{LOTE}_{i:020d}_000.jpg"


def linha(i: int, quem: str, cena: str, legivel: int = 0, pulada: int = 0, **defeitos) -> list:
    valores = [defeitos.get(d, 0) for d in DEFEITOS]
    return [nome(i), "010199", quem, cena, *valores, legivel, pulada, "4.5", "2026-10-08T13:17:32"]


def escrever_csv(caminho, linhas) -> None:
    with caminho.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CABECALHO)
        w.writerows(linhas)


@pytest.fixture
def fotos(db_session):
    """Seis fotos ficticias; a 1 tem leitura, nota, inferencia e revisao humana."""
    lote = Lote(nome=LOTE, csv_nome=f"{LOTE}.csv", checksum_csv="0" * 64)
    db_session.add(lote)
    db_session.flush()
    leitura = Leitura(lote_id=lote.id, linha_csv=2, posicao_lida=987654, nota_codigo="Z902")
    db_session.add(leitura)
    db_session.flush()
    criadas = {}
    for i in range(1, 7):
        f = Foto(
            lote_id=lote.id,
            leitura_id=leitura.id if i == 1 else None,
            principal=i == 1,
            nome_arquivo=nome(i),
            id_externo=f"{i:020d}",
            sequencia=0,
            caminho_storage=f"{LOTE}/{nome(i)}",
            largura=360,
            altura=480,
            bytes=1,
            sha256=f"{i:064d}",
        )
        db_session.add(f)
        criadas[i] = f
    db_session.flush()
    db_session.add(
        ResultadoInferencia(
            foto_id=criadas[1].id, modelo="heuristica@v1", saida={"p_ruim": 0.123456}
        )
    )
    return criadas


@pytest.fixture
def lab02(db_session, fotos, tmp_path):
    """Amostra = fotos 1 a 4 rotuladas no Lab 2 por dois rotuladores externos."""
    caminho = tmp_path / "rotulos.csv"
    escrever_csv(
        caminho,
        [
            linha(1, "ext_x", "eletromecanico", reflexo=1),
            linha(1, "ext_y", "eletromecanico", reflexo=1),
            linha(2, "ext_x", "digital", legivel=1),
            linha(2, "ext_y", "eletromecanico"),
            linha(3, "ext_x", "eletromecanico"),
            linha(3, "ext_y", "eletromecanico"),
            linha(4, "ext_x", "ocorrencia"),
            linha(4, "ext_y", "ocorrencia"),
        ],
    )
    importador.importar(db_session, caminho)
    return fotos


@pytest.fixture
def rotuladores(db_session, contas):
    extras = {
        f"sup{i}": criar_usuario(db_session, f"sup{i}@teste.com.br", Papel.SUPERVISOR)
        for i in (2, 3)
    }
    return {"sup1": contas["supervisor"], **extras, "admin": contas["admin"]}


def rotulo(rodada: str, cena: str = "digital", legivel: bool = True, defeitos=()) -> dict:
    return {"rodada": rodada, "cena": cena, "legivel": legivel, "defeitos": list(defeitos)}


# ------------------------------------------------------------------ migracao


def test_migracao_reversivel():
    from alembic import command
    from alembic.config import Config

    from inspecao.db import engine
    from tests.conftest import RAIZ_BACKEND

    cfg = Config(str(RAIZ_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ_BACKEND / "migrations"))
    engine.dispose()
    command.downgrade(cfg, "1840bea5fdb9")
    assert "rotulos" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert "rotulos" in insp.get_table_names()
    indices = {i["name"]: i for i in insp.get_indexes("rotulos")}
    assert indices["uq_rotulos_foto_rodada_usuario"]["unique"]
    assert indices["uq_rotulos_foto_rodada_externo"]["unique"]


def _inserir(db_session, **campos):
    base = {"rodada": "a", "cena": "digital", "legivel": True}
    base |= {d: False for d in DEFEITOS}
    with db_session.begin_nested():
        db_session.add(Rotulo(**(base | campos)))
        db_session.flush()


def test_mesmo_rotulador_nao_rotula_a_mesma_foto_duas_vezes_na_rodada(db_session, fotos, contas):
    uid, foto = contas["supervisor"].id, fotos[1].id
    _inserir(db_session, foto_id=foto, usuario_id=uid)
    with pytest.raises(IntegrityError):
        _inserir(db_session, foto_id=foto, usuario_id=uid)
    # outra rodada fora da dupla-cega, ou outro rotulador, pode
    _inserir(db_session, foto_id=foto, usuario_id=uid, rodada="producao")
    _inserir(db_session, foto_id=foto, usuario_id=contas["admin"].id)
    _inserir(db_session, foto_id=foto, rotulador_externo="ext_x", rodada="lab02")
    with pytest.raises(IntegrityError):
        _inserir(db_session, foto_id=foto, rotulador_externo="ext_x", rodada="lab02")


@pytest.mark.parametrize(
    "campos",
    [
        {},  # nem usuario nem externo
        {"rotulador_externo": "ext_x", "usuario": True},  # os dois
        {"rotulador_externo": "ext_x", "rodada": "rodada_c"},
        {"rotulador_externo": "ext_x", "cena": "medidor_de_agua"},
    ],
)
def test_check_constraints(db_session, fotos, contas, campos):
    if campos.pop("usuario", False):
        campos["usuario_id"] = contas["supervisor"].id
    with pytest.raises(IntegrityError):
        _inserir(db_session, foto_id=fotos[1].id, **campos)


# ------------------------------------------------------------------ importador


def test_importador_idempotente_e_conta_nao_mapeadas(db_session, fotos, tmp_path):
    caminho = tmp_path / "rotulos.csv"
    linhas = [
        linha(1, "ext_x", "digital", legivel=1),
        linha(1, "ext_y", "digital", legivel=1, reflexo=1),
        linha(2, "ext_x", "nao_medidor"),
        linha(3, "ext_x", "digital", pulada=1),
        linha(99, "ext_x", "digital"),  # foto que nao existe no banco
    ]
    escrever_csv(caminho, linhas)

    r1 = importador.importar(db_session, caminho)
    assert (r1.linhas, r1.puladas, r1.fotos_csv, r1.fotos_mapeadas) == (5, 1, 3, 2)
    assert r1.nao_mapeadas == [nome(99)]
    assert (r1.inseridos, r1.atualizados, r1.inalterados) == (3, 0, 0)

    r2 = importador.importar(db_session, caminho)
    assert (r2.inseridos, r2.atualizados, r2.inalterados) == (0, 0, 3)
    total = db_session.scalars(select(Rotulo).where(Rotulo.rodada == "lab02")).all()
    assert len(total) == 3

    linhas[0] = linha(1, "ext_x", "digital", legivel=0, fora_de_foco=1)
    escrever_csv(caminho, linhas)
    r3 = importador.importar(db_session, caminho)
    assert (r3.inseridos, r3.atualizados, r3.inalterados) == (0, 1, 2)
    corrigido = db_session.scalar(
        select(Rotulo).where(Rotulo.foto_id == fotos[1].id, Rotulo.rotulador_externo == "ext_x")
    )
    assert corrigido.fora_de_foco and not corrigido.legivel and corrigido.usuario_id is None
    assert corrigido.gravado_em == datetime(2026, 10, 8, 16, 17, 32, tzinfo=UTC)
    assert corrigido.segundos_na_foto == 4.5


def test_importador_marca_controle_e_export_traz_a_coluna(
    client, db_session, fotos, tmp_path, contas
):
    caminho = tmp_path / "rotulos.csv"
    escrever_csv(
        caminho,
        [
            linha(1, "ext_x", "digital", legivel=1),
            linha(1, "ext_y", "digital", legivel=1),
            linha(2, "ext_x", "digital", legivel=1),
        ],
    )
    sha = hashlib.sha256(nome(1).encode()).hexdigest()
    arq_controle = tmp_path / "controle.dat"
    arq_controle.write_text(f"{sha}\n{'f' * 64}\n", encoding="ascii")
    hashes = importador.ler_controle(arq_controle)

    r1 = importador.importar(db_session, caminho, hashes)
    assert r1.fotos_controle == 1 and r1.inseridos == 3
    # sem o controle.dat o campo nao e tocado: continua idempotente
    r2 = importador.importar(db_session, caminho)
    assert r2.fotos_controle is None and r2.inalterados == 3
    assert importador.importar(db_session, caminho, hashes).inalterados == 3

    cab = login(client, contas["admin"])
    linhas = {
        row["nome_arquivo"]: row
        for row in csv.DictReader(client.get("/rotulagem/export", headers=cab).text.splitlines())
    }
    assert (linhas[nome(1)]["controle"], linhas[nome(2)]["controle"]) == ("1", "0")
    consolidado = client.get("/rotulagem/consolidacao", headers=cab).json()
    assert consolidado["fotos_dupla_rotulagem"] == 1

    arq_controle.write_text("nao-e-hash\n", encoding="ascii")
    with pytest.raises(importador.CsvInvalido, match="sha256"):
        importador.ler_controle(arq_controle)


@pytest.mark.parametrize(
    "linhas, trecho",
    [
        ([linha(1, "ext_x", "medidor_de_agua")], "cena"),
        ([linha(1, "ext_x", "digital"), linha(1, "ext_x", "digital")], "duas vezes"),
        ([linha(1, "ext_x", "digital", legivel=2)], "legivel"),
    ],
)
def test_importador_recusa_csv_invalido(db_session, fotos, tmp_path, linhas, trecho):
    caminho = tmp_path / "rotulos.csv"
    escrever_csv(caminho, linhas)
    with pytest.raises(importador.CsvInvalido, match=trecho):
        importador.importar(db_session, caminho)
    assert db_session.scalars(select(Rotulo)).first() is None


# ------------------------------------------------------------------ cegueira


def test_proxima_nao_vaza_rotulo_alheio_maquina_nem_leiturista(
    client, db_session, lab02, rotuladores
):
    """A foto 1 tem tudo o que poderia enviesar: dois rotulos lab02, um rotulo da
    rodada a de outro usuario, leitura com posicao e nota, score da heuristica e
    reprovacao de auditor. A resposta nao pode trazer nada disso."""
    db_session.add(
        RevisaoHumana(
            leitura_id=lab02[1].leitura_id,
            usuario_id=rotuladores["admin"].id,
            decisao="reprovada",
            motivo="glare",
        )
    )
    db_session.flush()
    cab1 = login(client, rotuladores["sup1"])
    # sup1 rotula na rodada a todas as fotos da amostra, com cenas distintas da lab02
    cenas_a = {}
    while (
        r := client.post("/rotulagem/proxima", params={"rodada": "a"}, headers=cab1)
    ).status_code == 200:
        foto_id = r.json()["foto_id"]
        cenas_a[foto_id] = "nao_medidor"
        g = client.post(
            f"/rotulagem/{foto_id}", json=rotulo("a", "nao_medidor", False), headers=cab1
        )
        assert g.status_code == 201, g.text
    assert set(cenas_a) == {lab02[i].id for i in (1, 2, 3, 4)}

    # leiturista e maquina; rotulos alheios; nomes de campo que nao deveriam existir
    proibidos = "987654 Z902 0.123456 p_ruim heuristica glare reprov".split()
    proibidos += "ext_x ext_y eletromecanico ocorrencia nao_medidor digital reflexo".split()
    proibidos += "legivel cena rotulador usuario nota posicao medidor nome_arquivo leitura".split()
    cab2 = login(client, rotuladores["sup2"])
    vistas = []
    while (
        r := client.post("/rotulagem/proxima", params={"rodada": "b"}, headers=cab2)
    ).status_code == 200:
        corpo = r.json()
        assert set(corpo) == CAMPOS_PROXIMA
        assert corpo["url"] == f"/api/fotos/{corpo['foto_id']}/arquivo"
        for termo in proibidos:
            assert termo.lower() not in r.text.lower(), termo
        assert corpo["foto_id"] not in vistas, "devolveu foto que o usuario ja rotulou"
        vistas.append(corpo["foto_id"])
        g = client.post(
            f"/rotulagem/{corpo['foto_id']}", json=rotulo("b", "digital", True), headers=cab2
        )
        assert g.status_code == 201
        # o POST devolve o rotulo do proprio usuario, nunca o da rodada a nem do lab02
        assert g.json()["cena"] == "digital" and g.json()["rodada"] == "b"
        assert set(g.json()) == {
            "id",
            "foto_id",
            "rodada",
            "cena",
            "defeitos",
            "legivel",
            "gravado_em",
        }
    assert r.status_code == 404
    assert sorted(vistas) == sorted(cenas_a)


def test_proxima_nunca_devolve_foto_ja_rotulada_pelo_usuario(client, lab02, rotuladores):
    cab = login(client, rotuladores["sup1"])
    primeira = client.post("/rotulagem/proxima", params={"rodada": "a"}, headers=cab).json()
    assert primeira["restantes"] == 4
    client.post(f"/rotulagem/{primeira['foto_id']}", json=rotulo("a"), headers=cab)
    segunda = client.post("/rotulagem/proxima", params={"rodada": "a"}, headers=cab).json()
    assert segunda["foto_id"] != primeira["foto_id"] and segunda["restantes"] == 3
    # rodada b: quem rotulou na a nao recebe a mesma foto
    for _ in range(4):
        r = client.post("/rotulagem/proxima", params={"rodada": "b"}, headers=cab)
        if r.status_code != 200:
            break
        assert r.json()["foto_id"] != primeira["foto_id"]
        client.post(f"/rotulagem/{r.json()['foto_id']}", json=rotulo("b"), headers=cab)


def test_desempate_so_para_terceiro_e_so_fotos_divergentes(client, lab02, rotuladores):
    cab = {k: login(client, rotuladores[k]) for k in ("sup1", "sup2", "sup3")}
    f1, f2 = lab02[1].id, lab02[2].id
    # f1: a e b concordam em tudo; f2: divergem em legivel
    assert client.post(f"/rotulagem/{f1}", json=rotulo("a"), headers=cab["sup1"]).status_code == 201
    assert client.post(f"/rotulagem/{f1}", json=rotulo("b"), headers=cab["sup2"]).status_code == 201
    assert client.post(f"/rotulagem/{f2}", json=rotulo("a"), headers=cab["sup1"]).status_code == 201
    r = client.post(f"/rotulagem/{f2}", json=rotulo("b", "digital", False), headers=cab["sup2"])
    assert r.status_code == 201

    for quem in ("sup1", "sup2"):
        r = client.post("/rotulagem/proxima", params={"rodada": "desempate"}, headers=cab[quem])
        assert r.status_code == 404
    r = client.post("/rotulagem/proxima", params={"rodada": "desempate"}, headers=cab["sup3"])
    assert r.status_code == 200 and r.json()["foto_id"] == f2 and r.json()["restantes"] == 1
    assert set(r.json()) == CAMPOS_PROXIMA

    # gravar desempate de foto que nao diverge, ou sendo rotulador de a/b, e recusado
    assert (
        client.post(f"/rotulagem/{f1}", json=rotulo("desempate"), headers=cab["sup3"]).status_code
        == 409
    )
    assert (
        client.post(f"/rotulagem/{f2}", json=rotulo("desempate"), headers=cab["sup1"]).status_code
        == 409
    )
    assert (
        client.post(f"/rotulagem/{f2}", json=rotulo("desempate"), headers=cab["sup3"]).status_code
        == 201
    )
    r = client.post("/rotulagem/proxima", params={"rodada": "desempate"}, headers=cab["sup3"])
    assert r.status_code == 404


# ------------------------------------------------------------------ gravar


@pytest.mark.parametrize(
    "corpo",
    [
        rotulo("a", "nao_medidor", False, ["reflexo"]),
        rotulo("a", "nao_medidor", True),
        rotulo("a", "ocorrencia", True),
        rotulo("a", "digital", True, ["display_apagado"]),
        rotulo("a", "digital", False, ["reflexo", "reflexo"]),
        rotulo("lab02"),
        rotulo("producao"),
        rotulo("a", "medidor_de_agua"),
        rotulo("a", "digital", False, ["glare"]),
    ],
)
def test_gravar_recusa_rotulo_inconsistente_com_o_esquema_v2(client, lab02, rotuladores, corpo):
    cab = login(client, rotuladores["sup1"])
    r = client.post(f"/rotulagem/{lab02[1].id}", json=corpo, headers=cab)
    assert r.status_code == 422, r.text


def test_gravar_casos_de_erro(client, lab02, rotuladores):
    cab = login(client, rotuladores["sup1"])
    assert client.post("/rotulagem/999999999", json=rotulo("a"), headers=cab).status_code == 404
    # foto 5 nao esta na amostra (sem rotulo lab02)
    assert (
        client.post(f"/rotulagem/{lab02[5].id}", json=rotulo("a"), headers=cab).status_code == 409
    )
    ok = client.post(
        f"/rotulagem/{lab02[1].id}", json=rotulo("a", "digital", False, ["reflexo"]), headers=cab
    )
    assert ok.status_code == 201 and ok.json()["defeitos"] == ["reflexo"]
    # duas vezes na mesma rodada, ou a mesma foto na rodada b
    assert (
        client.post(f"/rotulagem/{lab02[1].id}", json=rotulo("a"), headers=cab).status_code == 409
    )
    assert (
        client.post(f"/rotulagem/{lab02[1].id}", json=rotulo("b"), headers=cab).status_code == 409
    )


# ------------------------------------------------------------------ kappa e export


def test_consolidacao_kappa_por_classe_com_valores_conhecidos(client, lab02, rotuladores):
    """Nas 4 fotos do lab02 da fixture:
    cena    x = [E, D, E, O], y = [E, E, E, O]: po = 3/4,
            pe = (2*3 + 1*0 + 1*1)/16 = 7/16, kappa = (12/16 - 7/16)/(9/16) = 5/9
    legivel x = [0, 1, 0, 0], y = [0, 0, 0, 0]: po = 3/4, pe = 3/4, kappa = 0
    reflexo x = y = [1, 0, 0, 0]: kappa = 1
    demais defeitos: todos 0 nos dois, kappa indefinido
    """
    cab = login(client, rotuladores["sup1"])
    r = client.get("/rotulagem/consolidacao", headers=cab)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["rodadas"] == ["lab02"] and corpo["limiar"] == 0.6
    assert (corpo["rotulos"], corpo["fotos"], corpo["fotos_dupla_rotulagem"]) == (8, 4, 4)
    assert corpo["fotos_com_empate"] == 1  # foto 2: cena e legivel empatam
    classes = {c["classe"]: c for c in corpo["por_classe"]}
    assert classes["cena"]["kappa"] == pytest.approx(5 / 9) and not classes["cena"]["passa"]
    assert classes["legivel"]["kappa"] == pytest.approx(0.0) and not classes["legivel"]["passa"]
    assert classes["reflexo"]["kappa"] == pytest.approx(1.0) and classes["reflexo"]["passa"]
    assert classes["reflexo"]["n"] == 4
    assert classes["tampa_suja"] == {"classe": "tampa_suja", "kappa": None, "n": 0, "passa": False}
    assert [(p["a"], p["b"], p["n"]) for p in corpo["pares"]] == [("ext_x", "ext_y", 4)]


def test_consolidacao_das_rodadas_a_e_b_pareia_os_usuarios(client, lab02, rotuladores):
    cab = login(client, rotuladores["sup1"])
    admin = login(client, rotuladores["admin"])
    client.post(f"/rotulagem/{lab02[1].id}", json=rotulo("a"), headers=cab)
    client.post(f"/rotulagem/{lab02[2].id}", json=rotulo("a"), headers=cab)
    ok = client.get("/rotulagem/consolidacao", params={"rodada": ["a", "b"]}, headers=admin)
    assert ok.status_code == 200 and ok.json()["pares"] == []  # um rotulador so
    cab3 = login(client, rotuladores["sup3"])
    client.post(f"/rotulagem/{lab02[1].id}", json=rotulo("b"), headers=cab3)
    r = client.get("/rotulagem/consolidacao", params={"rodada": ["a", "b"]}, headers=admin)
    assert r.status_code == 200 and r.json()["pares"][0]["n"] == 1


def test_consolidacao_das_rodadas_cegas_nao_vaza_por_diferencial(client, lab02, rotuladores):
    """Com n = 1, kappa n/d quer dizer que concordaram e 0 que divergiram: quem votou
    nao pode consultar o kappa das rodadas cegas, nem antes nem depois do voto."""
    cab = {k: login(client, rotuladores[k]) for k in ("sup1", "sup2", "sup3", "admin")}
    f1 = lab02[1].id

    def consultar(quem, rodadas):
        return client.get("/rotulagem/consolidacao", params={"rodada": rodadas}, headers=cab[quem])

    assert consultar("sup2", ["a", "b"]).status_code == 200  # ainda nao votou: nada a vazar
    assert client.post(f"/rotulagem/{f1}", json=rotulo("a"), headers=cab["sup1"]).status_code == 201
    assert client.post(f"/rotulagem/{f1}", json=rotulo("b"), headers=cab["sup2"]).status_code == 201
    for quem, propria in (("sup1", "a"), ("sup2", "b")):
        for rodadas in (["a", "b"], [propria], ["a", "b", "desempate"], ["lab02", propria]):
            r = consultar(quem, rodadas)
            assert r.status_code == 403, (quem, rodadas, r.text)
            assert "kappa" not in r.json() and "pares" not in r.json()
        assert consultar(quem, ["lab02"]).status_code == 200  # lab02 ja fechou
        assert consultar(quem, ["desempate"]).status_code == 200  # nao votou no desempate
    assert consultar("sup3", ["a", "b"]).status_code == 200
    assert consultar("admin", ["a", "b"]).json()["pares"][0]["n"] == 1


def test_rodadas_cegas_se_excluem_por_rotulador_e_foto(client, db_session, lab02, rotuladores):
    """O terceiro do desempate nao pode votar na mesma foto em a ou b: senao a
    consolidacao a+b+desempate teria o mesmo rotulador duas vezes e daria 422 a todos."""
    cab = {k: login(client, rotuladores[k]) for k in ("sup1", "sup2", "sup3", "admin")}
    f2, f3 = lab02[2].id, lab02[3].id
    client.post(f"/rotulagem/{f2}", json=rotulo("a"), headers=cab["sup1"])
    client.post(f"/rotulagem/{f2}", json=rotulo("b", "digital", False), headers=cab["sup2"])
    assert (
        client.post(f"/rotulagem/{f2}", json=rotulo("desempate"), headers=cab["sup3"]).status_code
        == 201
    )
    for rodada in ("a", "b"):
        r = client.post(f"/rotulagem/{f2}", json=rotulo(rodada), headers=cab["sup3"])
        assert r.status_code == 409, rodada
        # e o /proxima tambem nao a oferece
        for _ in range(4):
            p = client.post("/rotulagem/proxima", params={"rodada": rodada}, headers=cab["sup3"])
            if p.status_code != 200:
                break
            assert p.json()["foto_id"] != f2
            client.post(
                f"/rotulagem/{p.json()['foto_id']}", json=rotulo(rodada), headers=cab["sup3"]
            )
    # quem votou na a nao vota na b da mesma foto (ja coberto) nem no desempate
    client.post(f"/rotulagem/{f3}", json=rotulo("a"), headers=cab["sup1"])
    r = client.get(
        "/rotulagem/consolidacao", params={"rodada": ["a", "b", "desempate"]}, headers=cab["admin"]
    )
    assert r.status_code == 200, r.text
    assert (
        client.get(
            "/rotulagem/export", params={"rodada": ["a", "b", "desempate"]}, headers=cab["admin"]
        ).status_code
        == 200
    )


def test_indice_unico_fecha_a_corrida_entre_rodadas_cegas(db_session, fotos, contas):
    uid, foto = contas["supervisor"].id, fotos[1].id
    _inserir(db_session, foto_id=foto, usuario_id=uid, rodada="a")
    for rodada in ("b", "desempate"):
        with pytest.raises(IntegrityError):
            _inserir(db_session, foto_id=foto, usuario_id=uid, rodada=rodada)
    _inserir(db_session, foto_id=foto, usuario_id=uid, rodada="producao")  # fora da dupla-cega


def test_colisao_em_a_e_b_nao_derruba_o_desempate(client, db_session, lab02, rotuladores):
    """So por escrita fora da API (externo nas duas rodadas): 409, nunca 500."""
    for rodada in ("a", "b"):
        _inserir(db_session, foto_id=lab02[1].id, rotulador_externo="ext_z", rodada=rodada)
    cab = login(client, rotuladores["sup3"])
    r = client.post("/rotulagem/proxima", params={"rodada": "desempate"}, headers=cab)
    assert r.status_code == 409 and "ext_z" in r.text
    r = client.post(f"/rotulagem/{lab02[1].id}", json=rotulo("desempate"), headers=cab)
    assert r.status_code == 409


def test_consolidacao_colisao_de_rotulador_da_422(client, db_session, lab02, rotuladores):
    _inserir(db_session, foto_id=lab02[1].id, rotulador_externo="ext_x", rodada="a")
    cab = login(client, rotuladores["sup1"])
    r = client.get("/rotulagem/consolidacao", params={"rodada": ["lab02", "a"]}, headers=cab)
    assert r.status_code == 422 and "ext_x" in r.text


def test_export_consolidado_por_maioria(client, lab02, rotuladores):
    cab = login(client, rotuladores["admin"])
    r = client.get("/rotulagem/export", headers=cab)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    linhas = {row["nome_arquivo"]: row for row in csv.DictReader(r.text.splitlines())}
    assert list(linhas) == sorted(nome(i) for i in (1, 2, 3, 4))
    f1, f2, f4 = linhas[nome(1)], linhas[nome(2)], linhas[nome(4)]
    assert (f1["cena"], f1["reflexo"], f1["legivel"], f1["empate"]) == (
        "eletromecanico",
        "1",
        "0",
        "0",
    )
    assert (f2["cena"], f2["legivel"], f2["empate"], f2["n_rotuladores"]) == ("", "", "1", "2")
    assert (f4["cena"], f4["legivel"], f4["lote"]) == ("ocorrencia", "0", LOTE)
    assert f1["controle"] == ""  # importado sem controle.dat
    # supervisor rotula: nao pode ver o consolidado por foto
    sup = login(client, rotuladores["sup1"])
    assert client.get("/rotulagem/export", headers=sup).status_code == 403
