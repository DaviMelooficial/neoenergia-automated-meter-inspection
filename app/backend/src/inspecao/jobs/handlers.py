"""Handlers dos tipos de job. Cada um recebe (contexto, payload) e roda dentro
de uma transacao aberta pelo worker: excecao = rollback + backoff."""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from inspecao.config import Settings
from inspecao.enums import StatusLeitura, TipoJob
from inspecao.estados import transicionar
from inspecao.inference.detector import Detector, decodificar
from inspecao.inference.politica import POLITICA_ATUAL, Decisao, Politica
from inspecao.jobs import fila
from inspecao.models import Foto, Leitura, Lote, ResultadoInferencia
from inspecao.storage.local import LocalFileStorage

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Contexto:
    settings: Settings
    detector: Detector
    storage: LocalFileStorage
    politica: Politica = POLITICA_ATUAL


def _leitura(sessao: Session, leitura_id: int) -> Leitura:
    leitura = sessao.get(Leitura, leitura_id)
    if leitura is None:
        raise LookupError(f"leitura {leitura_id} nao existe")
    return leitura


def triagem_qualidade(ctx: Contexto, sessao: Session, payload: dict) -> None:
    """Roda o detector na foto principal, grava o resultado e decide pela politica."""
    leitura = _leitura(sessao, int(payload["leitura_id"]))
    if leitura.status_atual != StatusLeitura.RECEBIDA:
        # Job duplicado ou lote reprocessado: nada a fazer, nao e erro.
        log.info("leitura %s ja esta em %s, triagem ignorada", leitura.id, leitura.status_atual)
        return

    foto = sessao.scalar(
        select(Foto).where(Foto.leitura_id == leitura.id, Foto.principal.is_(True))
    )
    if foto is None:
        raise LookupError(f"leitura {leitura.id} em recebida sem foto principal")

    ator = f"modelo:{ctx.detector.nome}"
    transicionar(sessao, leitura, StatusLeitura.TRIAGEM_QUALIDADE, ator=ator)

    inicio = time.perf_counter()
    imagem = decodificar(ctx.storage.abrir(foto.caminho_storage))
    scores = ctx.detector.avaliar(imagem)
    latencia_ms = int((time.perf_counter() - inicio) * 1000)

    sessao.add(
        ResultadoInferencia(
            foto_id=foto.id, modelo=ctx.detector.nome, saida=scores, latencia_ms=latencia_ms
        )
    )

    decisao, p_ruim = ctx.politica.decidir(scores)
    detalhe = {"politica": ctx.politica.versao, "decisao": decisao.value}
    if decisao is Decisao.REJEITAR:
        transicionar(sessao, leitura, StatusLeitura.REJEITADA_QUALIDADE, ator, p_ruim, detalhe)
    elif decisao is Decisao.SEGUIR:
        transicionar(sessao, leitura, StatusLeitura.VALIDACAO_LEITURA, ator, p_ruim, detalhe)
        fila.enfileirar(sessao, TipoJob.VALIDACAO_LEITURA, {"leitura_id": leitura.id})
    else:
        transicionar(sessao, leitura, StatusLeitura.FILA_HUMANA, ator, p_ruim, detalhe)


def validacao_leitura(ctx: Contexto, sessao: Session, payload: dict) -> None:
    """Ponto B. Sem classificador de cena nem OCR (bloqueados por dados e pelo
    gate de resolucao), toda leitura que chega aqui vai para a fila humana."""
    leitura = _leitura(sessao, int(payload["leitura_id"]))
    if leitura.status_atual != StatusLeitura.VALIDACAO_LEITURA:
        log.info("leitura %s ja esta em %s, validacao ignorada", leitura.id, leitura.status_atual)
        return
    transicionar(
        sessao,
        leitura,
        StatusLeitura.FILA_HUMANA,
        ator="sistema",
        detalhe={"motivo": "ponto_b_indisponivel"},
    )


def reprocessar_lote(ctx: Contexto, sessao: Session, payload: dict) -> None:
    """Enfileira triagem para todas as leituras do lote ainda em `recebida`."""
    lote = sessao.get(Lote, int(payload["lote_id"]))
    if lote is None:
        raise LookupError(f"lote {payload['lote_id']} nao existe")
    ids = sessao.scalars(
        select(Leitura.id).where(
            Leitura.lote_id == lote.id, Leitura.status_atual == StatusLeitura.RECEBIDA
        )
    ).all()
    n = fila.enfileirar_em_lote(sessao, TipoJob.TRIAGEM_QUALIDADE, ({"leitura_id": i} for i in ids))
    log.info("lote %s: %d jobs de triagem enfileirados", lote.nome, n)


def ingerir_lote(ctx: Contexto, sessao: Session, payload: dict) -> None:
    """Ingestao assincrona disparada por POST /ingest/batch."""
    from inspecao.ingest import notas as notas_mod
    from inspecao.ingest.batch import BatchFolderSource, ingerir

    s = ctx.settings
    origem = BatchFolderSource(s.caminho_absoluto(s.ingest_staging_dir))
    notas_mod.sincronizar(sessao, s.caminho_absoluto(s.ingest_notas_xlsx))
    r = ingerir(
        sessao, origem, payload["lote"], ctx.storage, reprocessar=bool(payload.get("reprocessar"))
    )
    log.info(
        "ingestao de %s via job: %d leituras, %d fotos, pulado=%s",
        r.lote,
        r.leituras,
        r.fotos,
        r.pulado,
    )


REGISTRY = {
    TipoJob.TRIAGEM_QUALIDADE.value: triagem_qualidade,
    TipoJob.VALIDACAO_LEITURA.value: validacao_leitura,
    TipoJob.REPROCESSAR_LOTE.value: reprocessar_lote,
    TipoJob.INGERIR_LOTE.value: ingerir_lote,
}
