"""Normalize the verified legacy Chan database bar units.

Revision ID: 0008_legacy_bar_units
Revises: 0007_decision_readiness
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_legacy_bar_units"
down_revision: str | None = "0007_decision_readiness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The retired v1 importer persisted the same Tushare daily fields used by
    # the provider: volume in lots and amount in thousands of yuan. Preserve
    # those native values before converting to the canonical shares/yuan pair.
    op.execute("""
        UPDATE bars SET raw_volume = volume, raw_amount = amount,
            volume = volume * 100.0,
            amount = amount * 1000.0,
            quality_status = 'ok',
            unit_contract_version = 'cn-equity-v1'
        WHERE unit_contract_version = 'unknown'
          AND data_source_id IN (SELECT id FROM data_sources WHERE key = 'legacy_chan_db')
    """)
    op.execute("""
        UPDATE bars SET trade_status = CASE WHEN volume > 0 THEN 'trading' ELSE 'suspended' END
        WHERE data_source_id IN (SELECT id FROM data_sources WHERE key = 'legacy_chan_db')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE bars SET volume = COALESCE(raw_volume, volume / 100.0),
            amount = COALESCE(raw_amount, amount / 1000.0),
            raw_volume = NULL,
            raw_amount = NULL,
            quality_status = 'quarantined',
            unit_contract_version = 'unknown'
        WHERE data_source_id IN (SELECT id FROM data_sources WHERE key = 'legacy_chan_db')
    """)
