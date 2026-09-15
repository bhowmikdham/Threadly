"""Index owner-scoped local mailbox search without changing stored mail."""

import sqlalchemy as sa

from alembic import op

revision = "b7180d3f9e62"
down_revision = "a6417c29d805"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_messages_owner_search_time",
        "messages",
        [
            "user_id",
            sa.text("coalesce(received_at, sent_at) DESC"),
            sa.text('id DESC'),
        ],
    )


def downgrade():
    op.drop_index("ix_messages_owner_search_time", table_name="messages")
