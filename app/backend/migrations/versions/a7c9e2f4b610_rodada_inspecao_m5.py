"""rodada `inspecao_m5`: a inspecao das 200 fotos dos extremos do M5 vira rotulo

Revision ID: a7c9e2f4b610
Revises: e4b7c2a9d815
Create Date: 2026-10-17 18:00:00.000000

A inspecao humana das 200 fotos (`ml/reports/heuristicas_amostra_200.csv`) calibrou o
limiar 0,85 da `politica@v1`: as fotos dela sao calibracao e nao podem entrar na medida de
falso positivo da auto-rejeicao. Para a exclusao enxerga-las, elas entram em `rotulos` com
rodada propria, `inspecao_m5`, pelo importador `inspecao.rotulagem.cli importar-inspecao-m5`.
A inspecao so declara legibilidade: cena e defeitos ficam nulos, como em `producao`, e
`legivel` fica nulo enquanto a foto nao foi inspecionada.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'a7c9e2f4b610'
down_revision: str | None = 'e4b7c2a9d815'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAMPOS_V2 = ('cena', 'reflexo', 'fora_de_foco', 'tampa_suja', 'enquadramento', 'display_apagado', 'legivel')
COMPLETOS = ' AND '.join(f'{c} IS NOT NULL' for c in CAMPOS_V2)


def upgrade() -> None:
    op.drop_constraint('ck_rotulos_rodada', 'rotulos', type_='check')
    op.create_check_constraint(
        'ck_rotulos_rodada', 'rotulos',
        "rodada IN ('lab02', 'a', 'b', 'desempate', 'producao', 'inspecao_m5')",
    )
    op.drop_constraint('ck_rotulos_completo', 'rotulos', type_='check')
    op.create_check_constraint(
        'ck_rotulos_completo', 'rotulos', f"rodada IN ('producao', 'inspecao_m5') OR ({COMPLETOS})"
    )


def downgrade() -> None:
    op.execute("DELETE FROM rotulos WHERE rodada = 'inspecao_m5'")
    op.drop_constraint('ck_rotulos_completo', 'rotulos', type_='check')
    op.create_check_constraint('ck_rotulos_completo', 'rotulos', f"rodada = 'producao' OR ({COMPLETOS})")
    op.drop_constraint('ck_rotulos_rodada', 'rotulos', type_='check')
    op.create_check_constraint(
        'ck_rotulos_rodada', 'rotulos', "rodada IN ('lab02', 'a', 'b', 'desempate', 'producao')"
    )
