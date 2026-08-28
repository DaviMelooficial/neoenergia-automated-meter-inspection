"""Aplicacao FastAPI."""

import logging

from fastapi import FastAPI
from sqlalchemy import text

from inspecao.config import get_settings
from inspecao.db import engine
from inspecao.storage.local import LocalFileStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("inspecao")

settings = get_settings()

app = FastAPI(
    title="Inspecao de Fotos de Leitura",
    description="Verificacao automatizada de fotos de leitura, Neoenergia Pernambuco",
    version="0.1.0",
    root_path="/api",
)


def _checar_banco() -> tuple[bool, str | None]:
    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
        return True, None
    except Exception as erro:
        return False, str(erro)[:200]


def _checar_storage() -> tuple[bool, str | None]:
    try:
        return LocalFileStorage().verificar_saude(), None
    except Exception as erro:
        return False, str(erro)[:200]


@app.get("/health", tags=["infra"])
def health() -> dict:
    """Checa banco e storage. Usado pelo healthcheck do compose e pelo gate M0."""
    banco_ok, banco_erro = _checar_banco()
    storage_ok, storage_erro = _checar_storage()

    modelo_ok = True
    modelo_detalhe = "desativado"
    if settings.modelo_qualidade_ativo:
        caminho = settings.caminho_absoluto(settings.modelo_qualidade_path)
        modelo_ok = caminho.is_file()
        modelo_detalhe = str(caminho) if modelo_ok else f"ausente: {caminho}"

    return {
        "status": "ok" if (banco_ok and storage_ok and modelo_ok) else "degradado",
        "componentes": {
            "banco": {"ok": banco_ok, "erro": banco_erro},
            "storage": {
                "ok": storage_ok,
                "erro": storage_erro,
                "backend": settings.storage_backend,
            },
            "modelo_qualidade": {"ok": modelo_ok, "detalhe": modelo_detalhe},
        },
    }
