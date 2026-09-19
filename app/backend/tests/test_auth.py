"""Login, rate limiting, rotacao de refresh, senha e seed."""

from sqlalchemy import func, select

from inspecao.auth import security, service
from inspecao.config import get_settings
from inspecao.enums import EventoAcesso, Papel
from inspecao.models import LogAcesso, RefreshToken, Usuario
from tests.usuarios_fixture import SENHA, login

COOKIE = "refresh_token"


def com_cookie(client, valor: str | None):
    """httpx descontinuou cookies por requisicao: controla o jar do cliente."""
    client.cookies.clear()
    if valor is not None:
        client.cookies.set(COOKIE, valor)
    return client


def test_login_devolve_access_token_e_cookie_de_refresh(client, contas):
    r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": SENHA})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["token_type"] == "bearer"
    assert corpo["expires_in"] == get_settings().access_token_minutes * 60
    claims = security.decodificar_access_token(corpo["access_token"])
    assert claims["sub"] == str(contas["auditor"].id)
    assert claims["papel"] == Papel.AUDITOR.value
    assert COOKIE in r.cookies
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_email_e_normalizado(client, contas):
    r = client.post("/auth/login", json={"email": "  AUDITOR@Teste.Com.Br ", "senha": SENHA})
    assert r.status_code == 200


def test_senha_errada_e_email_inexistente_dao_a_mesma_resposta(client, contas, db_session):
    r1 = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": "errada!!!"})
    r2 = client.post("/auth/login", json={"email": "ninguem@teste.com.br", "senha": "errada!!!"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()
    assert COOKIE not in r1.cookies and COOKIE not in r2.cookies

    eventos = db_session.scalars(select(LogAcesso.evento)).all()
    assert eventos.count(EventoAcesso.LOGIN_FALHO.value) == 2


def test_usuario_inativo_nao_entra(client, contas, db_session):
    db_session.get(Usuario, contas["auditor"].id).ativo = False
    db_session.flush()
    r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": SENHA})
    assert r.status_code == 401


def test_rate_limit_por_email(client, contas, db_session):
    limite = get_settings().login_max_tentativas
    for _ in range(limite):
        r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": "x"})
        assert r.status_code == 401
    # A partir daqui, ate a senha certa e recusada com 429.
    r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": SENHA})
    assert r.status_code == 429
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(LogAcesso)
            .where(LogAcesso.evento == EventoAcesso.LOGIN_BLOQUEADO.value)
        )
        == 1
    )
    # Outro usuario no mesmo IP continua entrando: o limite por IP e mais folgado.
    assert (
        client.post(
            "/auth/login", json={"email": contas["supervisor"].email, "senha": SENHA}
        ).status_code
        == 200
    )


def test_me_e_troca_de_senha(client, contas, db_session):
    cab = login(client, contas["auditor"])
    me = client.get("/auth/me", headers=cab)
    assert me.status_code == 200
    assert me.json()["papel"] == "auditor"
    assert me.json()["precisa_trocar_senha"] is False

    r = client.post(
        "/auth/senha",
        headers=cab,
        json={"senha_atual": "errada!!!!", "senha_nova": "nova-senha-forte-1"},
    )
    assert r.status_code == 401
    r = client.post("/auth/senha", headers=cab, json={"senha_atual": SENHA, "senha_nova": "curta"})
    assert r.status_code == 422
    r = client.post(
        "/auth/senha", headers=cab, json={"senha_atual": SENHA, "senha_nova": "nova-senha-forte-1"}
    )
    assert r.status_code == 204
    # senha nova vale, antiga nao
    assert (
        client.post(
            "/auth/login", json={"email": contas["auditor"].email, "senha": SENHA}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login", json={"email": contas["auditor"].email, "senha": "nova-senha-forte-1"}
        ).status_code
        == 200
    )


def test_sem_token_ou_token_invalido(client, contas):
    assert client.get("/auth/me").status_code == 401
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401
    )


def test_refresh_rotaciona_e_detecta_reuso(client, contas, db_session):
    r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": SENHA})
    primeiro = r.cookies[COOKIE]

    r2 = com_cookie(client, primeiro).post("/auth/refresh")
    assert r2.status_code == 200
    segundo = r2.cookies[COOKIE]
    assert segundo != primeiro
    assert r2.json()["access_token"]

    registros = db_session.scalars(
        select(RefreshToken).where(RefreshToken.usuario_id == contas["auditor"].id)
    ).all()
    assert len(registros) == 2
    antigo = next(t for t in registros if t.token_sha256 == security.hash_refresh(primeiro))
    novo = next(t for t in registros if t.token_sha256 == security.hash_refresh(segundo))
    assert antigo.revogado and antigo.substituido_por == novo.id
    assert not novo.revogado

    # Reapresentar o PRIMEIRO (ja rotacionado) derruba a cadeia inteira.
    r3 = com_cookie(client, primeiro).post("/auth/refresh")
    assert r3.status_code == 401
    db_session.expire_all()
    assert all(t.revogado for t in registros)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(LogAcesso)
            .where(LogAcesso.evento == EventoAcesso.REFRESH_REUSO.value)
        )
        == 1
    )
    # E o segundo, que era valido, tambem morreu.
    assert com_cookie(client, segundo).post("/auth/refresh").status_code == 401


def test_refresh_sem_cookie_ou_desconhecido(client, contas):
    assert com_cookie(client, None).post("/auth/refresh").status_code == 401
    assert com_cookie(client, "inventado").post("/auth/refresh").status_code == 401


def test_logout_revoga_o_refresh(client, contas, db_session):
    r = client.post("/auth/login", json={"email": contas["auditor"].email, "senha": SENHA})
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    cookie = r.cookies[COOKIE]
    assert com_cookie(client, cookie).post("/auth/logout", headers=cab).status_code == 204
    assert com_cookie(client, cookie).post("/auth/refresh").status_code == 401


def test_seed_do_admin_e_idempotente(db_session):
    assert db_session.scalar(select(func.count()).select_from(Usuario)) == 0
    admin = service.garantir_admin(db_session)
    assert admin is not None and admin.papel == "admin" and admin.senha_alterada_em is None
    assert service.garantir_admin(db_session) is None
    assert db_session.scalar(select(func.count()).select_from(Usuario)) == 1


def test_seed_recria_admin_quando_so_ha_auditores(db_session):
    """Condicao e 'nenhum admin ativo', nao 'tabela vazia': um banco so com
    auditores (ou com o admin desativado) nao pode ficar sem porta de entrada."""
    from tests.usuarios_fixture import criar_usuario

    criar_usuario(db_session, "so.auditor@teste.com.br", Papel.AUDITOR)
    admin = service.garantir_admin(db_session)
    assert admin is not None and admin.papel == "admin"
    admin.ativo = False
    db_session.flush()
    assert service.garantir_admin(db_session) is None  # mesmo e-mail existe: nao duplica
