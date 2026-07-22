"""Persist redacted technical diagnostics for the administrative error-log UI.

Revision ID: 0037
Revises: 0036
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technical_error_logs",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=20), server_default="error", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_category", sa.String(length=40), nullable=True),
        sa.Column("exception_type", sa.String(length=255), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("operation", sa.String(length=30), nullable=True),
        sa.Column("sql_template", sa.Text(), nullable=True),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "severity IN ('warning', 'error', 'critical')",
            name=op.f("ck_technical_error_logs_technical_error_logs_severity_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"],
            name=op.f("fk_technical_error_logs_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"],
            name=op.f("fk_technical_error_logs_pipeline_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name=op.f("fk_technical_error_logs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_technical_error_logs")),
        sa.UniqueConstraint("correlation_id", name=op.f("uq_technical_error_logs_correlation_id")),
    )
    op.create_index(op.f("ix_technical_error_logs_agent_run_id"), "technical_error_logs", ["agent_run_id"], unique=False)
    op.create_index(op.f("ix_technical_error_logs_error_code"), "technical_error_logs", ["error_code"], unique=False)
    op.create_index(op.f("ix_technical_error_logs_pipeline_run_id"), "technical_error_logs", ["pipeline_run_id"], unique=False)
    op.create_index(op.f("ix_technical_error_logs_project_id"), "technical_error_logs", ["project_id"], unique=False)
    op.create_index(op.f("ix_technical_error_logs_severity"), "technical_error_logs", ["severity"], unique=False)
    op.create_index(op.f("ix_technical_error_logs_stage"), "technical_error_logs", ["stage"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_technical_error_logs_stage"), table_name="technical_error_logs")
    op.drop_index(op.f("ix_technical_error_logs_severity"), table_name="technical_error_logs")
    op.drop_index(op.f("ix_technical_error_logs_project_id"), table_name="technical_error_logs")
    op.drop_index(op.f("ix_technical_error_logs_pipeline_run_id"), table_name="technical_error_logs")
    op.drop_index(op.f("ix_technical_error_logs_error_code"), table_name="technical_error_logs")
    op.drop_index(op.f("ix_technical_error_logs_agent_run_id"), table_name="technical_error_logs")
    op.drop_table("technical_error_logs")
