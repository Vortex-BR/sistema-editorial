#!/usr/bin/env python3
"""Audit application-controlled paths across every layer of a Docker archive."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile
from typing import BinaryIO
from urllib.parse import quote


TAR_BLOCK_SIZE = 512
TAR_END_SIZE = TAR_BLOCK_SIZE * 2
COPY_CHUNK_SIZE = 1024 * 1024
CONTENT_ADDRESSED_LAYER_PATTERN = re.compile(r"blobs/sha256/([0-9a-f]{64})")


@dataclass(frozen=True)
class Limits:
    max_archive_bytes: int = 4 * 1024**3
    max_layers: int = 256
    max_outer_members: int = 10_000
    max_layer_members: int = 250_000
    max_layer_bytes: int = 2 * 1024**3
    max_manifest_bytes: int = 4 * 1024**2
    max_config_bytes: int = 16 * 1024**2
    max_name_bytes: int = 4096


DEFAULT_LIMITS = Limits()

CONTROLLED_ROOTS = (
    "app/backend",
    "app/skills",
    "app/frontend",
    "app/static",
    "usr/share/nginx/html",
    "var/lib/seo",
)
CONTROLLED_FILES = {
    "usr/local/bin/seo-entrypoint",
    "etc/nginx/nginx.conf",
    "etc/supervisor/seo-supervisord.conf",
}
STATIC_ROOTS = {
    "app/frontend",
    "app/static",
    "usr/share/nginx/html",
}
FORBIDDEN_DIRECTORY_NAMES = {
    "test",
    "tests",
    "fixtures",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "htmlcov",
    "node_modules",
}
FORBIDDEN_EXACT_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".coverage",
    "coverage.xml",
    "credentials.json",
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
}
FORBIDDEN_SUFFIXES = {
    ".7z",
    ".bak",
    ".db",
    ".dump",
    ".key",
    ".log",
    ".pem",
    ".rar",
    ".sql",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".zip",
}
PRECOMPRESSED_SUFFIXES = {".br", ".gz"}
PRECOMPRESSED_ASSET_SUFFIXES = {
    ".avif",
    ".css",
    ".eot",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".png",
    ".svg",
    ".ttf",
    ".txt",
    ".wasm",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
}


class AuditError(Exception):
    """Expected validation failure with deliberately constrained output."""

    def __init__(
        self,
        code: str,
        *,
        layer_index: int | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.layer_index = layer_index
        self.path = path

    def render(self) -> str:
        layer = "-" if self.layer_index is None else str(self.layer_index)
        path = "-" if self.path is None else sanitize_path(self.path)
        return f"IMAGE_LAYER_AUDIT_ERROR: code={self.code} layer={layer} path={path}"


def sanitize_path(value: str) -> str:
    """Return a bounded, single-line representation without exposing content."""

    return quote(value, safe="/._-")[:1024] or "-"


def _validate_text(
    value: str,
    *,
    limits: Limits,
    allow_parent: bool,
    allow_absolute: bool = False,
) -> list[str]:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise AuditError("invalid_path_encoding", path=value) from error

    if not value or len(encoded) > limits.max_name_bytes:
        raise AuditError("invalid_path_length", path=value)
    if "\x00" in value or "\\" in value:
        raise AuditError("invalid_path_characters", path=value)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AuditError("invalid_path_characters", path=value)
    if value.startswith("/") and not allow_absolute:
        raise AuditError("absolute_path", path=value)

    candidate = value
    if allow_absolute and candidate.startswith("/"):
        candidate = candidate[1:]
    if candidate.startswith("./"):
        candidate = candidate[2:]
    if candidate.endswith("/"):
        candidate = candidate[:-1]
    if not candidate or "//" in candidate:
        raise AuditError("ambiguous_path", path=value)

    parts = candidate.split("/")
    if any(part in {"", "."} for part in parts):
        raise AuditError("ambiguous_path", path=value)
    if not allow_parent and ".." in parts:
        raise AuditError("path_traversal", path=value)
    return parts


def normalize_member_name(value: str, *, limits: Limits = DEFAULT_LIMITS) -> str:
    return "/".join(_validate_text(value, limits=limits, allow_parent=False))


def resolve_link_target(
    member_path: str,
    linkname: str,
    *,
    is_symlink: bool,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    # Absolute symlink targets are rooted inside the image, never on the host.
    is_absolute = is_symlink and linkname.startswith("/")
    target_parts = _validate_text(
        linkname,
        limits=limits,
        allow_parent=True,
        allow_absolute=is_absolute,
    )
    resolved = member_path.split("/")[:-1] if is_symlink and not is_absolute else []
    for part in target_parts:
        if part == "..":
            if not resolved:
                raise AuditError("link_target_escape", path=member_path)
            resolved.pop()
        else:
            resolved.append(part)
    if not resolved:
        raise AuditError("link_target_escape", path=member_path)
    return "/".join(resolved)


def controlled_root(path: str) -> str | None:
    for root in CONTROLLED_ROOTS:
        if path == root or path.startswith(f"{root}/"):
            return root
    if path in CONTROLLED_FILES:
        return path
    return None


def _is_allowed_precompressed_asset(path: str, root: str) -> bool:
    if root not in STATIC_ROOTS:
        return False
    source_name = PurePosixPath(path).with_suffix("")
    return source_name.suffix.lower() in PRECOMPRESSED_ASSET_SUFFIXES


def forbidden_reason(path: str, *, is_directory: bool) -> str | None:
    root = controlled_root(path)
    if root is None:
        return None

    relative = path.removeprefix(root).lstrip("/")
    parts = [part.lower() for part in relative.split("/") if part]
    if any(part in FORBIDDEN_DIRECTORY_NAMES for part in parts):
        return "forbidden_directory"
    if not parts or is_directory:
        return None

    name = parts[-1]
    suffix = PurePosixPath(name).suffix.lower()
    if (
        name == "conftest.py"
        or (name.startswith("test_") and suffix == ".py")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    ):
        return "test_artifact"
    if (
        name in FORBIDDEN_EXACT_NAMES
        or name.startswith(".env.")
        or name.startswith("id_rsa")
        or name.startswith("secrets.")
    ):
        return "local_or_sensitive_artifact"
    if suffix in PRECOMPRESSED_SUFFIXES:
        if _is_allowed_precompressed_asset(path, root):
            return None
        return "unexpected_compressed_artifact"
    if suffix in FORBIDDEN_SUFFIXES:
        return "forbidden_artifact"
    if root == "app/backend" and (
        name.startswith("readme") or suffix in {".md", ".rst"}
    ):
        return "internal_documentation"
    return None


def _validate_tar_termination(file: BinaryIO, size: int, *, code: str) -> None:
    if size < TAR_END_SIZE or size % TAR_BLOCK_SIZE:
        raise AuditError(code)
    file.seek(size - TAR_END_SIZE)
    if file.read(TAR_END_SIZE) != b"\x00" * TAR_END_SIZE:
        raise AuditError(code)
    file.seek(0)


def _spool_archive(source: BinaryIO, destination: Path, *, limits: Limits) -> int:
    total = 0
    with destination.open("wb") as output:
        while True:
            chunk = source.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limits.max_archive_bytes:
                raise AuditError("archive_size_limit")
            output.write(chunk)
    if total == 0:
        raise AuditError("empty_archive")
    return total


def _load_manifest(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    *,
    limits: Limits,
) -> tuple[list[str], str]:
    manifest_member = members.get("manifest.json")
    if manifest_member is None or not manifest_member.isfile():
        raise AuditError("missing_manifest")
    if manifest_member.size > limits.max_manifest_bytes:
        raise AuditError("manifest_size_limit", path="manifest.json")
    manifest_file = archive.extractfile(manifest_member)
    if manifest_file is None:
        raise AuditError("invalid_manifest", path="manifest.json")
    try:
        manifest = json.load(io.TextIOWrapper(manifest_file, encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AuditError("invalid_manifest", path="manifest.json") from error
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise AuditError("invalid_manifest", path="manifest.json")

    entry = manifest[0]
    if not isinstance(entry, dict):
        raise AuditError("invalid_manifest", path="manifest.json")
    layers = entry.get("Layers")
    config = entry.get("Config")
    if not isinstance(layers, list) or not layers:
        raise AuditError("missing_layers", path="manifest.json")
    if len(layers) > limits.max_layers:
        raise AuditError("layer_count_limit", path="manifest.json")
    if not isinstance(config, str):
        raise AuditError("invalid_manifest", path="manifest.json")

    normalized_layers: list[str] = []
    seen_layers: set[str] = set()
    for layer in layers:
        if not isinstance(layer, str) or not layer:
            raise AuditError("invalid_manifest", path="manifest.json")
        if layer.endswith("/"):
            raise AuditError("ambiguous_path", path=layer)
        normalized = normalize_member_name(layer, limits=limits)
        if normalized in seen_layers:
            raise AuditError("invalid_layer_reference", path=normalized)
        seen_layers.add(normalized)
        normalized_layers.append(normalized)

    normalized_config = normalize_member_name(config, limits=limits)
    config_member = members.get(normalized_config)
    if config_member is None or not config_member.isfile():
        raise AuditError("missing_config", path=normalized_config)
    if config_member.size > limits.max_config_bytes:
        raise AuditError("config_size_limit", path=normalized_config)
    return normalized_layers, normalized_config


def _validate_content_addressed_digest(
    layer_file: BinaryIO,
    layer_path: str,
    *,
    layer_index: int,
) -> None:
    match = CONTENT_ADDRESSED_LAYER_PATTERN.fullmatch(layer_path)
    if match is None:
        return

    expected_digest = match.group(1)
    digest = hashlib.sha256()
    while True:
        chunk = layer_file.read(COPY_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise AuditError(
            "IMAGE_LAYER_DIGEST_MISMATCH",
            layer_index=layer_index,
            path=layer_path,
        )
    layer_file.seek(0)


def _validate_layer_tail(
    file: BinaryIO,
    *,
    layer_index: int,
    layer_path: str,
    limits: Limits,
) -> None:
    decoded_size = file.tell()
    trailing_size = 0
    while True:
        chunk = file.read(COPY_CHUNK_SIZE)
        if not chunk:
            break
        decoded_size += len(chunk)
        trailing_size += len(chunk)
        if decoded_size > limits.max_layer_bytes:
            raise AuditError(
                "invalid_layer",
                layer_index=layer_index,
                path=layer_path,
            )
        if any(chunk):
            raise AuditError(
                "truncated_layer",
                layer_index=layer_index,
                path=layer_path,
            )
    if (
        decoded_size < TAR_END_SIZE
        or decoded_size % TAR_BLOCK_SIZE
        or trailing_size < TAR_BLOCK_SIZE
    ):
        raise AuditError(
            "truncated_layer",
            layer_index=layer_index,
            path=layer_path,
        )


def _audit_layer(
    archive: tarfile.TarFile,
    layer_member: tarfile.TarInfo,
    *,
    layer_path: str,
    layer_index: int,
    limits: Limits,
) -> int:
    if not layer_member.isfile() or layer_member.size > limits.max_layer_bytes:
        raise AuditError(
            "invalid_layer",
            layer_index=layer_index,
            path=layer_path,
        )
    layer_file = archive.extractfile(layer_member)
    if layer_file is None:
        raise AuditError(
            "invalid_layer",
            layer_index=layer_index,
            path=layer_path,
        )

    try:
        _validate_content_addressed_digest(
            layer_file,
            layer_path,
            layer_index=layer_index,
        )
        layer_archive = tarfile.open(fileobj=layer_file, mode="r:*")
    except (AuditError, EOFError, tarfile.TarError, OSError, ValueError) as error:
        layer_file.close()
        if isinstance(error, AuditError):
            raise
        raise AuditError(
            "invalid_layer",
            layer_index=layer_index,
            path=layer_path,
        ) from error

    member_count = 0
    try:
        for member in layer_archive:
            member_count += 1
            if member_count > limits.max_layer_members:
                raise AuditError(
                    "layer_member_limit",
                    layer_index=layer_index,
                    path=member.name,
                )
            if layer_archive.offset > limits.max_layer_bytes:
                raise AuditError(
                    "invalid_layer",
                    layer_index=layer_index,
                    path=layer_path,
                )
            if member.name in {".", "./"}:
                if member.isdir():
                    continue
                raise AuditError(
                    "ambiguous_path",
                    layer_index=layer_index,
                    path=member.name,
                )
            try:
                path = normalize_member_name(member.name, limits=limits)
            except AuditError as error:
                raise AuditError(
                    error.code,
                    layer_index=layer_index,
                    path=member.name,
                ) from error

            root = controlled_root(path)
            is_whiteout = PurePosixPath(path).name.startswith(".wh.")
            if member.issym() or member.islnk():
                try:
                    target = resolve_link_target(
                        path,
                        member.linkname,
                        is_symlink=member.issym(),
                        limits=limits,
                    )
                except AuditError as error:
                    raise AuditError(
                        error.code,
                        layer_index=layer_index,
                        path=path,
                    ) from error
                if root is not None:
                    target_root = controlled_root(target)
                    if target_root != root:
                        raise AuditError(
                            "controlled_link_escape",
                            layer_index=layer_index,
                            path=path,
                        )
                    reason = forbidden_reason(target, is_directory=False)
                    if reason is not None:
                        raise AuditError(reason, layer_index=layer_index, path=path)
            elif root is not None and not (
                member.isfile() or member.isdir() or is_whiteout
            ):
                raise AuditError(
                    "unexpected_member_type",
                    layer_index=layer_index,
                    path=path,
                )

            reason = forbidden_reason(path, is_directory=member.isdir())
            if reason is not None and not is_whiteout:
                raise AuditError(reason, layer_index=layer_index, path=path)

        _validate_layer_tail(
            layer_archive.fileobj,
            layer_index=layer_index,
            layer_path=layer_path,
            limits=limits,
        )
    except (AuditError, EOFError, tarfile.TarError, OSError, ValueError) as error:
        if isinstance(error, AuditError):
            raise
        raise AuditError(
            "invalid_layer",
            layer_index=layer_index,
            path=layer_path,
        ) from error
    finally:
        try:
            layer_archive.close()
        finally:
            layer_file.close()
    return member_count


def audit_archive(path: Path, *, limits: Limits = DEFAULT_LIMITS) -> tuple[int, int]:
    size = path.stat().st_size
    with path.open("rb") as raw_archive:
        _validate_tar_termination(raw_archive, size, code="truncated_archive")

    try:
        archive = tarfile.open(path, mode="r:")
    except (tarfile.TarError, OSError) as error:
        raise AuditError("invalid_archive") from error

    try:
        members: dict[str, tarfile.TarInfo] = {}
        outer_count = 0
        for member in archive:
            outer_count += 1
            if outer_count > limits.max_outer_members:
                raise AuditError("outer_member_limit", path=member.name)
            if member.name in {".", "./"}:
                if member.isdir():
                    continue
                raise AuditError("ambiguous_path", path=member.name)
            try:
                normalized = normalize_member_name(member.name, limits=limits)
            except AuditError as error:
                raise AuditError(error.code, path=member.name) from error
            if member.issym() or member.islnk():
                raise AuditError("invalid_outer_member", path=normalized)
            if not (member.isfile() or member.isdir()):
                raise AuditError("invalid_outer_member", path=normalized)
            if normalized in members:
                raise AuditError("duplicate_outer_member", path=normalized)
            members[normalized] = member

        layers, _config = _load_manifest(archive, members, limits=limits)
        total_layer_members = 0
        for layer_index, layer_path in enumerate(layers, start=1):
            layer_member = members.get(layer_path)
            if layer_member is None:
                raise AuditError(
                    "missing_layer",
                    layer_index=layer_index,
                    path=layer_path,
                )
            total_layer_members += _audit_layer(
                archive,
                layer_member,
                layer_path=layer_path,
                layer_index=layer_index,
                limits=limits,
            )
        return len(layers), total_layer_members
    except (AuditError, tarfile.TarError, OSError) as error:
        if isinstance(error, AuditError):
            raise
        raise AuditError("invalid_archive") from error
    finally:
        archive.close()


def audit_stream(
    source: BinaryIO,
    *,
    temp_parent: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[int, int]:
    preferred_parent = temp_parent
    if preferred_parent is None:
        runner_temp = os.environ.get("RUNNER_TEMP")
        preferred_parent = Path(runner_temp) if runner_temp else None

    temporary = tempfile.TemporaryDirectory(
        prefix="seo-image-layer-audit-",
        dir=preferred_parent,
    )
    try:
        temporary_path = Path(temporary.name).resolve()
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
        if temporary_path.is_relative_to(workspace):
            raise AuditError("unsafe_temp_location")
        archive_path = temporary_path / "image.tar"
        _spool_archive(source, archive_path, limits=limits)
        return audit_archive(archive_path, limits=limits)
    finally:
        temporary.cleanup()


def main() -> int:
    try:
        layer_count, member_count = audit_stream(sys.stdin.buffer)
    except AuditError as error:
        print(error.render(), file=sys.stderr)
        return 1
    except Exception:
        print(
            "IMAGE_LAYER_AUDIT_ERROR: code=unexpected_failure layer=- path=-",
            file=sys.stderr,
        )
        return 1
    print(f"IMAGE_LAYER_AUDIT: passed layers={layer_count} members={member_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
