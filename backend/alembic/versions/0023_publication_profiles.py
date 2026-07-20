"""Add reusable publication profiles and per-content editorial briefs.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "publication_profiles",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("brand_name", sa.String(length=200), nullable=False),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("segment", sa.String(length=160), nullable=False),
        sa.Column("brand_description", sa.Text(), nullable=False),
        sa.Column("mission", sa.Text(), nullable=True),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("audience_description", sa.Text(), nullable=False),
        sa.Column("tone_of_voice", sa.Text(), nullable=False),
        sa.Column("research_summary", sa.Text(), nullable=True),
        sa.Column(
            "profile_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="publication_profiles_status_valid",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="publication_profiles_version_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_publication_profiles_segment"),
        "publication_profiles",
        ["segment"],
        unique=False,
    )
    op.create_index(
        op.f("ix_publication_profiles_status"),
        "publication_profiles",
        ["status"],
        unique=False,
    )
    op.add_column(
        "projects",
        sa.Column(
            "publication_profile_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "briefing",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        op.f("ix_projects_publication_profile_id"),
        "projects",
        ["publication_profile_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_projects_publication_profile_id",
        "projects",
        "publication_profiles",
        ["publication_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_projects_publication_profile_id",
        "projects",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_projects_publication_profile_id"),
        table_name="projects",
    )
    op.drop_column("projects", "briefing")
    op.drop_column("projects", "publication_profile_id")
    op.drop_index(
        op.f("ix_publication_profiles_status"),
        table_name="publication_profiles",
    )
    op.drop_index(
        op.f("ix_publication_profiles_segment"),
        table_name="publication_profiles",
    )
    op.drop_table("publication_profiles")
