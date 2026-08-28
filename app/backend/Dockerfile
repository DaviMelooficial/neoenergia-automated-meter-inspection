FROM python:3.12-slim

# uv para instalar as dependencias a partir do lock
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Camada de dependencias, cacheada enquanto o lock nao muda
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Codigo
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "inspecao.main:app", "--host", "0.0.0.0", "--port", "8000"]
