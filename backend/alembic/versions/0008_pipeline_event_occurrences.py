"""Add cycle-aware identity and audit context to pipeline events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_events",
        sa.Column("stage_occurrence_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "pipeline_events", sa.Column("research_cycle", sa.Integer(), nullable=True)
    )
    op.add_column(
        "pipeline_events", sa.Column("editor_cycle", sa.Integer(), nullable=True)
    )
    op.add_column(
        "pipeline_events", sa.Column("run_attempt", sa.Integer(), nullable=True)
    )
    op.add_column(
        "pipeline_events", sa.Column("stage_attempt", sa.Integer(), nullable=True)
    )
    op.add_column(
        "pipeline_events",
        sa.Column("checkpoint_sequence", sa.Integer(), nullable=True),
    )
    op.add_column(
        "pipeline_events",
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_pipeline_events_agent_run_id",
        "pipeline_events",
        "agent_runs",
        ["agent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "event_research_cycle_nonnegative",
        "pipeline_events",
        "research_cycle IS NULL OR research_cycle >= 0",
    )
    op.create_check_constraint(
        "event_editor_cycle_nonnegative",
        "pipeline_events",
        "editor_cycle IS NULL OR editor_cycle >= 0",
    )
    op.create_check_constraint(
        "event_run_attempt_positive",
        "pipeline_events",
        "run_attempt IS NULL OR run_attempt >= 1",
    )
    op.create_check_constraint(
        "event_stage_attempt_positive",
        "pipeline_events",
        "stage_attempt IS NULL OR stage_attempt >= 1",
    )
    op.create_check_constraint(
        "event_checkpoint_sequence_positive",
        "pipeline_events",
        "checkpoint_sequence IS NULL OR checkpoint_sequence >= 1",
    )
    op.create_index(
        "ix_pipeline_events_stage_occurrence",
        "pipeline_events",
        ["pipeline_run_id", "stage_occurrence_id", "sequence"],
    )
    op.create_index(
        "ix_pipeline_events_agent_run_id",
        "pipeline_events",
        ["agent_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_events_agent_run_id", table_name="pipeline_events")
    op.drop_index(
        "ix_pipeline_events_stage_occurrence", table_name="pipeline_events"
    )
    op.drop_constraint(
        "event_checkpoint_sequence_positive", "pipeline_events", type_="check"
    )
    op.drop_constraint(
        "event_stage_attempt_positive", "pipeline_events", type_="check"
    )
    op.drop_constraint(
        "event_run_attempt_positive", "pipeline_events", type_="check"
    )
    op.drop_constraint(
        "event_editor_cycle_nonnegative", "pipeline_events", type_="check"
    )
    op.drop_constraint(
        "event_research_cycle_nonnegative", "pipeline_events", type_="check"
    )
    op.drop_constraint(
        "fk_pipeline_events_agent_run_id", "pipeline_events", type_="foreignkey"
    )
    op.drop_column("pipeline_events", "agent_run_id")
    op.drop_column("pipeline_events", "checkpoint_sequence")
    op.drop_column("pipeline_events", "stage_attempt")
    op.drop_column("pipeline_events", "run_attempt")
    op.drop_column("pipeline_events", "editor_cycle")
    op.drop_column("pipeline_events", "research_cycle")
    op.drop_column("pipeline_events", "stage_occurrence_id")
