"""Handlers dos tipos de job. Cada um recebe (contexto, payload) e roda dentro
de uma transacao aberta pelo worker: excecao = rollback + backoff."""

import logging
import time
from collections import Counter
from dataclasses import dataclass
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
from inspecao.models import (
    AuditoriaAmostral,
    Foto,
    HistoricoStatus,
    Leitura,
    Lote,
    ResultadoInferencia,
)
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


def _redecidivel():
    """Condicao SQL de leitura que a maquina pode reabrir: decidida pela maquina, sem
    reserva ativa de auditor e sem auditoria amostral pendente reservada por um segundo
    revisor (reabrir invalidaria a auditoria na mao dele, e o 404 que ele veria diria que
    o item era automatico). Relogio do banco (`now()`), o mesmo da fila humana."""
    auditoria_reservada = exists().where(
        AuditoriaAmostral.leitura_id == Leitura.id,
        AuditoriaAmostral.decisao.is_(None),
        AuditoriaAmostral.invalidada_em.is_(None),
        AuditoriaAmostral.reservado_ate > func.now(),
    )
    return (
        Leitura.status_atual.in_([s.value for s in ESTADOS_REDECIDIVEIS])
        & or_(Leitura.reservado_ate.is_(None), Leitura.reservado_ate < func.now())
        & ~auditoria_reservada
    )


def _travar_redecidiveis(sessao: Session, consulta):
    """Executa `consulta` (que seleciona Leitura) com FOR UPDATE OF leituras SKIP LOCKED.

    A condicao de `_redecidivel` vai no WHERE do proprio SELECT que trava: no Postgres
    a linha travada e reavaliada na versao mais recente, entao reserva ou decisao
    humana gravadas antes do lock tiram a leitura do resultado. Linha travada por
    outra transacao (auditor pegando o item agora) e pulada, nao esperada. Depois do
    lock, `/fila-humana/proxima` (tambem SKIP LOCKED) nao entrega esses itens e
    `/decisao` espera o commit, quando ja ve o status novo.
    """
    return sessao.execute(
        consulta.where(_redecidivel())
        .with_for_update(of=Leitura, skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()


def _travar_auditorias_pendentes(sessao: Session, leitura_id: int) -> bool:
    """Trava (FOR UPDATE SKIP LOCKED) as auditorias pendentes da leitura antes de reabri-la.

    O `/auditoria/proxima` disputa o mesmo lock (`reservar_proxima` tambem e SKIP LOCKED):
    - se o revisor esta reservando agora, a linha dele esta travada e a leitura e pulada;
    - se ele ja reservou (commit antes), a reserva ativa aparece aqui e a leitura e pulada;
    - se o job trava primeiro, o `/proxima` pula essa auditoria e nao a entrega, e a
      invalidacao em `transicionar` acontece sem ninguem com o item na mao.
    Assim nenhum revisor ve a auditoria sumir depois de reservada (o 404 diria a origem).
    """
    # Mesmo predicado da invalidacao em `estados.transicionar` (so decisao automatica: a
    # auditoria de fila confere uma decisao humana, que nunca e reaberta).
    pendente = (
        AuditoriaAmostral.leitura_id == leitura_id,
        AuditoriaAmostral.origem != "fila",
        AuditoriaAmostral.decisao.is_(None),
        AuditoriaAmostral.invalidada_em.is_(None),
    )
    total = sessao.scalar(select(func.count()).select_from(AuditoriaAmostral).where(*pendente))
    travadas = sessao.execute(
        select(AuditoriaAmostral.id, AuditoriaAmostral.reservado_ate)
        .where(*pendente)
        .with_for_update(skip_locked=True)
    ).all()
    if len(travadas) < (total or 0):
        return False
    agora = sessao.scalar(select(func.now()))
    return not any(ate is not None and ate > agora for _, ate in travadas)


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
    # A auditoria amostral da decisao automatica (M8) e sorteada dentro de `transicionar`.
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

    reaberta = status is not StatusLeitura.RECEBIDA
    if reaberta:
        # O status lido acima pode estar velho: trava e reconfere antes de reabrir.
        if not _travar_redecidiveis(sessao, select(Leitura).where(Leitura.id == leitura.id)):
            if sessao.scalar(select(Leitura.id).where(Leitura.id == leitura.id, _redecidivel())):
                # Re-decidivel, mas travada por outra transacao (auditor pegando o item).
                log.warning(
                    "leitura %s travada por outra transacao: triagem com %s nao feita; "
                    "reenfileire (reprocessar_lote) para tentar de novo",
                    leitura.id,
                    ctx.detector.nome,
                )
            else:
                log.info("leitura %s reservada ou decidida: nada a reabrir", leitura.id)
            return
        if not _travar_auditorias_pendentes(sessao, leitura.id):
            log.warning(
                "leitura %s com auditoria amostral em uso: troca para %s adiada; reenfileire",
                leitura.id,
                ctx.detector.nome,
            )
            return
        status = StatusLeitura(leitura.status_atual)
    foto = _foto_principal(sessao, leitura)
    if reaberta and _ja_inferida(sessao, foto.id, ctx.detector.nome):
        log.info("leitura %s em %s ja tem resultado de %s", leitura.id, status, ctx.detector.nome)
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


# Saidas da triagem: a linha do historico que diz qual detector e politica decidiram por
# ultimo (a mesma definicao de `monitoramento.qualidade.por_detector`).
SAIDAS_DA_TRIAGEM = (
    StatusLeitura.REJEITADA_QUALIDADE.value,
    StatusLeitura.VALIDACAO_LEITURA.value,
    StatusLeitura.FILA_HUMANA.value,
)


def _ultima_decisao_automatica(
    sessao: Session, leitura_ids: list[int]
) -> dict[int, tuple[str, str | None, str | None]]:
    """leitura_id -> (ator, politica, decisao) da ultima saida da triagem gravada pela
    maquina."""
    if not leitura_ids:
        return {}
    consulta = (
        select(
            HistoricoStatus.leitura_id,
            HistoricoStatus.ator,
            HistoricoStatus.detalhe["politica"].astext,
            HistoricoStatus.detalhe["decisao"].astext,
        )
        .where(
            HistoricoStatus.leitura_id.in_(leitura_ids),
            HistoricoStatus.ator.like("modelo:%"),
            HistoricoStatus.status.in_(SAIDAS_DA_TRIAGEM),
        )
        .distinct(HistoricoStatus.leitura_id)
        .order_by(HistoricoStatus.leitura_id, HistoricoStatus.id.desc())
    )
    return {
        lid: (ator, politica, decisao) for lid, ator, politica, decisao in sessao.execute(consulta)
    }


def recalcular_decisao(ctx: Contexto, sessao: Session, payload: dict) -> None:
    """Reaplica uma politica aos resultados ja gravados do lote, sem abrir imagem.

    Payload: `{lote_id, politica}` (versao, ex.: "politica@v1"; padrao: a do worker).
    So mexe em leituras em ESTADOS_REDECIDIVEIS sem reserva ativa, travadas por
    `_travar_redecidiveis` ate o commit, usando o resultado mais recente do detector
    da politica para a foto principal. Leitura cuja decisao nao muda E cuja ultima
    decisao automatica ja e desta politica (mesmo ator) nao ganha linha de historico.
    Decisao igual vinda de outra politica e regravada (reabre e decide de novo, pelo
    `transicionar`): sem isso, voltar da v2 para a v1 deixaria a ultima linha citando a
    v2, e a atribuicao por politica (`/metricas/qualidade`), o estrato de falso positivo
    e o sorteio da auditoria da v1 ficariam com a v2.
    """
    lote = sessao.get(Lote, int(payload["lote_id"]))
    if lote is None:
        raise LookupError(f"lote {payload['lote_id']} nao existe")
    politica = politica_mod.obter(payload.get("politica") or ctx.politica.versao)

    ultimo = (
        select(ResultadoInferencia.foto_id, ResultadoInferencia.modelo, ResultadoInferencia.saida)
        .join(Foto, Foto.id == ResultadoInferencia.foto_id)
        .where(Foto.lote_id == lote.id, Foto.principal.is_(True))
        .distinct(ResultadoInferencia.foto_id)
        .order_by(ResultadoInferencia.foto_id, ResultadoInferencia.id.desc())
    )
    if politica.modelo is not None:
        ultimo = ultimo.where(ResultadoInferencia.modelo == politica.modelo)
    ultimo = ultimo.subquery()

    consulta = (
        select(Leitura, ultimo.c.modelo, ultimo.c.saida)
        .join(Foto, (Foto.leitura_id == Leitura.id) & Foto.principal.is_(True))
        .join(ultimo, ultimo.c.foto_id == Foto.id)
        .where(Leitura.lote_id == lote.id)
        .order_by(Leitura.id)
    )
    candidatas = sessao.scalar(
        select(func.count()).select_from(consulta.where(_redecidivel()).order_by(None).subquery())
    )
    linhas = _travar_redecidiveis(sessao, consulta)
    ultimas = _ultima_decisao_automatica(sessao, [leitura.id for leitura, _, _ in linhas])

    contagem: Counter[str] = Counter()
    for leitura, modelo, saida in linhas:
        decisao, p_ruim, detalhe = _decidir(politica, modelo, saida)
        # "Mesma" = a ultima decisao gravada pela maquina e esta (ator, politica e decisao),
        # e nao o status: `seguir` sai para validacao_leitura e acaba na fila pelo sistema,
        # e comparar status reabriria essa leitura a cada passada.
        if ultimas.get(leitura.id) == (f"modelo:{modelo}", politica.versao, decisao.value):
            contagem["inalterada"] += 1
            continue
        # Reconfere a linha antes de reabrir (defesa em profundidade: o lock acima ja
        # impede reserva e decisao vindas de outra transacao).
        if not _travar_redecidiveis(sessao, select(Leitura).where(Leitura.id == leitura.id)):
            contagem["reconferencia_falhou"] += 1  # reservada ou decidida depois do SELECT
            continue
        if not _travar_auditorias_pendentes(sessao, leitura.id):
            contagem["auditoria_em_uso"] += 1  # auditoria amostral reservada ou travada (M8)
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
    # `candidatas` e contada sem lock, antes do SELECT que trava: uma reserva commitada
    # entre os dois tira a leitura das candidatas sem ela estar travada por ninguem.
    # Por isso a diferenca e uma estimativa das linhas travadas por outra transacao.
    travadas_por_outra = max(candidatas - len(linhas), 0)
    log.info(
        "lote %s: recalcular_decisao com %s: ~%d candidatas, %d travadas pelo job: %s",
        lote.nome,
        politica.versao,
        candidatas,
        len(linhas),
        dict(contagem),
    )
    if travadas_por_outra:
        # SKIP LOCKED: linhas em uso por outra transacao (auditor pegando o item) ficam de
        # fora desta passada, sem erro. O job e idempotente: rodar de novo as alcanca.
        log.warning(
            "lote %s: ~%d leituras puladas por estarem travadas por outra transacao "
            "(aproximado: contagem feita antes do lock); rode recalcular_decisao de novo "
            "para alcanca-las",
            lote.nome,
            travadas_por_outra,
        )
    if contagem["reconferencia_falhou"]:
        log.warning(
            "lote %s: %d leituras nao reabertas: reservadas ou decididas depois do SELECT",
            lote.nome,
            contagem["reconferencia_falhou"],
        )
    if contagem["auditoria_em_uso"]:
        log.warning(
            "lote %s: %d leituras nao reabertas: auditoria amostral em uso por um revisor; "
            "rode recalcular_decisao de novo depois que ela for decidida ou vencer",
            lote.nome,
            contagem["auditoria_em_uso"],
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
    (troca para um detector novo). Os resultados antigos ficam, para comparacao.

    Voltar para um detector ja usado no lote nao reabre nada: o resultado dele ja
    esta gravado e a imagem nao e reprocessada. Nesse caso o job avisa no log; quem
    re-decide a partir desses resultados e `recalcular_decisao` com a politica
    desse detector.
    """
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

    n_volta = _decididas_por_outro_detector(sessao, lote.id, ctx.detector.nome)
    if n_volta:
        log.warning(
            "lote %s: %d leituras ja tem resultado de %s, mas a ultima decisao veio de outro "
            "detector; nada foi reenfileirado. Use recalcular_decisao com a politica de %s",
            lote.nome,
            n_volta,
            ctx.detector.nome,
            ctx.detector.nome,
        )


def _decididas_por_outro_detector(sessao: Session, lote_id: int, modelo: str) -> int:
    """Leituras re-decidiveis do lote com resultado de `modelo` cujo resultado mais
    recente e de outro detector: o caso "voltar para um detector ja usado"."""
    ultimo = (
        select(ResultadoInferencia.foto_id, ResultadoInferencia.modelo)
        .join(Foto, Foto.id == ResultadoInferencia.foto_id)
        .where(Foto.lote_id == lote_id, Foto.principal.is_(True))
        .distinct(ResultadoInferencia.foto_id)
        .order_by(ResultadoInferencia.foto_id, ResultadoInferencia.id.desc())
        .subquery()
    )
    return sessao.scalar(
        select(func.count())
        .select_from(Leitura)
        .join(Foto, (Foto.leitura_id == Leitura.id) & Foto.principal.is_(True))
        .join(ultimo, ultimo.c.foto_id == Foto.id)
        .where(
            Leitura.lote_id == lote_id,
            Leitura.status_atual.in_([s.value for s in ESTADOS_REDECIDIVEIS]),
            ultimo.c.modelo != modelo,
            exists().where(
                ResultadoInferencia.foto_id == Foto.id, ResultadoInferencia.modelo == modelo
            ),
        )
    )


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
