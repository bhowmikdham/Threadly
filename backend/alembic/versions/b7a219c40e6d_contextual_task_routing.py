"""Durable routing and saved clarification outcomes.

Revision ID: b7a219c40e6d
Revises: 3c6e9a1207bd
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b7a219c40e6d"
down_revision = "3c6e9a1207bd"
branch_labels = None
depends_on = None
OLD_STATES = "state IN ('queued','running','succeeded','failed','cancelled')"
NEW_STATES = (
    "state IN ('queued','running','succeeded','failed','cancelled',"
    "'needs_clarification','unsupported')"
)


def upgrade():
    op.add_column("assistant_tasks", sa.Column("intent_hint", sa.String(16)))
    op.add_column("assistant_tasks", sa.Column("route", postgresql.JSONB()))
    op.alter_column(
        "assistant_tasks", "context_snapshot_id", existing_type=sa.String(36), nullable=True
    )
    op.alter_column("assistant_tasks", "state", existing_type=sa.String(16), type_=sa.String(24))
    op.drop_constraint("ck_task_state", "assistant_tasks", type_="check")
    op.create_check_constraint("ck_task_state", "assistant_tasks", NEW_STATES)


def downgrade():
    # Old workers cannot understand these requests. Fail before changing or deleting data.
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM assistant_tasks WHERE "
            "context_snapshot_id IS NULL OR release->>'workflow' = 'contextual-task-1.0.0'"
        )
    )
    if count:
        raise RuntimeError(
            "Cannot downgrade while contextual tasks exist; retain this schema "
            "or explicitly export/remove those tasks first."
        )
    op.drop_constraint("ck_task_state", "assistant_tasks", type_="check")
    op.create_check_constraint("ck_task_state", "assistant_tasks", OLD_STATES)
    op.alter_column("assistant_tasks", "state", existing_type=sa.String(24), type_=sa.String(16))
    op.alter_column(
        "assistant_tasks", "context_snapshot_id", existing_type=sa.String(36), nullable=False
    )
    op.drop_column("assistant_tasks", "route")
    op.drop_column("assistant_tasks", "intent_hint")
