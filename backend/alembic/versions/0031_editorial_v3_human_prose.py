"""Editorial V3 human prose profile and larger writer envelope.

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE v3_procedural_quality_evaluations
        ALTER COLUMN rubric_version
        SET DEFAULT 'quality-rubric.procedural-guide.v3'
        """
    )
    # Structured article JSON carries evidence per factual sentence.  A real
    # 2,600–3,500 word guide needs more output room than the visible article
    # alone suggests; otherwise the model tends to compress the body or end at
    # the token ceiling.
    op.execute(
        """
        UPDATE model_routes
           SET parameters = (COALESCE(parameters, '{}'::jsonb) - 'temperature')
                || jsonb_build_object(
                     'reasoning_effort', 'low',
                     'max_output_tokens', 20000,
                     'timeout_seconds', 300,
                     'max_retries', 1,
                     'input_cost_per_million', 2.5,
                     'output_cost_per_million', 15.0
                   )
         WHERE agent_role = 'writer'
           AND primary_provider = 'openai'
           AND primary_model = 'gpt-5.4'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE v3_procedural_quality_evaluations
        ALTER COLUMN rubric_version
        SET DEFAULT 'quality-rubric.procedural-guide.v2'
        """
    )
    # Operational model-route settings are intentionally not reduced on
    # downgrade; lowering the writer output limit could reintroduce truncation.
