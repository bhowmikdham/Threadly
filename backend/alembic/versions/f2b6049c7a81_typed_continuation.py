"""Durable questions and append-only typed inputs, preserving original task requests."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f2b6049c7a81"
down_revision = "e9b7120c4a63"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assistant_tasks", sa.Column("continuation_release", postgresql.JSONB()))
    op.add_column("assistant_tasks", sa.Column("effective_context_snapshot_id", sa.String(36)))
    op.create_foreign_key(
        "fk_task_effective_context",
        "assistant_tasks",
        "context_snapshots",
        ["effective_context_snapshot_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "assistant_tasks",
        sa.Column("input_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_task_input_version", "assistant_tasks", "input_version >= 0 AND input_version <= 5"
    )
    op.create_table(
        "task_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("input_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            ondelete="CASCADE",
            name="fk_question_owned_task",
        ),
        sa.UniqueConstraint("id", "task_id", "user_id", name="uq_question_owned_id"),
        sa.UniqueConstraint("task_id", "input_version", name="uq_question_input_version"),
        sa.CheckConstraint("state IN ('open','answered','cancelled')", name="ck_question_state"),
        sa.CheckConstraint("task_version >= 1 AND input_version >= 0", name="ck_question_versions"),
    )
    op.create_table(
        "task_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(36), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("input_version", sa.Integer(), nullable=False),
        sa.Column("answer", postgresql.JSONB(), nullable=False),
        sa.Column("effective_fields", postgresql.JSONB(), nullable=False),
        sa.Column("context_snapshot_id", sa.String(36)),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("draft_input", postgresql.JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            ondelete="CASCADE",
            name="fk_input_owned_task",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "task_id", "user_id"],
            ["task_questions.id", "task_questions.task_id", "task_questions.user_id"],
            ondelete="CASCADE",
            name="fk_input_owned_question",
        ),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id", "user_id"],
            ["context_snapshots.id", "context_snapshots.user_id"],
            ondelete="CASCADE",
            name="fk_input_owned_context",
        ),
        sa.UniqueConstraint("task_id", "request_id", name="uq_task_input_request"),
        sa.UniqueConstraint("task_id", "input_version", name="uq_task_input_version"),
        sa.UniqueConstraint("question_id", name="uq_input_question"),
        sa.CheckConstraint("input_version >= 1 AND input_version <= 5", name="ck_input_version"),
    )


def downgrade():
    count = op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM assistant_tasks "
            "WHERE continuation_release IS NOT NULL OR input_version > 0 "
            "OR effective_context_snapshot_id IS NOT NULL) + "
            "(SELECT count(*) FROM task_questions) + (SELECT count(*) FROM task_inputs)"
        )
    )
    if count:
        raise RuntimeError(
            "Cannot downgrade while continuation-enabled tasks or input history exist."
        )
    op.drop_table("task_inputs")
    op.drop_table("task_questions")
    op.drop_constraint("ck_task_input_version", "assistant_tasks", type_="check")
    op.drop_column("assistant_tasks", "input_version")
    op.drop_constraint("fk_task_effective_context", "assistant_tasks", type_="foreignkey")
    op.drop_column("assistant_tasks", "effective_context_snapshot_id")
    op.drop_column("assistant_tasks", "continuation_release")
