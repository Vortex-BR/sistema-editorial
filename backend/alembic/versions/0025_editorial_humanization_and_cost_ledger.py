"""Editorial humanization, adaptive research, and provider cost ledger.

Revision ID: 0025_editorial_humanization_and_cost_ledger
Revises: 0024_writer_editorial_effort
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "billed_prompt_tokens", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "billed_completion_tokens", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "estimated_external_cost_usd",
            sa.Numeric(12, 6),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "pipeline_run_billed_tokens_nonnegative",
        "pipeline_runs",
        "billed_prompt_tokens >= 0 AND billed_completion_tokens >= 0",
    )
    op.create_check_constraint(
        "pipeline_run_external_cost_nonnegative",
        "pipeline_runs",
        "estimated_external_cost_usd >= 0",
    )

    op.add_column(
        "research_plans",
        sa.Column(
            "editorial_blueprint",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    op.add_column(
        "research_questions",
        sa.Column(
            "importance", sa.String(length=20), server_default="core", nullable=False
        ),
    )
    op.add_column(
        "research_questions",
        sa.Column("rationale", sa.Text(), server_default="", nullable=False),
    )
    op.create_index(
        "ix_research_questions_importance",
        "research_questions",
        ["importance"],
        unique=False,
    )
    # Existing plans predate the bounded 1..7 priority contract. Clamp
    # legacy values before adding the check so the production migration cannot
    # fail on otherwise valid historical research plans.
    op.execute(
        "UPDATE research_questions SET priority = GREATEST(1, LEAST(priority, 7))"
    )
    op.create_check_constraint(
        "research_question_importance_valid",
        "research_questions",
        "importance IN ('core', 'supporting', 'optional')",
    )
    op.create_check_constraint(
        "research_question_priority_range",
        "research_questions",
        "priority >= 1 AND priority <= 7",
    )

    op.create_table(
        "provider_attempts",
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("target_kind", sa.String(length=20), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "response_received", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "completion_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "estimated_cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_category", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "agent_run_id",
            "target_kind",
            "attempt_number",
            name="uq_provider_attempt_agent_target_number",
        ),
        sa.CheckConstraint(
            "target_kind IN ('primary', 'fallback')",
            name="provider_attempt_target_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'invalid_output')",
            name="provider_attempt_status_valid",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1 AND prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND estimated_cost_usd >= 0 AND latency_ms >= 0",
            name="provider_attempt_values_nonnegative",
        ),
    )
    op.create_index(
        "ix_provider_attempts_agent_run_id", "provider_attempts", ["agent_run_id"]
    )
    op.create_index(
        "ix_provider_attempts_project_id", "provider_attempts", ["project_id"]
    )
    op.create_index(
        "ix_provider_attempts_pipeline_run_id", "provider_attempts", ["pipeline_run_id"]
    )
    op.create_index("ix_provider_attempts_status", "provider_attempts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_provider_attempts_status", table_name="provider_attempts")
    op.drop_index(
        "ix_provider_attempts_pipeline_run_id", table_name="provider_attempts"
    )
    op.drop_index("ix_provider_attempts_project_id", table_name="provider_attempts")
    op.drop_index("ix_provider_attempts_agent_run_id", table_name="provider_attempts")
    op.drop_table("provider_attempts")
    op.drop_constraint(
        "research_question_priority_range", "research_questions", type_="check"
    )
    op.drop_constraint(
        "research_question_importance_valid", "research_questions", type_="check"
    )
    op.drop_index("ix_research_questions_importance", table_name="research_questions")
    op.drop_column("research_questions", "rationale")
    op.drop_column("research_questions", "importance")
    op.drop_column("research_plans", "editorial_blueprint")
    op.drop_constraint(
        "pipeline_run_external_cost_nonnegative", "pipeline_runs", type_="check"
    )
    op.drop_constraint(
        "pipeline_run_billed_tokens_nonnegative", "pipeline_runs", type_="check"
    )
    op.drop_column("pipeline_runs", "estimated_external_cost_usd")
    op.drop_column("pipeline_runs", "billed_completion_tokens")
    op.drop_column("pipeline_runs", "billed_prompt_tokens")
