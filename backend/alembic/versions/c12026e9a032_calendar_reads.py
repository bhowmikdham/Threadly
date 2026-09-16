"""Owned Calendar preferences and short-lived busy evidence.

Revision ID: c12026e9a032
Revises: b10c026e9a31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c12026e9a032"
down_revision = "b10c026e9a31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_preferences",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("account_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("preferences", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "version >= 1 AND account_version >= 1", name="ck_calendar_pref_versions"
        ),
    )
    op.create_table(
        "calendar_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("calendar_preferences.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("preferences_version", sa.Integer(), nullable=False),
        sa.Column("account_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "preferences_version >= 1 AND account_version >= 1 AND expires_at > checked_at",
            name="ck_calendar_evidence_versions",
        ),
    )
    op.create_index("ix_calendar_evidence_user_id", "calendar_evidence", ["user_id"])
    op.create_index("ix_calendar_evidence_expires_at", "calendar_evidence", ["expires_at"])
    op.execute("""CREATE FUNCTION guard_calendar_preferences() RETURNS trigger AS $$
      BEGIN
        IF NEW.user_id != OLD.user_id OR NEW.version != OLD.version + 1
           OR NEW.created_at != OLD.created_at THEN
          RAISE EXCEPTION 'Calendar preference version must advance' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER calendar_preferences_guard BEFORE UPDATE ON calendar_preferences
      FOR EACH ROW EXECUTE FUNCTION guard_calendar_preferences()""")
    op.execute("""CREATE FUNCTION guard_calendar_evidence() RETURNS trigger AS $$
      BEGIN
        RAISE EXCEPTION 'Calendar evidence is immutable' USING ERRCODE='23514';
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER calendar_evidence_guard BEFORE UPDATE ON calendar_evidence
      FOR EACH ROW EXECUTE FUNCTION guard_calendar_evidence()""")


def downgrade():
    conn = op.get_bind()
    if conn.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM calendar_preferences) "
            "OR EXISTS(SELECT 1 FROM calendar_evidence)"
        )
    ).scalar():
        raise RuntimeError("Cannot downgrade while Calendar preferences or evidence exist")
    op.drop_table("calendar_evidence")
    op.drop_table("calendar_preferences")
    op.execute("DROP FUNCTION guard_calendar_evidence()")
    op.execute("DROP FUNCTION guard_calendar_preferences()")
