"""Link manual reruns to their original job."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_job_retry"
down_revision: str | None = "0002_data_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("retry_of_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_retry_of_id_jobs", "jobs", ["retry_of_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_jobs_retry_of_id", ["retry_of_id"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_retry_of_id")
        batch.drop_constraint("fk_jobs_retry_of_id_jobs", type_="foreignkey")
        batch.drop_column("retry_of_id")
