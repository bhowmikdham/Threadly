"""Append-only draft revisions and exact review acknowledgements.

Revision ID: e9b7120c4a63
Revises: c6e0419a72df
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e9b7120c4a63"
down_revision = "c6e0419a72df"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("artifact_revisions", sa.Column("draft_envelope", postgresql.JSONB()))
    op.add_column("artifact_revisions", sa.Column("edit_request_id", sa.String(128)))
    op.add_column("artifact_revisions", sa.Column("edit_request_hash", sa.String(64)))
    op.execute("""
        UPDATE artifact_revisions AS a SET draft_envelope = t.draft_input
        FROM assistant_tasks AS t WHERE a.task_id = t.id AND a.user_id = t.user_id
        AND a.payload->>'kind' = 'draft'
    """)
    op.drop_constraint("uq_summary_task_artifact", "artifact_revisions", type_="unique")
    op.drop_constraint("ck_artifact_initial_revision", "artifact_revisions", type_="check")
    op.create_unique_constraint(
        "uq_artifact_task_revision", "artifact_revisions", ["task_id", "revision"]
    )
    op.create_unique_constraint(
        "uq_artifact_edit_request", "artifact_revisions", ["task_id", "edit_request_id"]
    )
    op.create_unique_constraint("uq_artifact_owner", "artifact_revisions", ["id", "user_id"])
    op.create_check_constraint("ck_artifact_revision", "artifact_revisions", "revision >= 1")
    op.create_check_constraint(
        "ck_artifact_edit",
        "artifact_revisions",
        "(revision = 1 AND edit_request_id IS NULL AND edit_request_hash IS NULL) OR "
        "(revision > 1 AND edit_request_id IS NOT NULL AND edit_request_hash IS NOT NULL "
        "AND draft_envelope IS NOT NULL AND COALESCE(payload->>'kind', '') = 'draft')",
    )
    op.create_table(
        "draft_reviews",
        sa.Column("artifact_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "user_id"],
            ["artifact_revisions.id", "artifact_revisions.user_id"],
            ondelete="CASCADE",
            name="fk_review_owned_artifact",
        ),
    )


def downgrade():
    count = op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM artifact_revisions WHERE revision > 1) + "
            "(SELECT count(*) FROM draft_reviews)"
        )
    )
    if count:
        raise RuntimeError("Cannot downgrade while edited drafts or review records exist.")
    op.drop_table("draft_reviews")
    for name in ("ck_artifact_edit", "ck_artifact_revision"):
        op.drop_constraint(name, "artifact_revisions", type_="check")
    for name in ("uq_artifact_owner", "uq_artifact_edit_request", "uq_artifact_task_revision"):
        op.drop_constraint(name, "artifact_revisions", type_="unique")
    op.create_unique_constraint("uq_summary_task_artifact", "artifact_revisions", ["task_id"])
    op.create_check_constraint("ck_artifact_initial_revision", "artifact_revisions", "revision = 1")
    for name in ("edit_request_hash", "edit_request_id", "draft_envelope"):
        op.drop_column("artifact_revisions", name)
