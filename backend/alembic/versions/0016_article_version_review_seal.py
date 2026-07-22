"""Seal reviewed article versions and human review packages."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _checksum(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _version_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    pipeline_run_id = row["pipeline_run_id"]
    return {
        "final_html": row["final_html"],
        "final_markdown": row["final_markdown"],
        "outline": row["outline"],
        "pipeline_run_id": str(pipeline_run_id) if pipeline_run_id else None,
        "seo_metadata": row["seo_metadata"],
        "source_report": row["source_report"],
        "version_number": row["version"],
    }


def _backfill_checksums() -> None:
    connection = op.get_bind()
    versions = list(
        connection.execute(
            sa.text(
                """
                SELECT id, pipeline_run_id, version, outline, final_markdown,
                       final_html, seo_metadata, source_report
                FROM article_versions
                """
            )
        ).mappings()
    )
    checksums: dict[Any, str] = {}
    for version in versions:
        checksum = _checksum(_version_payload(version))
        checksums[version["id"]] = checksum
        connection.execute(
            sa.text(
                "UPDATE article_versions SET content_checksum = :checksum "
                "WHERE id = :version_id"
            ),
            {"checksum": checksum, "version_id": version["id"]},
        )

    reviews = list(
        connection.execute(
            sa.text(
                """
                SELECT id, article_version_id, review_package_json
                FROM human_editorial_reviews
                """
            )
        ).mappings()
    )
    for review in reviews:
        package = review["review_package_json"] or {}
        if isinstance(package, str):
            package = json.loads(package)
        package = dict(package)
        package["article_version_checksum"] = checksums[
            review["article_version_id"]
        ]
        connection.execute(
            sa.text(
                """
                UPDATE human_editorial_reviews
                SET review_package_json = CAST(:package AS jsonb),
                    review_package_checksum = :checksum
                WHERE id = :review_id
                """
            ),
            {
                "package": json.dumps(package, ensure_ascii=False, default=str),
                "checksum": _checksum(package),
                "review_id": review["id"],
            },
        )


def upgrade() -> None:
    op.add_column(
        "article_versions",
        sa.Column("content_checksum", sa.String(64), nullable=True),
    )
    op.add_column(
        "article_versions",
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "human_editorial_reviews",
        sa.Column("review_package_checksum", sa.String(64), nullable=True),
    )

    _backfill_checksums()
    op.alter_column(
        "article_versions",
        "content_checksum",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.alter_column(
        "human_editorial_reviews",
        "review_package_checksum",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.execute(
        """
        UPDATE article_versions AS version
        SET sealed_at = COALESCE(review.reviewed_at, review.updated_at, now())
        FROM human_editorial_reviews AS review
        WHERE review.article_version_id = version.id
          AND review.decision = 'approved'
          AND version.editorial_status = 'human_approved'
        """
    )

    op.create_check_constraint(
        "article_version_content_checksum_sha256",
        "article_versions",
        "content_checksum ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "article_version_seal_status_valid",
        "article_versions",
        "sealed_at IS NULL OR editorial_status = 'human_approved'",
    )
    op.create_check_constraint(
        "human_editorial_review_package_checksum_sha256",
        "human_editorial_reviews",
        "review_package_checksum ~ '^[0-9a-f]{64}$'",
    )

    op.execute(
        """
        CREATE FUNCTION prevent_sealed_article_version_update()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.sealed_at IS NOT NULL AND (
               NEW.id IS DISTINCT FROM OLD.id
            OR NEW.article_id IS DISTINCT FROM OLD.article_id
            OR NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id
            OR NEW.version IS DISTINCT FROM OLD.version
            OR NEW.title IS DISTINCT FROM OLD.title
            OR NEW.outline IS DISTINCT FROM OLD.outline
            OR NEW.change_reason IS DISTINCT FROM OLD.change_reason
            OR NEW.final_markdown IS DISTINCT FROM OLD.final_markdown
            OR NEW.final_html IS DISTINCT FROM OLD.final_html
            OR NEW.seo_metadata IS DISTINCT FROM OLD.seo_metadata
            OR NEW.source_report IS DISTINCT FROM OLD.source_report
            OR NEW.content_checksum IS DISTINCT FROM OLD.content_checksum
            OR NEW.sealed_at IS DISTINCT FROM OLD.sealed_at
          ) THEN
            RAISE EXCEPTION 'sealed article versions are immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER article_versions_sealed_immutable
        BEFORE UPDATE ON article_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_sealed_article_version_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_review_package_update()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.review_package_checksum IS NOT NULL AND (
               NEW.id IS DISTINCT FROM OLD.id
            OR NEW.project_id IS DISTINCT FROM OLD.project_id
            OR NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id
            OR NEW.article_version_id IS DISTINCT FROM OLD.article_version_id
            OR NEW.review_package_json IS DISTINCT FROM OLD.review_package_json
            OR NEW.review_package_checksum IS DISTINCT FROM OLD.review_package_checksum
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'human editorial review packages are immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER human_editorial_review_packages_immutable
        BEFORE UPDATE ON human_editorial_reviews
        FOR EACH ROW EXECUTE FUNCTION prevent_review_package_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS human_editorial_review_packages_immutable "
        "ON human_editorial_reviews"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_review_package_update()")
    op.execute(
        "DROP TRIGGER IF EXISTS article_versions_sealed_immutable "
        "ON article_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_sealed_article_version_update()")
    op.drop_constraint(
        "human_editorial_review_package_checksum_sha256",
        "human_editorial_reviews",
        type_="check",
    )
    op.drop_constraint(
        "article_version_seal_status_valid",
        "article_versions",
        type_="check",
    )
    op.drop_constraint(
        "article_version_content_checksum_sha256",
        "article_versions",
        type_="check",
    )
    op.execute(
        "UPDATE human_editorial_reviews SET review_package_json = "
        "review_package_json - 'article_version_checksum'"
    )
    op.drop_column("human_editorial_reviews", "review_package_checksum")
    op.drop_column("article_versions", "sealed_at")
    op.drop_column("article_versions", "content_checksum")
