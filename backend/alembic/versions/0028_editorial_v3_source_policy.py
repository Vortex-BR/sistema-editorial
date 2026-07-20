"""Persist Editorial V3 source-policy assessments.

Revision ID: 0028
Revises: 0027
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_source_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "policy_version",
            sa.String(length=50),
            server_default="research-source-policy.v1",
            nullable=False,
        ),
        sa.Column("ownership_type", sa.String(length=50), nullable=False),
        sa.Column("page_type", sa.String(length=60), nullable=False),
        sa.Column("source_role", sa.String(length=60), nullable=False),
        sa.Column("usage_policy", sa.String(length=40), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column(
            "eligible_for_primary_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "eligible_for_corroborating_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "eligible_for_external_reference",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "counts_toward_independent_source_diversity",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "requires_independent_corroboration",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "minimum_independent_corroborators",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "absolute_claim_support_allowed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "allowed_evidence_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "signals_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority_score >= 0 AND priority_score <= 1",
            name="research_source_assessment_priority_range",
        ),
        sa.CheckConstraint(
            "minimum_independent_corroborators >= 0 AND minimum_independent_corroborators <= 5",
            name="research_source_assessment_corroborator_range",
        ),
        sa.CheckConstraint(
            "usage_policy IN ('authoritative_evidence', 'corroborating_evidence', 'discovery_only', 'comparison_only', 'rejected')",
            name="research_source_assessment_usage_valid",
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshots.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contract_id",
            "url_hash",
            "policy_version",
            name="uq_research_source_assessment_contract_url_policy",
        ),
    )
    op.create_index(
        "ix_research_source_assessments_contract_id",
        "research_source_assessments",
        ["contract_id"],
    )
    op.create_index(
        "ix_research_source_assessments_pipeline_run_id",
        "research_source_assessments",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_research_source_assessments_source_role",
        "research_source_assessments",
        ["source_role"],
    )
    op.create_index(
        "ix_research_source_assessments_usage_policy",
        "research_source_assessments",
        ["usage_policy"],
    )
    op.create_index(
        "ix_research_source_assessments_url_hash",
        "research_source_assessments",
        ["url_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_source_assessments_url_hash",
        table_name="research_source_assessments",
    )
    op.drop_index(
        "ix_research_source_assessments_usage_policy",
        table_name="research_source_assessments",
    )
    op.drop_index(
        "ix_research_source_assessments_source_role",
        table_name="research_source_assessments",
    )
    op.drop_index(
        "ix_research_source_assessments_pipeline_run_id",
        table_name="research_source_assessments",
    )
    op.drop_index(
        "ix_research_source_assessments_contract_id",
        table_name="research_source_assessments",
    )
    op.drop_table("research_source_assessments")
