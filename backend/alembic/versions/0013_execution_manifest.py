"""Add immutable per-run execution manifests."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_manifests",
        sa.Column("pipeline_run_id", postgresql.UUID(), nullable=False),
        sa.Column(
            "format_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "format_version >= 1",
            name=(
                "ck_execution_manifests_"
                "execution_manifest_format_version_positive"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name="fk_execution_manifests_pipeline_run_id_pipeline_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_manifests"),
        sa.UniqueConstraint(
            "pipeline_run_id",
            name="uq_execution_manifests_pipeline_run_id",
        ),
        sa.UniqueConstraint(
            "checksum",
            name="uq_execution_manifests_checksum",
        ),
    )
    op.create_index(
        "ix_execution_manifests_pipeline_run_id",
        "execution_manifests",
        ["pipeline_run_id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_execution_manifest_update()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'execution manifests are immutable'
            USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER execution_manifests_immutable
        BEFORE UPDATE ON execution_manifests
        FOR EACH ROW EXECUTE FUNCTION prevent_execution_manifest_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS execution_manifests_immutable "
        "ON execution_manifests"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_execution_manifest_update()")
    op.drop_table("execution_manifests")
