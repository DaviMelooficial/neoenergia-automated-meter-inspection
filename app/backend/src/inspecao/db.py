"""Engine, sessao e base declarativa do SQLAlchemy."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from inspecao.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # descarta conexao morta apos o container do banco reiniciar
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """Dependency do FastAPI: uma sessao por requisicao."""
    sessao = SessionLocal()
    try:
        yield sessao
    finally:
        sessao.close()
