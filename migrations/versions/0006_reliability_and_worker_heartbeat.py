"""Add atomic job cursors, analysis idempotency, and worker heartbeats.

Revision ID: 0006_reliability
Revises: 0005_job_archive
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_reliability"
down_revision: str | None = "0005_job_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("event_sequence", sa.Integer(), nullable=False, server_default="0"))

    op.execute(
        """
        UPDATE jobs
        SET event_sequence = COALESCE(
            (SELECT MAX(job_events.sequence) FROM job_events WHERE job_events.job_id = jobs.id),
            0
        )
        """
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("active_job_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["active_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("instance_id"),
    )
    op.create_index(
        op.f("ix_worker_heartbeats_heartbeat_at"),
        "worker_heartbeats",
        ["heartbeat_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_worker_heartbeats_active_job_id"),
        "worker_heartbeats",
        ["active_job_id"],
        unique=False,
    )

    with op.batch_alter_table("analysis_runs") as batch:
        batch.create_unique_constraint("uq_analysis_run_job_item", ["job_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_constraint("uq_analysis_run_job_item", type_="unique")

    op.drop_index(op.f("ix_worker_heartbeats_active_job_id"), table_name="worker_heartbeats")
    op.drop_index(op.f("ix_worker_heartbeats_heartbeat_at"), table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("event_sequence")