"""Gmail ordering, reply metadata and sync concurrency fence.

Revision ID: 3c6e9a1207bd
Revises: 8f3a7c2d901b
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "3c6e9a1207bd"
down_revision = "8f3a7c2d901b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("sync_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("threads", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("messages", sa.Column("received_at", sa.DateTime(timezone=True)))
    op.add_column("messages", sa.Column("subject", sa.Text()))
    op.add_column("messages", sa.Column("reply_metadata", postgresql.JSONB()))
    # Preserve existing mail. The next sync must hydrate metadata for unchanged messages too.
    op.execute("UPDATE users SET gmail_history_id = NULL")
    # Existing summaries have no source-version evidence and cannot be trusted after repair.
    op.execute("DELETE FROM summaries")


def downgrade() -> None:
    op.drop_column("messages", "reply_metadata")
    op.drop_column("messages", "subject")
    op.drop_column("messages", "received_at")
    op.drop_column("threads", "version")
    op.drop_column("users", "sync_version")
