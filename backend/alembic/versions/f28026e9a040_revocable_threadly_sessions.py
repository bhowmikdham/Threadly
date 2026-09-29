"""Separate Threadly sign-out from Google account and grant changes.

Revision ID: f28026e9a040
Revises: c23026e9a039
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "f28026e9a040"
down_revision = "c23026e9a039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("threadly_session_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_threadly_session_version", "users", "threadly_session_version >= 1"
    )
    op.add_column(
        "google_oauth_sessions", sa.Column("session_version", sa.Integer(), nullable=True)
    )
    # Legacy owned states were authorized by JWTs without revocable session
    # generations. Force a fresh reconnect rather than blessing their rollover.
    op.execute("DELETE FROM google_oauth_sessions WHERE user_id IS NOT NULL")
    op.create_check_constraint(
        "ck_oauth_session_version",
        "google_oauth_sessions",
        "(user_id IS NULL) = (session_version IS NULL)",
    )


def downgrade() -> None:
    # Even an empty users table cannot prove there are no old JWTs. The operator
    # must first stop every API issuer, rotate the signing key while stopped,
    # then explicitly confirm this offline rollback. Flags are assertions,
    # not a substitute for the shutdown/key-rotation procedure.
    if (
        os.environ.get("THREADLY_AUTH_SERVICES_STOPPED") != "1"
        or os.environ.get("THREADLY_SESSION_SIGNING_KEY_ROTATED") != "1"
    ):
        raise RuntimeError("Cannot downgrade session revocation without offline key rotation")
    op.drop_constraint("ck_oauth_session_version", "google_oauth_sessions", type_="check")
    op.drop_column("google_oauth_sessions", "session_version")
    op.drop_constraint("ck_threadly_session_version", "users", type_="check")
    op.drop_column("users", "threadly_session_version")
