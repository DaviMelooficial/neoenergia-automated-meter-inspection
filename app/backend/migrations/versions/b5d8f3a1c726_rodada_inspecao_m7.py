"""rodada `inspecao_m7`: a inspecao das 355 fotos que so a politica@v2 rejeita vira rotulo

Revision ID: b5d8f3a1c726
Revises: a7c9e2f4b610
Create Date: 2026-10-17 21:00:00.000000

A `politica@v2` (modelo `qualidade@v1`, M7) rejeita 355 fotos dos 4 lotes que a
`politica@v1` nao rejeita, e nenhuma delas foi vista por humano. A inspecao delas e o que
decide ativar o modelo: e calibracao, e precisa ficar fora da medida de falso positivo da
auto-rejeicao, como a `inspecao_m5`. Mesmo formato: so `legivel`, nulo enquanto pendente.
Amostra: `ml/reports/20261016-213656/inspecao_355.csv`; importador:
`inspecao.rotulagem.cli importar-inspecao-m7`.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'b5d8f3a1c726'
down_revision: str | None = 'a7c9e2f4b610'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAMPOS_V2 = ('cena', 'reflexo', 'fora_de_foco', 'tampa_suja', 'enquadramento', 'display_apagado', 'legivel')
COMPLETOS = ' AND '.join(f'{c} IS NOT NULL' for c in CAMPOS_V2)


def upgrade() -> None:
    op.drop_constraint('ck_rotulos_rodada', 'rotulos', type_='check')
    op.create_check_constraint(
        'ck_rotulos_rodada', 'rotulos',
        "rodada IN ('lab02', 'a', 'b', 'desempate', 'producao', 'inspecao_m5', 'inspecao_m7')",
    )
    op.drop_constraint('ck_rotulos_completo', 'rotulos', type_='check')
    op.create_check_constraint(
        'ck_rotulos_completo', 'rotulos',
        f"rodada IN ('producao', 'inspecao_m5', 'inspecao_m7') OR ({COMPLETOS})",
    )


def downgrade() -> None:
    op.execute("DELETE FROM rotulos WHERE rodada = 'inspecao_m7'")
    op.drop_constraint('ck_rotulos_completo', 'rotulos', type_='check')
    op.create_check_constraint(
        'ck_rotulos_completo', 'rotulos', f"rodada IN ('producao', 'inspecao_m5') OR ({COMPLETOS})"
    )
    op.drop_constraint('ck_rotulos_rodada', 'rotulos', type_='check')
    op.create_check_constraint(
        'ck_rotulos_rodada', 'rotulos',
        "rodada IN ('lab02', 'a', 'b', 'desempate', 'producao', 'inspecao_m5')",
    )
