"""Add structured AI report validation fields."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_ai_report"
down_revision: str | None = "0003_job_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_reports") as batch:
        batch.add_column(sa.Column("structured_payload", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("input_digest", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("validation_status", sa.String(length=24), nullable=False, server_default="legacy")
        )
        batch.add_column(sa.Column("validation_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_reports") as batch:
        batch.drop_column("validation_error")
        batch.drop_column("validation_status")
        batch.drop_column("input_digest")
        batch.drop_column("structured_payload")
