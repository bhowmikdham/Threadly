"""Add versioned MVP workflow input without changing historical task contracts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a17026e9a037"
down_revision = "f14026e9a036"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_artifact_edit", "artifact_revisions", type_="check")
    op.create_check_constraint(
        "ck_artifact_edit",
        "artifact_revisions",
        "(revision = 1 AND edit_request_id IS NULL AND edit_request_hash IS NULL) OR "
        "(revision > 1 AND edit_request_id IS NOT NULL AND edit_request_hash IS NOT NULL "
        "AND ((draft_envelope IS NOT NULL AND COALESCE(payload->>'kind', '') = 'draft') "
        "OR (COALESCE(payload->>'kind', '') = 'plan' AND "
        "COALESCE(provenance->>'policy', '') = 'action-plan-1.0.0')))",
    )
    op.drop_constraint("ck_step_ordinal", "assistant_steps", type_="check")
    op.create_check_constraint(
        "ck_step_ordinal",
        "assistant_steps",
        "ordinal IN (1, 2) OR (ordinal = 3 AND "
        "COALESCE(release->>'workflow','') = 'mvp-workflow-1.0.0')",
    )
    op.add_column(
        "assistant_tasks", sa.Column("workflow_input", postgresql.JSONB(none_as_null=True))
    )
    op.create_check_constraint(
        "ck_task_workflow_input",
        "assistant_tasks",
        "workflow_input IS NULL OR (scheduling_input IS NULL AND read_input IS NULL "
        "AND compound_input IS NULL AND COALESCE(release->>'workflow','') = 'mvp-workflow-1.0.0')",
    )
    op.execute("""CREATE FUNCTION guard_workflow_input() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.workflow_input IS DISTINCT FROM OLD.workflow_input THEN
        RAISE EXCEPTION 'Workflow input is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER workflow_input_guard BEFORE UPDATE ON assistant_tasks "
        "FOR EACH ROW EXECUTE FUNCTION guard_workflow_input()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM assistant_tasks WHERE workflow_input IS NOT NULL)
      OR EXISTS (SELECT 1 FROM assistant_steps WHERE ordinal > 2)
      OR EXISTS (SELECT 1 FROM artifact_revisions
        WHERE revision > 1 AND payload->>'kind' = 'plan') THEN
        RAISE EXCEPTION 'Cannot downgrade while MVP workflows exist';
      END IF;
    END $$""")
    op.execute("DROP TRIGGER workflow_input_guard ON assistant_tasks")
    op.execute("DROP FUNCTION guard_workflow_input()")
    op.drop_constraint("ck_task_workflow_input", "assistant_tasks", type_="check")
    op.drop_column("assistant_tasks", "workflow_input")
    op.drop_constraint("ck_artifact_edit", "artifact_revisions", type_="check")
    op.create_check_constraint(
        "ck_artifact_edit",
        "artifact_revisions",
        "(revision = 1 AND edit_request_id IS NULL AND edit_request_hash IS NULL) OR "
        "(revision > 1 AND edit_request_id IS NOT NULL AND edit_request_hash IS NOT NULL "
        "AND draft_envelope IS NOT NULL AND COALESCE(payload->>'kind', '') = 'draft')",
    )
    op.drop_constraint("ck_step_ordinal", "assistant_steps", type_="check")
    op.create_check_constraint("ck_step_ordinal", "assistant_steps", "ordinal IN (1, 2)")
