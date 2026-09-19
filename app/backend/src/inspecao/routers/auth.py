from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from inspecao.auth import service
from inspecao.auth.deps import UsuarioAtual, origem_da_requisicao
from inspecao.config import get_settings
from inspecao.db import get_db
from inspecao.schemas import LoginIn, TokenOut, TrocarSenhaIn, UsuarioOut

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_REFRESH = "refresh_token"
DB = Annotated[Session, Depends(get_db)]
RefreshCookie = Annotated[str | None, Cookie(alias=COOKIE_REFRESH)]


def _gravar_cookie(resposta: Response, valor: str) -> None:
    s = get_settings()
    resposta.set_cookie(
        COOKIE_REFRESH,
        valor,
        max_age=s.refresh_token_days * 86400,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


def _limpar_cookie(resposta: Response) -> None:
    resposta.delete_cookie(COOKIE_REFRESH, path="/")


def para_saida(usuario) -> UsuarioOut:
    saida = UsuarioOut.model_validate(usuario)
    saida.precisa_trocar_senha = usuario.senha_alterada_em is None
    return saida


@router.post("/login", response_model=TokenOut)
def login(dados: LoginIn, request: Request, resposta: Response, db: DB) -> TokenOut:
    origem = origem_da_requisicao(request)
    try:
        sessao = service.login(db, dados.email, dados.senha, origem)
    except service.LoginBloqueado:
        db.commit()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "muitas tentativas, aguarde alguns minutos"
        ) from None
    except service.ErroAutenticacao:
        db.commit()  # o log da falha precisa persistir para o rate limit
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, service.ErroAutenticacao.mensagem
        ) from None
    db.commit()
    _gravar_cookie(resposta, sessao.refresh_token)
    return TokenOut(access_token=sessao.access_token, expires_in=sessao.expires_in)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, resposta: Response, db: DB, refresh_token: RefreshCookie = None):
    origem = origem_da_requisicao(request)
    try:
        sessao = service.refresh(db, refresh_token, origem)
    except service.RefreshInvalido:
        db.commit()  # revogacoes e log de reuso precisam persistir
        _limpar_cookie(resposta)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh invalido") from None
    db.commit()
    _gravar_cookie(resposta, sessao.refresh_token)
    return TokenOut(access_token=sessao.access_token, expires_in=sessao.expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    resposta: Response,
    db: DB,
    usuario: UsuarioAtual,
    refresh_token: RefreshCookie = None,
) -> None:
    service.logout(db, refresh_token, origem_da_requisicao(request), usuario.id)
    db.commit()
    _limpar_cookie(resposta)


@router.get("/me", response_model=UsuarioOut)
def me(usuario: UsuarioAtual) -> UsuarioOut:
    return para_saida(usuario)


@router.post("/senha", status_code=status.HTTP_204_NO_CONTENT)
def trocar_senha(dados: TrocarSenhaIn, request: Request, db: DB, usuario: UsuarioAtual) -> None:
    try:
        service.trocar_senha(
            db, usuario, dados.senha_atual, dados.senha_nova, origem_da_requisicao(request)
        )
    except service.ErroAutenticacao:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "senha atual incorreta") from None
    except service.SenhaFraca as erro:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(erro)) from None
    db.commit()
