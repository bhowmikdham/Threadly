"""Encrypted bounded conversation state.

Revision ID: c23026e9a039
Revises: b17026e9a038
"""

import sqlalchemy as sa

from alembic import op

revision = "c23026e9a039"
down_revision = "b17026e9a038"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("account_version", sa.Integer(), nullable=False),
        sa.Column("state_enc", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("pending_request_id", sa.String(36)),
        sa.Column("pending_hash", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 0", name="ck_conversation_version"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_expiry", "conversations", ["expires_at"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM conversations)")).scalar():
        raise RuntimeError("Cannot downgrade while retained conversations exist")
    op.drop_table("conversations")
