"""Add versioned snapshots for the Editorial Intelligence Core.

Revision ID: 0034
Revises: 0033
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "editorial_intelligence_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "intelligence_version",
            sa.String(length=60),
            server_default="editorial-intelligence-v1",
            nullable=False,
        ),
        sa.Column(
            "state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "validation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="editorial_intelligence_revision_positive"),
        sa.CheckConstraint(
            "status IN ('planned', 'evidence_attached', 'writer_ready', 'draft_validated', 'blocked')",
            name="editorial_intelligence_status_valid",
        ),
        sa.CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'",
            name="editorial_intelligence_checksum_sha256",
        ),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_run_id", "stage", "checksum", name="uq_editorial_intelligence_run_stage_checksum"
        ),
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_project_id",
        "editorial_intelligence_snapshots",
        ["project_id"],
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_pipeline_run_id",
        "editorial_intelligence_snapshots",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_revision",
        "editorial_intelligence_snapshots",
        ["revision"],
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_contract_id",
        "editorial_intelligence_snapshots",
        ["contract_id"],
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_stage",
        "editorial_intelligence_snapshots",
        ["stage"],
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_status",
        "editorial_intelligence_snapshots",
        ["status"],
    )
    op.create_index(
        "ix_editorial_intelligence_snapshots_checksum",
        "editorial_intelligence_snapshots",
        ["checksum"],
    )


def downgrade() -> None:
    op.drop_index("ix_editorial_intelligence_snapshots_checksum", table_name="editorial_intelligence_snapshots")
    op.drop_index("ix_editorial_intelligence_snapshots_status", table_name="editorial_intelligence_snapshots")
    op.drop_index("ix_editorial_intelligence_snapshots_stage", table_name="editorial_intelligence_snapshots")
    op.drop_index("ix_editorial_intelligence_snapshots_contract_id", table_name="editorial_intelligence_snapshots")
    op.drop_index("ix_editorial_intelligence_snapshots_revision", table_name="editorial_intelligence_snapshots")
    op.drop_index("ix_editorial_intelligence_snapshots_pipeline_run_id", table_name="editorial_intelligence_snapshots")
    op.drop_index("ix_editorial_intelligence_snapshots_project_id", table_name="editorial_intelligence_snapshots")
    op.drop_table("editorial_intelligence_snapshots")
