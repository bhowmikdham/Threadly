"""Shared, bounded rate counters for public OAuth entry points."""

import sqlalchemy as sa

from alembic import op

revision = "c33026e9a040"
down_revision = "f28026e9a040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_rate_limits",
        sa.Column("bucket_key", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("request_count >= 1", name="ck_oauth_rate_count"),
    )
    op.create_index("ix_oauth_rate_expiry", "oauth_rate_limits", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_rate_expiry", table_name="oauth_rate_limits")
    op.drop_table("oauth_rate_limits")
