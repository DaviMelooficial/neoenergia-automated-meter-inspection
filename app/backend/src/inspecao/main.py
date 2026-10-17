"""Aplicacao FastAPI."""

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from inspecao.auth.service import garantir_admin
from inspecao.config import get_settings
from inspecao.db import SessionLocal, engine
from inspecao.routers import (
    auditoria,
    auth,
    fila_humana,
    metricas,
    operacao,
    quarentena,
    rotulagem,
    usuarios,
)
from inspecao.storage.local import LocalFileStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("inspecao")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Seed idempotente do admin. Se o banco estiver fora, a API sobe mesmo assim
    # e /health reporta o problema; nao vale derrubar o processo por isso.
    try:
        with SessionLocal() as sessao:
            garantir_admin(sessao)
            sessao.commit()
    except Exception as erro:  # pragma: no cover - depende do ambiente
        log.warning("seed do admin nao executado: %s", str(erro)[:200])
    yield


app = FastAPI(
    title="Inspecao de Fotos de Leitura",
    description="Verificacao automatizada de fotos de leitura, Neoenergia Pernambuco",
    version="0.3.0",
    root_path="/api",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_e_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    inicio = time.perf_counter()
    try:
        resposta = await call_next(request)
    except Exception:
        log.exception(
            "erro nao tratado request_id=%s %s %s", request_id, request.method, request.url.path
        )
        resposta = JSONResponse(
            status_code=500,
            content={"detail": "erro interno", "request_id": request_id},
        )
    duracao_ms = int((time.perf_counter() - inicio) * 1000)
    resposta.headers["X-Request-ID"] = request_id
    # Log estruturado em JSON: `docker compose logs` e o observability stack.
    log.info(
        json.dumps(
            {
                "request_id": request_id,
                "metodo": request.method,
                "rota": request.url.path,
                "status": resposta.status_code,
                "duracao_ms": duracao_ms,
                "usuario_id": getattr(request.state, "usuario_id", None),
            }
        )
    )
    return resposta


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


for roteador in (
    auth,
    operacao,
    fila_humana,
    auditoria,
    quarentena,
    metricas,
    rotulagem,
    usuarios,
):
    app.include_router(roteador.router)
