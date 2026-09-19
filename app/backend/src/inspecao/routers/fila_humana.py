"""Fila humana: listagem, reserva de item e decisao do auditor."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from inspecao.auth.deps import Auditor
from inspecao.db import get_db
from inspecao.enums import DecisaoHumana, StatusLeitura
from inspecao.estados import transicionar
from inspecao.models import Leitura, Lote, RevisaoHumana
from inspecao.routers.operacao import carregar_leitura, leitura_completa, leitura_resumo
from inspecao.schemas import DecisaoIn, LeituraOut, LeituraResumoOut, Pagina, RevisaoOut

router = APIRouter(prefix="/fila-humana", tags=["fila-humana"])
DB = Annotated[Session, Depends(get_db)]

RESERVA = timedelta(minutes=10)


def _agora() -> datetime:
    return datetime.now(UTC)


def _livre_para(usuario_id: int):
    """Item sem reserva, com reserva vencida, ou reservado pelo proprio usuario."""
    return or_(
        Leitura.reservado_ate.is_(None),
        Leitura.reservado_ate < func.now(),
        Leitura.reservado_por == usuario_id,
    )


@router.get("", response_model=Pagina[LeituraResumoOut])
def listar(
    db: DB,
    usuario: Auditor,
    lote: str | None = None,
    nota: str | None = None,
    ordenar: Literal["antigas", "recentes"] = "antigas",
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(50, ge=1, le=200),
) -> Pagina[LeituraResumoOut]:
    # Join com Lote sempre: a ordem "antigas" e por data de extracao do lote,
    # nao por id (os lotes nao foram ingeridos em ordem cronologica).
    base = (
        select(Leitura)
        .join(Lote)
        .where(Leitura.status_atual == StatusLeitura.FILA_HUMANA, _livre_para(usuario.id))
    )
    if lote:
        base = base.where(Lote.nome == lote)
    if nota:
        base = base.where(Leitura.nota_codigo == nota)

    total = db.scalar(select(func.count()).select_from(base.subquery()))
    ordem = (
        (Lote.data_extracao.asc(), Leitura.id.asc())
        if ordenar == "antigas"
        else (Lote.data_extracao.desc(), Leitura.id.desc())
    )
    itens = db.scalars(
        base.options(
            selectinload(Leitura.lote), selectinload(Leitura.medidores), selectinload(Leitura.fotos)
        )
        .order_by(*ordem)
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
    ).all()
    return Pagina(
        itens=[leitura_resumo(i) for i in itens], total=total, pagina=pagina, por_pagina=por_pagina
    )


def consulta_proxima(usuario_id: int, lote: str | None = None, excluir: list[int] | None = None):
    """SELECT ... FOR UPDATE OF leituras SKIP LOCKED do proximo item livre.

    `of=Leitura` e essencial: sem ele o lock cai tambem na linha de `lotes`
    compartilhada por todas as leituras do lote, e o segundo auditor a filtrar
    pelo mesmo lote receberia "fila vazia".
    """
    consulta = (
        select(Leitura.id)
        .join(Lote)
        .where(Leitura.status_atual == StatusLeitura.FILA_HUMANA, _livre_para(usuario_id))
        # Reserva propria primeiro. case() em vez de boolean DESC: NULL DESC viria antes.
        .order_by(
            case((Leitura.reservado_por == usuario_id, 0), else_=1),
            Lote.data_extracao,
            Leitura.id,
        )
        .with_for_update(of=Leitura, skip_locked=True)
        .limit(1)
    )
    if lote:
        consulta = consulta.where(Lote.nome == lote)
    if excluir:
        # Itens que o auditor pulou nesta sessao: ficam na fila para os outros.
        consulta = consulta.where(Leitura.id.not_in(excluir))
    return consulta


@router.post("/proxima", response_model=LeituraOut)
def proxima(
    db: DB,
    usuario: Auditor,
    lote: str | None = None,
    excluir: Annotated[list[int] | None, Query(max_length=200)] = None,
) -> LeituraOut:
    """Reserva o proximo item para este usuario por 10 minutos.

    `FOR UPDATE SKIP LOCKED` garante que dois auditores clicando ao mesmo tempo
    recebem itens diferentes. `excluir` recebe os ids pulados na sessao.
    """
    leitura_id = db.scalar(consulta_proxima(usuario.id, lote, excluir))
    if leitura_id is None:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fila vazia")

    leitura = db.get(Leitura, leitura_id)
    leitura.reservado_por = usuario.id
    leitura.reservado_ate = _agora() + RESERVA
    db.commit()
    return leitura_completa(db, carregar_leitura(db, leitura_id))


@router.post("/{leitura_id}/liberar", status_code=status.HTTP_204_NO_CONTENT)
def liberar(leitura_id: int, db: DB, usuario: Auditor) -> None:
    """Devolve a reserva do proprio usuario ("pular"). Reserva alheia nao e tocada.

    Com lock de linha: se a reserva venceu e outro auditor acabou de pegar o
    item, a checagem de dono acontece ja com a linha travada e nada e apagado.
    """
    leitura = db.scalar(select(Leitura).where(Leitura.id == leitura_id).with_for_update())
    if leitura is None:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leitura nao encontrada")
    if leitura.reservado_por == usuario.id:
        leitura.reservado_por = None
        leitura.reservado_ate = None
    db.commit()


@router.post(
    "/{leitura_id}/decisao", response_model=RevisaoOut, status_code=status.HTTP_201_CREATED
)
def decidir(leitura_id: int, dados: DecisaoIn, db: DB, usuario: Auditor) -> RevisaoOut:
    if dados.decisao is DecisaoHumana.REPROVADA and dados.motivo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "reprovacao exige motivo")
    if dados.decisao is DecisaoHumana.APROVADA and dados.motivo is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "aprovacao nao leva motivo")

    leitura = db.scalar(select(Leitura).where(Leitura.id == leitura_id).with_for_update())
    if leitura is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leitura nao encontrada")
    if leitura.status_atual != StatusLeitura.FILA_HUMANA:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"leitura nao esta na fila humana (status {leitura.status_atual})",
        )
    reservada_por_outro = (
        leitura.reservado_por not in (None, usuario.id)
        and leitura.reservado_ate is not None
        and leitura.reservado_ate > _agora()
    )
    if reservada_por_outro:
        raise HTTPException(status.HTTP_409_CONFLICT, "item reservado por outro auditor")

    revisao = RevisaoHumana(
        leitura_id=leitura.id,
        usuario_id=usuario.id,
        decisao=dados.decisao.value,
        motivo=dados.motivo.value if dados.motivo else None,
        observacao=dados.observacao,
    )
    db.add(revisao)

    destino = (
        StatusLeitura.APROVADA_HUMANO
        if dados.decisao is DecisaoHumana.APROVADA
        else StatusLeitura.REPROVADA_HUMANO
    )
    transicionar(
        db,
        leitura,
        destino,
        ator=f"usuario:{usuario.id}",
        detalhe={"motivo": revisao.motivo} if revisao.motivo else None,
    )
    leitura.reservado_por = None
    leitura.reservado_ate = None
    db.commit()
    db.refresh(revisao)
    return RevisaoOut.model_validate(revisao)
