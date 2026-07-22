from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, settings


_GIT_DIGEST = re.compile(r"^[0-9a-f]{40}$")


class BuildInfoError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildInfo:
    commit_sha: str
    build_version: str
    source_digest: str
    baked: bool

    def as_dict(self) -> dict[str, str]:
        return {
            "commit_sha": self.commit_sha,
            "build_version": self.build_version,
            "source_digest": self.source_digest,
        }


def _development_info(config: Settings) -> BuildInfo:
    return BuildInfo(
        commit_sha=config.app_commit_sha.strip() or "unversioned",
        build_version=config.app_build_version.strip() or "development",
        source_digest=config.app_source_digest.strip() or "unversioned",
        baked=False,
    )


def load_build_info(
    config: Settings = settings,
    *,
    required: bool | None = None,
) -> BuildInfo:
    must_exist = config.is_production if required is None else required
    path = Path(config.app_build_info_path)
    if not path.is_file():
        if must_exist:
            raise BuildInfoError("Baked build information is unavailable")
        return _development_info(config)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if must_exist:
            raise BuildInfoError("Baked build information is invalid") from exc
        return _development_info(config)
    if not isinstance(payload, dict):
        if must_exist:
            raise BuildInfoError("Baked build information is invalid")
        return _development_info(config)
    commit_sha = str(payload.get("commit_sha") or "").strip().lower()
    build_version = str(payload.get("build_version") or "").strip()
    source_digest = str(payload.get("source_digest") or "").strip().lower()
    if not _GIT_DIGEST.fullmatch(commit_sha):
        if must_exist:
            raise BuildInfoError("Baked commit SHA is invalid")
        return _development_info(config)
    if not build_version or build_version.lower() in {"development", "unversioned"}:
        if must_exist:
            raise BuildInfoError("Baked build version is invalid")
        return _development_info(config)
    if not _GIT_DIGEST.fullmatch(source_digest):
        if must_exist:
            raise BuildInfoError("Baked source digest is invalid")
        return _development_info(config)
    return BuildInfo(
        commit_sha=commit_sha,
        build_version=build_version,
        source_digest=source_digest,
        baked=True,
    )


def runtime_identity_gaps(config: Settings, info: BuildInfo) -> list[str]:
    gaps: list[str] = []
    runtime_values = {
        "APP_COMMIT_SHA_MISMATCH": config.app_commit_sha.strip().lower(),
        "APP_BUILD_VERSION_MISMATCH": config.app_build_version.strip(),
        "APP_SOURCE_DIGEST_MISMATCH": config.app_source_digest.strip().lower(),
    }
    baked_values = {
        "APP_COMMIT_SHA_MISMATCH": info.commit_sha,
        "APP_BUILD_VERSION_MISMATCH": info.build_version,
        "APP_SOURCE_DIGEST_MISMATCH": info.source_digest,
    }
    ignored = {"", "development", "unversioned"}
    for requirement, runtime_value in runtime_values.items():
        if runtime_value not in ignored and runtime_value != baked_values[requirement]:
            gaps.append(requirement)
    return gaps
