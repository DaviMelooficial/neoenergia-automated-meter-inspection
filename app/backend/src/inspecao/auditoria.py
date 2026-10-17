"""Auditoria amostral (T-M8-07): segundo revisor cego para 5% a 10% das decisoes.

Sorteio: com probabilidade `AUDITORIA_TAXA` (configuravel, 0,05 a 0,10), deterministico
por leitura, sha256 de "salt:auditoria:leitura_id" com o salt secreto do servidor
(`ROTULAGEM_SALT`): reprocessar nao sorteia de novo e o auditor nao consegue prever
quais decisoes dele serao conferidas. Duas origens:
- `fila`: na mesma transacao da decisao do auditor (`sortear`). Decisao de conta de
  teste (`inspecao.contas_teste`) nao e sorteada: nao e auditoria de verdade;
- `auto_aprovada` e `auto_rejeitada`: leitura que a maquina decidiu sozinha
  (`sortear_automaticas`, pelo `make auditoria-sortear`). Auto-aprovadas hoje sao 0
  (politica sem limiar_aprova); auto-rejeitadas sao a unica decisao automatica ligada, e
  o segundo revisor mede o falso positivo dela em operacao.

Regras da escrita, valendo no POST e no banco (nao so no `/proxima`):
- o segundo revisor nunca e o primeiro (`ck_auditorias_segundo_revisor`; o
  primeiro tambem nao reserva, `ck_auditorias_reserva_de_outro`);
- cada leitura tem no maximo uma auditoria, decidida uma vez (`uq_auditorias_leitura`,
  o lock da linha no POST e o gatilho que torna a decisao imutavel);
- so o papel `auditor` audita: supervisor e admin leem metricas, e o diferencial dos
  agregados (por lote, por status) diria a eles a decisao anterior;
- toda recusa de `/auditoria/{id}/*` e a mesma resposta (404 "auditoria indisponivel"),
  para "nao existe", "e sua", "ja decidida" e "reservada por outro": nada diz ao primeiro
  revisor que ele foi sorteado.
Cegueira: o item entregue (`ItemAuditoriaOut`) e igual para as tres origens e nao traz
status, historico, revisao anterior, scores da maquina nem id de leitura (nem na
quarentena). Fora dele, a decisao final de qualquer leitura (humana ou da maquina) so
aparece ao proprio autor e a admin (`routers.operacao`), `/lotes` junta os status finais
numa chave so para quem nao e admin, e a recusa da fila e de texto constante.

A decisao do segundo revisor nao muda o status da leitura: mede a concordancia
inter-anotador continua e vira rotulo `producao` (active learning), na mesma transacao.
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
from inspecao.enums import StatusLeitura
from inspecao.models import AuditoriaAmostral, Leitura, Lote, RevisaoHumana, Usuario
from inspecao.rotulagem.consolidacao import kappa_cohen

log = logging.getLogger(__name__)
RESERVA = timedelta(minutes=10)
# Recife nao tem horario de verao desde 2000: UTC-3 fixo, sem depender de tzdata na imagem.
RECIFE = timezone(timedelta(hours=-3), "America/Recife")
# Abaixo disto o supervisor nao ve a medida: com poucos pares, a concordancia diria quem
# discordou de quem.
MIN_PARES = 10


def sorteada(leitura_id: int, taxa: float, salt: str) -> bool:
    h = hashlib.sha256(f"{salt}:auditoria:{leitura_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64 < taxa


def _sorteia(leitura_id: int) -> bool:
    s = get_settings()
    return sorteada(leitura_id, s.auditoria_taxa, s.salt_da_rotulagem())


def sortear(db: Session, revisao: RevisaoHumana) -> AuditoriaAmostral | None:
    """Cria a auditoria se a leitura for sorteada. Sem commit: roda na transacao da
    decisao, que ja travou a leitura. Decisao de conta de teste nao e sorteada."""
    if not _sorteia(revisao.leitura_id):
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


def _sem_auditoria(coluna_leitura_id):
    return ~(
        select(AuditoriaAmostral.id)
        .where(AuditoriaAmostral.leitura_id == coluna_leitura_id)
        .exists()
    )


def _travar(db: Session, leitura_id: int) -> bool:
    """FOR UPDATE SKIP LOCKED na leitura: em uso por outra transacao, fica para a proxima."""
    consulta = select(Leitura.id).where(Leitura.id == leitura_id).with_for_update(skip_locked=True)
    return db.scalar(consulta) is not None


def _talvez_commit(db: Session, commit_por_item: bool) -> None:
    if commit_por_item:
        db.commit()


def sortear_decididas(db: Session, commit_por_item: bool = False) -> int:
    """Aplica o sorteio as decisoes de fila gravadas antes do M8. Idempotente (sorteio
    deterministico, leitura com auditoria nao e sorteada de novo).

    Cada leitura e travada com SKIP LOCKED (em uso por outra transacao, fica para a proxima
    passada) e vai num savepoint: uma colisao (a decisao acabou de sortear a mesma leitura)
    perde so aquele item. O lock so cai no commit; com `commit_por_item` (a CLI) cada item
    e commitado e solto na hora, sem segurar as leituras ate o fim do lote.
    """
    revisoes = db.scalars(
        select(RevisaoHumana)
        .where(_sem_auditoria(RevisaoHumana.leitura_id), usuario_real(RevisaoHumana.usuario_id))
        .distinct(RevisaoHumana.leitura_id)
        .order_by(RevisaoHumana.leitura_id, RevisaoHumana.id)
    ).all()
    n = 0
    for r in revisoes:
        if not _sorteia(r.leitura_id):
            continue
        try:
            with db.begin_nested():
                if _travar(db, r.leitura_id) and sortear(db, r) is not None:
                    n += 1
        except IntegrityError:
            log.warning("leitura %s ja ganhou auditoria em outra transacao", r.leitura_id)
        _talvez_commit(db, commit_por_item)
    return n


# Decisoes da maquina auditadas: status -> origem da auditoria.
ORIGEM_AUTOMATICA = {
    StatusLeitura.AUTO_APROVADA.value: "auto_aprovada",
    StatusLeitura.AUTO_REJEITADA.value: "auto_rejeitada",
    StatusLeitura.REJEITADA_QUALIDADE.value: "auto_rejeitada",
}


def sortear_automaticas(db: Session, commit_por_item: bool = False) -> dict[str, int]:
    """Sorteia para segundo revisor as decisoes da maquina, com a mesma taxa e as mesmas
    garantias de `sortear_decididas`: auto-aprovadas (hoje 0) e auto-rejeitadas
    (`rejeitada_qualidade`, a unica decisao automatica ligada). Na auto-rejeitada, o
    segundo revisor aprovar e um falso positivo da maquina (alvo: precisao >= 0,99)."""
    linhas = db.execute(
        select(Leitura.id, Leitura.status_atual)
        .where(Leitura.status_atual.in_(list(ORIGEM_AUTOMATICA)), _sem_auditoria(Leitura.id))
        .order_by(Leitura.id)
    ).all()
    contagem = dict.fromkeys(set(ORIGEM_AUTOMATICA.values()), 0)
    for leitura_id, status in linhas:
        if not _sorteia(leitura_id):
            continue
        origem = ORIGEM_AUTOMATICA[status]
        try:
            with db.begin_nested():
                if _travar(db, leitura_id):
                    db.add(AuditoriaAmostral(leitura_id=leitura_id, origem=origem))
                    db.flush()
                    contagem[origem] += 1
        except IntegrityError:
            log.warning("leitura %s ja ganhou auditoria em outra transacao", leitura_id)
        _talvez_commit(db, commit_por_item)
    return contagem


def livre_para(usuario_id: int):
    """Pendente, nao e decisao do usuario, sem reserva ativa de outro."""
    return (
        AuditoriaAmostral.decisao.is_(None)
        # IS DISTINCT FROM: auditoria de auto-aprovada nao tem revisor original (NULL).
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


def reservar_proxima(db: Session, usuario_id: int) -> AuditoriaAmostral | None:
    """Reserva por 10 min a proxima auditoria que este usuario pode fazer. Sem commit.

    `FOR UPDATE SKIP LOCKED`: dois revisores ao mesmo tempo recebem itens diferentes.
    """
    auditoria = db.scalar(
        select(AuditoriaAmostral)
        .where(livre_para(usuario_id))
        .order_by(
            case((AuditoriaAmostral.reservado_por == usuario_id, 0), else_=1),
            AuditoriaAmostral.sorteada_em,
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


def concordancia(db: Session, detalhe: bool) -> dict[str, Any]:
    """Primeiro x segundo revisor nas auditorias decididas, so com contas reais.

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

    def bloco_automatico(origem: str, erro: str) -> dict[str, Any]:
        linhas_auto = db.execute(
            select(AuditoriaAmostral.decisao, func.count())
            .where(
                AuditoriaAmostral.origem == origem,
                AuditoriaAmostral.decisao.is_not(None),
                usuario_real(AuditoriaAmostral.revisor_id),
            )
            .group_by(AuditoriaAmostral.decisao)
        ).all()
        n = sum(k for _, k in linhas_auto)
        erros = sum(k for d, k in linhas_auto if d == erro)
        bloco = {"n": n, "erros": erros, "taxa_erro": erros / n if n else None}
        if detalhe or n >= MIN_PARES:
            return bloco | {"suprimido": False}
        return _suprimido(bloco)

    # Auto-aprovada reprovada pelo revisor = falso negativo da maquina; auto-rejeitada
    # aprovada pelo revisor = falso positivo (1 - precisao da auto-rejeicao).
    auto_aprovadas = bloco_automatico("auto_aprovada", "reprovada")
    auto_rejeitadas = bloco_automatico("auto_rejeitada", "aprovada")
    total = _medidas(pares)
    base: dict[str, Any] = {
        "taxa_configurada": get_settings().auditoria_taxa,
        "min_pares": MIN_PARES,
        "detalhe": detalhe,
    }
    if not detalhe:
        return base | {
            "decisoes_na_fila": None,
            "sorteadas": None,
            "taxa_observada": None,
            "pendentes": None,
            "decididas": None,
            "total": total | {"suprimido": False} if len(pares) >= MIN_PARES else _suprimido(total),
            "auto_aprovadas": auto_aprovadas,
            "auto_rejeitadas": auto_rejeitadas,
            "por_lote": [],
            "serie": [],
        }

    por_lote: dict[str, list] = defaultdict(list)
    por_dia: dict[date, int] = {}
    for i, (a, b, c, d, lote, quando) in enumerate(linhas):
        por_lote[lote].append((a, b, c, d))
        por_dia[quando.astimezone(RECIFE).date()] = i + 1
    serie = [{"dia": dia, **_medidas(pares[:ate])} for dia, ate in sorted(por_dia.items())]

    sorteadas = (
        db.scalar(
            select(func.count())
            .select_from(AuditoriaAmostral)
            .where(AuditoriaAmostral.origem == "fila")
        )
        or 0
    )
    decisoes_fila = (
        db.scalar(
            select(func.count(func.distinct(RevisaoHumana.leitura_id))).where(
                usuario_real(RevisaoHumana.usuario_id)
            )
        )
        or 0
    )
    pendentes = (
        db.scalar(
            select(func.count())
            .select_from(AuditoriaAmostral)
            .where(AuditoriaAmostral.decisao.is_(None))
        )
        or 0
    )
    return base | {
        "decisoes_na_fila": decisoes_fila,
        "sorteadas": sorteadas,
        "taxa_observada": sorteadas / decisoes_fila if decisoes_fila else None,
        "pendentes": pendentes,
        "decididas": len(pares),
        "total": total | {"suprimido": False},
        "auto_aprovadas": auto_aprovadas,
        "auto_rejeitadas": auto_rejeitadas,
        "por_lote": [{"lote": lote, **_medidas(p)} for lote, p in sorted(por_lote.items())],
        "serie": serie,
    }
