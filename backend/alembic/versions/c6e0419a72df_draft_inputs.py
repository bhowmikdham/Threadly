"""Immutable recipient and reply bindings for draft tasks.

Revision ID: c6e0419a72df
Revises: b7a219c40e6d
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c6e0419a72df"
down_revision = "b7a219c40e6d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assistant_tasks", sa.Column("draft_input", postgresql.JSONB()))


def downgrade():
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM assistant_tasks WHERE "
            "draft_input IS NOT NULL OR release->>'workflow' = 'contextual-task-1.1.0'"
        )
    )
    if count:
        raise RuntimeError(
            "Cannot downgrade while draft-release tasks exist; preserve their bindings."
        )
    op.drop_column("assistant_tasks", "draft_input")
