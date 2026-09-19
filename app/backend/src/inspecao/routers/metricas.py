"""Metricas operacionais calculadas sobre o proprio banco. Sem Prometheus.

`/metricas/qualidade` preenche a lacuna de baseline do doc 02: o AS-IS nao
media nada disso. `/metricas/drift` devolve a serie por lote; os sinais que
dependem de modelo ficam nulos ate o M5.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import case, extract, func, select
from sqlalchemy.orm import Session, aliased

from inspecao.auth.deps import Supervisor
from inspecao.db import get_db
from inspecao.enums import DecisaoHumana, StatusLeitura
from inspecao.jobs import fila
from inspecao.models import (
    Foto,
    HistoricoStatus,
    Leitura,
    Lote,
    Quarentena,
    ResultadoInferencia,
    RevisaoHumana,
)

router = APIRouter(prefix="/metricas", tags=["metricas"])
DB = Annotated[Session, Depends(get_db)]

AUTO = {
    StatusLeitura.AUTO_APROVADA.value,
    StatusLeitura.AUTO_REJEITADA.value,
    StatusLeitura.REJEITADA_QUALIDADE.value,
}
DECIDIDAS_HUMANO = {StatusLeitura.APROVADA_HUMANO.value, StatusLeitura.REPROVADA_HUMANO.value}


def _por_status(db: Session, lote_id: int | None = None) -> dict[str, int]:
    stmt = select(Leitura.status_atual, func.count()).group_by(Leitura.status_atual)
    if lote_id is not None:
        stmt = stmt.where(Leitura.lote_id == lote_id)
    return dict(db.execute(stmt).all())


def _quarentena_por_tipo(db: Session, lote_id: int | None = None) -> dict[str, int]:
    stmt = select(Quarentena.tipo, func.count()).group_by(Quarentena.tipo)
    if lote_id is not None:
        stmt = stmt.where(Quarentena.lote_id == lote_id)
    return dict(db.execute(stmt).all())


def _tempo_medio_decisao(db: Session) -> float | None:
    """Segundos entre a entrada na fila humana e a decisao, media sobre as decididas."""
    entrada = aliased(HistoricoStatus)
    saida = aliased(HistoricoStatus)
    stmt = (
        select(func.avg(extract("epoch", saida.criado_em - entrada.criado_em)))
        .select_from(saida)
        .join(
            entrada,
            (entrada.leitura_id == saida.leitura_id)
            & (entrada.status == StatusLeitura.FILA_HUMANA.value)
            & (entrada.id < saida.id),
        )
        .where(saida.status.in_(DECIDIDAS_HUMANO))
    )
    valor = db.scalar(stmt)
    return float(valor) if valor is not None else None


@router.get("/qualidade")
def qualidade(db: DB, _: Supervisor) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(Leitura)) or 0
    com_foto = (
        db.scalar(
            select(func.count(func.distinct(Foto.leitura_id))).where(Foto.principal.is_(True))
        )
        or 0
    )
    por_status = _por_status(db)
    auto = sum(n for s, n in por_status.items() if s in AUTO)
    decididas = sum(n for s, n in por_status.items() if s in DECIDIDAS_HUMANO)
    pendentes = por_status.get(StatusLeitura.FILA_HUMANA.value, 0)
    periodo = db.execute(select(func.min(Lote.data_extracao), func.max(Lote.data_extracao))).one()

    motivos = dict(
        db.execute(
            select(RevisaoHumana.motivo, func.count())
            .where(RevisaoHumana.decisao == DecisaoHumana.REPROVADA.value)
            .group_by(RevisaoHumana.motivo)
        ).all()
    )
    revisoes = dict(
        db.execute(
            select(RevisaoHumana.decisao, func.count()).group_by(RevisaoHumana.decisao)
        ).all()
    )
    por_auditor = [
        {"usuario_id": uid, "decisoes": n}
        for uid, n in db.execute(
            select(RevisaoHumana.usuario_id, func.count()).group_by(RevisaoHumana.usuario_id)
        ).all()
    ]

    return {
        "periodo": {"de": periodo[0], "ate": periodo[1]},
        "total_leituras": total,
        "com_foto": com_foto,
        "sem_foto": total - com_foto,
        "auto_decididas": {
            "quantidade": auto,
            "percentual": round(100 * auto / com_foto, 2) if com_foto else 0.0,
        },
        "fila_humana": {"pendentes": pendentes, "decididas": decididas},
        "por_status": por_status,
        "quarentena_por_tipo": _quarentena_por_tipo(db),
        "defeitos_qualidade": motivos,
        "revisoes_por_decisao": revisoes,
        "produtividade_por_auditor": por_auditor,
        "tempo_medio_decisao_segundos": _tempo_medio_decisao(db),
        "jobs": fila.resumo(db),
    }


@router.get("/drift")
def drift(db: DB, _: Supervisor) -> dict[str, Any]:
    """Serie por lote. Sinais de modelo (confianca media, discordancia) so a partir do M5."""
    lotes = db.scalars(select(Lote).order_by(Lote.data_extracao, Lote.nome)).all()
    serie = []
    for lote in lotes:
        por_status = _por_status(db, lote.id)
        com_foto = (
            db.scalar(
                select(func.count())
                .select_from(Foto)
                .where(Foto.lote_id == lote.id, Foto.principal.is_(True))
            )
            or 0
        )
        rejeitadas = por_status.get(StatusLeitura.REJEITADA_QUALIDADE.value, 0)
        confianca = db.scalar(
            select(func.avg(HistoricoStatus.confianca))
            .join(Leitura, Leitura.id == HistoricoStatus.leitura_id)
            .where(Leitura.lote_id == lote.id, HistoricoStatus.confianca.is_not(None))
        )
        # discordancia: modelo mandou para humano e humano reprovou / aprovou
        humano = db.execute(
            select(
                func.sum(
                    case((RevisaoHumana.decisao == DecisaoHumana.REPROVADA.value, 1), else_=0)
                ),
                func.count(),
            )
            .join(Leitura, Leitura.id == RevisaoHumana.leitura_id)
            .where(Leitura.lote_id == lote.id)
        ).one()
        modelos = db.scalars(
            select(func.distinct(ResultadoInferencia.modelo))
            .join(Foto, Foto.id == ResultadoInferencia.foto_id)
            .where(Foto.lote_id == lote.id)
        ).all()
        serie.append(
            {
                "lote": lote.nome,
                "data_extracao": lote.data_extracao,
                "total": lote.total_linhas,
                "com_foto": com_foto,
                "por_status": por_status,
                "quarentena_por_tipo": _quarentena_por_tipo(db, lote.id),
                "taxa_auto_rejeicao": round(rejeitadas / com_foto, 4) if com_foto else None,
                "confianca_media": float(confianca) if confianca is not None else None,
                "taxa_reprovacao_humana": (
                    round(int(humano[0] or 0) / humano[1], 4) if humano[1] else None
                ),
                "modelos": modelos,
            }
        )
    return {"lotes": serie}
