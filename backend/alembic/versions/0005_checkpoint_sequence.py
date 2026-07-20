"""Add monotonic checkpoint sequencing per pipeline run."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "checkpoint_sequence", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "pipeline_checkpoints", sa.Column("sequence", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        WITH ordered AS (
          SELECT id,
            row_number() OVER (
              PARTITION BY pipeline_run_id
              ORDER BY completed_at, created_at, id
            ) AS checkpoint_sequence
          FROM pipeline_checkpoints
        )
        UPDATE pipeline_checkpoints checkpoint
        SET sequence = ordered.checkpoint_sequence
        FROM ordered
        WHERE checkpoint.id = ordered.id
        """
    )
    op.execute(
        """
        UPDATE pipeline_runs run
        SET checkpoint_sequence = latest.sequence
        FROM (
          SELECT pipeline_run_id, MAX(sequence) AS sequence
          FROM pipeline_checkpoints
          GROUP BY pipeline_run_id
        ) latest
        WHERE run.id = latest.pipeline_run_id
        """
    )
    op.alter_column("pipeline_checkpoints", "sequence", nullable=False)
    op.create_unique_constraint(
        "uq_checkpoint_sequence",
        "pipeline_checkpoints",
        ["pipeline_run_id", "sequence"],
    )
    op.create_check_constraint(
        "checkpoint_sequence_positive",
        "pipeline_checkpoints",
        "sequence >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "checkpoint_sequence_positive", "pipeline_checkpoints", type_="check"
    )
    op.drop_constraint(
        "uq_checkpoint_sequence", "pipeline_checkpoints", type_="unique"
    )
    op.drop_column("pipeline_checkpoints", "sequence")
    op.drop_column("pipeline_runs", "checkpoint_sequence")
