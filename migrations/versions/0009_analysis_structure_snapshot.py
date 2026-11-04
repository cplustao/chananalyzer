"""Store the complete Chan structure apart from the AI input snapshot.

Revision ID: 0009_structure_snapshot
Revises: 0008_legacy_bar_units
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_structure_snapshot"
down_revision: str | None = "0008_legacy_bar_units"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("structure_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_column("structure_snapshot")
