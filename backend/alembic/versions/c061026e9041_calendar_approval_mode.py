"""Chat-scoped calendar approval mode.

Revision ID: c061026e9041
Revises: c33026e9a040
"""

import sqlalchemy as sa

from alembic import op

revision = "c061026e9041"
down_revision = "c33026e9a040"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversations",
        sa.Column("calendar_approval_mode", sa.String(16), nullable=False, server_default="ask"),
    )
    op.add_column(
        "conversations",
        sa.Column("calendar_approval_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversations", sa.Column("calendar_approval_session_version", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_conversation_calendar_approval",
        "conversations",
        "calendar_approval_mode IN ('ask','always') AND calendar_approval_version >= 0",
    )


def downgrade():
    op.drop_constraint("ck_conversation_calendar_approval", "conversations", type_="check")
    op.drop_column("conversations", "calendar_approval_session_version")
    op.drop_column("conversations", "calendar_approval_version")
    op.drop_column("conversations", "calendar_approval_mode")
