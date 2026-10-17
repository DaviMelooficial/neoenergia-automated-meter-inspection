"""Auditoria amostral (marco M8): segundo revisor cego. Regras em `inspecao.auditoria`."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inspecao import auditoria as servico
from inspecao.auth.deps import Auditor
from inspecao.db import get_db
from inspecao.enums import DecisaoHumana, MotivoReprovacao
from inspecao.models import AuditoriaAmostral
from inspecao.rotulagem import producao
from inspecao.rotulagem.servico import eh_admin
from inspecao.routers.operacao import carregar_leitura, leitura_completa
from inspecao.schemas import AuditoriaDecisaoOut, DecisaoIn, ItemAuditoriaOut, QuarentenaCegaOut

router = APIRouter(prefix="/auditoria", tags=["auditoria"])
DB = Annotated[Session, Depends(get_db)]
PROPRIA_DECISAO = "o primeiro revisor nao audita a propria decisao"
ADMIN_NAO_AUDITA = "admin ve a concordancia por par e por lote e por isso nao audita"


def _barrar_admin(usuario) -> None:
    if eh_admin(usuario):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ADMIN_NAO_AUDITA)


def _item(db: Session, auditoria: AuditoriaAmostral, usuario_id: int) -> ItemAuditoriaOut:
    """Monta a lista fechada: sem status, historico, revisoes nem id da leitura."""
    completa = leitura_completa(db, carregar_leitura(db, auditoria.leitura_id))
    return ItemAuditoriaOut(
        auditoria_id=auditoria.id,
        lote=completa.lote,
        linha_csv=completa.linha_csv,
        medidores=completa.medidores,
        posicao_lida=completa.posicao_lida,
        nota_codigo=completa.nota_codigo,
        nota=completa.nota,
        foto_principal=completa.foto_principal,
        fotos=completa.fotos,
        inferencias=completa.inferencias,
        quarentena=[
            QuarentenaCegaOut(tipo=q.tipo, severidade=q.severidade, detalhe=q.detalhe)
            for q in completa.quarentena
        ],
        reservado_ate=auditoria.reservado_ate,
        restantes=servico.pendentes_para(db, usuario_id),
    )


@router.post("/proxima", response_model=ItemAuditoriaOut)
def proxima(db: DB, usuario: Auditor) -> ItemAuditoriaOut:
    """Reserva por 10 min a proxima auditoria que este usuario pode fazer: nunca uma
    decisao dele mesmo, nunca uma reservada por outro revisor. Admin nao audita."""
    _barrar_admin(usuario)
    auditoria = servico.reservar_proxima(db, usuario.id)
    if auditoria is None:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nenhuma auditoria pendente para voce")
    item = _item(db, auditoria, usuario.id)
    db.commit()
    return item


@router.post("/{auditoria_id}/liberar", status_code=status.HTTP_204_NO_CONTENT)
def liberar(auditoria_id: int, db: DB, usuario: Auditor) -> None:
    auditoria = db.scalar(
        select(AuditoriaAmostral).where(AuditoriaAmostral.id == auditoria_id).with_for_update()
    )
    if auditoria is None:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "auditoria nao encontrada")
    if auditoria.reservado_por == usuario.id:
        auditoria.reservado_por = None
        auditoria.reservado_ate = None
    db.commit()


@router.post(
    "/{auditoria_id}/decisao",
    response_model=AuditoriaDecisaoOut,
    status_code=status.HTTP_201_CREATED,
)
def decidir(auditoria_id: int, dados: DecisaoIn, db: DB, usuario: Auditor) -> AuditoriaDecisaoOut:
    """Grava a decisao do segundo revisor e o rotulo `producao` na mesma transacao.

    Vale aqui, e nao so no `/proxima`: o primeiro revisor recebe 403; auditoria ja
    decidida ou reservada por outro revisor, 409. O banco repete a regra
    (`ck_auditorias_segundo_revisor`), e a linha fica travada ate o commit.
    """
    _barrar_admin(usuario)
    if dados.decisao is DecisaoHumana.REPROVADA and dados.motivo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "reprovacao exige motivo")
    if dados.decisao is DecisaoHumana.APROVADA and dados.motivo is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "aprovacao nao leva motivo")

    auditoria = db.scalar(
        select(AuditoriaAmostral).where(AuditoriaAmostral.id == auditoria_id).with_for_update()
    )
    if auditoria is None:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "auditoria nao encontrada")
    if auditoria.revisor_original_id == usuario.id:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, PROPRIA_DECISAO)
    if auditoria.decisao is not None:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "auditoria ja decidida")
    agora = datetime.now(UTC)
    reservada_por_outro = (
        auditoria.reservado_por not in (None, usuario.id)
        and auditoria.reservado_ate is not None
        and auditoria.reservado_ate > agora
    )
    if reservada_por_outro:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "auditoria reservada por outro revisor")

    auditoria.revisor_id = usuario.id
    auditoria.decisao = dados.decisao.value
    auditoria.motivo = dados.motivo.value if dados.motivo else None
    auditoria.observacao = dados.observacao
    auditoria.decidida_em = agora
    auditoria.reservado_por = None
    auditoria.reservado_ate = None
    try:
        db.flush()
        producao.registrar(
            db,
            auditoria.leitura_id,
            usuario.id,
            dados.decisao,
            dados.motivo,
            auditoria_id=auditoria.id,
        )
        db.commit()
    except IntegrityError as erro:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "auditoria nao gravada") from erro
    return AuditoriaDecisaoOut(
        auditoria_id=auditoria.id,
        decisao=DecisaoHumana(auditoria.decisao),
        motivo=MotivoReprovacao(auditoria.motivo) if auditoria.motivo else None,
        observacao=auditoria.observacao,
        decidida_em=auditoria.decidida_em,
    )
