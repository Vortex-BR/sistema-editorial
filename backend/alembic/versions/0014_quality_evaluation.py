"""Add independent versioned quality evaluations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quality_evaluations",
        sa.Column("project_id", postgresql.UUID(), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(), nullable=False),
        sa.Column("article_version_id", postgresql.UUID(), nullable=False),
        sa.Column("rubric_version", sa.String(80), nullable=False),
        sa.Column("rubric_checksum", sa.String(64), nullable=False),
        sa.Column(
            "evaluator_kind",
            sa.String(40),
            nullable=False,
            server_default="deterministic",
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("thresholds_json", postgresql.JSONB(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("result_checksum", sa.String(64), nullable=False),
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "overall_score >= 0 AND overall_score <= 1",
            name="ck_quality_evaluations_quality_evaluation_score_range",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'needs_improvement', 'blocked')",
            name="ck_quality_evaluations_quality_evaluation_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_quality_evaluations_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name="fk_quality_evaluations_pipeline_run_id_pipeline_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["article_version_id"],
            ["article_versions.id"],
            name="fk_quality_evaluations_article_version_id_article_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quality_evaluations"),
        sa.UniqueConstraint(
            "pipeline_run_id", name="uq_quality_evaluations_pipeline_run_id"
        ),
        sa.UniqueConstraint(
            "article_version_id", name="uq_quality_evaluations_article_version_id"
        ),
    )
    op.create_index(
        "ix_quality_evaluations_project_id", "quality_evaluations", ["project_id"]
    )
    op.create_index(
        "ix_quality_evaluations_pipeline_run_id",
        "quality_evaluations",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_quality_evaluations_article_version_id",
        "quality_evaluations",
        ["article_version_id"],
    )
    op.create_index(
        "ix_quality_evaluations_status", "quality_evaluations", ["status"]
    )
    op.execute(
        """
        CREATE FUNCTION prevent_quality_evaluation_update()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'quality evaluations are immutable'
            USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER quality_evaluations_immutable
        BEFORE UPDATE ON quality_evaluations
        FOR EACH ROW EXECUTE FUNCTION prevent_quality_evaluation_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS quality_evaluations_immutable "
        "ON quality_evaluations"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_quality_evaluation_update()")
    op.drop_table("quality_evaluations")
