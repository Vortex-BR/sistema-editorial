"""Close Editorial Intelligence V3.6.1 flow and audit relationships.

Revision ID: 0035
Revises: 0034
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def _uuid_from_md5(expression: str) -> str:
    return (
        "(substr(md5(" + expression + "),1,8)||'-'||"
        "substr(md5(" + expression + "),9,4)||'-'||"
        "substr(md5(" + expression + "),13,4)||'-'||"
        "substr(md5(" + expression + "),17,4)||'-'||"
        "substr(md5(" + expression + "),21,12))::uuid"
    )


def upgrade() -> None:
    op.add_column(
        "v3_knowledge_claims",
        sa.Column("canonical_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE v3_knowledge_claims SET canonical_claim_id = "
        + _uuid_from_md5("pipeline_run_id::text || ':' || support_group")
        + " WHERE canonical_claim_id IS NULL"
    )
    op.alter_column(
        "v3_knowledge_claims",
        "canonical_claim_id",
        nullable=False,
    )
    op.create_index(
        "ix_v3_knowledge_claims_canonical_claim_id",
        "v3_knowledge_claims",
        ["canonical_claim_id"],
    )

    op.add_column(
        "editorial_intelligence_snapshots",
        sa.Column("validated_artifact_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "editorial_intelligence_snapshots",
        sa.Column("article_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "editorial_intelligence_snapshots",
        sa.Column("draft_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_foreign_key(
        "fk_editorial_intelligence_snapshot_article_version",
        "editorial_intelligence_snapshots",
        "article_versions",
        ["article_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_validated_artifact_hash",
        "editorial_intelligence_snapshots",
        ["validated_artifact_hash"],
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_article_version_id",
        "editorial_intelligence_snapshots",
        ["article_version_id"],
    )
    op.drop_constraint(
        "editorial_intelligence_status_valid",
        "editorial_intelligence_snapshots",
        type_="check",
    )
    op.create_check_constraint(
        "editorial_intelligence_status_valid",
        "editorial_intelligence_snapshots",
        "status IN ('planned', 'evidence_attached', 'writer_ready', "
        "'draft_pending_validation', 'draft_validated', 'blocked')",
    )
    op.create_check_constraint(
        "editorial_intelligence_draft_revision_nonnegative",
        "editorial_intelligence_snapshots",
        "draft_revision >= 0",
    )
    op.create_check_constraint(
        "editorial_intelligence_artifact_hash_sha256",
        "editorial_intelligence_snapshots",
        "validated_artifact_hash IS NULL OR validated_artifact_hash ~ '^[0-9a-f]{64}$'",
    )

    op.add_column(
        "sentence_claims",
        sa.Column("logical_sentence_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE sentence_claims SET logical_sentence_id = id WHERE logical_sentence_id IS NULL"
    )
    op.alter_column("sentence_claims", "logical_sentence_id", nullable=False)
    op.create_index(
        "ix_sentence_claims_logical_sentence_id",
        "sentence_claims",
        ["logical_sentence_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_sentence_claim_block_logical",
        "sentence_claims",
        ["block_id", "logical_sentence_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_sentence_claim_block_logical",
        "sentence_claims",
        type_="unique",
    )
    op.drop_index("ix_sentence_claims_logical_sentence_id", table_name="sentence_claims")
    op.drop_column("sentence_claims", "logical_sentence_id")

    op.drop_constraint(
        "editorial_intelligence_artifact_hash_sha256",
        "editorial_intelligence_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "editorial_intelligence_draft_revision_nonnegative",
        "editorial_intelligence_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "editorial_intelligence_status_valid",
        "editorial_intelligence_snapshots",
        type_="check",
    )
    op.create_check_constraint(
        "editorial_intelligence_status_valid",
        "editorial_intelligence_snapshots",
        "status IN ('planned', 'evidence_attached', 'writer_ready', 'draft_validated', 'blocked')",
    )
    op.drop_index(
        "ix_editorial_intelligence_snapshots_article_version_id",
        table_name="editorial_intelligence_snapshots",
    )
    op.drop_index(
        "ix_editorial_intelligence_snapshots_validated_artifact_hash",
        table_name="editorial_intelligence_snapshots",
    )
    op.drop_constraint(
        "fk_editorial_intelligence_snapshot_article_version",
        "editorial_intelligence_snapshots",
        type_="foreignkey",
    )
    op.drop_column("editorial_intelligence_snapshots", "draft_revision")
    op.drop_column("editorial_intelligence_snapshots", "article_version_id")
    op.drop_column("editorial_intelligence_snapshots", "validated_artifact_hash")

    op.drop_index("ix_v3_knowledge_claims_canonical_claim_id", table_name="v3_knowledge_claims")
    op.drop_column("v3_knowledge_claims", "canonical_claim_id")
