"""rotulos no esquema v2 (cena, 5 defeitos, legivel)

Revision ID: c3e1a7d9f204
Revises: 1840bea5fdb9
Create Date: 2026-10-16 15:00:00.000000

Os valores das CHECK ficam escritos aqui, e nao importados de `inspecao.enums`:
a migracao precisa continuar reproduzindo o esquema de hoje mesmo que o enum
mude depois (essa mudanca entra numa migracao nova).
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'c3e1a7d9f204'
down_revision: str | None = '1840bea5fdb9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('rotulos',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('foto_id', sa.BigInteger(), nullable=False),
    sa.Column('usuario_id', sa.Integer(), nullable=True),
    sa.Column('rotulador_externo', sa.String(length=60), nullable=True),
    sa.Column('rodada', sa.String(length=20), nullable=False),
    sa.Column('cena', sa.String(length=20), nullable=False),
    sa.Column('reflexo', sa.Boolean(), nullable=False),
    sa.Column('fora_de_foco', sa.Boolean(), nullable=False),
    sa.Column('tampa_suja', sa.Boolean(), nullable=False),
    sa.Column('enquadramento', sa.Boolean(), nullable=False),
    sa.Column('display_apagado', sa.Boolean(), nullable=False),
    sa.Column('legivel', sa.Boolean(), nullable=False),
    sa.Column('segundos_na_foto', sa.Float(), nullable=True),
    sa.Column('controle', sa.Boolean(), nullable=True),
    sa.Column('gravado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(usuario_id IS NULL) <> (rotulador_externo IS NULL)', name='ck_rotulos_um_rotulador'),
    sa.CheckConstraint("rodada IN ('lab02', 'a', 'b', 'desempate', 'producao')", name='ck_rotulos_rodada'),
    sa.CheckConstraint("cena IN ('digital', 'eletromecanico', 'ocorrencia', 'nao_medidor')", name='ck_rotulos_cena'),
    sa.ForeignKeyConstraint(['foto_id'], ['fotos.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_rotulos_rodada_foto', 'rotulos', ['rodada', 'foto_id'], unique=False)
    op.create_index(op.f('ix_rotulos_usuario_id'), 'rotulos', ['usuario_id'], unique=False)
    op.create_index('uq_rotulos_foto_rodada_externo', 'rotulos', ['foto_id', 'rodada', 'rotulador_externo'], unique=True, postgresql_where='rotulador_externo IS NOT NULL')
    op.create_index('uq_rotulos_foto_rodada_usuario', 'rotulos', ['foto_id', 'rodada', 'usuario_id'], unique=True, postgresql_where='usuario_id IS NOT NULL')
    op.create_index('uq_rotulos_foto_usuario_dupla_cega', 'rotulos', ['foto_id', 'usuario_id'], unique=True, postgresql_where="usuario_id IS NOT NULL AND rodada IN ('a', 'b', 'desempate')")


def downgrade() -> None:
    op.drop_index('uq_rotulos_foto_usuario_dupla_cega', table_name='rotulos', postgresql_where="usuario_id IS NOT NULL AND rodada IN ('a', 'b', 'desempate')")
    op.drop_index('uq_rotulos_foto_rodada_usuario', table_name='rotulos', postgresql_where='usuario_id IS NOT NULL')
    op.drop_index('uq_rotulos_foto_rodada_externo', table_name='rotulos', postgresql_where='rotulador_externo IS NOT NULL')
    op.drop_index(op.f('ix_rotulos_usuario_id'), table_name='rotulos')
    op.drop_index('ix_rotulos_rodada_foto', table_name='rotulos')
    op.drop_table('rotulos')
