"""persist international search queries per research question

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_questions",
        sa.Column(
            "semantic_terms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "research_questions",
        sa.Column(
            "search_queries",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("research_questions", "search_queries")
    op.drop_column("research_questions", "semantic_terms")
