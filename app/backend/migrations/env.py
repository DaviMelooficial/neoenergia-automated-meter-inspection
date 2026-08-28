"""Ambiente do Alembic. A URL do banco vem sempre de DATABASE_URL, nunca do ini."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from inspecao.config import get_settings
from inspecao.db import Base

# Importar os modelos aqui garante que Base.metadata esteja populada no autogenerate.
# noqa: F401 porque o import existe pelo efeito colateral de registro.
try:
    from inspecao import models  # noqa: F401
except ImportError:
    # Ate o marco M1 ainda nao existe models.py. Migracao vazia funciona sem ele.
    pass

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    conectavel = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with conectavel.connect() as conexao:
        context.configure(connection=conexao, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
