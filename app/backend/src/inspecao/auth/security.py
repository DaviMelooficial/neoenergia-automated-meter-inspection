"""Primitivas de seguranca: hash de senha, JWT de acesso e refresh token opaco.

Nenhuma regra de negocio aqui. O servico (`auth.service`) decide quando chamar.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt

from inspecao.config import get_settings

log = logging.getLogger(__name__)
_hasher = PasswordHasher()  # argon2id com os parametros padrao da biblioteca


def hash_senha(senha: str) -> str:
    return _hasher.hash(senha)


def verificar_senha(senha: str, senha_hash: str) -> bool:
    """Tempo constante por construcao da biblioteca."""
    try:
        return _hasher.verify(senha_hash, senha)
    except VerifyMismatchError:
        return False
    except Exception as erro:
        # hash corrompido ou de outro algoritmo: trata como invalido, nunca explode
        log.warning("senha_hash ilegivel no banco: %s", type(erro).__name__)
        return False


# Hash de uma senha aleatoria, usado para gastar o mesmo tempo quando o email
# nao existe. Sem isso, o tempo de resposta revelaria quais emails existem.
_HASH_FANTASMA = hash_senha(secrets.token_urlsafe(16))


def verificar_senha_fantasma(senha: str) -> None:
    verificar_senha(senha, _HASH_FANTASMA)


class TokenInvalido(ValueError):
    pass


def emitir_access_token(usuario_id: int, papel: str) -> tuple[str, int]:
    """Devolve (token, segundos ate expirar)."""
    settings = get_settings()
    agora = datetime.now(UTC)
    expira = agora + timedelta(minutes=settings.access_token_minutes)
    claims = {
        "sub": str(usuario_id),
        "papel": papel,
        "iat": int(agora.timestamp()),
        "exp": int(expira.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_alg)
    return token, settings.access_token_minutes * 60


def decodificar_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError as erro:
        raise TokenInvalido(str(erro)) from erro
    if "sub" not in claims or "papel" not in claims:
        raise TokenInvalido("claims obrigatorias ausentes")
    return claims


def gerar_refresh_token() -> tuple[str, str]:
    """Devolve (valor opaco para o cookie, sha256 para o banco)."""
    valor = secrets.token_urlsafe(32)
    return valor, hash_refresh(valor)


def hash_refresh(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def expiracao_refresh() -> datetime:
    return datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)
