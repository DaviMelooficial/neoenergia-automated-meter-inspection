"""Teste de regressao de dados.

Transforma os numeros levantados nos 4 lotes reais em assercao executavel.
Se o parser regredir, estes valores mudam e o teste quebra.

Roda contra o banco de DESENVOLVIMENTO (`engine_dados_reais`), nao contra o
banco de teste: os 4 lotes reais so existem la. Pula quando o banco nao esta
acessivel ou os lotes ainda nao foram ingeridos.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inspecao.enums import Severidade, StatusLeitura, TipoInconsistencia
from inspecao.models import (
    Foto,
    HistoricoStatus,
    Leitura,
    LeituraMedidor,
    Lote,
    NotaOcorrencia,
    Quarentena,
)

# Valores medidos em "Base cliente/", 4 lotes. Alterar apenas com justificativa
# no commit, nunca para acomodar um parser quebrado.
ESPERADO = {
    "leituras": 13_669,
    "fotos": 12_340,
    "notas": 61,
    "sem_nota": 9_109,
    "com_nota": 4_560,
    "fotos_orfas": 866,
    "multi_medidor": 1_316,
    "max_medidores": 8,
    "codigo_nao_mapeado": 6,
    "lotes": 4,
}

SEQUENCIAS = {0: 11_817, 1: 435, 2: 50, 3: 13, 4: 10, 5: 6, 6: 3, 7: 4, 8: 2}

# Marco M2. Tabela de aceite do plano (doc 02 do plano, secao 6), medida na
# reingestao dos 4 lotes em 2026-09-18. `posicao_ausente` nao estava na tabela
# original: foi adicionada pela regra de consistencia (plano 03, secao 6) e
# medida em zero.
QUARENTENA = {
    TipoInconsistencia.NOTA_EXIGE_SEM_FOTO: 296,
    TipoInconsistencia.FOTO_SEM_EXIGENCIA: 562,
    TipoInconsistencia.CODIGO_NAO_MAPEADO: 6,
    TipoInconsistencia.FOTO_ORFA: 866,
    TipoInconsistencia.MEDIDOR_MULTIPLO: 1_316,
    TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE: 0,
    TipoInconsistencia.POSICAO_INVALIDA: 0,
    TipoInconsistencia.POSICAO_AUSENTE: 0,
}

# 11.474 leituras referenciam foto (= fotos principais); as outras 2.195 nao.
# O exemplo de `/metricas/qualidade` no plano 03 citava 11.908 / 1.761 como
# ilustracao, nao como medida. Estes sao os valores reais.
# `sem_foto` e ancora de ingestao. As 11.474 com foto avancam pela maquina de
# estados conforme o worker roda, entao so a soma delas e fixa.
COM_FOTO = 11_474
SEM_FOTO = 2_195


@pytest.fixture(scope="module")
def sessao(engine_dados_reais):
    with Session(engine_dados_reais) as s:
        if s.scalar(select(func.count()).select_from(Lote)) != ESPERADO["lotes"]:
            pytest.skip("os 4 lotes nao estao ingeridos no banco de desenvolvimento")
        yield s


@pytest.fixture(autouse=True)
def _limpar_transacao(sessao):
    """Desfaz a transacao apos cada teste.

    Sem isso, um SQL invalido em um teste aborta a transacao e todos os testes
    seguintes falham com "current transaction is aborted", escondendo a causa.
    """
    yield
    sessao.rollback()


def contar(s, modelo, *filtros):
    stmt = select(func.count()).select_from(modelo)
    for f in filtros:
        stmt = stmt.where(f)
    return s.scalar(stmt)


def test_totais(sessao):
    assert contar(sessao, Leitura) == ESPERADO["leituras"]
    assert contar(sessao, Foto) == ESPERADO["fotos"]
    assert contar(sessao, NotaOcorrencia) == ESPERADO["notas"]


def test_particao_por_nota(sessao):
    sem = contar(sessao, Leitura, Leitura.nota_codigo.is_(None))
    com = contar(sessao, Leitura, Leitura.nota_codigo.is_not(None))
    assert sem == ESPERADO["sem_nota"]
    assert com == ESPERADO["com_nota"]
    assert sem + com == ESPERADO["leituras"]


def test_fotos_orfas(sessao):
    """866 fotos em disco que nenhum CSV referencia. Importadas, nao descartadas."""
    assert contar(sessao, Foto, Foto.leitura_id.is_(None)) == ESPERADO["fotos_orfas"]


def test_nenhuma_foto_referenciada_ausente(sessao):
    """Integridade CSV para disco e perfeita nos 4 lotes."""
    principais = contar(sessao, Foto, Foto.principal.is_(True))
    orfas = contar(sessao, Foto, Foto.leitura_id.is_(None))
    assert principais + orfas == ESPERADO["fotos"]


def test_multiplos_medidores(sessao):
    sub = (
        select(LeituraMedidor.leitura_id)
        .group_by(LeituraMedidor.leitura_id)
        .having(func.count() > 1)
        .subquery()
    )
    assert sessao.scalar(select(func.count()).select_from(sub)) == ESPERADO["multi_medidor"]

    # max(count(...)) direto seria agregado aninhado, que o Postgres recusa.
    por_leitura = (
        select(func.count().label("qtd"))
        .select_from(LeituraMedidor)
        .group_by(LeituraMedidor.leitura_id)
        .subquery()
    )
    maximo = sessao.scalar(select(func.max(por_leitura.c.qtd)))
    assert maximo == ESPERADO["max_medidores"]


def test_medidor_alfanumerico_preservado(sessao):
    """Identificadores como F00001 nao podem ter virado inteiro nem sumido."""
    achados = sessao.scalars(
        select(LeituraMedidor.numero).where(LeituraMedidor.numero.op("~")("[A-Za-z]")).limit(5)
    ).all()
    assert achados, "nenhum medidor alfanumerico encontrado"
    assert all(isinstance(n, str) for n in achados)


def test_codigo_nao_mapeado(sessao):
    """V100 aparece nos dados e nao existe na planilha. Deve entrar mesmo assim."""
    codigos = select(NotaOcorrencia.codigo)
    n = contar(
        sessao, Leitura, Leitura.nota_codigo.is_not(None), Leitura.nota_codigo.not_in(codigos)
    )
    assert n == ESPERADO["codigo_nao_mapeado"]


def test_distribuicao_de_sequencias(sessao):
    """A sequencia vai ate _008, corrigindo o que o doc 02 afirmava (_003)."""
    linhas = sessao.execute(
        select(Foto.sequencia, func.count()).group_by(Foto.sequencia).order_by(Foto.sequencia)
    ).all()
    assert dict(linhas) == SEQUENCIAS


def test_todas_as_imagens_360x480(sessao):
    outras = contar(sessao, Foto, (Foto.largura != 360) | (Foto.altura != 480))
    assert outras == 0


# ----------------------------------------------------------------------- M2


def test_quarentena_por_tipo(sessao):
    """A ancora factual do M2. Divergencia aqui e regressao no parser ou na regra."""
    linhas = sessao.execute(select(Quarentena.tipo, func.count()).group_by(Quarentena.tipo)).all()
    medido = {TipoInconsistencia(t): n for t, n in linhas}
    esperado = {t: n for t, n in QUARENTENA.items() if n > 0}
    assert medido == esperado


def test_apenas_nota_exige_sem_foto_e_bloqueante(sessao):
    bloq = sessao.execute(
        select(Quarentena.tipo, func.count())
        .where(Quarentena.severidade == Severidade.BLOQUEANTE.value)
        .group_by(Quarentena.tipo)
    ).all()
    assert dict(bloq) == {TipoInconsistencia.NOTA_EXIGE_SEM_FOTO.value: 296}


def test_codigo_nao_mapeado_e_sempre_v100(sessao):
    codigos = sessao.scalars(
        select(Quarentena.detalhe["nota_codigo"].astext).where(
            Quarentena.tipo == TipoInconsistencia.CODIGO_NAO_MAPEADO.value
        )
    ).all()
    assert len(codigos) == 6 and set(codigos) == {"V100"}


def test_distribuicao_de_status(sessao):
    linhas = dict(
        sessao.execute(
            select(Leitura.status_atual, func.count()).group_by(Leitura.status_atual)
        ).all()
    )
    assert linhas.pop(StatusLeitura.SEM_FOTO.value) == SEM_FOTO
    assert sum(linhas.values()) == COM_FOTO
    assert COM_FOTO + SEM_FOTO == ESPERADO["leituras"]


def test_sem_foto_e_exatamente_quem_nao_tem_foto_principal(sessao):
    com_foto = select(Foto.leitura_id).where(Foto.principal.is_(True))
    assert (
        contar(
            sessao,
            Leitura,
            Leitura.status_atual == StatusLeitura.SEM_FOTO,
            Leitura.id.in_(com_foto),
        )
        == 0
    )
    assert (
        contar(
            sessao,
            Leitura,
            Leitura.status_atual != StatusLeitura.SEM_FOTO,
            Leitura.id.not_in(com_foto),
        )
        == 0
    )


def test_historico_tem_nascimento_de_todas_e_transicao_das_sem_foto(sessao):
    por_status = dict(
        sessao.execute(
            select(HistoricoStatus.status, func.count()).group_by(HistoricoStatus.status)
        ).all()
    )
    assert por_status[StatusLeitura.RECEBIDA.value] == ESPERADO["leituras"]
    assert por_status[StatusLeitura.SEM_FOTO.value] == SEM_FOTO
    # Toda linha `recebida` e `sem_foto` do historico vem da ingestao.
    atores_ingestao = set(
        sessao.scalars(
            select(HistoricoStatus.ator)
            .where(HistoricoStatus.status.in_(["recebida", "sem_foto"]))
            .distinct()
        ).all()
    )
    assert atores_ingestao == {"ingestao"}


def test_toda_orfa_aponta_para_uma_foto_sem_leitura(sessao):
    """Cada registro `foto_orfa` referencia uma foto com leitura_id nulo, e vice-versa."""
    orfas = select(Foto.id).where(Foto.leitura_id.is_(None))
    n = contar(
        sessao,
        Quarentena,
        Quarentena.tipo == TipoInconsistencia.FOTO_ORFA.value,
        Quarentena.foto_id.in_(orfas),
    )
    assert n == ESPERADO["fotos_orfas"]


def test_hipotese_orfa_mesma_leitura_refutada(sessao):
    """T-M2-04: nenhuma orfa compartilha id_externo com foto principal do lote.

    Se um dia isso mudar, a leitura de `ml/reports/orfas.md` precisa ser revista.
    """
    com_leitura_provavel = contar(
        sessao,
        Quarentena,
        Quarentena.tipo == TipoInconsistencia.FOTO_ORFA.value,
        Quarentena.detalhe["leitura_provavel_id"].astext.is_not(None),
    )
    assert com_leitura_provavel == 0
