"""Handlers dos tipos de job. Cada um recebe (contexto, payload) e roda dentro
de uma transacao aberta pelo worker: excecao = rollback + backoff."""

import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from inspecao.config import Settings
from inspecao.enums import StatusLeitura, TipoJob
from inspecao.estados import ESTADOS_REDECIDIVEIS, transicionar
from inspecao.inference import politica as politica_mod
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


DESTINO: dict[Decisao, StatusLeitura] = {
    Decisao.REJEITAR: StatusLeitura.REJEITADA_QUALIDADE,
    Decisao.SEGUIR: StatusLeitura.VALIDACAO_LEITURA,
    Decisao.HUMANO: StatusLeitura.FILA_HUMANA,
}


def _foto_principal(sessao: Session, leitura: Leitura) -> Foto:
    foto = sessao.scalar(
        select(Foto).where(Foto.leitura_id == leitura.id, Foto.principal.is_(True))
    )
    if foto is None:
        raise LookupError(f"leitura {leitura.id} em {leitura.status_atual} sem foto principal")
    return foto


def _reservada(leitura: Leitura) -> bool:
    """Reservada por um auditor agora: nao se reabre o que alguem esta olhando."""
    return leitura.reservado_ate is not None and leitura.reservado_ate >= datetime.now(UTC)


def _decidir(
    politica: Politica, modelo: str, saida: dict[str, Any]
) -> tuple[Decisao, float | None, dict]:
    """Aplica a politica e monta o `detalhe` do historico. Politica calibrada para
    outro detector nao decide: tudo vai para humano, com o motivo registrado."""
    detalhe: dict[str, Any] = {"politica": politica.versao}
    if not politica.aceita(modelo):
        detalhe |= {"decisao": Decisao.HUMANO.value, "motivo": "politica_de_outro_detector"}
        return Decisao.HUMANO, None, detalhe
    decisao, p_ruim = politica.decidir(saida)
    detalhe["decisao"] = decisao.value
    return decisao, p_ruim, detalhe


def _sair_da_triagem(
    sessao: Session, leitura: Leitura, decisao: Decisao, ator: str, p_ruim, detalhe: dict
) -> None:
    transicionar(sessao, leitura, DESTINO[decisao], ator, p_ruim, detalhe)
    if decisao is Decisao.SEGUIR:
        fila.enfileirar(sessao, TipoJob.VALIDACAO_LEITURA, {"leitura_id": leitura.id})


def _ja_inferida(sessao: Session, foto_id: int, modelo: str) -> bool:
    return bool(
        sessao.scalar(
            select(
                exists().where(
                    ResultadoInferencia.foto_id == foto_id, ResultadoInferencia.modelo == modelo
                )
            )
        )
    )


def triagem_qualidade(ctx: Contexto, sessao: Session, payload: dict) -> None:
    """Roda o detector na foto principal, grava o resultado e decide pela politica.

    Leitura em `recebida`: triagem normal. Leitura ja decidida pela maquina
    (ESTADOS_REDECIDIVEIS) e sem resultado deste detector: troca de detector,
    reabre a leitura e decide de novo. Qualquer outro caso e job duplicado ou
    decisao humana ja tomada: nada a fazer, nao e erro.
    """
    leitura = _leitura(sessao, int(payload["leitura_id"]))
    status = StatusLeitura(leitura.status_atual)
    if status is not StatusLeitura.RECEBIDA and status not in ESTADOS_REDECIDIVEIS:
        log.info("leitura %s ja esta em %s, triagem ignorada", leitura.id, status)
        return

    foto = _foto_principal(sessao, leitura)
    reaberta = status is not StatusLeitura.RECEBIDA
    if reaberta:
        if _reservada(leitura) or _ja_inferida(sessao, foto.id, ctx.detector.nome):
            log.info("leitura %s em %s: nada a reabrir", leitura.id, status)
            return

    ator = f"modelo:{ctx.detector.nome}"
    transicionar(
        sessao,
        leitura,
        StatusLeitura.TRIAGEM_QUALIDADE,
        ator=ator,
        detalhe={"motivo": "troca_de_detector", "de": status.value} if reaberta else None,
    )

    inicio = time.perf_counter()
    imagem = decodificar(ctx.storage.abrir(foto.caminho_storage))
    saida = ctx.detector.avaliar(imagem)
    latencia_ms = int((time.perf_counter() - inicio) * 1000)

    sessao.add(
        ResultadoInferencia(
            foto_id=foto.id, modelo=ctx.detector.nome, saida=saida, latencia_ms=latencia_ms
        )
    )

    decisao, p_ruim, detalhe = _decidir(ctx.politica, ctx.detector.nome, saida)
    _sair_da_triagem(sessao, leitura, decisao, ator, p_ruim, detalhe)


def recalcular_decisao(ctx: Contexto, sessao: Session, payload: dict) -> None:
    """Reaplica uma politica aos resultados ja gravados do lote, sem abrir imagem.

    Payload: `{lote_id, politica}` (versao, ex.: "politica@v1"; padrao: a do worker).
    So mexe em leituras em ESTADOS_REDECIDIVEIS sem reserva ativa, usando o
    resultado mais recente do detector da politica para a foto principal. Leitura
    cuja decisao nao muda nao ganha linha de historico.
    """
    lote = sessao.get(Lote, int(payload["lote_id"]))
    if lote is None:
        raise LookupError(f"lote {payload['lote_id']} nao existe")
    politica = politica_mod.obter(payload.get("politica") or ctx.politica.versao)

    ultimo = (
        select(ResultadoInferencia.foto_id, ResultadoInferencia.modelo, ResultadoInferencia.saida)
        .distinct(ResultadoInferencia.foto_id)
        .order_by(ResultadoInferencia.foto_id, ResultadoInferencia.id.desc())
    )
    if politica.modelo is not None:
        ultimo = ultimo.where(ResultadoInferencia.modelo == politica.modelo)
    ultimo = ultimo.subquery()

    linhas = sessao.execute(
        select(Leitura, ultimo.c.modelo, ultimo.c.saida)
        .join(Foto, (Foto.leitura_id == Leitura.id) & Foto.principal.is_(True))
        .join(ultimo, ultimo.c.foto_id == Foto.id)
        .where(
            Leitura.lote_id == lote.id,
            Leitura.status_atual.in_([s.value for s in ESTADOS_REDECIDIVEIS]),
            or_(Leitura.reservado_ate.is_(None), Leitura.reservado_ate < func.now()),
        )
        .order_by(Leitura.id)
    ).all()

    contagem: Counter[str] = Counter()
    for leitura, modelo, saida in linhas:
        decisao, p_ruim, detalhe = _decidir(politica, modelo, saida)
        if DESTINO[decisao].value == leitura.status_atual:
            contagem["inalterada"] += 1
            continue
        ator = f"modelo:{modelo}"
        de = leitura.status_atual
        transicionar(
            sessao,
            leitura,
            StatusLeitura.TRIAGEM_QUALIDADE,
            ator=ator,
            detalhe={"motivo": "recalcular_decisao", "de": de, "politica": politica.versao},
        )
        _sair_da_triagem(sessao, leitura, decisao, ator, p_ruim, detalhe | {"recalculo": True})
        contagem[f"{de}->{DESTINO[decisao].value}"] += 1
    log.info(
        "lote %s: recalcular_decisao com %s sobre %d leituras: %s",
        lote.nome,
        politica.versao,
        len(linhas),
        dict(contagem),
    )


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
    """Enfileira triagem para as leituras do lote em `recebida` e para as ja
    decididas pela maquina que ainda nao tem resultado do detector deste worker
    (troca de detector). Os resultados antigos ficam, para comparacao."""
    lote = sessao.get(Lote, int(payload["lote_id"]))
    if lote is None:
        raise LookupError(f"lote {payload['lote_id']} nao existe")
    sem_resultado = (
        Leitura.status_atual.in_([s.value for s in ESTADOS_REDECIDIVEIS])
        & exists().where(Foto.leitura_id == Leitura.id, Foto.principal.is_(True))
        & ~exists().where(
            Foto.leitura_id == Leitura.id,
            Foto.principal.is_(True),
            ResultadoInferencia.foto_id == Foto.id,
            ResultadoInferencia.modelo == ctx.detector.nome,
        )
    )
    ids = sessao.scalars(
        select(Leitura.id)
        .where(
            Leitura.lote_id == lote.id,
            or_(Leitura.status_atual == StatusLeitura.RECEBIDA, sem_resultado),
        )
        .order_by(Leitura.id)
    ).all()
    n = fila.enfileirar_em_lote(sessao, TipoJob.TRIAGEM_QUALIDADE, ({"leitura_id": i} for i in ids))
    log.info("lote %s: %d jobs de triagem enfileirados (%s)", lote.nome, n, ctx.detector.nome)


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
    TipoJob.RECALCULAR_DECISAO.value: recalcular_decisao,
}
