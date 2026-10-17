"""Auditoria amostral (T-M8-07): segundo revisor cego para as decisoes de auditor e da
maquina.

A unidade e a DECISAO, nao a leitura:
- `fila`: decisao do auditor (`revisao_id`), sorteada na transacao da decisao
  (`sortear`) com `AUDITORIA_TAXA` (0,05 a 0,10). Decisao de conta de teste
  (`inspecao.contas_teste`) nao e sorteada;
- `auto_rejeitada` / `auto_aprovada`: decisao da maquina, identificada pela linha do
  historico em que ela foi aplicada (`historico_id`), sorteada no fim da triagem e do
  `recalcular_decisao` (`sortear_decisao_automatica`, na mesma transacao). Auto-rejeicao
  tem taxa propria, `AUDITORIA_TAXA_AUTO_REJEITADA` (padrao 1,0: decisao do Davi, para
  medir o falso positivo da unica decisao automatica ligada); auto-aprovacao usa a da
  fila (hoje sao 0). `make auditoria-sortear` aplica o mesmo sorteio ao que ja estava
  decidido antes do M8.
O sorteio e deterministico por decisao: sha256 de "salt:auditoria:<origem>:<id da
decisao>" com o salt secreto do servidor (`ROTULAGEM_SALT`). Leitura reaberta pela
maquina (`rejeitada_qualidade` e re-decidivel) volta a triagem e ganha decisao nova: a
auditoria PENDENTE da decisao antiga e marcada `invalidada_em` (em `estados.transicionar`)
e sai do `/proxima` e das medidas; a ja DECIDIDA continua valendo como medida daquela
decisao. A decisao nova, inclusive a de um auditor na fila, sorteia por conta propria.

Regras da escrita, valendo no POST e no banco (nao so no `/proxima`):
- o segundo revisor nunca e o primeiro (`ck_auditorias_segundo_revisor`; o
  primeiro tambem nao reserva, `ck_auditorias_reserva_de_outro`);
- cada decisao tem no maximo uma auditoria, decidida uma vez (`uq_auditorias_revisao`,
  `uq_auditorias_historico`, o lock da linha no POST e o gatilho de imutabilidade);
- so o papel `auditor` audita: supervisor e admin leem metricas, e o diferencial dos
  agregados (por lote, por status) diria a eles a decisao anterior;
- toda recusa de `/auditoria/{id}/*` e a mesma resposta (404 "auditoria indisponivel").
Cegueira: o item entregue (`ItemAuditoriaOut`) e igual para as tres origens e nao traz
status, historico, revisao anterior, scores da maquina, id de leitura nem contagem exata
de restantes; a ordem de entrega e um hash com salt do id da auditoria, e nao a ordem do
sorteio (que agruparia as automaticas). Fora dele, a decisao final de qualquer leitura so
aparece ao proprio autor e a admin (`routers.operacao`), `/lotes` junta os status finais
numa chave so para quem nao e admin, e a recusa da fila e de texto constante.
"""

import hashlib
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inspecao.config import get_settings
from inspecao.contas_teste import email_de_teste, usuario_real
from inspecao.enums import MotivoReprovacao, RodadaRotulagem, StatusLeitura
from inspecao.models import (
    AtivacaoPolitica,
    AuditoriaAmostral,
    Foto,
    HistoricoStatus,
    Leitura,
    Lote,
    RevisaoHumana,
    Rotulo,
    Usuario,
)
from inspecao.monitoramento.auto_aprovacao import amostras_necessarias, limite_superior
from inspecao.rotulagem.consolidacao import kappa_cohen

log = logging.getLogger(__name__)
RESERVA = timedelta(minutes=10)
# Recife nao tem horario de verao desde 2000: UTC-3 fixo, sem depender de tzdata na imagem.
RECIFE = timezone(timedelta(hours=-3), "America/Recife")
# Abaixo disto o supervisor nao ve a medida: com poucos pares, a concordancia diria quem
# discordou de quem.
MIN_PARES = 10

# Decisoes da maquina auditadas: status -> origem da auditoria.
ORIGEM_AUTOMATICA = {
    StatusLeitura.AUTO_APROVADA.value: "auto_aprovada",
    StatusLeitura.AUTO_REJEITADA.value: "auto_rejeitada",
    StatusLeitura.REJEITADA_QUALIDADE.value: "auto_rejeitada",
}
# Erro tolerado por origem automatica (plano 04, secao 5): precisao da auto-rejeicao >=
# 0,99; erro de auto-aprovacao < 0,5%.
ALVO_ERRO = {"auto_rejeitada": 0.01, "auto_aprovada": 0.005}


def sorteada(chave: object, taxa: float, salt: str) -> bool:
    if taxa >= 1.0:
        return True
    h = hashlib.sha256(f"{salt}:auditoria:{chave}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64 < taxa


def taxa_da_origem(origem: str) -> float:
    s = get_settings()
    return s.auditoria_taxa_auto_rejeitada if origem == "auto_rejeitada" else s.auditoria_taxa


def _sorteia(origem: str, decisao_id: int) -> bool:
    salt = get_settings().salt_da_rotulagem()
    return sorteada(f"{origem}:{decisao_id}", taxa_da_origem(origem), salt)


def sortear(db: Session, revisao: RevisaoHumana) -> AuditoriaAmostral | None:
    """Cria a auditoria da decisao de fila se ela for sorteada. Sem commit: roda na
    transacao da decisao, que ja travou a leitura. Conta de teste nao e sorteada."""
    if not _sorteia("fila", revisao.id):
        return None
    email = db.scalar(select(Usuario.email).where(Usuario.id == revisao.usuario_id))
    if email is None or email_de_teste(email):
        return None
    auditoria = AuditoriaAmostral(
        leitura_id=revisao.leitura_id,
        origem="fila",
        revisao_id=revisao.id,
        revisor_original_id=revisao.usuario_id,
    )
    revisao.redistribuida = True
    db.add(auditoria)
    db.flush()
    return auditoria


def sortear_decisao_automatica(
    db: Session, leitura_id: int, decisao: HistoricoStatus
) -> AuditoriaAmostral | None:
    """Auditoria da decisao da maquina registrada em `decisao` (a linha do historico), se
    for de uma origem auditada e sorteada. Num savepoint: decisao ja auditada (corrida com
    o sorteio retroativo) nao derruba a transacao de quem chama. Sem commit."""
    origem = ORIGEM_AUTOMATICA.get(decisao.status)
    if origem is None or not _sorteia(origem, decisao.id):
        return None
    try:
        with db.begin_nested():
            auditoria = AuditoriaAmostral(
                leitura_id=leitura_id, origem=origem, historico_id=decisao.id
            )
            db.add(auditoria)
            db.flush()
    except IntegrityError as erro:
        # So a colisao esperada (a mesma decisao auditada por outra transacao) e tolerada;
        # qualquer outra violacao e defeito e propaga.
        restricao = getattr(getattr(erro.orig, "diag", None), "constraint_name", None)
        if restricao != "uq_auditorias_historico":
            raise
        log.warning("decisao %s (historico) ja tem auditoria", decisao.id)
        return None
    return auditoria


def registrar_ativacao(db: Session, decisao: HistoricoStatus) -> None:
    """Grava a ativacao da politica de `decisao` (decisao automatica), se for a primeira.

    `lotes_anteriores` = os lotes que existem agora, inclusive o da propria decisao: a
    medida de falso positivo da politica so conta lotes ingeridos depois dela
    (`_estratos`). O lote em que ela decide pela primeira vez fica de fora, o lado
    conservador: nao da para separar um lote novo de um antigo reprocessado. Concorrente
    com outro worker: `ON CONFLICT DO NOTHING`, vale a primeira. Sem commit."""
    politica = (decisao.detalhe or {}).get("politica")
    if not politica:
        return
    if db.get(AtivacaoPolitica, politica) is not None:
        return
    lotes = select(
        func.coalesce(func.array_agg(aggregate_order_by(Lote.nome, Lote.nome)), text("'{}'"))
    ).scalar_subquery()
    db.execute(
        insert(AtivacaoPolitica)
        .values(politica=politica, historico_id=decisao.id, lotes_anteriores=lotes)
        .on_conflict_do_nothing(index_elements=[AtivacaoPolitica.politica])
    )
    log.info("politica %s ativada na decisao %s (historico)", politica, decisao.id)


def _travar(db: Session, leitura_id: int) -> bool:
    """FOR UPDATE SKIP LOCKED na leitura: em uso por outra transacao, fica para a proxima."""
    consulta = select(Leitura.id).where(Leitura.id == leitura_id).with_for_update(skip_locked=True)
    return db.scalar(consulta) is not None


def _talvez_commit(db: Session, commit_por_item: bool) -> None:
    if commit_por_item:
        db.commit()


def sortear_decididas(db: Session, commit_por_item: bool = False) -> int:
    """Aplica o sorteio as decisoes de fila gravadas antes do M8. Idempotente (sorteio
    deterministico, decisao com auditoria nao e sorteada de novo).

    Cada leitura e travada com SKIP LOCKED (em uso por outra transacao, fica para a proxima
    passada) e vai num savepoint: uma colisao perde so aquele item. O lock so cai no
    commit; com `commit_por_item` (a CLI) cada item e commitado e solto na hora.
    """
    sem_auditoria = ~(
        select(AuditoriaAmostral.id)
        .where(AuditoriaAmostral.revisao_id == RevisaoHumana.id)
        .exists()
    )
    revisoes = db.scalars(
        select(RevisaoHumana)
        .where(sem_auditoria, usuario_real(RevisaoHumana.usuario_id))
        .order_by(RevisaoHumana.id)
    ).all()
    n = 0
    for r in revisoes:
        if not _sorteia("fila", r.id):
            continue
        try:
            with db.begin_nested():
                if _travar(db, r.leitura_id) and sortear(db, r) is not None:
                    n += 1
        except IntegrityError:
            log.warning("revisao %s ja ganhou auditoria em outra transacao", r.id)
        _talvez_commit(db, commit_por_item)
    return n


def sortear_automaticas(db: Session, commit_por_item: bool = False) -> dict[str, int]:
    """Sorteio retroativo das decisoes da maquina ainda vigentes (a linha de historico mais
    recente com o status atual da leitura), com as mesmas garantias de
    `sortear_decididas`. O caminho normal e o sorteio no fim da triagem e do recalculo."""
    vigente = (
        select(HistoricoStatus.leitura_id, HistoricoStatus.id.label("historico_id"))
        .join(Leitura, Leitura.id == HistoricoStatus.leitura_id)
        .where(
            Leitura.status_atual.in_(list(ORIGEM_AUTOMATICA)),
            HistoricoStatus.status == Leitura.status_atual,
        )
        .distinct(HistoricoStatus.leitura_id)
        .order_by(HistoricoStatus.leitura_id, HistoricoStatus.id.desc())
        .subquery()
    )
    sem_auditoria = ~(
        select(AuditoriaAmostral.id)
        .where(AuditoriaAmostral.historico_id == vigente.c.historico_id)
        .exists()
    )
    linhas = db.execute(
        select(vigente.c.leitura_id, vigente.c.historico_id)
        .where(sem_auditoria)
        .order_by(vigente.c.leitura_id)
    ).all()
    contagem = dict.fromkeys(set(ORIGEM_AUTOMATICA.values()), 0)
    for leitura_id, historico_id in linhas:
        decisao = db.get(HistoricoStatus, historico_id)
        origem = ORIGEM_AUTOMATICA[decisao.status]
        if not _sorteia(origem, decisao.id):
            continue
        with db.begin_nested():
            if _travar(db, leitura_id) and sortear_decisao_automatica(db, leitura_id, decisao):
                contagem[origem] += 1
        _talvez_commit(db, commit_por_item)
    return contagem


def por_codigo(codigo):
    return AuditoriaAmostral.codigo == codigo


def livre_para(usuario_id: int):
    """Pendente, valida, nao e decisao do usuario, sem reserva ativa de outro."""
    return (
        AuditoriaAmostral.decisao.is_(None)
        & AuditoriaAmostral.invalidada_em.is_(None)
        # IS DISTINCT FROM: auditoria de decisao da maquina nao tem revisor original (NULL).
        & AuditoriaAmostral.revisor_original_id.is_distinct_from(usuario_id)
        & or_(
            AuditoriaAmostral.reservado_ate.is_(None),
            AuditoriaAmostral.reservado_ate < func.now(),
            AuditoriaAmostral.reservado_por == usuario_id,
        )
    )


def pendentes_para(db: Session, usuario_id: int) -> int:
    return (
        db.scalar(select(func.count()).select_from(AuditoriaAmostral).where(livre_para(usuario_id)))
        or 0
    )


def ordem_de_entrega():
    """md5 de salt secreto e id da auditoria: estavel, sem relacao com a origem nem com a
    ordem do sorteio (as automaticas sao sorteadas em bloco)."""
    salt = get_settings().salt_da_rotulagem()
    return func.md5(func.concat(salt, ":ordem:", AuditoriaAmostral.id))


def reservar_proxima(db: Session, usuario_id: int) -> AuditoriaAmostral | None:
    """Reserva por 10 min a proxima auditoria que este usuario pode fazer. Sem commit.

    Reserva propria primeiro; depois `ordem_de_entrega`. `FOR UPDATE SKIP LOCKED`: dois
    revisores ao mesmo tempo recebem itens diferentes.
    """
    auditoria = db.scalar(
        select(AuditoriaAmostral)
        .where(livre_para(usuario_id))
        .order_by(
            case((AuditoriaAmostral.reservado_por == usuario_id, 0), else_=1),
            ordem_de_entrega(),
            AuditoriaAmostral.id,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if auditoria is not None:
        auditoria.reservado_por = usuario_id
        auditoria.reservado_ate = datetime.now(UTC) + RESERVA
        db.flush()
    return auditoria


def _categoria(decisao: str, motivo: str | None) -> str:
    """Decisao com o motivo: `aprovada` ou o motivo da reprovacao."""
    return decisao if decisao == "aprovada" else (motivo or "reprovada")


def _medidas(pares: list[tuple[str, str | None, str, str | None]]) -> dict[str, Any]:
    n = len(pares)
    d1 = [p[0] for p in pares]
    d2 = [p[2] for p in pares]
    m1 = [_categoria(p[0], p[1]) for p in pares]
    m2 = [_categoria(p[2], p[3]) for p in pares]
    return {
        "n": n,
        "concordancia_decisao": (sum(a == b for a, b in zip(d1, d2, strict=True)) / n)
        if n
        else None,
        "kappa_decisao": kappa_cohen(d1, d2),
        "concordancia_motivo": (sum(a == b for a, b in zip(m1, m2, strict=True)) / n)
        if n
        else None,
        "kappa_motivo": kappa_cohen(m1, m2),
    }


def _suprimido(medidas: dict[str, Any]) -> dict[str, Any]:
    return dict.fromkeys(medidas) | {"suprimido": True}


MOTIVOS_DE_QUALIDADE = {m.value for m in MotivoReprovacao} - {MotivoReprovacao.OUTRO.value}
# Etapa da decisao automatica, que entra no estrato: rejeicao de qualidade (triagem) e
# rejeicao por divergencia (validacao_leitura, ponto B; hoje inalcancavel) medem coisas
# diferentes e nao se somam.
ETAPA = {
    StatusLeitura.REJEITADA_QUALIDADE.value: "qualidade",
    StatusLeitura.AUTO_REJEITADA.value: "validacao",
    StatusLeitura.AUTO_APROVADA.value: "validacao",
}
# Rodadas de rotulo que calibraram (Lab 2, inspecao do M5) ou treinaram (rodadas cegas, M7):
# foto com rotulo nelas nao e evidencia fora da amostra. Ela e sorteada normalmente (excluir
# do sorteio diria "esta na fila de auditoria, entao veio da fila") e so fica fora da
# medida, em `_estratos`.
RODADAS_DE_CALIBRACAO = (
    RodadaRotulagem.LAB02.value,
    RodadaRotulagem.A.value,
    RodadaRotulagem.B.value,
    RodadaRotulagem.DESEMPATE.value,
    RodadaRotulagem.INSPECAO_M5.value,
    RodadaRotulagem.INSPECAO_M7.value,
)


def foto_de_calibracao(coluna_leitura_id):
    """A foto principal da leitura tem rotulo de calibracao ou treino."""
    return exists().where(
        Foto.leitura_id == coluna_leitura_id,
        Foto.principal.is_(True),
        Rotulo.foto_id == Foto.id,
        Rotulo.rodada.in_(RODADAS_DE_CALIBRACAO),
    )


def e_erro_da_maquina(
    origem: str, decisao: str, motivo: str | None, etapa: str = "qualidade"
) -> bool:
    """Veredito do segundo revisor que contradiz a maquina.

    Auto-rejeitada na qualidade: so "reprovada por uma das 5 classes de qualidade"
    confirma; aprovada, ou reprovada por `outro`, e erro (o revisor nao viu a foto como
    ilegivel). Auto-rejeitada na validacao (divergencia): so aprovada e erro, porque
    `outro` e justamente o motivo de divergencia. Auto-aprovada: qualquer reprovada e erro
    (conservador)."""
    if origem == "auto_rejeitada" and etapa == "qualidade":
        return not (decisao == "reprovada" and motivo in MOTIVOS_DE_QUALIDADE)
    if origem == "auto_rejeitada":
        return decisao == "aprovada"
    return decisao == "reprovada"


Estrato = tuple[str, str, str]  # (detector, politica, etapa)


def _chave(ator: str | None, politica: str | None, status: str) -> Estrato:
    detector = (ator or "").removeprefix("modelo:") or "sem_registro"
    return detector, politica or "sem_registro", ETAPA.get(status, status)


def lote_posterior_a_ativacao(coluna_politica, coluna_lote_nome):
    """O lote foi ingerido depois da ativacao da politica: ela existe e o nome do lote nao
    esta em `lotes_anteriores`. Politica sem ativacao (decisao sem politica no historico,
    `sem_registro`) nunca e posterior."""
    return exists().where(
        AtivacaoPolitica.politica == coluna_politica,
        ~AtivacaoPolitica.lotes_anteriores.any(coluna_lote_nome),
    )


def _estratos(db: Session, origem: str) -> dict[Estrato, list[tuple[str, str | None]]]:
    """(detector, politica, etapa) -> vereditos, uma auditoria decidida por leitura.

    O estrato vem da linha do historico da decisao (`ator` = modelo:<detector>,
    `detalhe.politica`, `status` -> etapa). A mesma leitura pode ter varias decisoes
    auditadas (reaberta e decidida de novo pela maquina, com auditoria a 100%): dentro de
    um estrato so a auditoria decidida MAIS ANTIGA conta, para a unidade ser a foto e nao a
    decisao. So contam lotes ingeridos depois da ativacao da politica
    (`lote_posterior_a_ativacao`): os anteriores contem a calibracao e o treino dela.
    Foto de calibracao ou treino com rotulo (`RODADAS_DE_CALIBRACAO`) fica fora tambem,
    como protecao adicional.
    """
    politica = HistoricoStatus.detalhe["politica"].astext
    linhas = db.execute(
        select(
            HistoricoStatus.ator,
            politica,
            HistoricoStatus.status,
            AuditoriaAmostral.leitura_id,
            AuditoriaAmostral.decisao,
            AuditoriaAmostral.motivo,
        )
        .join(HistoricoStatus, HistoricoStatus.id == AuditoriaAmostral.historico_id)
        .join(Leitura, Leitura.id == AuditoriaAmostral.leitura_id)
        .join(Lote, Lote.id == Leitura.lote_id)
        .where(
            AuditoriaAmostral.origem == origem,
            AuditoriaAmostral.decisao.is_not(None),
            usuario_real(AuditoriaAmostral.revisor_id),
            lote_posterior_a_ativacao(politica, Lote.nome),
            ~foto_de_calibracao(AuditoriaAmostral.leitura_id),
        )
        .order_by(AuditoriaAmostral.decidida_em, AuditoriaAmostral.id)
    ).all()
    vistos: set[tuple[Estrato, int]] = set()
    estratos: dict[Estrato, list[tuple[str, str | None]]] = defaultdict(list)
    for ator, politica, status, leitura_id, decisao, motivo in linhas:
        chave = _chave(ator, politica, status)
        if (chave, leitura_id) in vistos:
            continue
        vistos.add((chave, leitura_id))
        estratos[chave].append((decisao, motivo))
    return estratos


# Sem estes rotulos importados (`make rotulos-importar`, `make rotulos-importar-inspecao`),
# a exclusao da calibracao nao enxerga nada e a medida mistura fotos dentro da amostra.
CALIBRACAO_OBRIGATORIA = (RodadaRotulagem.LAB02.value, RodadaRotulagem.INSPECAO_M5.value)
# A calibracao propria de um detector treinado: com ele configurado, a rodada tambem e
# obrigatoria (`make rotulos-importar-inspecao-m7` antes de ligar DETECTOR_QUALIDADE=onnx).
CALIBRACAO_OBRIGATORIA_DO_DETECTOR = {"qualidade@v1": (RodadaRotulagem.INSPECAO_M7.value,)}


def calibracao_obrigatoria() -> tuple[str, ...]:
    from inspecao.monitoramento.sinais import politica_ativa

    modelo = politica_ativa().modelo or ""
    return CALIBRACAO_OBRIGATORIA + CALIBRACAO_OBRIGATORIA_DO_DETECTOR.get(modelo, ())


def precondicao_da_exclusao(db: Session) -> tuple[dict[str, int], list[str]]:
    """(rotulos por rodada de calibracao, rodadas obrigatorias sem nenhum rotulo)."""
    contagem = dict(
        db.execute(
            select(Rotulo.rodada, func.count())
            .where(Rotulo.rodada.in_(RODADAS_DE_CALIBRACAO))
            .group_by(Rotulo.rodada)
        )
        .tuples()
        .all()
    )
    faltando = [r for r in calibracao_obrigatoria() if not contagem.get(r)]
    if faltando:
        log.warning(
            "exclusao da calibracao incompleta: nenhum rotulo em %s; o falso positivo das "
            "auto-rejeicoes inclui fotos de calibracao ate os importadores rodarem",
            ", ".join(faltando),
        )
    return {r: contagem.get(r, 0) for r in RODADAS_DE_CALIBRACAO}, faltando


def _medida_automatica(
    origem: str, estrato: Estrato, vereditos: list, detalhe: bool
) -> dict[str, Any]:
    """Erro da maquina num estrato, com o limite superior de 95% (Clopper-Pearson) e
    quantas leituras auditadas sustentariam o alvo com os erros de hoje (o total, nao o que
    falta). `alvo_atingido` quando o limite superior e menor ou igual ao alvo, nunca pela
    taxa observada."""
    n = len(vereditos)
    erros = sum(e_erro_da_maquina(origem, d, m, estrato[2]) for d, m in vereditos)
    alvo = ALVO_ERRO[origem]
    teto = limite_superior(erros, n)
    rotulo = {"detector": estrato[0], "politica": estrato[1], "etapa": estrato[2]}
    bloco = rotulo | {
        "n": n,
        "erros": erros,
        "taxa_erro": erros / n if n else None,
        "limite_superior_erro": teto,
        "alvo_erro": alvo,
        "necessarias": amostras_necessarias(erros, alvo),
        "alvo_atingido": teto is not None and teto <= alvo,
    }
    if detalhe or n >= MIN_PARES:
        return bloco | {"suprimido": False}
    return _suprimido(bloco) | {"alvo_erro": alvo} | rotulo


def _estrato_vigente(db: Session, origem: str) -> Estrato:
    """O estrato da ULTIMA decisao automatica desta origem gravada no historico: e quem
    esta decidindo de fato. Sem nenhuma, cai na politica do detector configurado
    (`sinais.politica_ativa`, a mesma do worker)."""
    from inspecao.monitoramento.sinais import politica_ativa

    status = [s for s, o in ORIGEM_AUTOMATICA.items() if o == origem]
    ultima = db.execute(
        select(
            HistoricoStatus.ator, HistoricoStatus.detalhe["politica"].astext, HistoricoStatus.status
        )
        .where(HistoricoStatus.status.in_(status), HistoricoStatus.ator.like("modelo:%"))
        .order_by(HistoricoStatus.id.desc())
        .limit(1)
    ).first()
    if ultima is not None:
        return _chave(*ultima)
    ativa = politica_ativa()
    etapa = "qualidade" if origem == "auto_rejeitada" else "validacao"
    return ativa.modelo or "sem_registro", ativa.versao, etapa


def _bloco_automatico(db: Session, origem: str, detalhe: bool) -> tuple[dict, list[dict]]:
    """(estrato vigente, todos os estratos). A tela e o supervisor usam o vigente (o da
    ultima decisao automatica); admin ve os demais. Erro e unidade em
    `e_erro_da_maquina` e `_estratos`."""
    vigente = _estrato_vigente(db, origem)
    estratos = _estratos(db, origem)
    principal = _medida_automatica(origem, vigente, estratos.get(vigente, []), detalhe)
    todos = [_medida_automatica(origem, k, v, detalhe) for k, v in sorted(estratos.items())]
    return principal, (todos if detalhe else [])


def concordancia(db: Session, detalhe: bool) -> dict[str, Any]:
    """Primeiro x segundo revisor nas auditorias decididas, so com contas reais, e o erro
    da maquina nas decisoes automaticas auditadas. Auditoria invalidada fica de fora.

    `detalhe` (admin): contagens, por lote e a serie acumulada por dia (America/Recife).
    Sem detalhe (supervisor): so os totais, e cada um so com ao menos `MIN_PARES` pares;
    contagens de sorteadas e pendentes ficam de fora, porque diriam ao primeiro revisor
    se ele foi sorteado.
    """
    linhas = db.execute(
        select(
            RevisaoHumana.decisao,
            RevisaoHumana.motivo,
            AuditoriaAmostral.decisao,
            AuditoriaAmostral.motivo,
            Lote.nome,
            AuditoriaAmostral.decidida_em,
        )
        .join(RevisaoHumana, RevisaoHumana.id == AuditoriaAmostral.revisao_id)
        .join(Leitura, Leitura.id == AuditoriaAmostral.leitura_id)
        .join(Lote, Lote.id == Leitura.lote_id)
        .where(
            AuditoriaAmostral.origem == "fila",
            AuditoriaAmostral.decisao.is_not(None),
            usuario_real(AuditoriaAmostral.revisor_original_id),
            usuario_real(AuditoriaAmostral.revisor_id),
        )
        .order_by(AuditoriaAmostral.decidida_em, AuditoriaAmostral.id)
    ).all()
    pares = [(a, b, c, d) for a, b, c, d, _lote, _quando in linhas]
    # Auto-aprovada reprovada pelo revisor = falso negativo da maquina; auto-rejeitada
    # aprovada pelo revisor = falso positivo (1 - precisao da auto-rejeicao).
    auto_aprovadas, estratos_aprovadas = _bloco_automatico(db, "auto_aprovada", detalhe)
    auto_rejeitadas, estratos_rejeitadas = _bloco_automatico(db, "auto_rejeitada", detalhe)
    total = _medidas(pares)
    s = get_settings()
    calibracao, faltando = precondicao_da_exclusao(db)
    base: dict[str, Any] = {
        "rotulos_de_calibracao": calibracao,
        "exclusao_incompleta": faltando,
        "taxa_configurada": s.auditoria_taxa,
        "taxa_auto_rejeitada": s.auditoria_taxa_auto_rejeitada,
        "min_pares": MIN_PARES,
        "detalhe": detalhe,
    }
    if not detalhe:
        return base | {
            "decisoes_na_fila": None,
            "sorteadas": None,
            "taxa_observada": None,
            "pendentes": None,
            "invalidadas": None,
            "decididas": None,
            "total": total | {"suprimido": False} if len(pares) >= MIN_PARES else _suprimido(total),
            "auto_aprovadas": auto_aprovadas,
            "auto_rejeitadas": auto_rejeitadas,
            "estratos_auto_aprovadas": [],
            "estratos_auto_rejeitadas": [],
            "por_lote": [],
            "serie": [],
        }

    por_lote: dict[str, list] = defaultdict(list)
    por_dia: dict[date, int] = {}
    for i, (a, b, c, d, lote, quando) in enumerate(linhas):
        por_lote[lote].append((a, b, c, d))
        por_dia[quando.astimezone(RECIFE).date()] = i + 1
    serie = [{"dia": dia, **_medidas(pares[:ate])} for dia, ate in sorted(por_dia.items())]

    def contar(*condicoes) -> int:
        return db.scalar(select(func.count()).select_from(AuditoriaAmostral).where(*condicoes)) or 0

    sorteadas = contar(AuditoriaAmostral.origem == "fila")
    decisoes_fila = (
        db.scalar(
            select(func.count(RevisaoHumana.id)).where(usuario_real(RevisaoHumana.usuario_id))
        )
        or 0
    )
    return base | {
        "decisoes_na_fila": decisoes_fila,
        "sorteadas": sorteadas,
        "taxa_observada": sorteadas / decisoes_fila if decisoes_fila else None,
        "pendentes": contar(
            AuditoriaAmostral.decisao.is_(None), AuditoriaAmostral.invalidada_em.is_(None)
        ),
        "invalidadas": contar(AuditoriaAmostral.invalidada_em.is_not(None)),
        "decididas": len(pares),
        "total": total | {"suprimido": False},
        "auto_aprovadas": auto_aprovadas,
        "auto_rejeitadas": auto_rejeitadas,
        "estratos_auto_aprovadas": estratos_aprovadas,
        "estratos_auto_rejeitadas": estratos_rejeitadas,
        "por_lote": [{"lote": lote, **_medidas(p)} for lote, p in sorted(por_lote.items())],
        "serie": serie,
    }
