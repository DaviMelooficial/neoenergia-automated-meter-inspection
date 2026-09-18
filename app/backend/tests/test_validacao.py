"""Integracao da validacao com o banco, usando um lote sintetico.

Cobre os caminhos que os 4 lotes reais nao exercitam (`foto_referenciada_ausente`,
`posicao_invalida`, `posicao_ausente`) e a mecanica de `transicionar`. Tudo roda
dentro de uma transacao revertida ao final: nada fica no banco.
Pula quando o banco nao esta acessivel.
"""

import csv
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import func, select, text

from inspecao.db import SessionLocal, engine
from inspecao.enums import Ator, Severidade, StatusLeitura, TipoInconsistencia
from inspecao.estados import TransicaoInvalida, transicionar, transicionar_em_lote
from inspecao.ingest.batch import BatchFolderSource, ingerir
from inspecao.models import Foto, HistoricoStatus, Leitura, Lote, NotaOcorrencia, Quarentena
from inspecao.storage.local import LocalFileStorage

LOTE = "PSP_EXTRATLEITIMPL_010199_0000"  # data impossivel: nunca colide com lote real
PREFIXO = f"{LOTE}_"
CABECALHO = [
    "Numero do medidor",
    "Posicao do medidor lida",
    "Nota de Leitura Atual",
    "Foto do medidor",
]


def foto(id_externo: int, seq: int = 0) -> str:
    return f"{PREFIXO}{id_externo:020d}_{seq:03d}.jpg"


# linha_csv comeca em 2 (a 1 e o cabecalho). Uma linha por cenario.
LINHAS = [
    # 2: normal, sem nota, com foto -> segue para triagem, sem quarentena
    ["1001", "123", "NA", foto(1)],
    # 3: nota exige foto e nao tem -> bloqueante + sem_foto
    ["1002", "456", "Z901", "NA"],
    # 4: nota nao exige e tem foto -> informativa, segue
    ["1003", "789", "Z902", foto(3)],
    # 5: codigo desconhecido, sem foto -> informativa + sem_foto
    ["1004", "NA", "Z999", "NA"],
    # 6: tres medidores, com foto -> medidor_multiplo, segue
    ["2001/2002/F2003", "10", "NA", foto(6)],
    # 7: CSV aponta foto que nao existe em disco -> bloqueante + sem_foto
    ["1007", "11", "Z901", foto(7)],
    # 8: posicao nao numerica -> posicao_invalida
    ["1008", "12ab", "NA", foto(8)],
    # 9: sem foto e sem nota -> sem_foto, sem quarentena
    ["1009", "13", "NA", "NA"],
    # 10: nota que pressupoe leitura (e nao exige foto), posicao vazia, com foto
    #     -> posicao_ausente + foto_sem_exigencia
    ["1010", "NA", "T111", foto(10)],
    # 11: aponta o MESMO arquivo da linha 2 -> a foto fica com a linha 2; esta vira
    #     foto_referenciada_ausente (bloqueante) + sem_foto
    ["1011", "14", "NA", foto(1)],
]
FOTOS_EM_DISCO = [foto(1), foto(3), foto(6), foto(8), foto(10), foto(999, 2)]  # 999 = orfa


def jpeg_bytes() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.zeros((480, 360, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture(scope="module")
def staging(tmp_path_factory) -> Path:
    raiz = tmp_path_factory.mktemp("staging")
    pasta = raiz / LOTE
    pasta.mkdir()
    with (pasta / "BaseExtracao_01011999_Dia.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        w.writerow(CABECALHO)
        w.writerows(LINHAS)
    dados = jpeg_bytes()
    for nome in FOTOS_EM_DISCO:
        (pasta / nome).write_bytes(dados)
    return raiz


@pytest.fixture
def sessao():
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("banco indisponivel")
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def ingerido(sessao, staging, tmp_path):
    # Notas sinteticas, na mesma transacao: nao dependem da planilha do cliente.
    sessao.add_all(
        [
            NotaOcorrencia(codigo="Z901", descricao="exige foto", exige_foto=True),
            NotaOcorrencia(codigo="Z902", descricao="nao exige foto", exige_foto=False),
        ]
    )
    # T111 e um codigo real (pressupoe leitura). O teste precisa controlar o seu
    # exige_foto, entao sobrescreve na transacao o que vier da planilha.
    t111 = sessao.get(NotaOcorrencia, "T111")
    if t111 is None:
        sessao.add(NotaOcorrencia(codigo="T111", descricao="tampa danificada", exige_foto=False))
    else:
        t111.exige_foto = False
    sessao.flush()

    origem = BatchFolderSource(staging)
    storage = LocalFileStorage(raiz=tmp_path / "storage")
    resultado = ingerir(sessao, origem, LOTE, storage)
    lote = sessao.scalar(select(Lote).where(Lote.nome == LOTE))
    return resultado, lote


def contar_quarentena(sessao, lote_id) -> dict[str, int]:
    linhas = sessao.execute(
        select(Quarentena.tipo, func.count())
        .where(Quarentena.lote_id == lote_id)
        .group_by(Quarentena.tipo)
    ).all()
    return dict(linhas)


def leitura_da_linha(sessao, lote_id, linha_csv) -> Leitura:
    return sessao.scalar(
        select(Leitura).where(Leitura.lote_id == lote_id, Leitura.linha_csv == linha_csv)
    )


def test_resultado_da_ingestao(ingerido):
    resultado, _ = ingerido
    assert resultado.leituras == 10
    assert resultado.fotos == 6
    assert resultado.fotos_orfas == 1
    assert resultado.fotos_ausentes == [foto(7)]
    assert resultado.validacao.sem_foto == 5  # linhas 3, 5, 7, 9, 11
    assert resultado.validacao.seguem_para_triagem == 5  # linhas 2, 4, 6, 8, 10
    assert resultado.validacao.bloqueantes == 3  # linhas 3, 7 e 11


def test_quarentena_por_tipo(ingerido, sessao):
    _, lote = ingerido
    assert contar_quarentena(sessao, lote.id) == {
        TipoInconsistencia.NOTA_EXIGE_SEM_FOTO.value: 1,
        TipoInconsistencia.FOTO_SEM_EXIGENCIA.value: 2,  # linhas 4 e 10
        TipoInconsistencia.CODIGO_NAO_MAPEADO.value: 1,
        TipoInconsistencia.MEDIDOR_MULTIPLO.value: 1,
        TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value: 2,  # linhas 7 e 11
        TipoInconsistencia.POSICAO_INVALIDA.value: 1,
        TipoInconsistencia.POSICAO_AUSENTE.value: 1,
        TipoInconsistencia.FOTO_ORFA.value: 1,
    }


def test_severidades(ingerido, sessao):
    _, lote = ingerido
    bloqueantes = sessao.scalars(
        select(Quarentena.tipo).where(
            Quarentena.lote_id == lote.id,
            Quarentena.severidade == Severidade.BLOQUEANTE.value,
        )
    ).all()
    assert sorted(bloqueantes) == sorted(
        [
            TipoInconsistencia.NOTA_EXIGE_SEM_FOTO.value,
            TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value,
            TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value,
        ]
    )


def test_foto_ausente_guarda_o_nome_prometido(ingerido, sessao):
    _, lote = ingerido
    por_leitura = {
        q.leitura_id: q
        for q in sessao.scalars(
            select(Quarentena).where(
                Quarentena.lote_id == lote.id,
                Quarentena.tipo == TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE.value,
            )
        )
    }
    ausente = por_leitura[leitura_da_linha(sessao, lote.id, 7).id]
    assert ausente.detalhe == {"foto_nome": foto(7)}

    duplicada = por_leitura[leitura_da_linha(sessao, lote.id, 11).id]
    assert duplicada.detalhe["foto_nome"] == foto(1)
    assert "outra linha" in duplicada.detalhe["motivo"]
    # a foto ficou com a linha 2, e so com ela
    assert sessao.scalar(select(Foto.leitura_id).where(Foto.nome_arquivo == foto(1))) == (
        leitura_da_linha(sessao, lote.id, 2).id
    )


def test_orfa_aponta_para_a_foto_e_nao_para_leitura(ingerido, sessao):
    _, lote = ingerido
    q = sessao.scalar(
        select(Quarentena).where(
            Quarentena.lote_id == lote.id, Quarentena.tipo == TipoInconsistencia.FOTO_ORFA.value
        )
    )
    assert q.leitura_id is None
    assert q.foto_id == sessao.scalar(select(Foto.id).where(Foto.nome_arquivo == foto(999, 2)))
    assert q.detalhe["sequencia"] == 2
    assert q.detalhe["leitura_provavel_id"] is None


def test_status_por_linha(ingerido, sessao):
    _, lote = ingerido
    esperado = {
        2: StatusLeitura.RECEBIDA,
        3: StatusLeitura.SEM_FOTO,
        4: StatusLeitura.RECEBIDA,
        5: StatusLeitura.SEM_FOTO,
        6: StatusLeitura.RECEBIDA,
        7: StatusLeitura.SEM_FOTO,
        8: StatusLeitura.RECEBIDA,
        9: StatusLeitura.SEM_FOTO,
        10: StatusLeitura.RECEBIDA,
        11: StatusLeitura.SEM_FOTO,
    }
    for linha, status in esperado.items():
        assert leitura_da_linha(sessao, lote.id, linha).status_atual == status.value, linha


def test_historico_tem_nascimento_e_transicao(ingerido, sessao):
    _, lote = ingerido
    sem_foto = leitura_da_linha(sessao, lote.id, 3)
    hist = sessao.scalars(
        select(HistoricoStatus)
        .where(HistoricoStatus.leitura_id == sem_foto.id)
        .order_by(HistoricoStatus.id)
    ).all()
    assert [h.status for h in hist] == ["recebida", "sem_foto"]
    assert {h.ator for h in hist} == {Ator.INGESTAO.value}
    assert hist[1].detalhe == {"origem": "consistencia_ingestao"}

    normal = leitura_da_linha(sessao, lote.id, 2)
    assert [h.status for h in normal.historico] == ["recebida"]


def test_reingestao_e_idempotente(ingerido, sessao, staging, tmp_path):
    _, lote = ingerido
    antes = contar_quarentena(sessao, lote.id)
    r = ingerir(sessao, BatchFolderSource(staging), LOTE, LocalFileStorage(raiz=tmp_path / "s2"))
    assert r.pulado
    assert contar_quarentena(sessao, lote.id) == antes


def test_transicionar_grava_historico_e_recusa_aresta_invalida(ingerido, sessao):
    _, lote = ingerido
    leitura = leitura_da_linha(sessao, lote.id, 2)

    registro = transicionar(
        sessao, leitura, StatusLeitura.TRIAGEM_QUALIDADE, ator="heuristica", confianca=0.5
    )
    assert leitura.status_atual == StatusLeitura.TRIAGEM_QUALIDADE.value
    assert registro.confianca == 0.5
    assert [h.status for h in leitura.historico] == ["recebida", "triagem_qualidade"]

    with pytest.raises(TransicaoInvalida):
        transicionar(sessao, leitura, StatusLeitura.APROVADA_HUMANO, ator="usuario:1")
    assert leitura.status_atual == StatusLeitura.TRIAGEM_QUALIDADE.value

    final = leitura_da_linha(sessao, lote.id, 3)  # ja esta em sem_foto
    with pytest.raises(TransicaoInvalida):
        transicionar(sessao, final, StatusLeitura.TRIAGEM_QUALIDADE, ator="heuristica")


def test_transicionar_em_lote_exige_que_todas_estejam_na_origem(ingerido, sessao):
    _, lote = ingerido
    recebida = leitura_da_linha(sessao, lote.id, 2)
    sem_foto = leitura_da_linha(sessao, lote.id, 3)

    with pytest.raises(TransicaoInvalida, match="1 leituras nao estavam em recebida"):
        transicionar_em_lote(
            sessao,
            [recebida.id, sem_foto.id],
            de=StatusLeitura.RECEBIDA,
            para=StatusLeitura.TRIAGEM_QUALIDADE,
            ator="heuristica",
        )

    # Atomicidade: a leitura que casava com a origem NAO pode ter mudado, e a
    # transacao continua utilizavel (savepoint, nao rollback total).
    sessao.expire_all()
    assert recebida.status_atual == StatusLeitura.RECEBIDA.value
    assert [h.status for h in recebida.historico] == ["recebida"]
    assert sessao.scalar(select(func.count()).select_from(Lote).where(Lote.id == lote.id)) == 1

    # Lista vazia nao executa nada, mas ainda valida a aresta.
    assert transicionar_em_lote(sessao, [], StatusLeitura.RECEBIDA, "sem_foto", "x") == 0
    with pytest.raises(TransicaoInvalida):
        transicionar_em_lote(sessao, [], StatusLeitura.SEM_FOTO, "recebida", "x")


def test_transicionar_em_lote_grava_confianca(ingerido, sessao):
    _, lote = ingerido
    leitura = leitura_da_linha(sessao, lote.id, 2)
    n = transicionar_em_lote(
        sessao,
        [leitura.id],
        de=StatusLeitura.RECEBIDA,
        para=StatusLeitura.TRIAGEM_QUALIDADE,
        ator="modelo:qualidade@v1",
        confianca=0.87,
    )
    assert n == 1
    sessao.expire_all()
    assert leitura.status_atual == StatusLeitura.TRIAGEM_QUALIDADE.value
    assert leitura.historico[-1].confianca == 0.87
