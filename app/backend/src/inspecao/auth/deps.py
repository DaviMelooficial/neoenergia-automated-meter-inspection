"""Dependencies do FastAPI para autenticacao e autorizacao."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from inspecao.auth.security import TokenInvalido, decodificar_access_token
from inspecao.auth.service import Origem
from inspecao.config import get_settings
from inspecao.db import get_db
from inspecao.enums import ORDEM_PAPEL, Papel
from inspecao.models import Usuario

_bearer = HTTPBearer(auto_error=False)


ROTAS_COM_SENHA_EXPIRADA = {"/auth/senha", "/auth/me", "/auth/logout"}


def origem_da_requisicao(request: Request) -> Origem:
    """IP de origem. X-Forwarded-For so conta atras de proxy confiavel, e ai vale a
    entrada mais a direita: e a que o proxy acrescentou, as demais sao do cliente."""
    ip = request.client.host if request.client else None
    if get_settings().confiar_x_forwarded_for:
        xff = [
            p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
        ]
        if xff:
            ip = xff[-1]
    return Origem(ip=ip, user_agent=request.headers.get("user-agent"))


def get_current_user(
    request: Request,
    credenciais: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> Usuario:
    if credenciais is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "nao autenticado")
    try:
        claims = decodificar_access_token(credenciais.credentials)
    except TokenInvalido as erro:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token invalido") from erro

    usuario = db.get(Usuario, int(claims["sub"]))
    if usuario is None or not usuario.ativo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuario inativo")
    request.state.usuario_id = usuario.id

    # Senha inicial ainda em uso: so pode trocar a senha, ver o proprio perfil ou sair.
    # Aplicado no servidor; o frontend esconder telas nao e controle de acesso.
    if usuario.senha_alterada_em is None and request.url.path not in ROTAS_COM_SENHA_EXPIRADA:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"codigo": "senha_expirada", "mensagem": "troque a senha inicial antes de continuar"},
        )
    return usuario


UsuarioAtual = Annotated[Usuario, Depends(get_current_user)]


def require_role(minimo: Papel) -> Callable[..., Usuario]:
    """Hierarquico: compara por ordem, nao por igualdade."""

    def _dep(usuario: UsuarioAtual) -> Usuario:
        if ORDEM_PAPEL[Papel(usuario.papel)] < ORDEM_PAPEL[minimo]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "permissao insuficiente")
        return usuario

    return _dep


Auditor = Annotated[Usuario, Depends(require_role(Papel.AUDITOR))]
Supervisor = Annotated[Usuario, Depends(require_role(Papel.SUPERVISOR))]
Admin = Annotated[Usuario, Depends(require_role(Papel.ADMIN))]
