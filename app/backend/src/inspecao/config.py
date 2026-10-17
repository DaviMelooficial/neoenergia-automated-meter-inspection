"""Configuracao da aplicacao, carregada de variaveis de ambiente."""

import os
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import model_validator
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

# Valor do .env.example: serve em dev, e recusado fora de dev como os demais exemplos.
SALT_ROTULAGEM_EXEMPLO = "9b846d587d76561b49133bcbd9da8808"
# Usado em dev com ROTULAGEM_SALT vazio. Publico (esta no codigo): recusado fora de dev.
SALT_ROTULAGEM_DEV = "rotulagem-dev-sem-salt"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(RAIZ_REPO / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # dev | producao. Em producao os placeholders de segredo derrubam a subida.
    ambiente: str = "dev"

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
    # Aceitar X-Forwarded-For so quando ha um proxy confiavel na frente (nginx do
    # compose). Sem isso qualquer cliente forjaria o IP do rate limit e do log.
    confiar_x_forwarded_for: bool = False
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    login_max_tentativas: int = 5
    login_bloqueio_minutos: int = 15
    admin_senha_inicial: str = "trocar_no_primeiro_login"
    admin_email: str = "admin@exemplo.com.br"
    # Cookie do refresh token. Navegadores aceitam Secure em http://localhost;
    # so desligue em ambiente onde isso nao vale.
    cookie_secure: bool = True

    # Rotulagem (M6). Salt secreto da ordem de entrega do /rotulagem/proxima: sem ele,
    # md5(foto:usuario) com ids sequenciais deixaria um rotulador calcular a ordem do
    # outro. Vazio so em dev (cai num valor fixo); fora de dev a API nao sobe.
    rotulagem_salt: str = ""

    # Auditoria amostral (M8): fracao dos itens decididos na fila humana que vai para
    # um segundo revisor. O plano pede de 5% a 10%; fora disso a API nao sobe.
    auditoria_taxa: float = 0.07

    # Drift (M8): lotes que formam a baseline dos 5 sinais. Padrao: os 3 lotes de maio
    # (treino e validacao do split temporal, plano 04 secao 3.2); julho e o teste.
    drift_lotes_baseline: list[str] = [
        "PSP_EXTRATLEITIMPL_200526_0352",
        "PSP_EXTRATLEITIMPL_210526_0335",
        "PSP_EXTRATLEITIMPL_220526_0408",
    ]

    # Workers
    worker_concurrency: int = 4
    worker_poll_segundos: int = 2
    worker_max_tentativas: int = 3

    # Modelos. DETECTOR_QUALIDADE escolhe o detector do worker enquanto nao ha
    # modelo treinado: "heuristica" (M5, padrao) ou "nulo" (tudo para humano).
    detector_qualidade: str = "heuristica"
    modelo_qualidade_path: Path = Path("./ml/artifacts/qualidade.onnx")
    modelo_qualidade_ativo: bool = False

    PLACEHOLDERS_INSEGUROS: ClassVar[frozenset[str]] = frozenset(
        {
            "trocar_em_producao",
            "trocar_em_producao_use_openssl_rand_hex_32",
            "trocar_no_primeiro_login",
            SALT_ROTULAGEM_EXEMPLO,
            SALT_ROTULAGEM_DEV,
        }
    )

    @model_validator(mode="after")
    def _proibir_placeholders_fora_de_dev(self) -> "Settings":
        if self.jwt_alg not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALG precisa ser HS256, HS384 ou HS512")
        if not 0.05 <= self.auditoria_taxa <= 0.10:
            raise ValueError("AUDITORIA_TAXA precisa estar entre 0.05 e 0.10 (5% a 10%)")
        if self.ambiente != "dev":
            inseguros = [
                nome
                for nome, valor in (
                    ("JWT_SECRET", self.jwt_secret),
                    ("ADMIN_SENHA_INICIAL", self.admin_senha_inicial),
                    ("ROTULAGEM_SALT", self.rotulagem_salt),
                )
                if valor in self.PLACEHOLDERS_INSEGUROS or len(valor) < 16
            ]
            if inseguros:
                raise ValueError(
                    f"AMBIENTE={self.ambiente}: defina valores fortes para {', '.join(inseguros)}"
                )
        return self

    def salt_da_rotulagem(self) -> str:
        return self.rotulagem_salt or SALT_ROTULAGEM_DEV

    def caminho_absoluto(self, caminho: Path) -> Path:
        """Resolve um caminho relativo contra a raiz do repositorio."""
        return caminho if caminho.is_absolute() else (RAIZ_REPO / caminho).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
