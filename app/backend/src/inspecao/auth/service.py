"""Regras de autenticacao: login, refresh com rotacao, logout, senha, seed.

Fica atras do Protocol `AuthProvider`. Hoje so existe `LocalPasswordAuthProvider`;
um `OIDCAuthProvider` futuro implementa `autenticar` e o resto (tokens, log,
rate limit) continua igual.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from inspecao.auth import security
from inspecao.config import get_settings
from inspecao.enums import EventoAcesso, Papel
from inspecao.models import LogAcesso, RefreshToken, Usuario

log = logging.getLogger(__name__)


class ErroAutenticacao(Exception):
    """Credenciais invalidas. A mensagem e sempre a mesma: nao enumerar usuarios."""

    mensagem = "credenciais invalidas"


class LoginBloqueado(Exception):
    """Excesso de tentativas falhas para o email ou o IP."""


class RefreshInvalido(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Origem:
    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class Sessao:
    usuario: Usuario
    access_token: str
    expires_in: int
    refresh_token: str  # valor opaco, vai para o cookie
    registro_refresh: RefreshToken


def normalizar_email(email: str) -> str:
    return email.strip().lower()


def registrar_acesso(
    sessao: Session,
    evento: EventoAcesso,
    origem: Origem,
    usuario_id: int | None = None,
    identificador: str | None = None,
) -> None:
    sessao.add(
        LogAcesso(
            usuario_id=usuario_id,
            identificador=identificador,
            evento=evento.value,
            ip_origem=origem.ip,
            user_agent=(origem.user_agent or "")[:300] or None,
        )
    )


# ------------------------------------------------------------------ provider


class AuthProvider(Protocol):
    def autenticar(self, sessao: Session, email: str, senha: str) -> Usuario | None: ...


class LocalPasswordAuthProvider:
    def autenticar(self, sessao: Session, email: str, senha: str) -> Usuario | None:
        usuario = sessao.scalar(select(Usuario).where(Usuario.email == email))
        if usuario is None or not usuario.ativo:
            security.verificar_senha_fantasma(senha)  # mesmo custo de tempo
            return None
        if not security.verificar_senha(senha, usuario.senha_hash):
            return None
        return usuario


PROVIDER: AuthProvider = LocalPasswordAuthProvider()


# ---------------------------------------------------------------- rate limit


def _falhas_recentes(sessao: Session, coluna, valor: str | None, minutos: int) -> int:
    if not valor:
        return 0
    desde = datetime.now(UTC) - timedelta(minutes=minutos)
    return sessao.scalar(
        select(func.count())
        .select_from(LogAcesso)
        .where(
            LogAcesso.evento == EventoAcesso.LOGIN_FALHO.value,
            coluna == valor,
            LogAcesso.criado_em > desde,
        )
    )


def bloqueado(sessao: Session, email: str, origem: Origem) -> bool:
    s = get_settings()
    por_email = _falhas_recentes(sessao, LogAcesso.identificador, email, s.login_bloqueio_minutos)
    por_ip = _falhas_recentes(sessao, LogAcesso.ip_origem, origem.ip, s.login_bloqueio_minutos)
    # Por IP o limite e mais folgado: varios usuarios legitimos atras de um NAT.
    return por_email >= s.login_max_tentativas or por_ip >= s.login_max_tentativas * 4


# --------------------------------------------------------------------- login


def _abrir_sessao(sessao: Session, usuario: Usuario) -> Sessao:
    access, expires_in = security.emitir_access_token(usuario.id, usuario.papel)
    valor, digest = security.gerar_refresh_token()
    registro = RefreshToken(
        usuario_id=usuario.id, token_sha256=digest, expira_em=security.expiracao_refresh()
    )
    sessao.add(registro)
    sessao.flush()
    return Sessao(
        usuario=usuario,
        access_token=access,
        expires_in=expires_in,
        refresh_token=valor,
        registro_refresh=registro,
    )


def login(sessao: Session, email: str, senha: str, origem: Origem) -> Sessao:
    email = normalizar_email(email)
    if bloqueado(sessao, email, origem):
        registrar_acesso(sessao, EventoAcesso.LOGIN_BLOQUEADO, origem, identificador=email)
        raise LoginBloqueado()

    usuario = PROVIDER.autenticar(sessao, email, senha)
    if usuario is None:
        registrar_acesso(sessao, EventoAcesso.LOGIN_FALHO, origem, identificador=email)
        raise ErroAutenticacao()

    registrar_acesso(sessao, EventoAcesso.LOGIN, origem, usuario.id, email)
    return _abrir_sessao(sessao, usuario)


# ------------------------------------------------------------------- refresh


def revogar_tokens_do_usuario(sessao: Session, usuario_id: int) -> None:
    """Derruba todas as sessoes de refresh do usuario (reuso, desativacao, senha)."""
    sessao.execute(
        update(RefreshToken)
        .where(RefreshToken.usuario_id == usuario_id, RefreshToken.revogado.is_(False))
        .values(revogado=True, revogado_em=func.now())
    )


def refresh(sessao: Session, valor: str | None, origem: Origem) -> Sessao:
    """Rotaciona o refresh token. Reuso de token ja rotacionado revoga a cadeia."""
    if not valor:
        raise RefreshInvalido("sem refresh token")

    atual = sessao.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_sha256 == security.hash_refresh(valor))
        .with_for_update()
    )
    if atual is None:
        raise RefreshInvalido("desconhecido")

    if atual.revogado:
        # Alguem apresentou um token que ja foi trocado: ou o cliente legitimo
        # perdeu a resposta anterior, ou o token vazou. Nas duas hipoteses a
        # cadeia inteira cai e o usuario faz login de novo.
        revogar_tokens_do_usuario(sessao, atual.usuario_id)
        registrar_acesso(sessao, EventoAcesso.REFRESH_REUSO, origem, atual.usuario_id)
        raise RefreshInvalido("reuso detectado")

    if atual.expira_em <= datetime.now(UTC):
        atual.revogado = True
        atual.revogado_em = func.now()
        raise RefreshInvalido("expirado")

    usuario = sessao.get(Usuario, atual.usuario_id)
    if usuario is None or not usuario.ativo:
        revogar_tokens_do_usuario(sessao, atual.usuario_id)
        raise RefreshInvalido("usuario inativo")

    nova = _abrir_sessao(sessao, usuario)
    atual.revogado = True
    atual.revogado_em = func.now()
    atual.substituido_por = nova.registro_refresh.id
    registrar_acesso(sessao, EventoAcesso.REFRESH, origem, usuario.id, usuario.email)
    return nova


def logout(sessao: Session, valor: str | None, origem: Origem, usuario_id: int | None) -> None:
    if valor:
        sessao.execute(
            update(RefreshToken)
            .where(RefreshToken.token_sha256 == security.hash_refresh(valor))
            .values(revogado=True, revogado_em=func.now())
        )
    registrar_acesso(sessao, EventoAcesso.LOGOUT, origem, usuario_id)


# --------------------------------------------------------------------- senha


class SenhaFraca(ValueError):
    pass


def validar_forca(senha: str) -> None:
    if len(senha) < 10:
        raise SenhaFraca("senha precisa ter ao menos 10 caracteres")


def trocar_senha(
    sessao: Session, usuario: Usuario, senha_atual: str, senha_nova: str, origem: Origem
) -> None:
    if not security.verificar_senha(senha_atual, usuario.senha_hash):
        raise ErroAutenticacao()
    validar_forca(senha_nova)
    usuario.senha_hash = security.hash_senha(senha_nova)
    usuario.senha_alterada_em = datetime.now(UTC)
    # Trocar a senha derruba as outras sessoes: quem tinha o refresh antigo sai.
    revogar_tokens_do_usuario(sessao, usuario.id)
    registrar_acesso(sessao, EventoAcesso.SENHA_ALTERADA, origem, usuario.id, usuario.email)


# ---------------------------------------------------------------------- seed


def garantir_admin(sessao: Session) -> Usuario | None:
    """Cria o admin inicial se nao existir nenhum usuario. Idempotente.

    Desvio consciente do plano (que previa migracao de seed): uma migracao que
    depende de variavel de ambiente e fragil e nao reexecuta. Esta funcao roda
    na subida da API e em `make seed-admin`. `senha_alterada_em` fica nulo para
    forcar a troca no primeiro login.
    """
    if sessao.scalar(select(func.count()).select_from(Usuario)):
        return None
    s = get_settings()
    admin = Usuario(
        nome="Administrador",
        email=normalizar_email(s.admin_email),
        senha_hash=security.hash_senha(s.admin_senha_inicial),
        papel=Papel.ADMIN.value,
        ativo=True,
        senha_alterada_em=None,
    )
    sessao.add(admin)
    sessao.flush()
    log.warning("usuario admin inicial criado (%s): troque a senha no primeiro login", admin.email)
    return admin
