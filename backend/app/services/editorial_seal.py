import hashlib
import json
from typing import Any


class EditorialSealError(RuntimeError):
    """Raised when reviewed content no longer matches its immutable seal."""


def canonical_checksum(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def article_version_payload(version: Any) -> dict[str, Any]:
    pipeline_run_id = getattr(version, "pipeline_run_id", None)
    return {
        "final_html": getattr(version, "final_html", None),
        "final_markdown": getattr(version, "final_markdown", None),
        "outline": getattr(version, "outline", None),
        "pipeline_run_id": str(pipeline_run_id) if pipeline_run_id else None,
        "seo_metadata": getattr(version, "seo_metadata", None),
        "source_report": getattr(version, "source_report", None),
        "version_number": getattr(version, "version", None),
    }


def article_version_checksum(version: Any) -> str:
    return canonical_checksum(article_version_payload(version))


def review_package_checksum(package: dict[str, Any]) -> str:
    return canonical_checksum(package)


def validate_review_seal(
    version: Any,
    review: Any,
    *,
    require_sealed: bool,
) -> None:
    stored_content_checksum = getattr(version, "content_checksum", None)
    if not stored_content_checksum:
        raise EditorialSealError("Article version checksum is unavailable")
    if article_version_checksum(version) != stored_content_checksum:
        raise EditorialSealError("Article version checksum mismatch")

    package = getattr(review, "review_package_json", None)
    stored_package_checksum = getattr(review, "review_package_checksum", None)
    if not isinstance(package, dict) or not stored_package_checksum:
        raise EditorialSealError("Review package checksum is unavailable")
    if review_package_checksum(package) != stored_package_checksum:
        raise EditorialSealError("Review package checksum mismatch")

    version_id = getattr(version, "id", None)
    version_number = getattr(version, "version", None)
    pipeline_run_id = getattr(version, "pipeline_run_id", None)
    if (
        package.get("article_version_id") != str(version_id)
        or package.get("article_version") != version_number
        or package.get("pipeline_run_id") != str(pipeline_run_id)
        or package.get("article_version_checksum") != stored_content_checksum
        or getattr(review, "article_version_id", None) != version_id
        or getattr(review, "pipeline_run_id", None) != pipeline_run_id
    ):
        raise EditorialSealError("Review package does not target this article version")

    changes = package.get("changes")
    if (
        isinstance(changes, dict)
        and "current_title" in changes
        and changes["current_title"] != getattr(version, "title", None)
    ):
        raise EditorialSealError("Reviewed title no longer matches the article version")

    if require_sealed and getattr(version, "sealed_at", None) is None:
        raise EditorialSealError("Article version is not sealed")
