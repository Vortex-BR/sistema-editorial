"""Track provider calls recovered by deterministic editorial repair.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_plans",
        sa.Column(
            "seo_brief",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "recovered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("recovery_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "recovered_by_agent_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_agent_runs_recovered_by_agent_run_id",
        "agent_runs",
        "agent_runs",
        ["recovered_by_agent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE model_routes
           SET primary_provider = 'openai',
               primary_model = CASE agent_role
                 WHEN 'researcher' THEN 'gpt-4o-mini'
                 WHEN 'writer' THEN 'gpt-5-mini'
                 WHEN 'editor' THEN 'gpt-5-mini'
                 ELSE 'gpt-4.1-mini'
               END,
               fallback_provider = NULL,
               fallback_model = NULL,
               parameters = CASE agent_role
                 WHEN 'researcher' THEN
                   '{"temperature": 0.1, "max_output_tokens": 1536,
                     "timeout_seconds": 60, "max_retries": 1,
                     "input_cost_per_million": 0.15,
                     "output_cost_per_million": 0.6}'::jsonb
                 WHEN 'writer' THEN
                   '{"reasoning_effort": "low", "max_output_tokens": 8192,
                     "timeout_seconds": 180, "max_retries": 2,
                     "input_cost_per_million": 0.25,
                     "output_cost_per_million": 2.0}'::jsonb
                 WHEN 'editor' THEN
                   '{"reasoning_effort": "medium", "max_output_tokens": 4096,
                     "timeout_seconds": 180, "max_retries": 2,
                     "input_cost_per_million": 0.25,
                     "output_cost_per_million": 2.0}'::jsonb
                 WHEN 'planner' THEN
                   '{"temperature": 0.1, "max_output_tokens": 4096,
                     "timeout_seconds": 90, "max_retries": 2,
                     "input_cost_per_million": 0.4,
                     "output_cost_per_million": 1.6}'::jsonb
                 WHEN 'research_gatekeeper' THEN
                   '{"temperature": 0.0, "max_output_tokens": 2048,
                     "timeout_seconds": 90, "max_retries": 2,
                     "input_cost_per_million": 0.4,
                     "output_cost_per_million": 1.6}'::jsonb
                 ELSE
                   '{"temperature": 0.1, "max_output_tokens": 3072,
                     "timeout_seconds": 120, "max_retries": 2,
                     "input_cost_per_million": 0.4,
                     "output_cost_per_million": 1.6}'::jsonb
               END
         WHERE agent_role IN (
           'planner', 'researcher', 'research_gatekeeper',
           'writer', 'editor', 'skill_curator'
         )
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_runs_recovered_by_agent_run_id",
        "agent_runs",
        type_="foreignkey",
    )
    op.drop_column("agent_runs", "recovered_by_agent_run_id")
    op.drop_column("agent_runs", "recovery_code")
    op.drop_column("agent_runs", "recovered")
    op.drop_column("research_plans", "seo_brief")
