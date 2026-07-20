"""Add durable pipeline run dispatch reservations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


dispatch_status = postgresql.ENUM(
    "claimed",
    "sent",
    "failed",
    "expired",
    "consumed",
    name="pipelinedispatchstatus",
    create_type=False,
)


def upgrade() -> None:
    dispatch_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_status", dispatch_status, nullable=True),
    )
    op.add_column(
        "pipeline_runs", sa.Column("dispatch_claimed_by", sa.String(160), nullable=True)
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_attempt", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_runs", sa.Column("last_dispatch_error", sa.Text(), nullable=True)
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_runs", sa.Column("celery_task_id", sa.String(160), nullable=True)
    )
    op.create_check_constraint(
        "pipeline_run_dispatch_attempt_nonnegative",
        "pipeline_runs",
        "dispatch_attempt >= 0",
    )
    op.create_check_constraint(
        "pipeline_run_dispatch_identity_present",
        "pipeline_runs",
        "dispatch_status IS NULL OR "
        "(dispatch_token IS NOT NULL AND dispatch_claimed_at IS NOT NULL)",
    )
    op.create_index(
        "ix_pipeline_runs_dispatch_eligibility",
        "pipeline_runs",
        ["status", "next_retry_at", "dispatch_not_before", "dispatch_expires_at"],
        postgresql_where=sa.text("status IN ('queued', 'waiting_retry')"),
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_dispatch_eligibility", table_name="pipeline_runs")
    op.drop_constraint(
        "pipeline_run_dispatch_identity_present", "pipeline_runs", type_="check"
    )
    op.drop_constraint(
        "pipeline_run_dispatch_attempt_nonnegative", "pipeline_runs", type_="check"
    )
    for column in (
        "celery_task_id",
        "last_dispatched_at",
        "last_dispatch_error",
        "dispatch_not_before",
        "dispatch_attempt",
        "dispatch_expires_at",
        "dispatch_claimed_at",
        "dispatch_claimed_by",
        "dispatch_status",
        "dispatch_token",
    ):
        op.drop_column("pipeline_runs", column)
    dispatch_status.drop(op.get_bind(), checkfirst=True)
