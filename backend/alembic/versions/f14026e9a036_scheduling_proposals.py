"""Add reviewed scheduling proposals without reinterpreting existing tasks.

Revision ID: f14026e9a036
Revises: f14026e9a035
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f14026e9a036"
down_revision = "f14026e9a035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduling_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("context_snapshot_id", sa.String(36)),
        sa.Column("binding", postgresql.JSONB(), nullable=False),
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
        sa.UniqueConstraint("user_id", "request_id", name="uq_scheduling_proposal_request"),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id", "user_id"],
            ["context_snapshots.id", "context_snapshots.user_id"],
            ondelete="CASCADE",
            name="fk_scheduling_proposal_context",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            ondelete="CASCADE",
            name="fk_scheduling_proposal_task",
        ),
        sa.CheckConstraint(
            "state IN ('planning','proposed','needs_clarification','unsupported',"
            "'failed','expired','consumed')",
            name="ck_scheduling_proposal_state",
        ),
        sa.CheckConstraint(
            "(state = 'consumed') = (task_id IS NOT NULL)", name="ck_scheduling_proposal_consumed"
        ),
        sa.CheckConstraint(
            "state NOT IN ('proposed','consumed') OR "
            "(result IS NOT NULL AND plan_hash IS NOT NULL)",
            name="ck_scheduling_proposal_result",
        ),
    )
    op.execute("""CREATE FUNCTION guard_scheduling_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (NEW.id,NEW.user_id,NEW.request_id,NEW.request_hash,NEW.request,
          NEW.context_snapshot_id,NEW.binding,NEW.source_hash,NEW.release,NEW.expires_at,NEW.created_at)
         IS DISTINCT FROM
         (OLD.id,OLD.user_id,OLD.request_id,OLD.request_hash,OLD.request,
          OLD.context_snapshot_id,OLD.binding,OLD.source_hash,OLD.release,
          OLD.expires_at,OLD.created_at) THEN
        RAISE EXCEPTION 'Scheduling proposal input is immutable' USING ERRCODE='23514';
      END IF;
      IF OLD.state <> 'planning' AND (NEW.result,NEW.plan_hash) IS DISTINCT FROM
                                    (OLD.result,OLD.plan_hash) THEN
        RAISE EXCEPTION 'Scheduling proposal result is immutable' USING ERRCODE='23514';
      END IF;
      IF NEW.state <> OLD.state AND NOT
         ((OLD.state='planning' AND NEW.state IN
            ('proposed','needs_clarification','unsupported','failed','expired'))
          OR (OLD.state='proposed' AND NEW.state IN ('consumed','expired'))) THEN
        RAISE EXCEPTION 'Invalid scheduling proposal transition' USING ERRCODE='23514';
      END IF;
      IF OLD.state='consumed' AND NEW.task_id IS DISTINCT FROM OLD.task_id THEN
        RAISE EXCEPTION 'Consumed task is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;""")

    op.execute(
        "CREATE TRIGGER scheduling_proposal_guard BEFORE UPDATE ON scheduling_proposals "
        "FOR EACH ROW EXECUTE FUNCTION guard_scheduling_proposal()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM scheduling_proposals) THEN
        RAISE EXCEPTION 'Cannot downgrade while scheduling proposals exist';
      END IF;
    END $$;""")
    op.drop_table("scheduling_proposals")
    op.execute("DROP FUNCTION guard_scheduling_proposal()")
