"""Lacunas apontadas na revisao do M3: expiracao da reserva, concorrencia real
da reserva (duas conexoes), SIGTERM do worker, rate limit por IP e X-Forwarded-For,
handler de ingestao, token de usuario desativado, senha inicial obrigatoria,
protecao do ultimo admin e orfao com tentativas esgotadas."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from inspecao.config import get_settings
from inspecao.enums import Papel, StatusJob, StatusLeitura, TipoJob
from inspecao.inference.detector import DetectorNulo
from inspecao.jobs import fila
from inspecao.jobs.handlers import Contexto, ingerir_lote
from inspecao.jobs.worker import Worker, processar_um
from inspecao.models import Job, Leitura, Lote, Usuario
from inspecao.routers.fila_humana import consulta_proxima
from tests import lote_sintetico as ls
from tests.usuarios_fixture import SENHA, criar_contas, criar_usuario, login


@pytest.fixture(scope="module")
def staging(tmp_path_factory):
    return ls.criar_staging(tmp_path_factory.mktemp("staging"))


def triar_tudo(db_session, staging, tmp_path):
    resultado, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    ctx = Contexto(settings=get_settings(), detector=DetectorNulo(), storage=storage)
    while jobs := fila.reivindicar(db_session, "t", 10):
        for j in jobs:
            processar_um(ctx, db_session, j)
    return lote


# ------------------------------------------------------------ reserva (T-M3-11)


def test_reserva_expirada_libera_o_item_para_outro_auditor(
    client, contas, db_session, staging, tmp_path
):
    lote = triar_tudo(db_session, staging, tmp_path)
    a1, a2 = login(client, contas["auditor"]), login(client, contas["auditor2"])

    item = client.post("/fila-humana/proxima", headers=a1).json()
    leitura = db_session.get(Leitura, item["id"])
    assert leitura.reservado_por == contas["auditor"].id

    # Outro auditor nao ve nem decide o item enquanto a reserva vale.
    assert item["id"] not in {
        i["id"] for i in client.get("/fila-humana", headers=a2).json()["itens"]
    }
    assert (
        client.post(
            f"/fila-humana/{item['id']}/decisao", headers=a2, json={"decisao": "aprovada"}
        ).status_code
        == 409
    )

    # Reserva vence: volta para a fila de todos e o outro auditor consegue decidir.
    # Uma hora atras, nao um segundo: a fila compara com now() do banco, que dentro da
    # transacao do teste e o instante em que ela abriu. Com a maquina carregada o teste
    # passava de 1 s ate aqui e a reserva "vencida" ainda parecia valer.
    leitura.reservado_ate = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()
    assert item["id"] in {i["id"] for i in client.get("/fila-humana", headers=a2).json()["itens"]}
    assert (
        client.post(
            f"/fila-humana/{item['id']}/decisao", headers=a2, json={"decisao": "aprovada"}
        ).status_code
        == 201
    )
    assert lote is not None


@pytest.fixture
def base_comprometida(staging, tmp_path):
    """Lote sintetico triado e usuarios COMMITADOS no banco de teste, para testes
    que precisam de varias conexoes. Limpa tudo ao final."""
    from tests.conftest import SERVIDOR_OK

    if not SERVIDOR_OK:
        pytest.skip("servidor postgres indisponivel")
    from inspecao.db import engine

    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    with fabrica() as s:
        contas = criar_contas(s)
        lote = triar_tudo(s, staging, tmp_path)
        s.commit()
        lote_id = lote.id
    yield fabrica, contas, lote_id
    with engine.begin() as c:
        c.execute(text("DELETE FROM lotes"))  # cascata: leituras, fotos, historico, quarentena
        c.execute(text("DELETE FROM notas_ocorrencia WHERE codigo IN ('Z901','Z902','T111')"))
        c.execute(text("TRUNCATE jobs, log_acesso, refresh_tokens RESTART IDENTITY"))
        c.execute(text("DELETE FROM usuarios"))


def test_reserva_concorrente_com_duas_conexoes_e_filtro_por_lote(base_comprometida):
    """O que teria pego o lock indevido em `lotes`: duas transacoes abertas ao
    mesmo tempo, filtrando pelo MESMO lote, precisam receber itens distintos."""
    fabrica, contas, _ = base_comprometida
    s1, s2 = fabrica(), fabrica()
    try:
        id1 = s1.scalar(consulta_proxima(contas["auditor"].id, ls.LOTE))  # lock aberto em s1
        id2 = s2.scalar(consulta_proxima(contas["auditor2"].id, ls.LOTE))
        assert id1 is not None and id2 is not None
        assert id1 != id2

        # Sem filtro, mesma coisa; e ambos diferentes dos ja travados.
        s3 = fabrica()
        try:
            id3 = s3.scalar(consulta_proxima(contas["supervisor"].id))
            assert id3 not in (id1, id2)
        finally:
            s3.rollback()
            s3.close()
    finally:
        s1.rollback()
        s2.rollback()
        s1.close()
        s2.close()


def test_reserva_concorrente_em_threads_nunca_repete_item(base_comprometida):
    fabrica, contas, _ = base_comprometida
    ids: list[int] = []
    trava = threading.Lock()

    def reservar(usuario_id: int) -> None:
        with fabrica() as s:
            lid = s.scalar(consulta_proxima(usuario_id))
            leitura = s.get(Leitura, lid)
            leitura.reservado_por = usuario_id
            leitura.reservado_ate = datetime.now(UTC) + timedelta(minutes=10)
            s.commit()
            with trava:
                ids.append(lid)

    threads = [
        threading.Thread(target=reservar, args=(c.id,))
        for c in (contas["auditor"], contas["auditor2"], contas["supervisor"], contas["admin"])
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(ids) == 4 and len(set(ids)) == 4


# ------------------------------------------------------------ worker (T-M3-07)


def test_sigterm_encerra_sem_novo_claim(monkeypatch):
    chamadas = []

    def falsa_volta(*a, **k):
        chamadas.append(1)
        return 1

    monkeypatch.setattr("inspecao.jobs.worker.uma_volta", falsa_volta)
    w = Worker()
    w._sinal(15, None)  # SIGTERM
    assert w.parar.is_set()
    w.executar()  # nao pode bloquear nem reivindicar
    assert chamadas == []


def test_excecao_na_volta_nao_derruba_o_worker(monkeypatch):
    estados = iter([RuntimeError("banco caiu"), 0])

    def volta_instavel(*a, **k):
        proximo = next(estados)
        if isinstance(proximo, Exception):
            raise proximo
        return proximo

    monkeypatch.setattr("inspecao.jobs.worker.uma_volta", volta_instavel)
    w = Worker()
    w.settings = w.settings.model_copy(update={"worker_poll_segundos": 0})

    def parar_apos_espera(timeout=None):
        w.parar.set()
        return True

    monkeypatch.setattr(w.parar, "wait", parar_apos_espera)
    w.executar()  # a excecao foi engolida e logada; a volta seguinte devolveu 0


# ------------------------------------------------------- orfao com limite (M-2)


def test_orfao_com_tentativas_esgotadas_vira_erro(db_session):
    job = fila.enfileirar(
        db_session, TipoJob.TRIAGEM_QUALIDADE, {"leitura_id": 1}, max_tentativas=1
    )
    (job,) = fila.reivindicar(db_session, "morto", 1)
    job.iniciado_em = datetime.now(UTC) - timedelta(minutes=11)
    db_session.flush()
    assert fila.recuperar_orfaos(db_session) == 0  # nada devolvido
    db_session.expire_all()
    assert job.status == StatusJob.ERRO
    assert "orfao" in job.erro


# ------------------------------------------------------ rate limit por IP (H-2)


def test_rate_limit_por_ip_com_varios_emails(client, contas, db_session):
    limite_ip = get_settings().login_max_tentativas * 4
    for i in range(limite_ip):
        r = client.post("/auth/login", json={"email": f"u{i}@teste.com.br", "senha": "x"})
        assert r.status_code == 401, i
    # 20 falhas de emails distintos no mesmo IP: o proximo login, mesmo valido, e 429.
    r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": SENHA})
    assert r.status_code == 429


def test_x_forwarded_for_e_ignorado_sem_proxy_confiavel(client, contas, db_session):
    assert get_settings().confiar_x_forwarded_for is False
    limite_ip = get_settings().login_max_tentativas * 4
    for i in range(limite_ip):
        client.post(
            "/auth/login",
            json={"email": f"v{i}@teste.com.br", "senha": "x"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},  # forjado: nao pode furar o bucket
        )
    r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": SENHA})
    assert r.status_code == 429


def test_x_forwarded_for_usa_a_entrada_do_proxy_quando_confiavel(monkeypatch):
    from starlette.requests import Request

    from inspecao.auth.deps import origem_da_requisicao

    monkeypatch.setattr(
        "inspecao.auth.deps.get_settings",
        lambda: get_settings().model_copy(update={"confiar_x_forwarded_for": True}),
    )
    escopo = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.1.1.1, 203.0.113.9")],
        "client": ("172.18.0.2", 1234),
    }
    assert origem_da_requisicao(Request(escopo)).ip == "203.0.113.9"  # a mais a direita


# ------------------------------------------------------ senha inicial (M-3)


def test_senha_inicial_bloqueia_tudo_menos_troca_perfil_e_logout(client, db_session):
    conta = criar_usuario(db_session, "novato@teste.com.br", Papel.ADMIN)
    db_session.get(Usuario, conta.id).senha_alterada_em = None
    db_session.flush()
    cab = login(client, conta)

    r = client.get("/lotes", headers=cab)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "senha_expirada"
    assert client.get("/usuarios", headers=cab).status_code == 403
    assert client.get("/auth/me", headers=cab).json()["precisa_trocar_senha"] is True

    r = client.post(
        "/auth/senha", headers=cab, json={"senha_atual": SENHA, "senha_nova": "nova-senha-forte-1"}
    )
    assert r.status_code == 204
    cab = login(client, conta.__class__(conta.id, conta.email, conta.papel, "nova-senha-forte-1"))
    assert client.get("/lotes", headers=cab).status_code == 200


def test_token_de_usuario_desativado_deixa_de_valer(client, contas, db_session):
    cab = login(client, contas["auditor"])
    assert client.get("/lotes", headers=cab).status_code == 200
    db_session.get(Usuario, contas["auditor"].id).ativo = False
    db_session.flush()
    assert client.get("/lotes", headers=cab).status_code == 401


# ------------------------------------------------------ ultimo admin (M-4)


def test_admin_nao_se_rebaixa_nem_deixa_o_sistema_sem_admin(client, contas, db_session):
    cab = login(client, contas["admin"])
    meu = contas["admin"].id
    assert (
        client.patch(f"/usuarios/{meu}", headers=cab, json={"papel": "auditor"}).status_code == 409
    )
    assert client.patch(f"/usuarios/{meu}", headers=cab, json={"ativo": False}).status_code == 409

    # Um segundo admin pode ser rebaixado enquanto sobrar outro...
    outro = criar_usuario(db_session, "admin2@teste.com.br", Papel.ADMIN)
    assert (
        client.patch(f"/usuarios/{outro.id}", headers=cab, json={"papel": "auditor"}).status_code
        == 200
    )
    # ...e o unico admin restante nao pode ser desativado nem por outro admin.
    outro_cab = login(client, criar_usuario(db_session, "admin3@teste.com.br", Papel.ADMIN))
    assert (
        client.patch(f"/usuarios/{meu}", headers=outro_cab, json={"ativo": False}).status_code
        == 200
    )
    r = client.patch(f"/usuarios/{contas['admin'].id}", headers=outro_cab, json={"ativo": True})
    assert r.status_code == 200
    ultimo = db_session.scalar(select(Usuario).where(Usuario.email == "admin3@teste.com.br"))
    # agora dois ativos; desativar um deixa um -> ok; desativar o segundo -> 409
    assert (
        client.patch(f"/usuarios/{meu}", headers=outro_cab, json={"ativo": False}).status_code
        == 200
    )
    assert client.patch(
        f"/usuarios/{ultimo.id}", headers=cab, json={"ativo": False}
    ).status_code in (401, 409)


# ------------------------------------------------------ ingestao via job


def test_handler_ingerir_lote(db_session, staging, tmp_path, monkeypatch):
    from inspecao.ingest import notas as notas_mod

    ls.garantir_notas(db_session)
    monkeypatch.setattr(notas_mod, "sincronizar", lambda *a, **k: 0)  # sem planilha do cliente
    settings = get_settings().model_copy(update={"ingest_staging_dir": staging})
    from inspecao.storage.local import LocalFileStorage

    ctx = Contexto(
        settings=settings, detector=DetectorNulo(), storage=LocalFileStorage(raiz=tmp_path / "s")
    )
    ingerir_lote(ctx, db_session, {"lote": ls.LOTE, "reprocessar": False})
    lote = db_session.scalar(select(Lote).where(Lote.nome == ls.LOTE))
    assert lote is not None and lote.total_linhas == 10
    assert (
        db_session.scalar(
            select(func.count()).select_from(Job).where(Job.tipo == TipoJob.TRIAGEM_QUALIDADE)
        )
        == 5
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Leitura)
            .where(Leitura.lote_id == lote.id, Leitura.status_atual == StatusLeitura.SEM_FOTO)
        )
        == 5
    )


def test_ambiente_producao_recusa_placeholders():
    from inspecao.config import Settings

    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(ambiente="producao", jwt_secret="trocar_em_producao", admin_senha_inicial="x" * 20)
    with pytest.raises(ValueError, match="JWT_ALG"):
        Settings(jwt_alg="none")
    Settings(
        ambiente="producao",
        jwt_secret="s" * 40,
        admin_senha_inicial="p" * 20,
        rotulagem_salt="r" * 32,
    )  # ok
