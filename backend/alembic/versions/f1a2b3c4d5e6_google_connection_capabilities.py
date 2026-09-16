"""Persist verified Google connection metadata and OAuth capability grants."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "d9302f5b7a14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "google_scopes",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "google_identity",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("users", sa.Column("google_email_verified", sa.Boolean(), nullable=True))
    op.add_column(
        "users",
        sa.Column("google_connected", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("google_account_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "users", sa.Column("google_connected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE users
        SET google_connected = TRUE, google_connected_at = created_at
        WHERE access_token_enc IS NOT NULL OR refresh_token_enc IS NOT NULL
        """
    )

    op.add_column(
        "users", sa.Column("google_token_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_check_constraint(
        "ck_google_versions", "users", "google_account_version >= 1 AND google_token_version >= 1"
    )
    op.create_table(
        "google_oauth_sessions",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("code_challenge", sa.String(43), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("account_version", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(user_id IS NULL) = (account_version IS NULL)", name="ck_oauth_owner_version"
        ),
    )
    op.create_index("ix_oauth_expiry", "google_oauth_sessions", ["expires_at"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM assistant_actions)")).scalar():
        raise RuntimeError(
            "Cannot downgrade Google capability versions while action history exists"
        )
    op.drop_index("ix_oauth_expiry", table_name="google_oauth_sessions")
    op.drop_table("google_oauth_sessions")
    op.drop_constraint("ck_google_versions", "users", type_="check")
    op.drop_column("users", "google_token_version")
    op.drop_column("users", "google_connected_at")
    op.drop_column("users", "google_account_version")
    op.drop_column("users", "google_connected")
    op.drop_column("users", "google_email_verified")
    op.drop_column("users", "google_identity")
    op.drop_column("users", "google_scopes")
