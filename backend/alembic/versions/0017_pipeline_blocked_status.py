"""Separate policy blocks from technical pipeline failures."""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS "
            "'blocked' AFTER 'needs_human_approval'"
        )
        op.execute(
            "ALTER TYPE pipelinerunstatus ADD VALUE IF NOT EXISTS "
            "'blocked' AFTER 'needs_human_approval'"
        )


def downgrade() -> None:
    # Downgrade is intentionally lossy only for the newly introduced value.
    # Historical technical failures already stored as `failed` are untouched.
    op.execute(
        "UPDATE pipeline_runs SET status = 'failed' "
        "WHERE status::text = 'blocked'"
    )
    op.execute(
        "UPDATE projects SET status = 'failed' "
        "WHERE status::text = 'blocked'"
    )

    # The partial predicate contains enum constants and must be recreated after
    # the pipeline enum is replaced.
    op.drop_index(
        "ix_pipeline_runs_dispatch_eligibility",
        table_name="pipeline_runs",
    )
    op.execute("ALTER TABLE pipeline_runs ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status TYPE varchar(30) "
        "USING status::text"
    )
    op.execute("DROP TYPE pipelinerunstatus")
    op.execute(
        "CREATE TYPE pipelinerunstatus AS ENUM "
        "('queued', 'running', 'waiting_retry', 'needs_review', "
        "'needs_human_approval', 'failed', 'cancelled', 'completed', 'rejected')"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status TYPE pipelinerunstatus "
        "USING status::text::pipelinerunstatus"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status "
        "SET DEFAULT 'queued'::pipelinerunstatus"
    )
    op.create_index(
        "ix_pipeline_runs_dispatch_eligibility",
        "pipeline_runs",
        [
            "status",
            "next_retry_at",
            "dispatch_not_before",
            "dispatch_expires_at",
        ],
        postgresql_where=sa.text("status IN ('queued', 'waiting_retry')"),
    )

    op.execute("ALTER TABLE projects ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE projects ALTER COLUMN status TYPE varchar(30) "
        "USING status::text"
    )
    op.execute("DROP TYPE projectstatus")
    op.execute(
        "CREATE TYPE projectstatus AS ENUM "
        "('draft', 'queued', 'running', 'needs_review', "
        "'needs_human_approval', 'completed', 'rejected', 'failed')"
    )
    op.execute(
        "ALTER TABLE projects ALTER COLUMN status TYPE projectstatus "
        "USING status::text::projectstatus"
    )
