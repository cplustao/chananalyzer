"""Add non-destructive job archival and clear legacy stale bar flags.

Revision ID: 0005_job_archive
Revises: 0004_ai_report
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_job_archive"
down_revision: str | None = "0004_ai_report"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
    # Fetch failures previously marked every historical row in a series stale.
    # Freshness is determined from the latest business date, so those flags are invalid.
    op.execute("UPDATE bars SET quality_status = 'ok' WHERE quality_status = 'stale'")


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("archived_at")