"""Contas de teste (E2E, demo, seed): decisao delas nao e auditoria e nao vira evidencia.

Mesma lista de `ml/src/mlinsp/relatorio_heuristicas.py` (DOMINIOS_DE_TESTE). No banco
local de 2026-10-16 eram as 55 decisoes da fila (auditor.e2e@exemplo.com.br e
demo@exemplo.com.br). Ficam fora do backfill de rotulos `producao`, do veredito humano,
da concordancia da auditoria, das contagens de aprovada/reprovada por humano e do
sorteio da auditoria amostral. A decisao continua gravada (a leitura sai da fila); so
nao conta como medida.
"""

from sqlalchemy import and_, exists, select
from sqlalchemy.sql.elements import ColumnElement

from inspecao.models import RevisaoHumana, Usuario

DOMINIOS_DE_TESTE: tuple[str, ...] = ("@exemplo.com.br", "@teste.com.br")


def email_de_teste(email: str) -> bool:
    return email.lower().endswith(DOMINIOS_DE_TESTE)


def email_real(coluna) -> ColumnElement[bool]:
    """Condicao SQL: o email nao termina em nenhum dominio de teste."""
    return and_(*[~coluna.ilike(f"%{d}") for d in DOMINIOS_DE_TESTE])


def usuario_real(coluna_usuario_id) -> ColumnElement[bool]:
    """Condicao SQL sobre uma coluna de id de usuario: a conta nao e de teste."""
    return exists().where(Usuario.id == coluna_usuario_id, email_real(Usuario.email))


def decidida_por_teste(coluna_leitura_id) -> ColumnElement[bool]:
    """Leitura com revisao humana feita por conta de teste."""
    return exists(
        select(RevisaoHumana.id)
        .join(Usuario, Usuario.id == RevisaoHumana.usuario_id)
        .where(RevisaoHumana.leitura_id == coluna_leitura_id, ~email_real(Usuario.email))
    )
