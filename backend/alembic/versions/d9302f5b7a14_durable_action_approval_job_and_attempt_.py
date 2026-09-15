"""Durable action approval job and attempt storage

Revision ID: d9302f5b7a14
Revises: c8291e4a6f03
Create Date: 2026-09-15 23:41:02.701907
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d9302f5b7a14"
down_revision = "c8291e4a6f03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=24), nullable=False),
        sa.Column("proposal_request_id", sa.String(length=128), nullable=False),
        sa.Column("proposal_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_schema", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("source_artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("source_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state", sa.String(length=24), server_default="proposed", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "result", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("action_type IN ('send_email','create_event')", name="ck_action_type"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_action_payload_object"),
        sa.CheckConstraint(
            "state IN ('proposed','approved','executing','outcome_unknown','succeeded','failed',"
            "'rejected','cancelled','expired','superseded')",
            name="ck_action_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_action_version"),
        sa.ForeignKeyConstraint(
            ["artifact_id", "task_id", "user_id"],
            ["artifact_revisions.id", "artifact_revisions.task_id", "artifact_revisions.user_id"],
            name="fk_action_owned_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            name="fk_action_owned_task",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", "payload_hash", name="uq_action_payload"),
        sa.UniqueConstraint("id", "user_id", name="uq_action_owner"),
        sa.UniqueConstraint("user_id", "proposal_request_id", name="uq_action_proposal_request"),
    )
    op.create_index("ix_action_expiry", "assistant_actions", ["state", "expires_at"], unique=False)
    op.create_index(
        "ix_action_task", "assistant_actions", ["task_id", "user_id", "state"], unique=False
    )
    op.create_table(
        "action_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("action_version >= 1", name="ck_approval_version"),
        sa.ForeignKeyConstraint(
            ["action_id", "user_id", "payload_hash"],
            ["assistant_actions.id", "assistant_actions.user_id", "assistant_actions.payload_hash"],
            name="fk_approval_exact_action",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", "action_version", name="uq_approval_action_version"),
        sa.UniqueConstraint("id", "action_id", "user_id", name="uq_approval_owned_action"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_approval_request"),
    )
    op.create_table(
        "action_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("dispatch_intent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_identifiers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provider_identifiers) = 'object'", name="ck_attempt_identifiers"
        ),
        sa.CheckConstraint(
            "state IN ('dispatched','outcome_unknown','succeeded','failed')",
            name="ck_attempt_state",
        ),
        sa.CheckConstraint(
            "number BETWEEN 1 AND 3 AND action_version >= 1", name="ck_attempt_versions"
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "user_id"],
            ["assistant_actions.id", "assistant_actions.user_id"],
            name="fk_attempt_owned_action",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id", "action_id", "user_id"],
            ["action_approvals.id", "action_approvals.action_id", "action_approvals.user_id"],
            name="fk_attempt_owned_approval",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", "number", name="uq_attempt_number"),
    )
    op.create_index(
        "uq_attempt_unresolved",
        "action_attempts",
        ["action_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('dispatched','outcome_unknown')"),
    )
    op.create_table(
        "action_jobs",
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="dispatch", nullable=False),
        sa.Column("state", sa.String(length=16), server_default="held", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state != 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_action_job_lease",
        ),
        sa.CheckConstraint("kind IN ('dispatch','reconcile')", name="ck_action_job_kind"),
        sa.CheckConstraint(
            "state IN ('held','queued','running','done')", name="ck_action_job_state"
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_action_job_attempts"),
        sa.ForeignKeyConstraint(
            ["action_id", "user_id"],
            ["assistant_actions.id", "assistant_actions.user_id"],
            name="fk_action_job_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id", "action_id", "user_id"],
            ["action_approvals.id", "action_approvals.action_id", "action_approvals.user_id"],
            name="fk_action_job_approval",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("action_id"),
    )
    op.create_index(
        "ix_action_jobs_due",
        "action_jobs",
        ["state", "available_at", "lease_expires_at"],
        unique=False,
    )

    op.execute("""CREATE FUNCTION threadly_action_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.state IN ('approved','executing','outcome_unknown') THEN
      RAISE EXCEPTION 'Action requires coordinated recovery before deletion' USING ERRCODE='23514';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.state != 'proposed' OR NEW.version != 1 THEN
      RAISE EXCEPTION 'Action must start proposed at version one' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF (to_jsonb(NEW) - ARRAY['state','version','updated_at','result','error_code'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['state','version','updated_at','result','error_code']) THEN
    RAISE EXCEPTION 'Action payload and source fields are immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.state = OLD.state THEN
    IF NEW.version != OLD.version OR NEW.result IS DISTINCT FROM OLD.result
       OR NEW.error_code IS DISTINCT FROM OLD.error_code THEN
      RAISE EXCEPTION 'Outcome updates require a state transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.version != OLD.version + 1 OR NOT (
    (OLD.state='proposed'
      AND NEW.state IN ('approved','rejected','cancelled','expired','superseded')) OR
    (OLD.state='approved' AND NEW.state IN ('executing','cancelled','expired','superseded')) OR
    (OLD.state='executing' AND NEW.state IN ('outcome_unknown','succeeded','failed')) OR
    (OLD.state='outcome_unknown' AND NEW.state IN ('succeeded','failed'))
  ) THEN
    RAISE EXCEPTION 'Illegal action state transition' USING ERRCODE='23514';
  END IF;
  IF NEW.state='approved' AND NOT EXISTS (
    SELECT 1 FROM action_approvals p WHERE p.action_id=NEW.id AND p.user_id=NEW.user_id
      AND p.payload_hash=NEW.payload_hash AND p.action_version=OLD.version
      AND p.expires_at > clock_timestamp() AND NEW.expires_at > clock_timestamp()
  ) THEN
    RAISE EXCEPTION 'Matching unexpired approval required' USING ERRCODE='23514';
  END IF;
  IF NEW.state='executing' AND NOT EXISTS (
    SELECT 1 FROM action_attempts a WHERE a.action_id=NEW.id AND a.user_id=NEW.user_id
      AND a.action_version=OLD.version AND a.state='dispatched'
  ) THEN
    RAISE EXCEPTION 'Persist dispatch intent before executing' USING ERRCODE='23514';
  END IF;
  IF NEW.state IN ('succeeded','failed') AND NOT EXISTS (
    SELECT 1 FROM action_attempts a WHERE a.action_id=NEW.id AND a.user_id=NEW.user_id
      AND a.state=NEW.state AND a.evidence IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'Terminal outcome requires attempt evidence' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;""")
    op.execute("""CREATE FUNCTION threadly_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='UPDATE' THEN
    IF NEW IS DISTINCT FROM OLD THEN
      RAISE EXCEPTION 'Approval records are immutable' USING ERRCODE='23514';
    END IF;
  ELSIF NOT EXISTS (
    SELECT 1 FROM assistant_actions a WHERE a.id=NEW.action_id AND a.user_id=NEW.user_id
      AND a.payload_hash=NEW.payload_hash AND a.version=NEW.action_version AND a.state='proposed'
      AND NEW.expires_at > clock_timestamp() AND NEW.expires_at <= a.expires_at
  ) THEN
    RAISE EXCEPTION 'Approval must bind current unexpired action' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;""")
    op.execute("""CREATE FUNCTION threadly_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.state IN ('dispatched','outcome_unknown') THEN
      RAISE EXCEPTION 'Unresolved attempt cannot be deleted' USING ERRCODE='23514';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.state!='dispatched' OR NOT EXISTS (
      SELECT 1 FROM assistant_actions a JOIN action_approvals p
        ON p.action_id=a.id AND p.user_id=a.user_id AND p.payload_hash=a.payload_hash
      WHERE a.id=NEW.action_id AND a.user_id=NEW.user_id AND a.state='approved'
        AND a.version=NEW.action_version AND p.id=NEW.approval_id
        AND p.expires_at > clock_timestamp() AND a.expires_at > clock_timestamp()
    ) THEN
      RAISE EXCEPTION 'Dispatch intent requires a current approved action' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF (to_jsonb(NEW) - ARRAY['state','evidence']) IS DISTINCT FROM
     (to_jsonb(OLD) - ARRAY['state','evidence']) THEN
    RAISE EXCEPTION 'Attempt identity is immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.state != OLD.state AND NOT (
    (OLD.state='dispatched' AND NEW.state IN ('outcome_unknown','succeeded','failed')) OR
    (OLD.state='outcome_unknown' AND NEW.state IN ('succeeded','failed'))
  ) THEN
    RAISE EXCEPTION 'Illegal attempt state transition' USING ERRCODE='23514';
  END IF;
  IF OLD.state IN ('succeeded','failed') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'Terminal attempt evidence is immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.state IN ('succeeded','failed') AND NEW.evidence IS NULL THEN
    RAISE EXCEPTION 'Terminal attempt requires outcome evidence' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;""")
    op.execute(
        "CREATE TRIGGER guard_action BEFORE INSERT OR UPDATE OR DELETE ON assistant_actions "
        "FOR EACH ROW EXECUTE FUNCTION threadly_action_guard()"
    )
    op.execute(
        "CREATE TRIGGER guard_approval BEFORE INSERT OR UPDATE ON action_approvals "
        "FOR EACH ROW EXECUTE FUNCTION threadly_approval_guard()"
    )
    op.execute(
        "CREATE TRIGGER guard_attempt BEFORE INSERT OR UPDATE OR DELETE ON action_attempts "
        "FOR EACH ROW EXECUTE FUNCTION threadly_attempt_guard()"
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM assistant_actions)")).scalar():
        raise RuntimeError("Cannot downgrade while action history exists")
    op.drop_index("ix_action_jobs_due", table_name="action_jobs")
    op.drop_table("action_jobs")
    op.drop_index(
        "uq_attempt_unresolved",
        table_name="action_attempts",
        postgresql_where=sa.text("state IN ('dispatched','outcome_unknown')"),
    )
    op.drop_table("action_attempts")
    op.drop_table("action_approvals")
    op.drop_index("ix_action_task", table_name="assistant_actions")
    op.drop_index("ix_action_expiry", table_name="assistant_actions")
    op.drop_table("assistant_actions")
    op.execute("DROP FUNCTION threadly_action_guard()")
    op.execute("DROP FUNCTION threadly_approval_guard()")
    op.execute("DROP FUNCTION threadly_attempt_guard()")
