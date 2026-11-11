"""Index bars.quality_status so the data gate can count quarantined bars quickly.

Without it the gate's anomaly count scanned all daily bars (about 50 seconds on
6.5 million rows) whenever its five-minute cache expired, and the today page
timed out behind it.

Revision ID: 0010_bar_quality_index
Revises: 0009_structure_snapshot
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_bar_quality_index"
down_revision: str | None = "0009_structure_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_bars_quality_status", "bars", ["quality_status"])


def downgrade() -> None:
    op.drop_index("ix_bars_quality_status", table_name="bars")
