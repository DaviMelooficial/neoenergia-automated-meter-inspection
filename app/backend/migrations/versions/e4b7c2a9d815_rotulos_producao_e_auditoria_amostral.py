"""rotulos de producao (active learning) e auditoria amostral (marco M8)

Revision ID: e4b7c2a9d815
Revises: c3e1a7d9f204
Create Date: 2026-10-17 10:00:00.000000

1. `rotulos`: a decisao do auditor nao declara cena nem todos os defeitos, entao na
   rodada `producao` esses campos podem ser nulos. Fora de `producao` a CHECK
   `ck_rotulos_completo` mantem o esquema v2 completo, como antes.
   `revisao_id` e `auditoria_id` ligam o rotulo de producao a decisao que o gerou
   (exatamente um dos dois em `producao`, nenhum nas outras rodadas).
2. `auditorias_amostrais`: item decidido sorteado para um segundo revisor. A
   regra "segundo revisor diferente do primeiro" vive tambem no banco
   (`ck_auditorias_segundo_revisor`), e nao so no endpoint.
   A auditoria confere uma DECISAO, nao uma leitura: `fila` aponta a revisao do
   auditor (`revisao_id`, unico) e `auto_aprovada`/`auto_rejeitada` apontam a linha do
   historico em que a maquina decidiu (`historico_id`, unico). Uma leitura reaberta pela
   maquina (recalcular, troca de detector) pode ter varias auditorias, uma por decisao;
   a pendente da decisao antiga ganha `invalidada_em` e sai das medidas, a decidida fica.
   Decisao gravada nao muda: o gatilho `trg_auditorias_decisao_imutavel` recusa UPDATE
   de revisor, decisao, motivo, observacao, data, leitura, revisao, historico, revisor
   original, origem e codigo depois de decidida. `codigo` (uuid aleatorio) e o
   identificador exposto ao revisor: o `id` sequencial, sorteado em rajada na triagem,
   diria a origem. DELETE fica
   permitido: a auditoria so some junto com a leitura (cascata de reingestao com
   `--reprocessar`), como a revisao humana que ela confere.
3. Backfill: cada revisao humana ja gravada vira um rotulo `producao`, com o mesmo
   mapeamento de `inspecao.rotulagem.producao` (escrito aqui em SQL para a migracao
   nao depender do codigo de hoje). Revisoes de contas de teste (dominios de
   `inspecao.contas_teste`, copiados aqui) ficam de fora.

Downgrade: apaga os rotulos `producao` (eles tem campos nulos, que o esquema
anterior nao aceita) e a tabela de auditoria. Os rotulos de revisao voltam no
proximo upgrade pelo backfill; os de auditoria, nao.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'e4b7c2a9d815'
down_revision: str | None = 'c3e1a7d9f204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAMPOS_V2 = ('cena', 'reflexo', 'fora_de_foco', 'tampa_suja', 'enquadramento', 'display_apagado', 'legivel')
MOTIVOS = "'fora_de_foco', 'glare', 'tampa_suja', 'enquadramento_incorreto', 'display_apagado', 'outro'"

BACKFILL = """
INSERT INTO rotulos (foto_id, usuario_id, rodada, cena, reflexo, fora_de_foco, tampa_suja,
                     enquadramento, display_apagado, legivel, revisao_id, gravado_em)
SELECT DISTINCT ON (f.id, r.usuario_id)
       f.id, r.usuario_id, 'producao', NULL,
       CASE WHEN r.decisao = 'reprovada' AND r.motivo = 'glare' THEN true END,
       CASE WHEN r.decisao = 'reprovada' AND r.motivo = 'fora_de_foco' THEN true END,
       CASE WHEN r.decisao = 'reprovada' AND r.motivo = 'tampa_suja' THEN true END,
       CASE WHEN r.decisao = 'reprovada' AND r.motivo = 'enquadramento_incorreto' THEN true END,
       CASE WHEN r.decisao = 'reprovada' AND r.motivo = 'display_apagado' THEN true END,
       CASE WHEN r.decisao = 'aprovada' THEN true
            WHEN r.decisao = 'reprovada' AND r.motivo <> 'outro' THEN false END,
       r.id, r.criado_em
FROM revisoes_humanas r
JOIN fotos f ON f.leitura_id = r.leitura_id AND f.principal
JOIN usuarios u ON u.id = r.usuario_id
WHERE u.email NOT ILIKE '%@exemplo.com.br' AND u.email NOT ILIKE '%@teste.com.br'
ORDER BY f.id, r.usuario_id, r.id
"""

GATILHO = """
CREATE FUNCTION auditorias_decisao_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.decisao IS NOT NULL AND (
        NEW.revisor_id IS DISTINCT FROM OLD.revisor_id
        OR NEW.decisao IS DISTINCT FROM OLD.decisao
        OR NEW.motivo IS DISTINCT FROM OLD.motivo
        OR NEW.observacao IS DISTINCT FROM OLD.observacao
        OR NEW.decidida_em IS DISTINCT FROM OLD.decidida_em
        OR NEW.leitura_id IS DISTINCT FROM OLD.leitura_id
        OR NEW.revisao_id IS DISTINCT FROM OLD.revisao_id
        OR NEW.historico_id IS DISTINCT FROM OLD.historico_id
        OR NEW.codigo IS DISTINCT FROM OLD.codigo
        OR NEW.revisor_original_id IS DISTINCT FROM OLD.revisor_original_id
        OR NEW.origem IS DISTINCT FROM OLD.origem
    ) THEN
        RAISE EXCEPTION 'auditoria % ja decidida: decisao imutavel', OLD.id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_auditorias_decisao_imutavel
    BEFORE UPDATE ON auditorias_amostrais
    FOR EACH ROW EXECUTE FUNCTION auditorias_decisao_imutavel();
"""


def upgrade() -> None:
    for campo in CAMPOS_V2:
        op.alter_column('rotulos', campo, nullable=True)
    op.add_column('rotulos', sa.Column('revisao_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_rotulos_revisao', 'rotulos', 'revisoes_humanas', ['revisao_id'], ['id'], ondelete='CASCADE')

    op.create_table('auditorias_amostrais',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('codigo', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('leitura_id', sa.BigInteger(), nullable=False),
    sa.Column('origem', sa.String(length=20), server_default='fila', nullable=False),
    sa.Column('revisao_id', sa.BigInteger(), nullable=True),
    sa.Column('historico_id', sa.BigInteger(), nullable=True),
    sa.Column('revisor_original_id', sa.Integer(), nullable=True),
    sa.Column('invalidada_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sorteada_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('reservado_por', sa.Integer(), nullable=True),
    sa.Column('reservado_ate', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revisor_id', sa.Integer(), nullable=True),
    sa.Column('decisao', sa.String(length=20), nullable=True),
    sa.Column('motivo', sa.String(length=40), nullable=True),
    sa.Column('observacao', sa.Text(), nullable=True),
    sa.Column('decidida_em', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("CASE WHEN origem = 'fila' THEN revisao_id IS NOT NULL AND revisor_original_id IS NOT NULL AND historico_id IS NULL WHEN origem IN ('auto_aprovada', 'auto_rejeitada') THEN historico_id IS NOT NULL AND revisao_id IS NULL AND revisor_original_id IS NULL ELSE false END", name='ck_auditorias_origem'),
    sa.CheckConstraint('invalidada_em IS NULL OR decisao IS NULL', name='ck_auditorias_invalidada_pendente'),
    sa.CheckConstraint('revisor_id IS NULL OR revisor_id <> revisor_original_id', name='ck_auditorias_segundo_revisor'),
    sa.CheckConstraint('reservado_por IS NULL OR reservado_por <> revisor_original_id', name='ck_auditorias_reserva_de_outro'),
    sa.CheckConstraint('(revisor_id IS NULL) = (decisao IS NULL) AND (decisao IS NULL) = (decidida_em IS NULL)', name='ck_auditorias_decisao_completa'),
    sa.CheckConstraint("decisao IS NULL OR decisao IN ('aprovada', 'reprovada')", name='ck_auditorias_decisao'),
    sa.CheckConstraint(f'motivo IS NULL OR motivo IN ({MOTIVOS})', name='ck_auditorias_motivo'),
    sa.CheckConstraint("decisao IS DISTINCT FROM 'reprovada' OR motivo IS NOT NULL", name='ck_auditorias_reprovada_com_motivo'),
    sa.CheckConstraint("decisao IS DISTINCT FROM 'aprovada' OR motivo IS NULL", name='ck_auditorias_aprovada_sem_motivo'),
    sa.ForeignKeyConstraint(['leitura_id'], ['leituras.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['revisao_id'], ['revisoes_humanas.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['historico_id'], ['historico_status.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['revisor_original_id'], ['usuarios.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reservado_por'], ['usuarios.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['revisor_id'], ['usuarios.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('revisao_id', name='uq_auditorias_revisao'),
    sa.UniqueConstraint('historico_id', name='uq_auditorias_historico'),
    sa.UniqueConstraint('codigo', name='uq_auditorias_codigo'),
    )
    op.create_index('ix_auditorias_leitura', 'auditorias_amostrais', ['leitura_id'], unique=False)
    op.create_index('ix_auditorias_pendentes', 'auditorias_amostrais', ['sorteada_em', 'id'], unique=False, postgresql_where='decisao IS NULL AND invalidada_em IS NULL')

    op.add_column('rotulos', sa.Column('auditoria_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_rotulos_auditoria', 'rotulos', 'auditorias_amostrais', ['auditoria_id'], ['id'], ondelete='CASCADE')
    completos = ' AND '.join(f'{c} IS NOT NULL' for c in CAMPOS_V2)
    op.create_check_constraint('ck_rotulos_completo', 'rotulos', f"rodada = 'producao' OR ({completos})")
    op.create_check_constraint(
        'ck_rotulos_origem_producao',
        'rotulos',
        "CASE WHEN rodada = 'producao' THEN (revisao_id IS NULL) <> (auditoria_id IS NULL) "
        "ELSE revisao_id IS NULL AND auditoria_id IS NULL END",
    )
    op.create_index('uq_rotulos_revisao', 'rotulos', ['revisao_id'], unique=True, postgresql_where='revisao_id IS NOT NULL')
    op.create_index('uq_rotulos_auditoria', 'rotulos', ['auditoria_id'], unique=True, postgresql_where='auditoria_id IS NOT NULL')

    op.execute(GATILHO)
    op.execute(BACKFILL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_auditorias_decisao_imutavel ON auditorias_amostrais")
    op.execute("DROP FUNCTION IF EXISTS auditorias_decisao_imutavel()")
    op.execute("DELETE FROM rotulos WHERE rodada = 'producao'")
    op.drop_index('uq_rotulos_auditoria', table_name='rotulos', postgresql_where='auditoria_id IS NOT NULL')
    op.drop_index('uq_rotulos_revisao', table_name='rotulos', postgresql_where='revisao_id IS NOT NULL')
    op.drop_constraint('ck_rotulos_origem_producao', 'rotulos', type_='check')
    op.drop_constraint('ck_rotulos_completo', 'rotulos', type_='check')
    op.drop_constraint('fk_rotulos_auditoria', 'rotulos', type_='foreignkey')
    op.drop_column('rotulos', 'auditoria_id')
    # Os indices saem com a tabela. Sem drop_index explicito, o downgrade tambem funciona
    # num banco que aplicou uma versao anterior desta migracao (editada antes do merge).
    op.drop_table('auditorias_amostrais')
    op.drop_constraint('fk_rotulos_revisao', 'rotulos', type_='foreignkey')
    op.drop_column('rotulos', 'revisao_id')
    for campo in CAMPOS_V2:
        op.alter_column('rotulos', campo, nullable=False)
