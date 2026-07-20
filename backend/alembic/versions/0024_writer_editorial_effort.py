"""Raise reasoning effort for repository-default writer routes.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE model_routes
           SET parameters = jsonb_set(
                 parameters,
                 '{reasoning_effort}',
                 '"medium"'::jsonb,
                 true
               )
         WHERE agent_role = 'writer'
           AND primary_provider = 'openai'
           AND primary_model = 'gpt-5-mini'
           AND parameters ->> 'reasoning_effort' = 'low'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE model_routes
           SET parameters = jsonb_set(
                 parameters,
                 '{reasoning_effort}',
                 '"low"'::jsonb,
                 true
               )
         WHERE agent_role = 'writer'
           AND primary_provider = 'openai'
           AND primary_model = 'gpt-5-mini'
           AND parameters ->> 'reasoning_effort' = 'medium'
        """
    )
