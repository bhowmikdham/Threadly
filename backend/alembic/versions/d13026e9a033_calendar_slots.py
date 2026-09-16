"""Durable owned slot queries with immutable anchors and published offers.

Revision ID: d13026e9a033
Revises: c12026e9a032
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d13026e9a033"
down_revision = "c12026e9a032"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_calendar_evidence_owner", "calendar_evidence", ["id", "user_id"]
    )
    op.create_table(
        "calendar_slot_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("calendar_preferences.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("anchor_from_request_id", sa.String(36)),
        sa.Column("anchor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("preferences_version", sa.Integer(), nullable=False),
        sa.Column("account_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("preferences", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("resolution", postgresql.JSONB(none_as_null=True)),
        sa.Column("evidence_id", sa.String(36)),
        sa.Column("calculated_at", sa.DateTime(timezone=True)),
        sa.Column("result", postgresql.JSONB(none_as_null=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "request_id", name="uq_calendar_slot_request_key"),
        sa.UniqueConstraint("id", "user_id", name="uq_calendar_slot_request_owner"),
        sa.ForeignKeyConstraint(
            ["evidence_id", "user_id"],
            ["calendar_evidence.id", "calendar_evidence.user_id"],
            ondelete="CASCADE",
            name="fk_calendar_slot_evidence_owner",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_from_request_id", "user_id"],
            ["calendar_slot_requests.id", "calendar_slot_requests.user_id"],
            ondelete="CASCADE",
            name="fk_calendar_slot_anchor_owner",
        ),
        sa.CheckConstraint(
            "preferences_version >= 1 AND account_version >= 1 AND expires_at > created_at",
            name="ck_calendar_slot_versions",
        ),
        sa.CheckConstraint(
            "state IN ('processing','needs_clarification','complete','unknown','failed')",
            name="ck_calendar_slot_state",
        ),
    )
    op.create_index("ix_calendar_slot_requests_user_id", "calendar_slot_requests", ["user_id"])
    op.create_index(
        "ix_calendar_slot_requests_expires_at", "calendar_slot_requests", ["expires_at"]
    )
    op.execute("""CREATE FUNCTION guard_calendar_slot_request() RETURNS trigger AS $$
      BEGIN
        IF OLD.state != 'processing' OR NEW.state = 'processing'
           OR (to_jsonb(NEW) - ARRAY['state','evidence_id','calculated_at','result',
                                     'error_code','expires_at']) IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['state','evidence_id','calculated_at','result',
                                     'error_code','expires_at'])
           OR NEW.expires_at > OLD.expires_at THEN
          RAISE EXCEPTION 'Calendar slot request is immutable except terminal publication'
            USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER calendar_slot_request_guard BEFORE UPDATE ON calendar_slot_requests
      FOR EACH ROW EXECUTE FUNCTION guard_calendar_slot_request()""")


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM calendar_slot_requests)"))
        .scalar()
    ):
        raise RuntimeError("Cannot downgrade while Calendar slot requests exist")
    op.drop_table("calendar_slot_requests")
    op.execute("DROP FUNCTION guard_calendar_slot_request()")
    op.drop_constraint("uq_calendar_evidence_owner", "calendar_evidence", type_="unique")
