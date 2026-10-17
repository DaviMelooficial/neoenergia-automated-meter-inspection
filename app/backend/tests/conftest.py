"""Infraestrutura de testes.

Banco de teste separado (`inspecao_test`, ou o nome em INSPECAO_TEST_DB), recriado
a cada sessao e migrado pelo Alembic, para que os testes tambem exercitem as migracoes. O banco de
desenvolvimento (com os 4 lotes reais) so e tocado pelo teste de regressao de
dados, via `engine_dados_reais`.

A URL do banco de teste e injetada em DATABASE_URL ANTES de qualquer import de
`inspecao.db`, que cria o engine na importacao.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from inspecao.config import get_settings

RAIZ_BACKEND = Path(__file__).resolve().parents[1]

URL_DADOS_REAIS = make_url(get_settings().database_url)
# Cada sessao de pytest apaga e recria este banco: duas suites no mesmo servidor
# (worktrees em paralelo) precisam de nomes diferentes.
URL_TESTE = URL_DADOS_REAIS.set(database=os.getenv("INSPECAO_TEST_DB", "inspecao_test"))
URL_MANUTENCAO = URL_DADOS_REAIS.set(database="postgres")

os.environ["DATABASE_URL"] = URL_TESTE.render_as_string(hide_password=False)
get_settings.cache_clear()


def _servidor_disponivel() -> bool:
    try:
        motor = create_engine(URL_MANUTENCAO, poolclass=None)
        with motor.connect() as c:
            c.execute(text("SELECT 1"))
        motor.dispose()
        return True
    except Exception:
        return False


SERVIDOR_OK = _servidor_disponivel()
if not SERVIDOR_OK and os.getenv("CI"):
    # Em CI a suite sem banco nao pode "passar" pulando quase tudo.
    raise RuntimeError("CI exige um servidor Postgres acessivel para os testes")


def _recriar_banco_teste() -> None:
    motor = create_engine(URL_MANUTENCAO, isolation_level="AUTOCOMMIT")
    with motor.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{URL_TESTE.database}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{URL_TESTE.database}"'))
    motor.dispose()


def _migrar_banco_teste() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAIZ_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ_BACKEND / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def banco_teste() -> Iterator[None]:
    if not SERVIDOR_OK:
        yield
        return
    _recriar_banco_teste()
    _migrar_banco_teste()
    yield
    from inspecao.db import engine

    engine.dispose()
    motor = create_engine(URL_MANUTENCAO, isolation_level="AUTOCOMMIT")
    with motor.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{URL_TESTE.database}" WITH (FORCE)'))
    motor.dispose()


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Sessao em transacao externa revertida ao final. `commit()` dentro dos
    routers vira release de savepoint, entao nada persiste entre testes."""
    if not SERVIDOR_OK:
        pytest.skip("servidor postgres indisponivel")
    from inspecao.db import engine

    conexao = engine.connect()
    transacao = conexao.begin()
    sessao = Session(bind=conexao, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield sessao
    finally:
        sessao.close()
        transacao.rollback()
        conexao.close()


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    from inspecao.db import get_db
    from inspecao.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    # Sem `with`: o lifespan faria o seed do admin fora da transacao do teste.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def contas(db_session):
    """Um usuario de cada papel (e um segundo auditor), na transacao do teste."""
    from tests.usuarios_fixture import criar_contas

    return criar_contas(db_session)


@pytest.fixture(scope="session")
def engine_dados_reais() -> Iterator[Engine]:
    """Banco de desenvolvimento com os 4 lotes reais. So o teste de regressao usa."""
    if not SERVIDOR_OK:
        pytest.skip("servidor postgres indisponivel")
    motor = create_engine(URL_DADOS_REAIS, pool_pre_ping=True)
    yield motor
    motor.dispose()
