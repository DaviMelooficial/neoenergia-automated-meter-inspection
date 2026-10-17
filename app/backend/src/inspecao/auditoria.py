"""Auditoria amostral (T-M8-07): segundo revisor cego para 5% a 10% das decisoes.

Sorteio: na mesma transacao da decisao na fila humana, a leitura e sorteada com
probabilidade `AUDITORIA_TAXA` (configuravel, 0,05 a 0,10). O sorteio e
deterministico por leitura, sha256 de "salt:auditoria:leitura_id" com o salt
secreto do servidor (`ROTULAGEM_SALT`): reprocessar nao sorteia de novo e o
auditor nao consegue prever quais decisoes dele serao conferidas.

Regras da escrita, valendo no POST e no banco (nao so no `/proxima`):
- o segundo revisor nunca e o primeiro (`ck_auditorias_segundo_revisor`; o
  primeiro tambem nao reserva, `ck_auditorias_reserva_de_outro`);
- cada leitura tem no maximo uma auditoria, decidida uma vez (`uq_auditorias_leitura`
  e o lock da linha no POST).
Cegueira: o item entregue (`ItemAuditoriaOut`) nao traz status, historico nem a
revisao anterior; `GET /leituras/{id}` de uma leitura em auditoria pendente so
responde ao revisor original.

A decisao do segundo revisor nao muda o status da leitura: mede a concordancia
inter-anotador continua e vira rotulo `producao` (active learning), na mesma
transacao.
"""

import hashlib
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from inspecao.config import get_settings
from inspecao.models import AuditoriaAmostral, Leitura, Lote, RevisaoHumana
from inspecao.rotulagem.consolidacao import kappa_cohen

RESERVA = timedelta(minutes=10)


def sorteada(leitura_id: int, taxa: float, salt: str) -> bool:
    h = hashlib.sha256(f"{salt}:auditoria:{leitura_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64 < taxa


def sortear(db: Session, revisao: RevisaoHumana) -> AuditoriaAmostral | None:
    """Cria a auditoria se a leitura for sorteada. Sem commit: roda na transacao da
    decisao, que ja travou a leitura."""
    s = get_settings()
    if not sorteada(revisao.leitura_id, s.auditoria_taxa, s.salt_da_rotulagem()):
        return None
    auditoria = AuditoriaAmostral(
        leitura_id=revisao.leitura_id,
        revisao_id=revisao.id,
        revisor_original_id=revisao.usuario_id,
    )
    revisao.redistribuida = True
    db.add(auditoria)
    db.flush()
    return auditoria


def sortear_decididas(db: Session) -> int:
    """Aplica o sorteio as decisoes gravadas antes do M8 (idempotente: o sorteio e
    deterministico e a leitura com auditoria nao e sorteada de novo)."""
    sem_auditoria = (
        select(RevisaoHumana)
        .where(
            ~select(AuditoriaAmostral.id)
            .where(AuditoriaAmostral.leitura_id == RevisaoHumana.leitura_id)
            .exists()
        )
        .distinct(RevisaoHumana.leitura_id)
        .order_by(RevisaoHumana.leitura_id, RevisaoHumana.id)
    )
    return sum(1 for r in db.scalars(sem_auditoria).all() if sortear(db, r) is not None)


def _livre_para(usuario_id: int):
    return (
        AuditoriaAmostral.decisao.is_(None)
        & (AuditoriaAmostral.revisor_original_id != usuario_id)
        & or_(
            AuditoriaAmostral.reservado_ate.is_(None),
            AuditoriaAmostral.reservado_ate < func.now(),
            AuditoriaAmostral.reservado_por == usuario_id,
        )
    )


def pendentes_para(db: Session, usuario_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(AuditoriaAmostral).where(_livre_para(usuario_id))
        )
        or 0
    )


def reservar_proxima(db: Session, usuario_id: int) -> AuditoriaAmostral | None:
    """Reserva por 10 min a proxima auditoria que este usuario pode fazer. Sem commit.

    `FOR UPDATE SKIP LOCKED`: dois revisores ao mesmo tempo recebem itens diferentes.
    """
    auditoria = db.scalar(
        select(AuditoriaAmostral)
        .where(_livre_para(usuario_id))
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


def pendente_da_leitura(db: Session, leitura_id: int) -> AuditoriaAmostral | None:
    return db.scalar(
        select(AuditoriaAmostral).where(
            AuditoriaAmostral.leitura_id == leitura_id, AuditoriaAmostral.decisao.is_(None)
        )
    )


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


def concordancia(db: Session) -> dict[str, Any]:
    """Primeiro x segundo revisor nas auditorias decididas: total, por lote e a serie
    acumulada por dia de decisao (a medida continua inter-anotador)."""
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
        .where(AuditoriaAmostral.decisao.is_not(None))
        .order_by(AuditoriaAmostral.decidida_em, AuditoriaAmostral.id)
    ).all()
    pares = [(a, b, c, d) for a, b, c, d, _lote, _quando in linhas]

    por_lote: dict[str, list] = defaultdict(list)
    por_dia: dict[date, int] = {}
    for i, (a, b, c, d, lote, quando) in enumerate(linhas):
        por_lote[lote].append((a, b, c, d))
        por_dia[quando.date()] = i + 1
    serie = [{"dia": dia, **_medidas(pares[:ate])} for dia, ate in sorted(por_dia.items())]

    sorteadas = db.scalar(select(func.count()).select_from(AuditoriaAmostral)) or 0
    decisoes_fila = db.scalar(select(func.count(func.distinct(RevisaoHumana.leitura_id)))) or 0
    return {
        "taxa_configurada": get_settings().auditoria_taxa,
        "decisoes_na_fila": decisoes_fila,
        "sorteadas": sorteadas,
        "taxa_observada": sorteadas / decisoes_fila if decisoes_fila else None,
        "pendentes": sorteadas - len(pares),
        "decididas": len(pares),
        "total": _medidas(pares),
        "por_lote": [{"lote": lote, **_medidas(p)} for lote, p in sorted(por_lote.items())],
        "serie": serie,
    }
