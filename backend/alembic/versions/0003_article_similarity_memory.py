"""Persist article fingerprints for duplicate-content prevention."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_fingerprint TEXT")
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_embedding VECTOR")
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_embedding_provider VARCHAR(30)"
    )
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_embedding_model VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_embedding_dimensions INTEGER"
    )


def downgrade() -> None:
    for column in (
        "content_embedding_dimensions",
        "content_embedding_model",
        "content_embedding_provider",
        "content_embedding",
        "content_fingerprint",
    ):
        op.execute(f"ALTER TABLE articles DROP COLUMN IF EXISTS {column}")
