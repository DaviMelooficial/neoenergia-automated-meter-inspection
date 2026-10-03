"""Usuarios de teste e helper de login. Importado pelos testes de API."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from inspecao.auth import security
from inspecao.enums import Papel
from inspecao.models import Usuario

SENHA = "senha-de-teste-123"


@dataclass(frozen=True, slots=True)
class Conta:
    id: int
    email: str
    papel: Papel
    senha: str = SENHA


def criar_usuario(sessao: Session, email: str, papel: Papel, senha: str = SENHA) -> Conta:
    from datetime import UTC, datetime

    u = Usuario(
        nome=email.split("@")[0],
        email=email,
        senha_hash=security.hash_senha(senha),
        papel=papel.value,
        ativo=True,
        senha_alterada_em=datetime.now(UTC),
    )
    sessao.add(u)
    sessao.flush()
    return Conta(id=u.id, email=email, papel=papel, senha=senha)


def criar_contas(sessao: Session) -> dict[str, Conta]:
    return {
        "auditor": criar_usuario(sessao, "auditor@teste.com.br", Papel.AUDITOR),
        "auditor2": criar_usuario(sessao, "auditor2@teste.com.br", Papel.AUDITOR),
        "supervisor": criar_usuario(sessao, "supervisor@teste.com.br", Papel.SUPERVISOR),
        "admin": criar_usuario(sessao, "admin@teste.com.br", Papel.ADMIN),
    }


def login(client, conta: Conta) -> dict[str, str]:
    r = client.post("/auth/login", json={"email": conta.email, "senha": conta.senha})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
