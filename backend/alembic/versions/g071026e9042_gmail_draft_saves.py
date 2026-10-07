"""Durable draft-only receipts; existing actions and approvals are unchanged."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "g071026e9042"
down_revision = "c061026e9041"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gmail_draft_saves",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("account_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(none_as_null=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "source_key", name="uq_gmail_draft_source"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_gmail_draft_request"),
        sa.CheckConstraint(
            "state IN ('saving','succeeded','failed','outcome_unknown')",
            name="ck_gmail_draft_state",
        ),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM gmail_draft_saves)")).scalar():
        raise RuntimeError("Cannot downgrade: preserve Gmail draft receipts and write evidence")
    op.drop_table("gmail_draft_saves")
