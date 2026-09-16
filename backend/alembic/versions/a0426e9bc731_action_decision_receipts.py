"""Immutable rejection/cancellation receipts.

Revision ID: a0426e9bc731
Revises: f1a2b3c4d5e6
"""

import sqlalchemy as sa

from alembic import op

revision = "a0426e9bc731"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "action_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "user_id"],
            ["assistant_actions.id", "assistant_actions.user_id"],
            name="fk_decision_owned_action",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "user_id", "operation", "request_id", name="uq_action_decision_request"
        ),
        sa.CheckConstraint("expected_version >= 1", name="ck_decision_version"),
        sa.CheckConstraint(
            "(operation='reject' AND decision='rejected') OR "
            "(operation='cancel' AND decision IN ('cancelled','cancellation_requested'))",
            name="ck_decision_operation",
        ),
    )
    op.create_index("ix_action_decisions_action", "action_decisions", ["action_id", "decision"])
    op.execute("""CREATE FUNCTION threadly_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'Action decisions are immutable' USING ERRCODE='23514';
    END $$;""")
    op.execute(
        "CREATE TRIGGER guard_decision BEFORE UPDATE OR DELETE ON action_decisions "
        "FOR EACH ROW EXECUTE FUNCTION threadly_decision_guard()"
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM action_decisions)")).scalar():
        raise RuntimeError("Cannot downgrade while action decision history exists")
    op.drop_index("ix_action_decisions_action", table_name="action_decisions")
    op.drop_table("action_decisions")
    op.execute("DROP FUNCTION threadly_decision_guard()")
