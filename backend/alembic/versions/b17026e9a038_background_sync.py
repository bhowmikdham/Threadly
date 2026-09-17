"""Bounded durable mailbox reads, staged before atomic publication."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b17026e9a038"
down_revision = "a17026e9a037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mail_sync_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("account_version", sa.Integer(), nullable=False),
        sa.Column("sync_version", sa.Integer(), nullable=False),
        sa.Column("full", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("cursor", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("resets", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("result", postgresql.JSONB(none_as_null=True)),
        sa.UniqueConstraint("user_id", "request_id", name="uq_mail_sync_request"),
        sa.UniqueConstraint("id", "user_id", name="uq_mail_sync_owner"),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed')", name="ck_mail_sync_state"
        ),
        sa.CheckConstraint(
            "phase IN ('start','listing','history','publish')", name="ck_mail_sync_phase"
        ),
        sa.CheckConstraint(
            "(state = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_mail_sync_lease",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 5 AND resets BETWEEN 0 AND 2", name="ck_mail_sync_attempts"
        ),
    )
    op.create_index(
        "uq_mail_sync_active",
        "mail_sync_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued','running')"),
    )
    op.create_table(
        "mail_sync_stage",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(none_as_null=True)),
        sa.ForeignKeyConstraint(
            ["job_id", "user_id"],
            ["mail_sync_jobs.id", "mail_sync_jobs.user_id"],
            ondelete="CASCADE",
            name="fk_mail_sync_stage_owner",
        ),
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM mail_sync_jobs WHERE state IN ('queued','running')) THEN
        RAISE EXCEPTION 'Cannot downgrade with active mailbox sync jobs';
      END IF;
    END $$""")
    op.drop_table("mail_sync_stage")
    op.drop_table("mail_sync_jobs")
