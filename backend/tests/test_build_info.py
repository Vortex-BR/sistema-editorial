import json

import pytest

from app.core.build_info import BuildInfoError, load_build_info, runtime_identity_gaps
from app.core.config import Settings


COMMIT = "0123456789abcdef0123456789abcdef01234567"
SOURCE = "89abcdef0123456789abcdef0123456789abcdef"
VERSION = "release-2026.07.15"


def production_settings(path, **overrides):
    values = {
        "app_env": "production",
        "app_commit_sha": COMMIT,
        "app_build_version": VERSION,
        "app_source_digest": SOURCE,
        "app_build_info_path": str(path),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def write_build_info(path):
    path.write_text(
        json.dumps(
            {
                "commit_sha": COMMIT,
                "build_version": VERSION,
                "source_digest": SOURCE,
            }
        ),
        encoding="utf-8",
    )


def test_production_reads_identity_from_baked_file(tmp_path):
    path = tmp_path / "build-info.json"
    write_build_info(path)

    info = load_build_info(production_settings(path))

    assert info.baked is True
    assert info.as_dict() == {
        "commit_sha": COMMIT,
        "build_version": VERSION,
        "source_digest": SOURCE,
    }


def test_production_rejects_missing_or_invalid_baked_identity(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(BuildInfoError):
        load_build_info(production_settings(missing))

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"commit_sha":"unversioned"}', encoding="utf-8")
    with pytest.raises(BuildInfoError):
        load_build_info(production_settings(invalid))


def test_runtime_override_must_match_baked_identity(tmp_path):
    path = tmp_path / "build-info.json"
    write_build_info(path)
    config = production_settings(path, app_commit_sha="f" * 40)
    info = load_build_info(config)

    assert runtime_identity_gaps(config, info) == ["APP_COMMIT_SHA_MISMATCH"]
