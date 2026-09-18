"""Validacoes cruzadas de um lote recem-ingerido: quarentena e estado inicial.

Roda ao final de `ingest.batch.ingerir`, dentro da mesma transacao. Aplica a
regra de consistencia (`inference.consistency`) a cada leitura, registra as
fotos orfas e move para `sem_foto` as leituras sem evidencia fotografica.

Nenhuma validacao bloqueia o lote inteiro. A granularidade e o registro:
quem nao pode faturar recebe quarentena bloqueante, o resto segue.
"""

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from inspecao.enums import Ator, Severidade, StatusLeitura, TipoInconsistencia
from inspecao.estados import transicionar_em_lote
from inspecao.inference.consistency import avaliar
from inspecao.ingest.parser import LinhaLeitura
from inspecao.models import Foto, NotaOcorrencia, Quarentena

log = logging.getLogger(__name__)

LOTE_INSERCAO = 2000


@dataclass(slots=True)
class ResumoValidacao:
    quarentena_por_tipo: dict[str, int] = field(default_factory=dict)
    bloqueantes: int = 0
    sem_foto: int = 0
    seguem_para_triagem: int = 0
    orfas_com_leitura_provavel: int = 0


def validar_lote(
    sessao: Session,
    lote_id: int,
    linhas: Sequence[LinhaLeitura],
    id_por_linha: Mapping[int, int],
    fotos_presentes: set[str],
) -> ResumoValidacao:
    """Gera quarentena e transicoes iniciais para um lote ja inserido.

    `fotos_presentes` sao os nomes de arquivo efetivamente importados do disco.
    Uma referencia do CSV fora desse conjunto e `foto_referenciada_ausente`.
    `tem_foto` e decidido pela ligacao real em `fotos.leitura_id`, nao pela
    presenca em disco: se duas linhas do CSV apontarem o mesmo arquivo, so uma
    fica com a foto e a outra e tratada como referencia nao atendida.
    """
    leituras_com_foto: set[int] = set(
        sessao.scalars(
            select(Foto.leitura_id).where(Foto.lote_id == lote_id, Foto.principal.is_(True))
        ).all()
    )
    exige_por_codigo: dict[str, bool] = dict(
        sessao.execute(select(NotaOcorrencia.codigo, NotaOcorrencia.exige_foto)).all()
    )

    resumo = ResumoValidacao()
    contagem: Counter[str] = Counter()
    pendentes: list[dict] = []
    sem_foto_ids: list[int] = []

    def registrar(**campos: object) -> None:
        pendentes.append({"lote_id": lote_id, **campos})
        contagem[str(campos["tipo"])] += 1
        if campos["severidade"] == Severidade.BLOQUEANTE.value:
            resumo.bloqueantes += 1

    # ---------------------------------------------------------------- leituras
    for ln in linhas:
        leitura_id = id_por_linha[ln.linha_csv]
        tem_foto = leitura_id in leituras_com_foto
        ausente = ln.foto_nome is not None and not tem_foto

        veredito = avaliar(
            nota_codigo=ln.nota_codigo,
            nota_conhecida=ln.nota_codigo in exige_por_codigo,
            nota_exige_foto=exige_por_codigo.get(ln.nota_codigo or "", False),
            tem_foto=tem_foto,
            foto_referenciada_ausente=ausente,
            posicao_lida=ln.posicao_lida,
            posicao_invalida=ln.posicao_invalida,
            quantidade_medidores=len(ln.medidores),
        )

        for achado in veredito.achados:
            detalhe = dict(achado.detalhe or {})
            if achado.tipo is TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE:
                detalhe["foto_nome"] = ln.foto_nome
                if ln.foto_nome in fotos_presentes:
                    detalhe["motivo"] = "arquivo existe mas ja esta ligado a outra linha"
            registrar(
                leitura_id=leitura_id,
                foto_id=None,
                tipo=achado.tipo.value,
                severidade=achado.severidade.value,
                detalhe=detalhe or None,
            )

        if veredito.novo_status is StatusLeitura.SEM_FOTO:
            sem_foto_ids.append(leitura_id)
        elif veredito.novo_status is None:
            resumo.seguem_para_triagem += 1
        else:  # pragma: no cover - a regra so devolve SEM_FOTO ou None hoje
            raise AssertionError(f"estado inesperado da consistencia: {veredito.novo_status}")

    # ------------------------------------------------------------------- orfas
    # Hipotese T-M2-04: a orfa e uma foto adicional da mesma leitura (mesmo id
    # externo, sequencia diferente). Gravar a evidencia no detalhe permite
    # responder a pergunta por SQL, sem reprocessar.
    leitura_por_id_externo: dict[str, int] = {
        id_externo: leitura_id
        for id_externo, leitura_id in sessao.execute(
            select(Foto.id_externo, Foto.leitura_id).where(
                Foto.lote_id == lote_id, Foto.principal.is_(True)
            )
        ).all()
        if id_externo  # nome fora do padrao grava "", que nao pode casar com nada
    }
    orfas = sessao.execute(
        select(Foto.id, Foto.nome_arquivo, Foto.id_externo, Foto.sequencia).where(
            Foto.lote_id == lote_id, Foto.leitura_id.is_(None)
        )
    ).all()
    for foto_id, nome, id_externo, sequencia in orfas:
        leitura_provavel = leitura_por_id_externo.get(id_externo) if id_externo else None
        if leitura_provavel is not None:
            resumo.orfas_com_leitura_provavel += 1
        registrar(
            leitura_id=None,
            foto_id=foto_id,
            tipo=TipoInconsistencia.FOTO_ORFA.value,
            severidade=Severidade.INFORMATIVO.value,
            detalhe={
                "nome_arquivo": nome,
                "id_externo": id_externo,
                "sequencia": sequencia,
                "leitura_provavel_id": leitura_provavel,
            },
        )

    # -------------------------------------------------------------- persistir
    for i in range(0, len(pendentes), LOTE_INSERCAO):
        sessao.bulk_insert_mappings(Quarentena, pendentes[i : i + LOTE_INSERCAO])

    resumo.sem_foto = transicionar_em_lote(
        sessao,
        sem_foto_ids,
        de=StatusLeitura.RECEBIDA,
        para=StatusLeitura.SEM_FOTO,
        ator=Ator.INGESTAO.value,
        detalhe={"origem": "consistencia_ingestao"},
    )
    resumo.quarentena_por_tipo = dict(sorted(contagem.items()))
    return resumo
