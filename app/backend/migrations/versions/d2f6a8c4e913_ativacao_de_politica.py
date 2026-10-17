"""ativacoes_politica: o falso positivo de cada politica so mede lotes posteriores a ela

Revision ID: d2f6a8c4e913
Revises: b5d8f3a1c726
Create Date: 2026-10-17 21:10:00.000000

Uma linha por politica, gravada na primeira decisao automatica dela
(`estados.transicionar`), com os nomes dos lotes que ja existiam: esses lotes contem a
calibracao e o treino da politica e nunca entram no estrato de falso positivo dela
(`auditoria._estratos`).

Retroativo: toda politica que ja tem decisao automatica no historico recebe a ativacao com
`ativada_em` e `historico_id` da primeira decisao dela, e `lotes_anteriores` = TODOS os
lotes do banco no momento da migracao (e nao so os ingeridos antes daquela decisao). E o
lado conservador: um lote de calibracao reingerido depois da primeira decisao tem
`ingerido_em` novo e passaria por posterior. No banco do projeto os 4 lotes sao a
calibracao da v1 (a inspecao do M5 sorteou nos 4) e o treino e a calibracao da v2.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd2f6a8c4e913'
down_revision: str | None = 'b5d8f3a1c726'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTOMATICAS = "('rejeitada_qualidade', 'auto_rejeitada', 'auto_aprovada')"


def upgrade() -> None:
    op.create_table('ativacoes_politica',
    sa.Column('politica', sa.String(length=40), nullable=False),
    sa.Column('ativada_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('historico_id', sa.BigInteger(), nullable=True),
    sa.Column('lotes_anteriores', postgresql.ARRAY(sa.String(length=120)), nullable=False),
    sa.ForeignKeyConstraint(['historico_id'], ['historico_status.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('politica'),
    )
    op.execute(
        f"""
        INSERT INTO ativacoes_politica (politica, ativada_em, historico_id, lotes_anteriores)
        SELECT DISTINCT ON (h.detalhe->>'politica')
               h.detalhe->>'politica', h.criado_em, h.id,
               COALESCE((SELECT array_agg(nome ORDER BY nome) FROM lotes), '{{}}')
          FROM historico_status h
         WHERE h.status IN {AUTOMATICAS}
           AND h.ator LIKE 'modelo:%'
           AND h.detalhe->>'politica' IS NOT NULL
         ORDER BY h.detalhe->>'politica', h.id
        """
    )


def downgrade() -> None:
    op.drop_table('ativacoes_politica')
