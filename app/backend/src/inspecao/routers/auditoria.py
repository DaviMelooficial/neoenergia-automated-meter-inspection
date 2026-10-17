"""Auditoria amostral (marco M8): segundo revisor cego. Regras em `inspecao.auditoria`.

So o papel `auditor` audita (403 constante para supervisor e admin, que leem as
metricas). Toda outra recusa em `/auditoria/{id}/*` e a mesma resposta, 404
`AUDITORIA_INDISPONIVEL`: "nao existe", "a decisao e sua", "ja decidida", "reservada por
outro" e falha de gravacao nao se distinguem, para o primeiro revisor nao descobrir que
foi sorteado. A validacao do corpo (422) vem antes e nao depende da auditoria.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inspecao import auditoria as servico
from inspecao.auth.deps import Auditor
from inspecao.db import get_db
from inspecao.enums import DecisaoHumana, MotivoReprovacao, Papel
from inspecao.models import AuditoriaAmostral, Usuario
from inspecao.rotulagem import producao
from inspecao.routers.operacao import carregar_leitura, leitura_completa
from inspecao.schemas import AuditoriaDecisaoOut, DecisaoIn, ItemAuditoriaOut, QuarentenaCegaOut

router = APIRouter(prefix="/auditoria", tags=["auditoria"])
DB = Annotated[Session, Depends(get_db)]
SO_AUDITOR = "auditoria amostral e feita so pelo papel auditor"
AUDITORIA_INDISPONIVEL = "auditoria indisponivel"


def _so_auditor(usuario: Usuario) -> None:
    """Depende so do papel de quem chama, nunca da auditoria: nao e oraculo."""
    if Papel(usuario.papel) is not Papel.AUDITOR:
        raise HTTPException(status.HTTP_403_FORBIDDEN, SO_AUDITOR)


def _indisponivel(db: Session) -> HTTPException:
    db.rollback()
    return HTTPException(status.HTTP_404_NOT_FOUND, AUDITORIA_INDISPONIVEL)


def _item(db: Session, auditoria: AuditoriaAmostral, usuario_id: int) -> ItemAuditoriaOut:
    """Lista fechada, igual para as tres origens: sem status, historico, revisoes, scores
    da maquina nem id da leitura."""
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
        quarentena=[
            QuarentenaCegaOut(tipo=q.tipo, severidade=q.severidade, detalhe=q.detalhe)
            for q in completa.quarentena
        ],
        reservado_ate=auditoria.reservado_ate,
        # o item ja reservado conta em pendentes_para; ha_mais = existe outro alem dele
        ha_mais=servico.pendentes_para(db, usuario_id) > 1,
    )


def _travar_disponivel(db: Session, auditoria_id: int, usuario_id: int) -> AuditoriaAmostral:
    """A auditoria travada se este usuario pode decidi-la ou libera-la agora; senao a
    resposta unica. Mesma condicao do `/proxima` (`servico.livre_para`)."""
    auditoria = db.scalar(
        select(AuditoriaAmostral)
        .where(AuditoriaAmostral.id == auditoria_id, servico.livre_para(usuario_id))
        .with_for_update()
    )
    if auditoria is None:
        raise _indisponivel(db)
    return auditoria


@router.post("/proxima", response_model=ItemAuditoriaOut)
def proxima(db: DB, usuario: Auditor) -> ItemAuditoriaOut:
    """Reserva por 10 min a proxima auditoria que este usuario pode fazer: nunca uma
    decisao dele mesmo, nunca uma reservada por outro revisor."""
    _so_auditor(usuario)
    auditoria = servico.reservar_proxima(db, usuario.id)
    if auditoria is None:
        raise _indisponivel(db)
    item = _item(db, auditoria, usuario.id)
    db.commit()
    return item


@router.post("/{auditoria_id}/liberar", status_code=status.HTTP_204_NO_CONTENT)
def liberar(auditoria_id: int, db: DB, usuario: Auditor) -> None:
    """Devolve a reserva propria. Qualquer outro caso: a resposta unica."""
    _so_auditor(usuario)
    auditoria = _travar_disponivel(db, auditoria_id, usuario.id)
    if auditoria.reservado_por != usuario.id:
        raise _indisponivel(db)
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

    A regra vale aqui, e nao so no `/proxima`: a auditoria tem de estar pendente, nao ser
    decisao do proprio usuario e nao estar reservada por outro (lock da linha ate o
    commit). O banco repete a regra (`ck_auditorias_segundo_revisor`, gatilho de
    imutabilidade). Toda recusa e a resposta unica.
    """
    _so_auditor(usuario)
    if dados.decisao is DecisaoHumana.REPROVADA and dados.motivo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "reprovacao exige motivo")
    if dados.decisao is DecisaoHumana.APROVADA and dados.motivo is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "aprovacao nao leva motivo")

    auditoria = _travar_disponivel(db, auditoria_id, usuario.id)
    auditoria.revisor_id = usuario.id
    auditoria.decisao = dados.decisao.value
    auditoria.motivo = dados.motivo.value if dados.motivo else None
    auditoria.observacao = dados.observacao
    auditoria.decidida_em = datetime.now(UTC)
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
        raise _indisponivel(db) from erro
    return AuditoriaDecisaoOut(
        auditoria_id=auditoria.id,
        decisao=DecisaoHumana(auditoria.decisao),
        motivo=MotivoReprovacao(auditoria.motivo) if auditoria.motivo else None,
        observacao=auditoria.observacao,
        decidida_em=auditoria.decidida_em,
    )
