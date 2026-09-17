"""Pinned assistant scheduling input; historical task data remains unchanged."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f14026e9a035"
down_revision = "e14026e9a034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "assistant_tasks",
        sa.Column("scheduling_input", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_task_scheduling_input",
        "assistant_tasks",
        "scheduling_input IS NULL OR (read_input IS NULL AND compound_input IS NULL "
        "AND COALESCE(draft_input, 'null'::jsonb) = 'null'::jsonb "
        "AND COALESCE(intent_hint, '') = 'plan_schedule' "
        "AND COALESCE(release->>'workflow', '') = 'assistant-scheduling-1.0.0')",
    )
    op.execute("""CREATE FUNCTION guard_task_scheduling_input() RETURNS trigger AS $$
      BEGIN
        IF NEW.scheduling_input IS DISTINCT FROM OLD.scheduling_input THEN
          RAISE EXCEPTION 'Accepted scheduling input is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER task_scheduling_input_guard BEFORE UPDATE ON assistant_tasks
      FOR EACH ROW EXECUTE FUNCTION guard_task_scheduling_input()""")


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM assistant_tasks WHERE scheduling_input IS NOT NULL)")
    ):
        raise RuntimeError("Cannot downgrade while scheduling tasks exist")
    op.execute("DROP TRIGGER task_scheduling_input_guard ON assistant_tasks")
    op.execute("DROP FUNCTION guard_task_scheduling_input()")
    op.drop_constraint("ck_task_scheduling_input", "assistant_tasks", type_="check")
    op.drop_column("assistant_tasks", "scheduling_input")
