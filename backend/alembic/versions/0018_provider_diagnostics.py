"""provider diagnostics and stable Gemini route

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("error_code", sa.String(100)))
    op.add_column("agent_runs", sa.Column("error_category", sa.String(40)))
    op.add_column("agent_runs", sa.Column("http_status", sa.Integer()))
    op.add_column("agent_runs", sa.Column("retryable", sa.Boolean()))
    op.add_column("agent_runs", sa.Column("correlation_id", sa.String(36)))

    op.execute(
        """
        UPDATE model_routes
           SET primary_provider = 'gemini',
               primary_model = 'gemini-3.5-flash',
               fallback_provider = NULL,
               fallback_model = NULL
         WHERE agent_role IN (
           'planner', 'researcher', 'research_gatekeeper',
           'writer', 'editor', 'skill_curator'
         )
        """
    )
    op.execute(
        """
        UPDATE model_routes
           SET parameters = jsonb_set(parameters, '{max_retries}', '2'::jsonb, true)
         WHERE parameters ? 'max_retries'
           AND jsonb_typeof(parameters->'max_retries') = 'number'
           AND (parameters->>'max_retries')::integer > 2
        """
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "correlation_id")
    op.drop_column("agent_runs", "retryable")
    op.drop_column("agent_runs", "http_status")
    op.drop_column("agent_runs", "error_category")
    op.drop_column("agent_runs", "error_code")
