"""Add reviewed command plans without reinterpreting existing tasks.

Revision ID: b10c026e9a31
Revises: a0426e9bc731
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b10c026e9a31"
down_revision = "a0426e9bc731"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "command_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("context_snapshot_id", sa.String(36)),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("release", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("plan_hash", sa.String(64)),
        sa.Column("task_id", sa.String(36)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "request_id", name="uq_command_plan_request"),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id", "user_id"],
            ["context_snapshots.id", "context_snapshots.user_id"],
            ondelete="CASCADE",
            name="fk_command_plan_context",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            ondelete="CASCADE",
            name="fk_command_plan_task",
        ),
        sa.CheckConstraint(
            "state IN ('planning','proposed','needs_clarification','unsupported',"
            "'failed','expired','consumed')",
            name="ck_command_plan_state",
        ),
        sa.CheckConstraint(
            "(state = 'consumed') = (task_id IS NOT NULL)", name="ck_command_plan_consumed"
        ),
        sa.CheckConstraint(
            "state NOT IN ('proposed','consumed') OR "
            "(result IS NOT NULL AND plan_hash IS NOT NULL)",
            name="ck_command_plan_result",
        ),
    )
    op.execute("""CREATE FUNCTION guard_command_plan() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (NEW.id,NEW.user_id,NEW.request_id,NEW.request_hash,NEW.request,
          NEW.context_snapshot_id,NEW.source_hash,NEW.release,NEW.expires_at,NEW.created_at)
         IS DISTINCT FROM
         (OLD.id,OLD.user_id,OLD.request_id,OLD.request_hash,OLD.request,
          OLD.context_snapshot_id,OLD.source_hash,OLD.release,OLD.expires_at,OLD.created_at) THEN
        RAISE EXCEPTION 'Command plan input is immutable' USING ERRCODE='23514';
      END IF;
      IF OLD.state <> 'planning' AND (NEW.result,NEW.plan_hash) IS DISTINCT FROM
                                    (OLD.result,OLD.plan_hash) THEN
        RAISE EXCEPTION 'Command plan result is immutable' USING ERRCODE='23514';
      END IF;
      IF NEW.state <> OLD.state AND NOT
         ((OLD.state='planning' AND NEW.state IN
            ('proposed','needs_clarification','unsupported','failed','expired'))
          OR (OLD.state='proposed' AND NEW.state IN ('consumed','expired'))) THEN
        RAISE EXCEPTION 'Invalid command plan transition' USING ERRCODE='23514';
      END IF;
      IF OLD.state='consumed' AND NEW.task_id IS DISTINCT FROM OLD.task_id THEN
        RAISE EXCEPTION 'Consumed task is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;""")

    op.execute(
        "CREATE TRIGGER command_plan_guard BEFORE UPDATE ON command_plans "
        "FOR EACH ROW EXECUTE FUNCTION guard_command_plan()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM command_plans) THEN
        RAISE EXCEPTION 'Cannot downgrade while command plans exist';
      END IF;
    END $$;""")
    op.drop_table("command_plans")
    op.execute("DROP FUNCTION guard_command_plan()")
