from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inspecao.auth.deps import Supervisor
from inspecao.db import get_db
from inspecao.enums import Severidade, TipoInconsistencia
from inspecao.models import Lote, Quarentena
from inspecao.schemas import Pagina, QuarentenaOut, ResolverQuarentenaIn

router = APIRouter(prefix="/quarentena", tags=["quarentena"])
DB = Annotated[Session, Depends(get_db)]


@router.get("", response_model=Pagina[QuarentenaOut])
def listar(
    db: DB,
    _: Supervisor,
    tipo: TipoInconsistencia | None = None,
    severidade: Severidade | None = None,
    resolvido: bool | None = False,
    lote: str | None = None,
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(50, ge=1, le=200),
) -> Pagina[QuarentenaOut]:
    base = select(Quarentena)
    if tipo:
        base = base.where(Quarentena.tipo == tipo.value)
    if severidade:
        base = base.where(Quarentena.severidade == severidade.value)
    if resolvido is not None:
        base = base.where(Quarentena.resolvido.is_(resolvido))
    if lote:
        base = base.join(Lote).where(Lote.nome == lote)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    itens = db.scalars(
        base.order_by(Quarentena.id).offset((pagina - 1) * por_pagina).limit(por_pagina)
    ).all()
    return Pagina(itens=itens, total=total, pagina=pagina, por_pagina=por_pagina)


@router.get("/resumo")
def resumo(db: DB, _: Supervisor) -> dict[str, dict[str, int]]:
    linhas = db.execute(
        select(Quarentena.tipo, Quarentena.resolvido, func.count()).group_by(
            Quarentena.tipo, Quarentena.resolvido
        )
    ).all()
    saida: dict[str, dict[str, int]] = {}
    for tipo, res, n in linhas:
        saida.setdefault(tipo, {"pendentes": 0, "resolvidos": 0})
        saida[tipo]["resolvidos" if res else "pendentes"] = n
    return saida


@router.post("/{quarentena_id}/resolver", response_model=QuarentenaOut)
def resolver(
    quarentena_id: int, dados: ResolverQuarentenaIn, db: DB, usuario: Supervisor
) -> QuarentenaOut:
    item = db.get(Quarentena, quarentena_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "registro nao encontrado")
    if item.resolvido:
        raise HTTPException(status.HTTP_409_CONFLICT, "ja resolvido")
    item.resolvido = True
    item.resolvido_por = f"usuario:{usuario.id}"
    item.resolvido_em = datetime.now(UTC)
    if dados.observacao:
        item.detalhe = {**(item.detalhe or {}), "resolucao": dados.observacao}
    db.commit()
    db.refresh(item)
    return QuarentenaOut.model_validate(item)
