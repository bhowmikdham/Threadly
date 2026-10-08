"""Retain encrypted chat exchanges outside the active model window."""

import sqlalchemy as sa
from alembic import op

revision = "h071026e9043"
down_revision = "g071026e9042"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_goals",
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("goal_id", sa.String(80), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("updated_version", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_enc", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("updated_version > 0", name="ck_conversation_goal_version"),
        sa.CheckConstraint("status IN ('retained','closed')", name="ck_conversation_goal_status"),
    )
    op.create_index(
        "ix_conversation_goals_recent", "conversation_goals", ["conversation_id", "updated_version"]
    )
    op.create_table(
        "conversation_exchanges",
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("payload_enc", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_conversation_exchange_version"),
        sa.UniqueConstraint(
            "conversation_id", "request_id", name="uq_conversation_exchange_request"
        ),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM conversation_exchanges) "
                "OR EXISTS (SELECT 1 FROM conversation_goals)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Cannot downgrade: preserve retained conversation exchanges")
    op.drop_table("conversation_exchanges")
    op.drop_table("conversation_goals")
