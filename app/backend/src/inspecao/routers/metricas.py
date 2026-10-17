"""Metricas operacionais calculadas sobre o proprio banco. Sem Prometheus.

`/metricas/qualidade` preenche a lacuna de baseline do doc 02: o AS-IS nao
media nada disso. `/metricas/drift` devolve a serie por lote com os 5 sinais do
plano 04, secao 7.1, contra a baseline. `/metricas/auto-aprovacao` explica por que
a auto-aprovacao esta desligada e conta a evidencia acumulada; `/metricas/auditoria`
e a concordancia continua da auditoria amostral. Definicoes em `inspecao.monitoramento`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, extract, func, select
from sqlalchemy.orm import Session, aliased

from inspecao import auditoria
from inspecao.auth.deps import Supervisor
from inspecao.config import get_settings
from inspecao.contas_teste import decidida_por_teste, usuario_real
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
from inspecao.monitoramento import auto_aprovacao, sinais
from inspecao.monitoramento import drift as drift_mod
from inspecao.monitoramento import qualidade as qualidade_mod
from inspecao.rotulagem.servico import eh_admin
from inspecao.schemas import AutoAprovacaoOut, ConcordanciaAuditoriaOut, DriftOut, QualidadeOut

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
    """Segundos entre a entrada na fila humana e a decisao, media sobre as decididas.

    Uma entrada por decisao: a ULTIMA `fila_humana` antes dela. A leitura pode ter entrado
    na fila varias vezes (reaberta pela maquina, regravada pela troca de politica), e o
    self-join com todas as entradas anteriores contaria a decisao uma vez por entrada."""
    entrada = aliased(HistoricoStatus)
    saida = aliased(HistoricoStatus)
    ultima_entrada = (
        select(entrada.criado_em)
        .where(
            entrada.leitura_id == saida.leitura_id,
            entrada.status == StatusLeitura.FILA_HUMANA.value,
            entrada.id < saida.id,
        )
        .order_by(entrada.id.desc())
        .limit(1)
        .correlate(saida)
        .scalar_subquery()
    )
    stmt = select(func.avg(extract("epoch", saida.criado_em - ultima_entrada))).where(
        saida.status.in_(DECIDIDAS_HUMANO), ~decidida_por_teste(saida.leitura_id)
    )
    valor = db.scalar(stmt)
    return float(valor) if valor is not None else None


Politica = Annotated[
    str | None,
    Query(description="versao da politica (registro POLITICAS); padrao: a ativa"),
]


def _politica(versao: str | None):
    try:
        return sinais.obter_politica(versao)
    except LookupError as erro:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(erro)) from erro


@router.get("/qualidade", response_model=QualidadeOut)
def qualidade(db: DB, _: Supervisor, politica: Politica = None) -> QualidadeOut:
    p = _politica(politica)
    total = db.scalar(select(func.count()).select_from(Leitura)) or 0
    com_foto = (
        db.scalar(
            select(func.count(func.distinct(Foto.leitura_id))).where(Foto.principal.is_(True))
        )
        or 0
    )
    por_status = _por_status(db)
    auto = sum(n for s, n in por_status.items() if s in AUTO)
    pendentes = por_status.get(StatusLeitura.FILA_HUMANA.value, 0)
    periodo = db.execute(select(func.min(Lote.data_extracao), func.max(Lote.data_extracao))).one()

    real = usuario_real(RevisaoHumana.usuario_id)
    motivos = dict(
        db.execute(
            select(RevisaoHumana.motivo, func.count())
            .where(RevisaoHumana.decisao == DecisaoHumana.REPROVADA.value, real)
            .group_by(RevisaoHumana.motivo)
        ).all()
    )
    revisoes = dict(
        db.execute(
            select(RevisaoHumana.decisao, func.count()).where(real).group_by(RevisaoHumana.decisao)
        ).all()
    )
    por_auditor = [
        {"usuario_id": uid, "decisoes": n}
        for uid, n in db.execute(
            select(RevisaoHumana.usuario_id, func.count())
            .where(real)
            .group_by(RevisaoHumana.usuario_id)
        ).all()
    ]
    baseline, por_lote = qualidade_mod.por_lote(db)
    # Decisoes de contas de teste (E2E, demo) nao contam como decididas por auditor.
    decididas = baseline["contagens"]["aprovada_humano"] + baseline["contagens"]["reprovada_humano"]

    return QualidadeOut.model_validate(
        {
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
            "decididas_conta_teste": baseline["contagens"]["decidida_conta_teste"],
            "jobs": fila.resumo(db),
            "baseline": baseline,
            "por_lote": por_lote,
            "por_detector": qualidade_mod.por_detector(db),
            "concordancia_maquina_humano": qualidade_mod.concordancia_maquina_humano(db, p),
        }
    )


@router.get("/drift", response_model=DriftOut)
def drift(db: DB, _: Supervisor, politica: Politica = None) -> DriftOut:
    """Serie por lote com os 5 sinais contra a baseline (criterio em `monitoramento.drift`),
    mais as contagens operacionais de cada lote."""
    p = _politica(politica)
    baseline = drift_mod.lotes_baseline(p, get_settings().drift_lotes_baseline)
    calculo = drift_mod.calcular(db, p, baseline)
    lotes = {lote.id: lote for lote in db.scalars(select(Lote)).all()}
    serie = []
    for item in calculo.pop("serie"):
        lote = lotes[item["lote_id"]]
        com_foto = (
            db.scalar(
                select(func.count())
                .select_from(Foto)
                .where(Foto.lote_id == lote.id, Foto.principal.is_(True))
            )
            or 0
        )
        humano = db.execute(
            select(
                func.sum(
                    case((RevisaoHumana.decisao == DecisaoHumana.REPROVADA.value, 1), else_=0)
                ),
                func.count(),
            )
            .join(Leitura, Leitura.id == RevisaoHumana.leitura_id)
            .where(Leitura.lote_id == lote.id, usuario_real(RevisaoHumana.usuario_id))
        ).one()
        modelos = db.scalars(
            select(func.distinct(ResultadoInferencia.modelo))
            .join(Foto, Foto.id == ResultadoInferencia.foto_id)
            .where(Foto.lote_id == lote.id)
        ).all()
        serie.append(
            item
            | {
                "total": lote.total_linhas,
                "com_foto": com_foto,
                "por_status": _por_status(db, lote.id),
                "quarentena_por_tipo": _quarentena_por_tipo(db, lote.id),
                "taxa_reprovacao_humana": (
                    round(int(humano[0] or 0) / humano[1], 4) if humano[1] else None
                ),
                "modelos": sorted(modelos),
            }
        )
    return DriftOut.model_validate(calculo | {"lotes": serie})


@router.get("/auto-aprovacao", response_model=AutoAprovacaoOut)
def estado_auto_aprovacao(db: DB, _: Supervisor, politica: Politica = None) -> AutoAprovacaoOut:
    """Estado dos limiares da politica e o contador de evidencia para ligar a
    auto-aprovacao (600 rotuladas na zona de aprovacao, zero erro)."""
    return AutoAprovacaoOut.model_validate(auto_aprovacao.estado(db, _politica(politica)))


@router.get("/auditoria", response_model=ConcordanciaAuditoriaOut)
def concordancia_auditoria(db: DB, usuario: Supervisor) -> ConcordanciaAuditoriaOut:
    """Concordancia primeiro x segundo revisor da auditoria amostral. Supervisor ve so os
    totais com ao menos `min_pares` pares; contagens, por lote e serie so admin (que por
    isso nao audita)."""
    return ConcordanciaAuditoriaOut.model_validate(
        auditoria.concordancia(db, detalhe=eh_admin(usuario))
    )
