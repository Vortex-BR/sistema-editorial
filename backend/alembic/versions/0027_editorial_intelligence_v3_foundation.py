"""Editorial Intelligence V3 foundation and knowledge graph storage.

Revision ID: 0027
Revises: 0026
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


editorial_pipeline_version = sa.Enum("v2", "v3", name="editorialpipelineversion")


def upgrade() -> None:
    editorial_pipeline_version.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "projects",
        sa.Column(
            "editorial_pipeline_version",
            editorial_pipeline_version,
            server_default="v2",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_projects_editorial_pipeline_version",
        "projects",
        ["editorial_pipeline_version"],
    )

    op.create_table(
        "content_knowledge_contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contract_version", sa.String(length=30), server_default="editorial-v3", nullable=False),
        sa.Column("content_type", sa.String(length=60), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("reader_start_state", sa.Text(), nullable=False),
        sa.Column("reader_final_state", sa.Text(), nullable=False),
        sa.Column("article_promise", sa.Text(), nullable=False),
        sa.Column("scope_limit", sa.Text(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=200), nullable=True),
        sa.Column("contract_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="draft", nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("producer", sa.String(length=100), server_default="deterministic", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'validated', 'active', 'superseded', 'blocked')",
            name="content_knowledge_contract_status_valid",
        ),
        sa.CheckConstraint("version >= 1", name="content_knowledge_contract_version_positive"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_content_knowledge_contract_project_version"
        ),
        sa.UniqueConstraint(
            "project_id", "checksum", name="uq_content_knowledge_contract_project_checksum"
        ),
        sa.UniqueConstraint(
            "pipeline_run_id", "checksum", name="uq_content_knowledge_contract_run_checksum"
        ),
    )
    op.create_index(
        "ix_content_knowledge_contracts_project_id",
        "content_knowledge_contracts",
        ["project_id"],
    )
    op.create_index(
        "ix_content_knowledge_contracts_pipeline_run_id",
        "content_knowledge_contracts",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_content_knowledge_contracts_content_type",
        "content_knowledge_contracts",
        ["content_type"],
    )
    op.create_index(
        "ix_content_knowledge_contracts_status",
        "content_knowledge_contracts",
        ["status"],
    )
    op.create_index(
        "ix_content_knowledge_contracts_checksum",
        "content_knowledge_contracts",
        ["checksum"],
    )

    op.create_table(
        "knowledge_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_key", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_type", sa.String(length=60), nullable=False),
        sa.Column("title_function", sa.Text(), nullable=False),
        sa.Column("editorial_goal", sa.Text(), nullable=False),
        sa.Column("reader_state_before", sa.Text(), nullable=False),
        sa.Column("reader_state_after", sa.Text(), nullable=False),
        sa.Column("central_question", sa.Text(), nullable=False),
        sa.Column("depends_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_knowledge", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_evidence_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("completion_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("branches", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("convergence_node_key", sa.String(length=100), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="knowledge_node_sequence_positive"),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id", "node_key", name="uq_knowledge_node_contract_key"),
        sa.UniqueConstraint("contract_id", "sequence", name="uq_knowledge_node_contract_sequence"),
    )
    op.create_index("ix_knowledge_nodes_contract_id", "knowledge_nodes", ["contract_id"])
    op.create_index("ix_knowledge_nodes_node_type", "knowledge_nodes", ["node_type"])

    op.create_table(
        "knowledge_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_node_key", sa.String(length=100), nullable=False),
        sa.Column("to_node_key", sa.String(length=100), nullable=False),
        sa.Column("relation", sa.String(length=50), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("from_node_key <> to_node_key", name="knowledge_edge_not_self_referencing"),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contract_id",
            "from_node_key",
            "to_node_key",
            "relation",
            name="uq_knowledge_edge_contract_path",
        ),
    )
    op.create_index("ix_knowledge_edges_contract_id", "knowledge_edges", ["contract_id"])

    op.create_table(
        "knowledge_gaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_key", sa.String(length=100), nullable=False),
        sa.Column("gap_type", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("essential", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="open", nullable=False),
        sa.Column("original_problem", sa.Text(), server_default="", nullable=False),
        sa.Column("reframed_problem", sa.Text(), server_default="", nullable=False),
        sa.Column("resolution_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'researching', 'resolved', 'resolved_conditionally', 'disputed', 'blocked')",
            name="knowledge_gap_status_valid",
        ),
        sa.ForeignKeyConstraint(["contract_id"], ["content_knowledge_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_gaps_contract_id", "knowledge_gaps", ["contract_id"])
    op.create_index("ix_knowledge_gaps_node_key", "knowledge_gaps", ["node_key"])
    op.create_index("ix_knowledge_gaps_gap_type", "knowledge_gaps", ["gap_type"])
    op.create_index("ix_knowledge_gaps_status", "knowledge_gaps", ["status"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_gaps_status", table_name="knowledge_gaps")
    op.drop_index("ix_knowledge_gaps_gap_type", table_name="knowledge_gaps")
    op.drop_index("ix_knowledge_gaps_node_key", table_name="knowledge_gaps")
    op.drop_index("ix_knowledge_gaps_contract_id", table_name="knowledge_gaps")
    op.drop_table("knowledge_gaps")

    op.drop_index("ix_knowledge_edges_contract_id", table_name="knowledge_edges")
    op.drop_table("knowledge_edges")

    op.drop_index("ix_knowledge_nodes_node_type", table_name="knowledge_nodes")
    op.drop_index("ix_knowledge_nodes_contract_id", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")

    op.drop_index("ix_content_knowledge_contracts_checksum", table_name="content_knowledge_contracts")
    op.drop_index("ix_content_knowledge_contracts_status", table_name="content_knowledge_contracts")
    op.drop_index("ix_content_knowledge_contracts_content_type", table_name="content_knowledge_contracts")
    op.drop_index("ix_content_knowledge_contracts_pipeline_run_id", table_name="content_knowledge_contracts")
    op.drop_index("ix_content_knowledge_contracts_project_id", table_name="content_knowledge_contracts")
    op.drop_table("content_knowledge_contracts")

    op.drop_index("ix_projects_editorial_pipeline_version", table_name="projects")
    op.drop_column("projects", "editorial_pipeline_version")
    editorial_pipeline_version.drop(op.get_bind(), checkfirst=True)
