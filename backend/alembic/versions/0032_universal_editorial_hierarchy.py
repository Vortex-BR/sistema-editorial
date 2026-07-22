"""Add universal editorial hierarchy fields to V2 planning.

Revision ID: 0032
Revises: 0031
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_plans",
        sa.Column(
            "hierarchy_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "research_questions",
        sa.Column(
            "node_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.drop_constraint(
        "research_question_priority_range",
        "research_questions",
        type_="check",
    )
    op.create_check_constraint(
        "research_question_priority_range",
        "research_questions",
        "priority >= 1 AND priority <= 20",
    )


def downgrade() -> None:
    op.drop_constraint(
        "research_question_priority_range",
        "research_questions",
        type_="check",
    )
    op.create_check_constraint(
        "research_question_priority_range",
        "research_questions",
        "priority >= 1 AND priority <= 7",
    )
    op.drop_column("research_questions", "node_ids")
    op.drop_column("research_plans", "hierarchy_json")
