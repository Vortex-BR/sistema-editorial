"""Preserve billed provider attempts across repeated agent-run executions.

Revision ID: 0026_provider_attempt_run_identity
Revises: 0025_editorial_humanization_and_cost_ledger
"""

from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_attempts",
        sa.Column("run_attempt", sa.Integer(), server_default="1", nullable=False),
    )
    op.drop_constraint(
        "uq_provider_attempt_agent_target_number",
        "provider_attempts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_provider_attempt_agent_run_target_number",
        "provider_attempts",
        ["agent_run_id", "run_attempt", "target_kind", "attempt_number"],
    )
    op.drop_constraint(
        "provider_attempt_values_nonnegative",
        "provider_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "provider_attempt_values_nonnegative",
        "provider_attempts",
        "run_attempt >= 1 AND attempt_number >= 1 AND prompt_tokens >= 0 "
        "AND completion_tokens >= 0 AND estimated_cost_usd >= 0 "
        "AND latency_ms >= 0",
    )


def downgrade() -> None:
    # Rows from later run attempts cannot coexist under the previous uniqueness
    # contract. Keep the first persisted execution of each provider-attempt key.
    op.execute(
        """
        DELETE FROM provider_attempts newer
        USING provider_attempts older
        WHERE newer.agent_run_id = older.agent_run_id
          AND newer.target_kind = older.target_kind
          AND newer.attempt_number = older.attempt_number
          AND (
              newer.run_attempt > older.run_attempt
              OR (
                  newer.run_attempt = older.run_attempt
                  AND newer.id::text > older.id::text
              )
          )
        """
    )
    op.drop_constraint(
        "provider_attempt_values_nonnegative",
        "provider_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "provider_attempt_values_nonnegative",
        "provider_attempts",
        "attempt_number >= 1 AND prompt_tokens >= 0 AND completion_tokens >= 0 "
        "AND estimated_cost_usd >= 0 AND latency_ms >= 0",
    )
    op.drop_constraint(
        "uq_provider_attempt_agent_run_target_number",
        "provider_attempts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_provider_attempt_agent_target_number",
        "provider_attempts",
        ["agent_run_id", "target_kind", "attempt_number"],
    )
    op.drop_column("provider_attempts", "run_attempt")
