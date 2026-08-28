"""Teste de regressao de dados.

Transforma os numeros levantados nos 4 lotes reais em assercao executavel.
Se o parser regredir, estes valores mudam e o teste quebra.

Pula quando o banco nao esta acessivel ou os lotes ainda nao foram ingeridos,
para nao quebrar em ambiente limpo (CI sem os dados do cliente).
"""

import pytest
from sqlalchemy import func, select, text

from inspecao.db import SessionLocal, engine
from inspecao.models import Foto, Leitura, LeituraMedidor, Lote, NotaOcorrencia

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


@pytest.fixture(scope="module")
def sessao():
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("banco indisponivel")

    with SessionLocal() as s:
        if s.scalar(select(func.count()).select_from(Lote)) != ESPERADO["lotes"]:
            pytest.skip("os 4 lotes nao estao ingeridos")
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
    """Identificadores como F50081 nao podem ter virado inteiro nem sumido."""
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
