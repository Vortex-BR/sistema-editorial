"""Executable Editorial Intelligence V3 artifacts.

Revision ID: 0029
Revises: 0028
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "v3_source_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("document_type", sa.String(length=60), nullable=False),
        sa.Column("source_role", sa.String(length=60), nullable=False),
        sa.Column("usage_policy", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("document_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("assessment_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="accepted", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('accepted', 'comparison_only', 'discovery_only', 'rejected', 'unavailable')",
            name="v3_source_document_status_valid",
        ),
        sa.CheckConstraint(
            "url_hash ~ '^[0-9a-f]{64}$' AND content_hash ~ '^[0-9a-f]{64}$'",
            name="v3_source_document_hashes_sha256",
        ),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_run_id", "url_hash", "content_hash", name="uq_v3_source_document_run_url_content"
        ),
    )
    for column in ("contract_id", "pipeline_run_id", "url_hash", "document_type", "source_role", "usage_policy", "content_hash", "status"):
        op.create_index(f"ix_v3_source_documents_{column}", "v3_source_documents", [column])

    op.create_table(
        "v3_knowledge_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_key", sa.String(length=120), nullable=False),
        sa.Column("support_group", sa.String(length=120), nullable=False),
        sa.Column("knowledge_node_key", sa.String(length=100), nullable=False),
        sa.Column("evidence_role", sa.String(length=50), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("exact_quote", sa.Text(), nullable=False),
        sa.Column("source_locator", sa.String(length=500), nullable=False),
        sa.Column("method_ids", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("applicability", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("conclusion_status", sa.String(length=40), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("critical", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("conflict_group", sa.String(length=160), nullable=True),
        sa.Column("approved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("validation_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("confidence_score >= 0 AND confidence_score <= 1", name="v3_knowledge_claim_confidence_range"),
        sa.CheckConstraint(
            "conclusion_status IN ('confirmed', 'well_supported', 'conditional', 'disputed', 'insufficient_evidence')",
            name="v3_knowledge_claim_conclusion_valid",
        ),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["v3_source_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fact_id"], ["fact_ledger.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", "claim_key", name="uq_v3_knowledge_claim_run_key"),
    )
    for column in ("contract_id", "pipeline_run_id", "source_document_id", "fact_id", "support_group", "knowledge_node_key", "evidence_role", "conclusion_status", "conflict_group", "approved"):
        op.create_index(f"ix_v3_knowledge_claims_{column}", "v3_knowledge_claims", [column])

    op.create_table(
        "v3_method_dossiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method_key", sa.String(length=100), nullable=False),
        sa.Column("dossier_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="validated", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="v3_method_dossier_checksum_sha256"),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", "method_key", name="uq_v3_method_dossier_run_key"),
    )
    for column in ("contract_id", "pipeline_run_id", "checksum", "status"):
        op.create_index(f"ix_v3_method_dossiers_{column}", "v3_method_dossiers", [column])

    op.create_table(
        "v3_section_dossiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.String(length=100), nullable=False),
        sa.Column("dossier_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="validated", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="v3_section_dossier_checksum_sha256"),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", "section_key", name="uq_v3_section_dossier_run_key"),
    )
    for column in ("contract_id", "pipeline_run_id", "checksum", "status"):
        op.create_index(f"ix_v3_section_dossiers_{column}", "v3_section_dossiers", [column])

    op.create_table(
        "v3_decision_matrices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matrix_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="validated", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="v3_decision_matrix_checksum_sha256"),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id"),
    )
    for column in ("contract_id", "pipeline_run_id", "checksum", "status"):
        op.create_index(f"ix_v3_decision_matrices_{column}", "v3_decision_matrices", [column])

    op.create_table(
        "v3_stage_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("attempt >= 1", name="v3_stage_review_attempt_positive"),
        sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="v3_stage_review_checksum_sha256"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", "stage", "attempt", name="uq_v3_stage_review_run_stage_attempt"),
    )
    for column in ("project_id", "pipeline_run_id", "stage", "status", "checksum"):
        op.create_index(f"ix_v3_stage_reviews_{column}", "v3_stage_reviews", [column])

    op.create_table(
        "v3_procedural_quality_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("article_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rubric_version", sa.String(length=80), server_default="quality-rubric.procedural-guide.v1", nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("overall_score >= 0 AND overall_score <= 1", name="v3_procedural_quality_score_range"),
        sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="v3_procedural_quality_checksum_sha256"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["article_version_id"], ["article_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id"),
    )
    for column in ("project_id", "pipeline_run_id", "article_version_id", "status", "checksum"):
        op.create_index(f"ix_v3_procedural_quality_evaluations_{column}", "v3_procedural_quality_evaluations", [column])


def downgrade() -> None:
    for table, columns in (
        ("v3_procedural_quality_evaluations", ("checksum", "status", "article_version_id", "pipeline_run_id", "project_id")),
        ("v3_stage_reviews", ("checksum", "status", "stage", "pipeline_run_id", "project_id")),
        ("v3_decision_matrices", ("status", "checksum", "pipeline_run_id", "contract_id")),
        ("v3_section_dossiers", ("status", "checksum", "pipeline_run_id", "contract_id")),
        ("v3_method_dossiers", ("status", "checksum", "pipeline_run_id", "contract_id")),
        ("v3_knowledge_claims", ("approved", "conflict_group", "conclusion_status", "evidence_role", "knowledge_node_key", "support_group", "fact_id", "source_document_id", "pipeline_run_id", "contract_id")),
        ("v3_source_documents", ("status", "content_hash", "usage_policy", "source_role", "document_type", "url_hash", "pipeline_run_id", "contract_id")),
    ):
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
