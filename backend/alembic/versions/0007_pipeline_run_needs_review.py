"""Preserve human review as a distinct terminal pipeline outcome."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires a newly-added enum label to be committed before it is
    # used by the data correction below.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE pipelinerunstatus ADD VALUE IF NOT EXISTS "
            "'needs_review' AFTER 'waiting_retry'"
        )
    op.execute(
        """
        WITH candidates AS (
          SELECT run.id, run.status::text AS previous_status, run.current_stage
          FROM pipeline_runs run
          JOIN projects project ON project.id = run.project_id
          WHERE run.status::text IN ('failed', 'cancelled')
            AND (
              EXISTS (
                SELECT 1 FROM pipeline_events event
                WHERE event.pipeline_run_id = run.id
                  AND event.event_type = 'pipeline.needs_review'
              )
              OR (
                run.trigger_type::text = 'legacy'
                AND project.status::text = 'needs_review'
              )
            )
        )
        INSERT INTO pipeline_state_transitions (
          id, pipeline_run_id, from_status, to_status, stage,
          origin, reason, created_at
        )
        SELECT md5(id::text || '-needs-review-0007')::uuid, id,
          previous_status, 'needs_review', current_stage,
          'migration.0007',
          'Correct legacy human-review outcome previously represented as failure',
          now()
        FROM candidates
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE pipeline_runs run
        SET status = 'needs_review',
          finished_at = COALESCE(run.finished_at, now()),
          failed_at = NULL,
          error_code = NULL,
          error_message = NULL,
          retryable = false,
          next_retry_at = NULL,
          lock_version = run.lock_version + 1,
          updated_at = now()
        FROM projects project
        WHERE project.id = run.project_id
          AND run.status::text IN ('failed', 'cancelled')
          AND (
            EXISTS (
              SELECT 1 FROM pipeline_events event
              WHERE event.pipeline_run_id = run.id
                AND event.event_type = 'pipeline.needs_review'
            )
            OR (
              run.trigger_type::text = 'legacy'
              AND project.status::text = 'needs_review'
            )
          )
        """
    )


def downgrade() -> None:
    # The previous schema cannot represent this editorial outcome. Preserve row
    # validity by mapping it to its former representation before rebuilding the
    # PostgreSQL enum without the new label.
    op.execute(
        "DELETE FROM pipeline_state_transitions "
        "WHERE origin = 'migration.0007'"
    )
    op.execute(
        "UPDATE pipeline_runs SET status = 'failed' "
        "WHERE status = 'needs_review'"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status TYPE varchar(30) "
        "USING status::text"
    )
    op.execute("DROP TYPE pipelinerunstatus")
    op.execute(
        "CREATE TYPE pipelinerunstatus AS ENUM "
        "('queued', 'running', 'waiting_retry', 'failed', 'cancelled', 'completed')"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status TYPE pipelinerunstatus "
        "USING status::pipelinerunstatus"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status SET DEFAULT 'queued'"
    )
