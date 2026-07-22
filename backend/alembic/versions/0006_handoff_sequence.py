"""Add cycle-aware, monotonic handoff identity."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("handoff_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column(
        "agent_handoffs",
        "idempotency_key",
        existing_type=sa.String(200),
        type_=sa.String(300),
        existing_nullable=True,
    )
    op.add_column("agent_handoffs", sa.Column("sequence", sa.Integer()))
    op.add_column(
        "agent_handoffs",
        sa.Column("producer_agent_run_id", postgresql.UUID(as_uuid=True)),
    )
    op.execute(
        """
        WITH ordered AS (
          SELECT id,
            row_number() OVER (
              PARTITION BY pipeline_run_id
              ORDER BY created_at, id
            ) AS handoff_sequence
          FROM agent_handoffs
        )
        UPDATE agent_handoffs handoff
        SET sequence = ordered.handoff_sequence
        FROM ordered
        WHERE handoff.id = ordered.id
        """
    )
    op.execute(
        """
        UPDATE pipeline_runs run
        SET handoff_sequence = latest.sequence
        FROM (
          SELECT pipeline_run_id, MAX(sequence) AS sequence
          FROM agent_handoffs
          WHERE pipeline_run_id IS NOT NULL
          GROUP BY pipeline_run_id
        ) latest
        WHERE run.id = latest.pipeline_run_id
        """
    )
    op.alter_column("agent_handoffs", "sequence", nullable=False)
    op.create_foreign_key(
        "fk_agent_handoffs_producer_agent_run_id",
        "agent_handoffs",
        "agent_runs",
        ["producer_agent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_handoffs_producer_agent_run_id",
        "agent_handoffs",
        ["producer_agent_run_id"],
    )
    op.create_unique_constraint(
        "uq_handoff_sequence",
        "agent_handoffs",
        ["pipeline_run_id", "sequence"],
    )
    op.create_check_constraint(
        "handoff_sequence_positive", "agent_handoffs", "sequence >= 1"
    )


def downgrade() -> None:
    op.drop_constraint(
        "handoff_sequence_positive", "agent_handoffs", type_="check"
    )
    op.drop_constraint("uq_handoff_sequence", "agent_handoffs", type_="unique")
    op.drop_index(
        "ix_agent_handoffs_producer_agent_run_id", table_name="agent_handoffs"
    )
    op.drop_constraint(
        "fk_agent_handoffs_producer_agent_run_id",
        "agent_handoffs",
        type_="foreignkey",
    )
    op.drop_column("agent_handoffs", "producer_agent_run_id")
    op.drop_column("agent_handoffs", "sequence")
    op.alter_column(
        "agent_handoffs",
        "idempotency_key",
        existing_type=sa.String(300),
        type_=sa.String(200),
        existing_nullable=True,
    )
    op.drop_column("pipeline_runs", "handoff_sequence")
