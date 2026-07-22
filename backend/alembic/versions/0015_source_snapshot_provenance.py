"""Persist immutable source provenance on each run snapshot."""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_snapshots", sa.Column("title", sa.Text(), nullable=True))
    op.add_column(
        "source_snapshots", sa.Column("author", sa.String(255), nullable=True)
    )
    op.add_column(
        "source_snapshots", sa.Column("publisher", sa.String(255), nullable=True)
    )
    op.add_column(
        "source_snapshots",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_snapshots", sa.Column("canonical_url", sa.Text(), nullable=True)
    )
    op.add_column(
        "source_snapshots", sa.Column("domain", sa.String(255), nullable=True)
    )
    op.add_column(
        "source_snapshots", sa.Column("source_type", sa.String(50), nullable=True)
    )
    op.add_column(
        "source_snapshots", sa.Column("reliability_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "source_snapshots",
        sa.Column("extraction_method", sa.String(40), nullable=True),
    )

    op.execute(
        r"""
        CREATE FUNCTION migration_0015_safe_snapshot_url(raw_url text)
        RETURNS text AS $$
          SELECT regexp_replace(
            regexp_replace(
              regexp_replace(
                regexp_replace(
                  raw_url,
                  '^(https?://)[^/@]+@', '\1', 'i'
                ),
                '([?&])(access[_-]?token|api[_-]?key|apikey|auth|authorization|credential|key|password|signature|token|x[_-]amz[_-](credential|signature)|x[_-]goog[_-]signature)=[^&#]*&?',
                '\1', 'gi'
              ),
              '\?&', '?', 'g'
            ),
            '[?&]+$', ''
          )
        $$ LANGUAGE SQL IMMUTABLE STRICT
        """
    )

    # Older rows cannot recover metadata that was never captured. Use the best
    # state available at migration time once, then protect that backfill from drift.
    op.execute(
        """
        UPDATE source_snapshots AS snapshot
        SET title = COALESCE(
              NULLIF(snapshot.metadata_json->>'title', ''), source.title
            ),
            author = LEFT(NULLIF(COALESCE(
              snapshot.metadata_json->>'author', source.metadata_json->>'author'
            ), ''), 255),
            publisher = LEFT(COALESCE(
              NULLIF(snapshot.metadata_json->>'publisher', ''), source.publisher
            ), 255),
            published_at = source.published_at,
            canonical_url = migration_0015_safe_snapshot_url(COALESCE(
              NULLIF(snapshot.metadata_json->>'canonical_url', ''),
              source.canonical_url
            )),
            domain = LEFT(LOWER(COALESCE(
              NULLIF(SPLIT_PART(SPLIT_PART(
                migration_0015_safe_snapshot_url(COALESCE(
                  NULLIF(snapshot.metadata_json->>'canonical_url', ''),
                  source.canonical_url
                )), '://', 2), '/', 1), ''),
              'unknown'
            )), 255),
            source_type = source.source_type,
            reliability_score = source.reliability_score,
            extraction_method = LEFT(COALESCE(
              NULLIF(snapshot.metadata_json->>'extraction_method', ''),
              NULLIF(snapshot.metadata_json->>'capture_method', ''),
              'legacy_import'
            ), 40)
        FROM sources AS source
        WHERE source.id = snapshot.source_id
        """
    )
    op.execute("DROP FUNCTION migration_0015_safe_snapshot_url(text)")

    for column_name, column_type in (
        ("title", sa.Text()),
        ("canonical_url", sa.Text()),
        ("domain", sa.String(255)),
        ("source_type", sa.String(50)),
        ("reliability_score", sa.Float()),
        ("extraction_method", sa.String(40)),
    ):
        op.alter_column(
            "source_snapshots",
            column_name,
            existing_type=column_type,
            nullable=False,
        )

    op.create_check_constraint(
        "source_snapshot_reliability_range",
        "source_snapshots",
        "reliability_score >= 0 AND reliability_score <= 1",
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM fact_ledger WHERE source_snapshot_id IS NULL
          ) THEN
            RAISE EXCEPTION
              'cannot enforce immutable provenance: fact without source snapshot';
          END IF;
        END;
        $$
        """
    )
    op.alter_column(
        "fact_ledger",
        "source_snapshot_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.execute(
        """
        CREATE FUNCTION prevent_source_snapshot_update()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.source_id IS DISTINCT FROM OLD.source_id
             OR NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id
             OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
             OR NEW.snapshot_text IS DISTINCT FROM OLD.snapshot_text
             OR NEW.accessed_at IS DISTINCT FROM OLD.accessed_at
             OR NEW.title IS DISTINCT FROM OLD.title
             OR NEW.author IS DISTINCT FROM OLD.author
             OR NEW.publisher IS DISTINCT FROM OLD.publisher
             OR NEW.published_at IS DISTINCT FROM OLD.published_at
             OR NEW.canonical_url IS DISTINCT FROM OLD.canonical_url
             OR NEW.domain IS DISTINCT FROM OLD.domain
             OR NEW.source_type IS DISTINCT FROM OLD.source_type
             OR NEW.reliability_score IS DISTINCT FROM OLD.reliability_score
             OR NEW.extraction_method IS DISTINCT FROM OLD.extraction_method
             OR NEW.metadata_json IS DISTINCT FROM OLD.metadata_json
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
          THEN
            RAISE EXCEPTION 'source snapshots are immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER source_snapshots_immutable
        BEFORE UPDATE ON source_snapshots
        FOR EACH ROW EXECUTE FUNCTION prevent_source_snapshot_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS source_snapshots_immutable ON source_snapshots"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_source_snapshot_update()")
    op.drop_constraint(
        "source_snapshot_reliability_range",
        "source_snapshots",
        type_="check",
    )
    op.alter_column(
        "fact_ledger",
        "source_snapshot_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    for column_name in (
        "extraction_method",
        "reliability_score",
        "source_type",
        "domain",
        "canonical_url",
        "published_at",
        "publisher",
        "author",
        "title",
    ):
        op.drop_column("source_snapshots", column_name)
