"""Active learning (marco M8): decisao de auditor vira rotulo `producao`.

Cada decisao na fila humana (`/fila-humana/{id}/decisao`) e cada decisao do segundo
revisor na auditoria amostral grava uma linha em `rotulos` com `rodada = producao`,
na MESMA transacao da decisao: se o rotulo nao entra, a decisao tambem nao.

A decisao do auditor nao declara o esquema v2 inteiro. Mapeamento, so com o que a
decisao afirma (o resto fica nulo):

| decisao                  | legivel | defeito marcado                    | cena |
| aprovada                 | true    | nenhum (todos nulos)               | nulo |
| reprovada, motivo defeito| false   | o do motivo = true, outros nulos   | nulo |
| reprovada, motivo outro  | nulo    | todos nulos                        | nulo |

Motivo -> coluna: `fora_de_foco` -> fora_de_foco, `glare` -> reflexo, `tampa_suja`
-> tampa_suja, `enquadramento_incorreto` -> enquadramento, `display_apagado` ->
display_apagado. Aprovar exige ler o display, por isso `legivel = true`; um defeito
leve que nao impede a leitura (reflexo fora dos digitos) continua possivel, entao os
defeitos de uma aprovada ficam nulos e nao `false`. Reprovar por `outro` pode ser
divergencia de leitura com foto boa: `legivel` fica nulo.

As rodadas cegas (a, b, desempate) nao sao afetadas: elas so consultam a propria
rodada e o `lab02`, e os indices unicos delas filtram por rodada.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from inspecao.enums import DecisaoHumana, MotivoReprovacao, RodadaRotulagem
from inspecao.models import Foto, Rotulo

MOTIVO_PARA_COLUNA: dict[MotivoReprovacao, str] = {
    MotivoReprovacao.FORA_DE_FOCO: "fora_de_foco",
    MotivoReprovacao.GLARE: "reflexo",
    MotivoReprovacao.TAMPA_SUJA: "tampa_suja",
    MotivoReprovacao.ENQUADRAMENTO_INCORRETO: "enquadramento",
    MotivoReprovacao.DISPLAY_APAGADO: "display_apagado",
}
COLUNAS_DEFEITO = ("reflexo", "fora_de_foco", "tampa_suja", "enquadramento", "display_apagado")


def campos_da_decisao(
    decisao: DecisaoHumana, motivo: MotivoReprovacao | None
) -> dict[str, bool | str | None]:
    """Colunas do esquema v2 que a decisao preenche; as nao afirmadas vao nulas."""
    campos: dict[str, bool | str | None] = {c: None for c in COLUNAS_DEFEITO}
    campos["cena"] = None
    if decisao is DecisaoHumana.APROVADA:
        campos["legivel"] = True
    elif motivo is None or motivo is MotivoReprovacao.OUTRO:
        campos["legivel"] = None
    else:
        campos["legivel"] = False
        campos[MOTIVO_PARA_COLUNA[motivo]] = True
    return campos


def registrar(
    db: Session,
    leitura_id: int,
    usuario_id: int,
    decisao: DecisaoHumana,
    motivo: MotivoReprovacao | None,
    *,
    revisao_id: int | None = None,
    auditoria_id: int | None = None,
) -> Rotulo | None:
    """Adiciona o rotulo `producao` da foto principal da leitura, sem commit.

    Quem chama esta na transacao da decisao e faz o commit (ou o rollback, se o
    flush daqui violar um indice unico). Leitura sem foto principal nao gera rotulo:
    o rotulo e da foto. Devolve o rotulo, ou None quando nao ha foto.
    """
    foto_id = db.scalar(
        select(Foto.id).where(Foto.leitura_id == leitura_id, Foto.principal.is_(True))
    )
    if foto_id is None:
        return None
    rotulo = Rotulo(
        foto_id=foto_id,
        usuario_id=usuario_id,
        rodada=RodadaRotulagem.PRODUCAO.value,
        revisao_id=revisao_id,
        auditoria_id=auditoria_id,
        **campos_da_decisao(decisao, motivo),
    )
    db.add(rotulo)
    db.flush()
    return rotulo
