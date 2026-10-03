"""Lotes, leituras, fotos e ingestao."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from inspecao.auth.deps import Admin, Auditor
from inspecao.db import get_db
from inspecao.enums import TipoJob
from inspecao.jobs import fila
from inspecao.models import Foto, Leitura, Lote, NotaOcorrencia, Quarentena
from inspecao.schemas import (
    FotoOut,
    IngestBatchIn,
    JobOut,
    LeituraOut,
    LeituraResumoOut,
    LoteOut,
    NotaOut,
)
from inspecao.storage.local import LocalFileStorage

router = APIRouter(tags=["operacao"])
DB = Annotated[Session, Depends(get_db)]


def foto_para_saida(foto: Foto) -> FotoOut:
    return FotoOut(
        id=foto.id,
        nome_arquivo=foto.nome_arquivo,
        sequencia=foto.sequencia,
        principal=foto.principal,
        largura=foto.largura,
        altura=foto.altura,
        bytes=foto.bytes,
        url=f"/api/fotos/{foto.id}/arquivo",
    )


def leitura_resumo(leitura: Leitura) -> LeituraResumoOut:
    principal = next((f for f in leitura.fotos if f.principal), None)
    return LeituraResumoOut(
        id=leitura.id,
        lote=leitura.lote.nome,
        linha_csv=leitura.linha_csv,
        medidores=[m.numero for m in sorted(leitura.medidores, key=lambda m: m.ordem)],
        posicao_lida=leitura.posicao_lida,
        nota_codigo=leitura.nota_codigo,
        status_atual=leitura.status_atual,
        foto_principal=foto_para_saida(principal) if principal else None,
        reservado_por=leitura.reservado_por,
        reservado_ate=leitura.reservado_ate,
        criado_em=leitura.criado_em,
    )


def carregar_leitura(db: Session, leitura_id: int) -> Leitura:
    leitura = db.scalar(
        select(Leitura)
        .where(Leitura.id == leitura_id)
        .options(
            selectinload(Leitura.lote),
            selectinload(Leitura.medidores),
            selectinload(Leitura.fotos).selectinload(Foto.inferencias),
            selectinload(Leitura.historico),
            selectinload(Leitura.revisoes),
        )
    )
    if leitura is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leitura nao encontrada")
    return leitura


def leitura_completa(db: Session, leitura: Leitura) -> LeituraOut:
    resumo = leitura_resumo(leitura)
    nota = db.get(NotaOcorrencia, leitura.nota_codigo) if leitura.nota_codigo else None
    quarentena = db.scalars(
        select(Quarentena).where(Quarentena.leitura_id == leitura.id).order_by(Quarentena.id)
    ).all()
    return LeituraOut(
        **resumo.model_dump(),
        nota=NotaOut.model_validate(nota) if nota else None,
        fotos=[foto_para_saida(f) for f in sorted(leitura.fotos, key=lambda f: f.sequencia)],
        historico=leitura.historico,
        inferencias=[i for f in leitura.fotos for i in f.inferencias],
        quarentena=quarentena,
        revisoes=leitura.revisoes,
    )


@router.get("/lotes", response_model=list[LoteOut])
def listar_lotes(db: DB, _: Auditor) -> list[LoteOut]:
    lotes = db.scalars(select(Lote).order_by(Lote.data_extracao, Lote.nome)).all()
    contagens = db.execute(
        select(Leitura.lote_id, Leitura.status_atual, func.count()).group_by(
            Leitura.lote_id, Leitura.status_atual
        )
    ).all()
    por_lote: dict[int, dict[str, int]] = {}
    for lote_id, st, n in contagens:
        por_lote.setdefault(lote_id, {})[st] = n
    saida = []
    for lote in lotes:
        item = LoteOut.model_validate(lote)
        item.por_status = por_lote.get(lote.id, {})
        saida.append(item)
    return saida


@router.get("/leituras/{leitura_id}", response_model=LeituraOut)
def obter_leitura(leitura_id: int, db: DB, _: Auditor) -> LeituraOut:
    return leitura_completa(db, carregar_leitura(db, leitura_id))


@router.get("/fotos/{foto_id}/arquivo")
def arquivo_da_foto(foto_id: int, db: DB, _: Auditor) -> Response:
    foto = db.get(Foto, foto_id)
    if foto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "foto nao encontrada")
    try:
        dados = LocalFileStorage().abrir(foto.caminho_storage)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "arquivo ausente do storage") from None
    return Response(
        content=dados,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600", "ETag": f'"{foto.sha256[:16]}"'},
    )


@router.post("/ingest/batch", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def disparar_ingestao(dados: IngestBatchIn, db: DB, _: Admin) -> JobOut:
    """Enfileira a ingestao; quem executa e o worker."""
    job = fila.enfileirar(
        db,
        TipoJob.INGERIR_LOTE,
        {"lote": dados.lote, "reprocessar": dados.reprocessar},
        max_tentativas=1,  # ingestao nao e idempotente no meio: nao repetir as cegas
    )
    db.commit()
    return JobOut.model_validate(job)


@router.post("/ingest/webhook", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def webhook_desativado(_: Admin) -> dict:
    """Endpoint reservado. Fica desativado ate a integracao com o sistema de campo."""
    return {"detail": "webhook desativado: integracao com o sistema de campo ainda nao negociada"}
