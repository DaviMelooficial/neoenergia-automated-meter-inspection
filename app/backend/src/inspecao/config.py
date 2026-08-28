"""Configuracao da aplicacao, carregada de variaveis de ambiente."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Marcadores que identificam a raiz do projeto ao subir a arvore de diretorios.
_MARCADORES_RAIZ = ("docker-compose.yml", ".git")


def _descobrir_raiz() -> Path:
    """Resolve a raiz do projeto.

    No host, o codigo vive em app/backend/src/inspecao/ e a raiz esta 4 niveis
    acima. No container, vive em /app/src/inspecao/ e essa contagem nao existe.
    Por isso a raiz e descoberta por marcador, com override explicito por
    APP_ROOT e fallback para o diretorio de trabalho.
    """
    if raiz_env := os.getenv("APP_ROOT"):
        return Path(raiz_env).resolve()

    for pai in Path(__file__).resolve().parents:
        if any((pai / marcador).exists() for marcador in _MARCADORES_RAIZ):
            return pai

    return Path.cwd().resolve()


RAIZ_REPO = _descobrir_raiz()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(RAIZ_REPO / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Banco
    database_url: str = "postgresql+psycopg://inspecao:inspecao_dev@localhost:5432/inspecao"

    # Armazenamento
    storage_backend: str = "local"
    storage_local_root: Path = Path("./data/storage")

    # Ingestao
    ingest_source: str = "batch_folder"
    ingest_staging_dir: Path = Path("./data/staging")
    ingest_notas_xlsx: Path = Path(
        "./Base cliente/DESCRIÇÃO NOTAS LEITURISTAS X SOLICITAÇÃO DE FOTO.xlsx"
    )

    # Autenticacao
    auth_provider: str = "local_password"
    jwt_secret: str = "trocar_em_producao"
    jwt_alg: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    login_max_tentativas: int = 5
    login_bloqueio_minutos: int = 15
    admin_senha_inicial: str = "trocar_no_primeiro_login"

    # Workers
    worker_concurrency: int = 4
    worker_poll_segundos: int = 2
    worker_max_tentativas: int = 3

    # Modelos
    modelo_qualidade_path: Path = Path("./ml/artifacts/qualidade.onnx")
    modelo_qualidade_ativo: bool = False

    def caminho_absoluto(self, caminho: Path) -> Path:
        """Resolve um caminho relativo contra a raiz do repositorio."""
        return caminho if caminho.is_absolute() else (RAIZ_REPO / caminho).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
