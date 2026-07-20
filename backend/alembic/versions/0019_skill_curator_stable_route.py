"""move skill curator to the stable Gemini route

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-15
"""

from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE model_routes
           SET primary_provider = 'gemini',
               primary_model = 'gemini-3.5-flash',
               fallback_provider = NULL,
               fallback_model = NULL
         WHERE agent_role = 'skill_curator'
        """
    )


def downgrade() -> None:
    # Route data is intentionally not rewritten on downgrade. Historical values
    # cannot be reconstructed safely, and the 0018 schema remains compatible.
    pass
