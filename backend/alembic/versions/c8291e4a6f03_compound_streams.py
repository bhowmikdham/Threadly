"""Separate intermediate streams and pin the final task result without renumbering history."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c8291e4a6f03"
down_revision = "b7180d3f9e62"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "artifact_revisions",
        sa.Column("stream_key", sa.String(32), server_default="result", nullable=False),
    )
    op.drop_constraint("uq_artifact_task_revision", "artifact_revisions", type_="unique")
    op.create_unique_constraint(
        "uq_artifact_stream_revision", "artifact_revisions", ["task_id", "stream_key", "revision"]
    )
    op.create_unique_constraint(
        "uq_artifact_task_owner", "artifact_revisions", ["id", "task_id", "user_id"]
    )
    op.add_column("assistant_tasks", sa.Column("final_artifact_id", sa.String(36)))
    op.add_column(
        "assistant_tasks", sa.Column("compound_input", postgresql.JSONB(none_as_null=True))
    )
    op.execute("""UPDATE assistant_tasks t SET final_artifact_id = (
        SELECT a.id FROM artifact_revisions a WHERE a.task_id=t.id AND a.user_id=t.user_id
        ORDER BY a.revision DESC LIMIT 1)""")
    op.create_foreign_key(
        "fk_task_final_artifact",
        "assistant_tasks",
        "artifact_revisions",
        ["final_artifact_id", "id", "user_id"],
        ["id", "task_id", "user_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "assistant_steps",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("release", postgresql.JSONB(), nullable=False),
        sa.Column("artifact_id", sa.String(36)),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("error_code", sa.String(64)),
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            name="fk_step_owned_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "task_id", "user_id"],
            ["artifact_revisions.id", "artifact_revisions.task_id", "artifact_revisions.user_id"],
            name="fk_step_owned_artifact",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("ordinal IN (1, 2)", name="ck_step_ordinal"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_step_attempts"),
        sa.CheckConstraint(
            "state IN ('pending','running','succeeded','failed','cancelled')", name="ck_step_state"
        ),
        sa.CheckConstraint(
            "(state = 'succeeded' AND artifact_id IS NOT NULL AND output_hash IS NOT NULL) OR "
            "(state != 'succeeded' AND artifact_id IS NULL AND output_hash IS NULL)",
            name="ck_step_output",
        ),
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(
        sa.text("""SELECT EXISTS(SELECT 1 FROM assistant_steps)
        OR EXISTS(SELECT 1 FROM assistant_tasks WHERE compound_input IS NOT NULL)
        OR EXISTS(SELECT 1 FROM artifact_revisions WHERE stream_key != 'result')""")
    ).scalar():
        raise RuntimeError("Cannot downgrade while compound tasks or intermediate artifacts exist")
    op.drop_table("assistant_steps")
    op.drop_constraint("fk_task_final_artifact", "assistant_tasks", type_="foreignkey")
    op.drop_column("assistant_tasks", "compound_input")
    op.drop_column("assistant_tasks", "final_artifact_id")
    op.drop_constraint("uq_artifact_task_owner", "artifact_revisions", type_="unique")
    op.drop_constraint("uq_artifact_stream_revision", "artifact_revisions", type_="unique")
    op.create_unique_constraint(
        "uq_artifact_task_revision", "artifact_revisions", ["task_id", "revision"]
    )
    op.drop_column("artifact_revisions", "stream_key")
