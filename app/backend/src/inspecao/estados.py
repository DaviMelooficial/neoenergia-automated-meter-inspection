"""Maquina de estados da leitura.

Este modulo e o UNICO lugar do codigo autorizado a escrever `leituras.status_atual`
e a inserir em `historico_status`. Routers, jobs e ingestao chamam `transicionar`
ou `transicionar_em_lote`; nenhum deles toca a coluna diretamente. Um teste
(`tests/test_estados.py`) varre o codigo-fonte para garantir isso.
"""

from collections.abc import Iterable, Sequence

from sqlalchemy import update
from sqlalchemy.orm import Session

from inspecao.enums import StatusLeitura
from inspecao.models import HistoricoStatus, Leitura

ESTADO_INICIAL = StatusLeitura.RECEBIDA

TRANSICOES: dict[StatusLeitura, frozenset[StatusLeitura]] = {
    StatusLeitura.RECEBIDA: frozenset(
        {StatusLeitura.TRIAGEM_QUALIDADE, StatusLeitura.SEM_FOTO}
    ),
    StatusLeitura.TRIAGEM_QUALIDADE: frozenset(
        {
            StatusLeitura.REJEITADA_QUALIDADE,
            StatusLeitura.VALIDACAO_LEITURA,
            StatusLeitura.FILA_HUMANA,
        }
    ),
    StatusLeitura.VALIDACAO_LEITURA: frozenset(
        {
            StatusLeitura.AUTO_APROVADA,
            StatusLeitura.AUTO_REJEITADA,
            StatusLeitura.FILA_HUMANA,
        }
    ),
    StatusLeitura.FILA_HUMANA: frozenset(
        {StatusLeitura.APROVADA_HUMANO, StatusLeitura.REPROVADA_HUMANO}
    ),
    # Estados finais: nenhuma saida.
    StatusLeitura.REJEITADA_QUALIDADE: frozenset(),
    StatusLeitura.AUTO_APROVADA: frozenset(),
    StatusLeitura.AUTO_REJEITADA: frozenset(),
    StatusLeitura.APROVADA_HUMANO: frozenset(),
    StatusLeitura.REPROVADA_HUMANO: frozenset(),
    StatusLeitura.SEM_FOTO: frozenset(),
}

ESTADOS_FINAIS: frozenset[StatusLeitura] = frozenset(
    estado for estado, saidas in TRANSICOES.items() if not saidas
)


class TransicaoInvalida(ValueError):
    """Tentativa de mover uma leitura por uma aresta que nao existe."""


def validar_transicao(de: StatusLeitura | str, para: StatusLeitura | str) -> None:
    origem = StatusLeitura(de)
    destino = StatusLeitura(para)
    if destino not in TRANSICOES[origem]:
        raise TransicaoInvalida(f"transicao nao permitida: {origem.value} -> {destino.value}")


def transicionar(
    sessao: Session,
    leitura: Leitura,
    novo_status: StatusLeitura | str,
    ator: str,
    confianca: float | None = None,
    detalhe: dict | None = None,
) -> HistoricoStatus:
    """Move UMA leitura de estado e grava o historico na mesma transacao.

    Nao faz commit: quem chama decide a fronteira transacional.
    """
    destino = StatusLeitura(novo_status)
    # Relê a linha com lock: dois workers concorrentes nao podem transicionar a
    # mesma leitura duas vezes a partir de um estado ja obsoleto em memoria.
    sessao.refresh(leitura, with_for_update=True)
    validar_transicao(leitura.status_atual, destino)

    leitura.status_atual = destino.value
    registro = HistoricoStatus(
        leitura_id=leitura.id,
        status=destino.value,
        ator=ator,
        confianca=confianca,
        detalhe=detalhe,
    )
    sessao.add(registro)
    sessao.flush()
    return registro


def transicionar_em_lote(
    sessao: Session,
    leitura_ids: Sequence[int],
    de: StatusLeitura | str,
    para: StatusLeitura | str,
    ator: str,
    confianca: float | None = None,
    detalhe: dict | None = None,
) -> int:
    """Move varias leituras pela MESMA aresta, em duas instrucoes SQL.

    Usado pela ingestao (milhares de leituras por lote). O UPDATE exige que a
    leitura esteja em `de`: se alguma nao estiver, a funcao desfaz o UPDATE
    parcial (savepoint) e levanta TransicaoInvalida. Nunca deixa status alterado
    sem a linha correspondente em historico_status, independentemente de quem
    chama fazer rollback ou nao.
    """
    origem = StatusLeitura(de)
    destino = StatusLeitura(para)
    validar_transicao(origem, destino)
    if not leitura_ids:
        return 0

    ids = list(leitura_ids)
    with sessao.begin_nested() as savepoint:
        atualizados = sessao.execute(
            update(Leitura)
            .where(Leitura.id.in_(ids), Leitura.status_atual == origem.value)
            .values(status_atual=destino.value)
            .returning(Leitura.id)
        ).scalars().all()

        if len(atualizados) != len(ids):
            faltantes = sorted(set(ids) - set(atualizados))[:5]
            savepoint.rollback()
            raise TransicaoInvalida(
                f"{len(ids) - len(atualizados)} leituras nao estavam em {origem.value}; "
                f"exemplos: {faltantes}"
            )

        sessao.bulk_insert_mappings(
            HistoricoStatus,
            [
                {
                    "leitura_id": lid,
                    "status": destino.value,
                    "ator": ator,
                    "confianca": confianca,
                    "detalhe": detalhe,
                }
                for lid in atualizados
            ],
        )
    return len(atualizados)


def registrar_estado_inicial(sessao: Session, leitura_ids: Iterable[int], ator: str) -> int:
    """Grava a primeira linha do historico (`recebida`) para leituras recem-criadas.

    Nao e uma transicao: e o nascimento do registro. A leitura ja foi inserida
    com `status_atual = ESTADO_INICIAL` pela ingestao.
    """
    linhas = [
        {"leitura_id": lid, "status": ESTADO_INICIAL.value, "ator": ator}
        for lid in leitura_ids
    ]
    if linhas:
        sessao.bulk_insert_mappings(HistoricoStatus, linhas)
    return len(linhas)
