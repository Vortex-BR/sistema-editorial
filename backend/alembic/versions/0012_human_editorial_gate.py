"""Add the human editor-in-chief publication gate."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS "
            "'needs_human_approval' AFTER 'needs_review'"
        )
        op.execute(
            "ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS "
            "'rejected' AFTER 'completed'"
        )
        op.execute(
            "ALTER TYPE pipelinerunstatus ADD VALUE IF NOT EXISTS "
            "'needs_human_approval' AFTER 'needs_review'"
        )
        op.execute(
            "ALTER TYPE pipelinerunstatus ADD VALUE IF NOT EXISTS "
            "'rejected' AFTER 'completed'"
        )

    op.create_table(
        "human_editorial_reviews",
        sa.Column("project_id", postgresql.UUID(), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(), nullable=False),
        sa.Column("article_version_id", postgresql.UUID(), nullable=False),
        sa.Column("reviewer", sa.String(160), nullable=True),
        sa.Column(
            "decision",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column(
            "review_package_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_idempotency_key", sa.String(160), nullable=True),
        sa.Column("revision_run_id", postgresql.UUID(), nullable=True),
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
            "decision IN ('pending', 'approved', 'rejected', 'revision_requested')",
            name=(
                "ck_human_editorial_reviews_"
                "human_editorial_review_decision_valid"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_human_editorial_reviews_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name=(
                "fk_human_editorial_reviews_pipeline_run_id_pipeline_runs"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["article_version_id"],
            ["article_versions.id"],
            name=(
                "fk_human_editorial_reviews_article_version_id_"
                "article_versions"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revision_run_id"],
            ["pipeline_runs.id"],
            name=(
                "fk_human_editorial_reviews_revision_run_id_pipeline_runs"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_human_editorial_reviews"),
        sa.UniqueConstraint(
            "pipeline_run_id",
            name="uq_human_editorial_reviews_pipeline_run_id",
        ),
        sa.UniqueConstraint(
            "article_version_id",
            name="uq_human_editorial_reviews_article_version_id",
        ),
        sa.UniqueConstraint(
            "decision_idempotency_key",
            name="uq_human_editorial_reviews_decision_idempotency_key",
        ),
    )
    for column in (
        "project_id",
        "pipeline_run_id",
        "article_version_id",
        "decision",
        "revision_run_id",
    ):
        op.create_index(
            f"ix_human_editorial_reviews_{column}",
            "human_editorial_reviews",
            [column],
        )

    op.execute(
        """
        INSERT INTO human_editorial_reviews (
          id, project_id, pipeline_run_id, article_version_id,
          decision, review_package_json, created_at, updated_at
        )
        SELECT md5(run.id::text || '-human-editorial-review-0012')::uuid,
          run.project_id, run.id, version.id, 'pending',
          jsonb_build_object(
            'article_version_id', version.id,
            'article_version', version.version,
            'pipeline_run_id', run.id,
            'seo', COALESCE(version.seo_metadata, '{}'::jsonb),
            'facts', '[]'::jsonb,
            'sources', '[]'::jsonb,
            'coverage', jsonb_build_object(
              'complete', false,
              'questions', '[]'::jsonb
            ),
            'conflicts', '[]'::jsonb,
            'changes', jsonb_build_object(
              'previous_version', NULL,
              'change_reason', version.change_reason
            ),
            'risks', jsonb_build_array(
              'Conteúdo histórico migrado: revisão humana obrigatória.'
            ),
            'migrated_by', '0012'
          ),
          now(), now()
        FROM articles article
        JOIN article_versions version
          ON version.article_id = article.id
         AND version.version = article.current_version
        JOIN pipeline_runs run ON run.id = version.pipeline_run_id
        WHERE run.status::text = 'completed'
        ON CONFLICT (pipeline_run_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO pipeline_state_transitions (
          id, pipeline_run_id, from_status, to_status, stage,
          origin, reason, created_at
        )
        SELECT md5(run.id::text || '-human-gate-0012')::uuid,
          run.id, 'completed', 'needs_human_approval',
          'human_approval', 'migration.0012',
          'Legacy automated completion requires explicit human approval', now()
        FROM pipeline_runs run
        WHERE run.status::text = 'completed'
          AND EXISTS (
            SELECT 1 FROM human_editorial_reviews review
            WHERE review.pipeline_run_id = run.id
          )
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE article_versions version
        SET editorial_status = 'needs_human_approval', updated_at = now()
        FROM human_editorial_reviews review
        WHERE review.article_version_id = version.id
          AND review.decision = 'pending'
        """
    )
    op.execute(
        """
        UPDATE articles article
        SET status = 'needs_human_approval', updated_at = now()
        FROM human_editorial_reviews review
        WHERE review.project_id = article.project_id
          AND review.decision = 'pending'
        """
    )
    op.execute(
        """
        UPDATE pipeline_runs run
        SET status = 'needs_human_approval', current_stage = 'human_approval',
          lock_version = lock_version + 1, updated_at = now()
        FROM human_editorial_reviews review
        WHERE review.pipeline_run_id = run.id
          AND review.decision = 'pending'
        """
    )
    op.execute(
        """
        UPDATE projects project
        SET status = 'needs_human_approval',
          current_stage = 'human_approval', updated_at = now()
        WHERE project.status::text = 'completed'
          AND EXISTS (
            SELECT 1 FROM human_editorial_reviews review
            WHERE review.project_id = project.id
              AND review.decision = 'pending'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        "UPDATE pipeline_runs SET status = 'needs_review' "
        "WHERE status::text = 'needs_human_approval'"
    )
    op.execute(
        "UPDATE pipeline_runs SET status = 'failed' "
        "WHERE status::text = 'rejected'"
    )
    op.execute(
        "UPDATE projects SET status = 'needs_review' "
        "WHERE status::text = 'needs_human_approval'"
    )
    op.execute(
        "UPDATE projects SET status = 'failed' "
        "WHERE status::text = 'rejected'"
    )
    op.drop_table("human_editorial_reviews")

    # The partial predicate stores enum-typed constants, so PostgreSQL cannot
    # rewrite it while status temporarily becomes varchar. Recreate the exact
    # 0011 index only after the original enum has been restored.
    op.drop_index(
        "ix_pipeline_runs_dispatch_eligibility",
        table_name="pipeline_runs",
    )
    op.execute("ALTER TABLE pipeline_runs ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status TYPE varchar(30) "
        "USING status::text"
    )
    op.execute("DROP TYPE pipelinerunstatus")
    op.execute(
        "CREATE TYPE pipelinerunstatus AS ENUM "
        "('queued', 'running', 'waiting_retry', 'needs_review', "
        "'failed', 'cancelled', 'completed')"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status TYPE pipelinerunstatus "
        "USING status::text::pipelinerunstatus"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status "
        "SET DEFAULT 'queued'::pipelinerunstatus"
    )
    op.create_index(
        "ix_pipeline_runs_dispatch_eligibility",
        "pipeline_runs",
        [
            "status",
            "next_retry_at",
            "dispatch_not_before",
            "dispatch_expires_at",
        ],
        postgresql_where=sa.text("status IN ('queued', 'waiting_retry')"),
    )

    op.execute("ALTER TABLE projects ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE projects ALTER COLUMN status TYPE varchar(30) "
        "USING status::text"
    )
    op.execute("DROP TYPE projectstatus")
    op.execute(
        "CREATE TYPE projectstatus AS ENUM "
        "('draft', 'queued', 'running', 'needs_review', 'completed', 'failed')"
    )
    op.execute(
        "ALTER TABLE projects ALTER COLUMN status TYPE projectstatus "
        "USING status::text::projectstatus"
    )
