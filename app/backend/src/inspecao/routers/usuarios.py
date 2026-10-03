from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inspecao.auth import security, service
from inspecao.auth.deps import Admin
from inspecao.db import get_db
from inspecao.enums import Papel
from inspecao.models import Usuario
from inspecao.routers.auth import para_saida
from inspecao.schemas import UsuarioIn, UsuarioOut, UsuarioPatch

router = APIRouter(prefix="/usuarios", tags=["usuarios"])
DB = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[UsuarioOut])
def listar(db: DB, _: Admin) -> list[UsuarioOut]:
    return [para_saida(u) for u in db.scalars(select(Usuario).order_by(Usuario.id)).all()]


@router.post("", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
def criar(dados: UsuarioIn, db: DB, _: Admin) -> UsuarioOut:
    email = service.normalizar_email(dados.email)
    if db.scalar(select(Usuario).where(Usuario.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email ja cadastrado")
    usuario = Usuario(
        nome=dados.nome,
        email=email,
        senha_hash=security.hash_senha(dados.senha_inicial),
        papel=dados.papel.value,
        ativo=True,
        senha_alterada_em=None,  # forca troca no primeiro login
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return para_saida(usuario)


@router.patch("/{usuario_id}", response_model=UsuarioOut)
def atualizar(usuario_id: int, dados: UsuarioPatch, db: DB, admin: Admin) -> UsuarioOut:
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "usuario nao encontrado")
    if usuario.id == admin.id and (
        dados.ativo is False or (dados.papel and dados.papel != Papel.ADMIN)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "nao e possivel desativar ou rebaixar a si mesmo"
        )

    if dados.nome is not None:
        usuario.nome = dados.nome
    if dados.papel is not None:
        usuario.papel = dados.papel.value
    if dados.ativo is not None:
        usuario.ativo = dados.ativo
        if not dados.ativo:
            service.revogar_tokens_do_usuario(db, usuario.id)
    if dados.resetar_senha is not None:
        usuario.senha_hash = security.hash_senha(dados.resetar_senha)
        usuario.senha_alterada_em = None
        service.revogar_tokens_do_usuario(db, usuario.id)
    db.flush()
    admins_ativos = db.scalar(
        select(func.count())
        .select_from(Usuario)
        .where(Usuario.papel == Papel.ADMIN.value, Usuario.ativo.is_(True))
    )
    if not admins_ativos:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a operacao deixaria o sistema sem admin ativo"
        )
    db.commit()
    db.refresh(usuario)
    return para_saida(usuario)
