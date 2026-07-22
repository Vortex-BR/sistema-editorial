"""move editorial agents to OpenAI GPT-4o mini

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-15
"""

from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE model_routes
           SET primary_provider = 'openai',
               primary_model = 'gpt-4o-mini',
               fallback_provider = NULL,
               fallback_model = NULL
         WHERE agent_role IN (
           'planner', 'researcher', 'research_gatekeeper',
           'writer', 'editor', 'skill_curator'
         )
        """
    )


def downgrade() -> None:
    # Historical route values cannot be reconstructed safely. The schema and
    # existing run manifests remain compatible with the previous revision.
    pass
