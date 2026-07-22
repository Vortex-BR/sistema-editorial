"""Remove the deprecated jurisdiction field from active editorial contracts.

Revision ID: 0036
Revises: 0035
"""

from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("content_knowledge_contracts", "jurisdiction")


def downgrade() -> None:
    op.add_column(
        "content_knowledge_contracts",
        sa.Column("jurisdiction", sa.String(length=200), nullable=True),
    )
