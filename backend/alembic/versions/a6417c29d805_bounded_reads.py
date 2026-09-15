"""Persist explicit saved-source read options without rewriting original task contracts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a6417c29d805"
down_revision = "f2b6049c7a81"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assistant_tasks", sa.Column("read_input", postgresql.JSONB()))


def downgrade():
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM assistant_tasks WHERE read_input IS NOT NULL "
            "OR release->>'workflow' = 'bounded-reads-task-1.0.0'"
        )
    )
    if count:
        raise RuntimeError("Cannot downgrade while bounded read tasks exist.")
    op.drop_column("assistant_tasks", "read_input")
