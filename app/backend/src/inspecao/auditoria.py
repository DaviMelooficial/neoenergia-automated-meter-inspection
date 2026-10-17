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

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inspecao.config import get_settings
from inspecao.contas_teste import email_de_teste, usuario_real
from inspecao.enums import MotivoReprovacao, StatusLeitura
from inspecao.models import (
    AuditoriaAmostral,
    HistoricoStatus,
    Leitura,
    Lote,
    RevisaoHumana,
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


def e_erro_da_maquina(origem: str, decisao: str, motivo: str | None) -> bool:
    """Veredito do segundo revisor que contradiz a maquina.

    Auto-rejeitada: so "reprovada por uma das 5 classes de qualidade" confirma; aprovada, ou
    reprovada por `outro`, e erro (o revisor nao viu a foto como ilegivel).
    Auto-aprovada: qualquer reprovada e erro (conservador: `outro` pode ser divergencia
    de leitura, mas aprovar sozinho uma leitura que o humano reprova e o risco caro)."""
    if origem == "auto_rejeitada":
        return not (decisao == "reprovada" and motivo in MOTIVOS_DE_QUALIDADE)
    return decisao == "reprovada"


def _estratos(db: Session, origem: str) -> dict[tuple[str, str], list[tuple[str, str | None]]]:
    """(detector, politica) -> vereditos, uma auditoria decidida por leitura por estrato.

    O estrato vem da linha do historico da decisao (`ator` = modelo:<detector>,
    `detalhe.politica`). A mesma leitura pode ter varias decisoes auditadas (reaberta e
    decidida de novo pela maquina, com auditoria a 100%): dentro de um estrato so a
    auditoria decidida MAIS ANTIGA conta, para a unidade ser a foto e nao a decisao.
    """
    linhas = db.execute(
        select(
            HistoricoStatus.ator,
            HistoricoStatus.detalhe["politica"].astext,
            AuditoriaAmostral.leitura_id,
            AuditoriaAmostral.decisao,
            AuditoriaAmostral.motivo,
        )
        .join(HistoricoStatus, HistoricoStatus.id == AuditoriaAmostral.historico_id)
        .where(
            AuditoriaAmostral.origem == origem,
            AuditoriaAmostral.decisao.is_not(None),
            usuario_real(AuditoriaAmostral.revisor_id),
        )
        .order_by(AuditoriaAmostral.decidida_em, AuditoriaAmostral.id)
    ).all()
    vistos: set[tuple[str, str, int]] = set()
    estratos: dict[tuple[str, str], list[tuple[str, str | None]]] = defaultdict(list)
    for ator, politica, leitura_id, decisao, motivo in linhas:
        chave = ((ator or "").removeprefix("modelo:") or "sem_registro", politica or "sem_registro")
        if (*chave, leitura_id) in vistos:
            continue
        vistos.add((*chave, leitura_id))
        estratos[chave].append((decisao, motivo))
    return estratos


def _medida_automatica(
    origem: str, estrato: tuple[str, str], vereditos: list, detalhe: bool
) -> dict[str, Any]:
    """Erro da maquina num estrato, com o limite superior de 95% (Clopper-Pearson) e
    quantas leituras auditadas sustentariam o alvo com os erros de hoje (o total, nao o que
    falta). `alvo_atingido` quando o limite superior e menor ou igual ao alvo, nunca pela
    taxa observada."""
    n = len(vereditos)
    erros = sum(e_erro_da_maquina(origem, d, m) for d, m in vereditos)
    alvo = ALVO_ERRO[origem]
    teto = limite_superior(erros, n)
    bloco = {
        "detector": estrato[0],
        "politica": estrato[1],
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
    return _suprimido(bloco) | {"alvo_erro": alvo, "detector": estrato[0], "politica": estrato[1]}


def _bloco_automatico(db: Session, origem: str, detalhe: bool) -> tuple[dict, list[dict]]:
    """(estrato da politica ativa, todos os estratos). A tela e o supervisor usam o da
    politica ativa; admin ve os demais. Erro e unidade em `e_erro_da_maquina` e
    `_estratos`."""
    from inspecao.monitoramento.sinais import politica_ativa

    ativa = politica_ativa()
    chave_ativa = (ativa.modelo or "sem_registro", ativa.versao)
    estratos = _estratos(db, origem)
    principal = _medida_automatica(origem, chave_ativa, estratos.get(chave_ativa, []), detalhe)
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
    base: dict[str, Any] = {
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
