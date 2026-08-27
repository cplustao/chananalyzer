"""Add canonical market units, decision readiness, and research-plan fields.

Revision ID: 0007_decision_readiness
Revises: 0006_reliability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_decision_readiness"
down_revision: str | None = "0006_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("instruments") as batch:
        batch.add_column(sa.Column("asset_type", sa.String(length=16), nullable=False, server_default="stock"))
        batch.create_index("ix_instruments_asset_type", ["asset_type"], unique=False)
    op.execute("UPDATE instruments SET asset_type = 'index' WHERE length(code) <> 6")

    with op.batch_alter_table("bars") as batch:
        batch.add_column(sa.Column("raw_volume", sa.Float(), nullable=True))
        batch.add_column(sa.Column("raw_amount", sa.Float(), nullable=True))
        batch.add_column(sa.Column("trade_status", sa.String(length=24), nullable=False, server_default="trading"))
        batch.add_column(sa.Column("unit_contract_version", sa.String(length=32), nullable=False, server_default="legacy-v1"))
        batch.create_index("ix_bars_trade_status", ["trade_status"], unique=False)
        batch.create_index("ix_bars_unit_contract_version", ["unit_contract_version"], unique=False)

    op.execute("""
        UPDATE bars SET raw_volume = volume, raw_amount = amount
        WHERE unit_contract_version = 'legacy-v1'
          AND data_source_id IN (SELECT id FROM data_sources WHERE key IN ('tushare', 'akshare'))
    """)
    op.execute("""
        UPDATE bars SET volume = volume * 100.0, amount = amount * 1000.0,
            unit_contract_version = 'cn-equity-v1'
        WHERE unit_contract_version = 'legacy-v1'
          AND data_source_id IN (SELECT id FROM data_sources WHERE key = 'tushare')
    """)
    op.execute("""
        UPDATE bars SET volume = volume * 100.0, unit_contract_version = 'cn-equity-v1'
        WHERE unit_contract_version = 'legacy-v1'
          AND data_source_id IN (SELECT id FROM data_sources WHERE key = 'akshare')
    """)
    op.execute("""
        UPDATE bars SET unit_contract_version = 'cn-equity-v1'
        WHERE unit_contract_version = 'legacy-v1'
          AND data_source_id IN (SELECT id FROM data_sources WHERE key = 'baostock')
    """)
    op.execute("UPDATE bars SET trade_status = CASE WHEN volume > 0 THEN 'trading' ELSE 'suspended' END")
    op.execute("""
        UPDATE bars SET quality_status = 'quarantined', unit_contract_version = 'unknown'
        WHERE unit_contract_version = 'legacy-v1'
    """)

    with op.batch_alter_table("watchlist_items") as batch:
        batch.add_column(sa.Column("thesis", sa.Text(), nullable=True))
        batch.add_column(sa.Column("confirmation_trigger", sa.Text(), nullable=True))
        batch.add_column(sa.Column("invalidation_condition", sa.Text(), nullable=True))
        batch.add_column(sa.Column("next_action", sa.Text(), nullable=True))
        batch.add_column(sa.Column("next_review_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("research_status", sa.String(length=24), nullable=False, server_default="watching"))
        batch.create_index("ix_watchlist_items_next_review_date", ["next_review_date"], unique=False)
        batch.create_index("ix_watchlist_items_research_status", ["research_status"], unique=False)

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("current_stage", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("checkpoint", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("failure_summary", sa.Text(), nullable=True))
        batch.create_index("ix_jobs_current_stage", ["current_stage"], unique=False)

    op.create_table(
        "data_health_snapshots",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_data_health_snapshots_generated_at", "data_health_snapshots", ["generated_at"])
    op.create_table(
        "data_reliability_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("decision_usable", sa.Boolean(), nullable=False),
        sa.Column("coverage_rate", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_summary", sa.JSON(), nullable=True),
        sa.Column("blocking_reasons", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_reliability_samples_trade_date", "data_reliability_samples", ["trade_date"])
    op.create_index("ix_data_reliability_samples_checked_at", "data_reliability_samples", ["checked_at"])
    op.create_index("ix_data_reliability_samples_status", "data_reliability_samples", ["status"])


def downgrade() -> None:
    op.drop_table("data_reliability_samples")
    op.drop_table("data_health_snapshots")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_current_stage")
        batch.drop_column("failure_summary")
        batch.drop_column("checkpoint")
        batch.drop_column("current_stage")
    with op.batch_alter_table("watchlist_items") as batch:
        batch.drop_index("ix_watchlist_items_research_status")
        batch.drop_index("ix_watchlist_items_next_review_date")
        batch.drop_column("research_status")
        batch.drop_column("next_review_date")
        batch.drop_column("next_action")
        batch.drop_column("invalidation_condition")
        batch.drop_column("confirmation_trigger")
        batch.drop_column("thesis")
    with op.batch_alter_table("bars") as batch:
        batch.drop_index("ix_bars_unit_contract_version")
        batch.drop_index("ix_bars_trade_status")
        batch.drop_column("unit_contract_version")
        batch.drop_column("trade_status")
        batch.drop_column("raw_amount")
        batch.drop_column("raw_volume")
    with op.batch_alter_table("instruments") as batch:
        batch.drop_index("ix_instruments_asset_type")
        batch.drop_column("asset_type")
