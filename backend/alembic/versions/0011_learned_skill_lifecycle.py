"""Add evidence-based learned skill lifecycle and audit trail."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("project_id", postgresql.UUID(), nullable=True))
    op.add_column("skills", sa.Column("fingerprint", sa.String(64), nullable=True))
    op.add_column(
        "skills",
        sa.Column(
            "lifecycle_status",
            sa.String(30),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "auto_inject",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_foreign_key(
        "fk_skills_project_id_projects",
        "skills",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_skills_project_id", "skills", ["project_id"])
    op.create_index("ix_skills_lifecycle_status", "skills", ["lifecycle_status"])
    op.create_index(
        "uq_skills_project_fingerprint",
        "skills",
        ["project_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_skills_skill_lifecycle_status_valid",
        "skills",
        "lifecycle_status IN ('candidate', 'corroborated', 'human_approved', "
        "'stable', 'active', 'disabled', 'rejected')",
    )
    op.execute(
        """
        UPDATE skills AS skill
        SET project_id = article.project_id
        FROM skill_versions AS version
        JOIN articles AS article ON article.id = version.origin_article_id
        WHERE version.skill_id = skill.id
          AND version.version = skill.current_version
          AND skill.kind = 'learned'
        """
    )
    op.execute(
        """
        UPDATE skills
        SET lifecycle_status = 'candidate', enabled = false,
            stable = false, auto_inject = false
        WHERE kind = 'learned'
        """
    )

    op.create_table(
        "skill_validations",
        sa.Column("skill_version_id", postgresql.UUID(), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(), nullable=False),
        sa.Column("article_id", postgresql.UUID(), nullable=False),
        sa.Column("article_version_id", postgresql.UUID(), nullable=False),
        sa.Column(
            "evidence_source",
            sa.String(50),
            nullable=False,
            server_default="pipeline_outcome",
        ),
        sa.Column(
            "editorial_rework_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("rubric_score", sa.Float(), nullable=False),
        sa.Column(
            "factual_regression",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "corroborating",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "outcome_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("id", postgresql.UUID(), nullable=False),
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
            "editorial_rework_count >= 0",
            name="ck_skill_validations_skill_validation_rework_nonnegative",
        ),
        sa.CheckConstraint(
            "rubric_score >= 0 AND rubric_score <= 1",
            name="ck_skill_validations_skill_validation_rubric_range",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name="fk_skill_validations_skill_version_id_skill_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name="fk_skill_validations_pipeline_run_id_pipeline_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="fk_skill_validations_article_id_articles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["article_version_id"],
            ["article_versions.id"],
            name="fk_skill_validations_article_version_id_article_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skill_validations"),
        sa.UniqueConstraint(
            "skill_version_id",
            "pipeline_run_id",
            name="uq_skill_validation_run",
        ),
    )
    for column in (
        "skill_version_id",
        "pipeline_run_id",
        "article_id",
        "article_version_id",
    ):
        op.create_index(f"ix_skill_validations_{column}", "skill_validations", [column])

    op.create_table(
        "skill_lifecycle_events",
        sa.Column("skill_id", postgresql.UUID(), nullable=False),
        sa.Column("skill_version_id", postgresql.UUID(), nullable=True),
        sa.Column("pipeline_run_id", postgresql.UUID(), nullable=True),
        sa.Column("article_id", postgresql.UUID(), nullable=True),
        sa.Column("from_status", sa.String(30), nullable=False),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name="fk_skill_lifecycle_events_skill_id_skills",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name="fk_skill_lifecycle_events_skill_version_id_skill_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name="fk_skill_lifecycle_events_pipeline_run_id_pipeline_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="fk_skill_lifecycle_events_article_id_articles",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skill_lifecycle_events"),
    )
    for column in (
        "skill_id",
        "skill_version_id",
        "pipeline_run_id",
        "article_id",
        "action",
        "created_at",
    ):
        op.create_index(
            f"ix_skill_lifecycle_events_{column}",
            "skill_lifecycle_events",
            [column],
        )


def downgrade() -> None:
    op.drop_table("skill_lifecycle_events")
    op.drop_table("skill_validations")
    op.drop_constraint(
        "ck_skills_skill_lifecycle_status_valid", "skills", type_="check"
    )
    op.drop_index("uq_skills_project_fingerprint", table_name="skills")
    op.drop_index("ix_skills_lifecycle_status", table_name="skills")
    op.drop_index("ix_skills_project_id", table_name="skills")
    op.drop_constraint(
        "fk_skills_project_id_projects", "skills", type_="foreignkey"
    )
    op.drop_column("skills", "auto_inject")
    op.drop_column("skills", "lifecycle_status")
    op.drop_column("skills", "fingerprint")
    op.drop_column("skills", "project_id")
