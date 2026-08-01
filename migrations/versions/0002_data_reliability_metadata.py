"""Add data refresh and radar provenance metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_data_reliability"
down_revision: str | None = "0001_v2_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ingestion_runs") as batch:
        batch.add_column(sa.Column("provider_chain", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("data_time", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("fetched_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("freshness", sa.String(length=16), nullable=False, server_default="missing")
        )
        batch.add_column(sa.Column("coverage_rate", sa.Float(), nullable=True))
        batch.add_column(sa.Column("missing_fields", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("fallback_errors", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "contract_version", sa.String(length=32), nullable=False, server_default="market-data-v2"
            )
        )
        batch.add_column(sa.Column("input_digest", sa.String(length=64), nullable=True))
    with op.batch_alter_table("radar_snapshots") as batch:
        batch.add_column(sa.Column("source_summary", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("data_time", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("freshness", sa.String(length=16), nullable=False, server_default="fresh"))
        batch.add_column(sa.Column("coverage_rate", sa.Float(), nullable=True))
        batch.add_column(sa.Column("missing_fields", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("input_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("radar_snapshots") as batch:
        batch.drop_column("input_digest")
        batch.drop_column("missing_fields")
        batch.drop_column("coverage_rate")
        batch.drop_column("freshness")
        batch.drop_column("data_time")
        batch.drop_column("source_summary")
    with op.batch_alter_table("ingestion_runs") as batch:
        batch.drop_column("input_digest")
        batch.drop_column("contract_version")
        batch.drop_column("fallback_errors")
        batch.drop_column("missing_fields")
        batch.drop_column("coverage_rate")
        batch.drop_column("freshness")
        batch.drop_column("fetched_at")
        batch.drop_column("data_time")
        batch.drop_column("provider_chain")


# Defaults are retained after migration so rows created by old clients remain valid.
# No generic provider cache table is introduced: each Bar keeps one source and one
# adjustment contract for the complete series written during a refresh.
