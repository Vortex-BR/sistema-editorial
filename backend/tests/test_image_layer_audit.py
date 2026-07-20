from __future__ import annotations

from dataclasses import replace
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).parents[2]
HELPER_PATH = ROOT / "scripts" / "ci" / "audit_image_layers.py"
SPEC = importlib.util.spec_from_file_location("audit_image_layers", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
audit_image_layers = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_image_layers
SPEC.loader.exec_module(audit_image_layers)


def add_tar_member(
    archive: tarfile.TarFile,
    name: str,
    *,
    data: bytes = b"",
    member_type: bytes = tarfile.REGTYPE,
    linkname: str = "",
) -> None:
    member = tarfile.TarInfo(name)
    member.type = member_type
    member.linkname = linkname
    if member_type == tarfile.REGTYPE:
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    else:
        archive.addfile(member)


def build_layer(entries: list[dict[str, object]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for entry in entries:
            add_tar_member(archive, **entry)
    return output.getvalue()


def content_addressed_name(data: bytes) -> str:
    return f"blobs/sha256/{hashlib.sha256(data).hexdigest()}"


def build_docker_archive(
    layers: list[bytes],
    *,
    include_manifest: bool = True,
    layout: str = "legacy",
    layer_names: list[str] | None = None,
    layer_members: list[dict[str, object]] | None = None,
) -> bytes:
    output = io.BytesIO()
    config_data = b"{}"
    if layout == "legacy":
        config_name = f"{'c' * 64}.json"
        default_layer_names = [
            f"layer-{index}/layer.tar" for index in range(1, len(layers) + 1)
        ]
    elif layout == "content-addressed":
        config_name = content_addressed_name(config_data)
        default_layer_names = [content_addressed_name(layer) for layer in layers]
    else:
        raise ValueError(f"unsupported test archive layout: {layout}")

    manifest_layer_names = default_layer_names if layer_names is None else layer_names
    manifest = [
        {
            "Config": config_name,
            "RepoTags": ["seo-research-ledger:test"],
            "Layers": manifest_layer_names,
        }
    ]

    if layer_members is None:
        layer_members = [
            {"name": name, "data": layer}
            for name, layer in zip(manifest_layer_names, layers, strict=True)
        ]

    with tarfile.open(fileobj=output, mode="w") as archive:
        add_tar_member(archive, ".", member_type=tarfile.DIRTYPE)
        add_tar_member(archive, config_name, data=config_data)
        for layer_member in layer_members:
            add_tar_member(archive, **layer_member)
        if include_manifest:
            add_tar_member(
                archive,
                "manifest.json",
                data=json.dumps(manifest).encode("utf-8"),
            )
    return output.getvalue()


def audit_bytes(
    archive: bytes,
    temp_path: Path,
    *,
    limits: object | None = None,
) -> tuple[int, int]:
    try:
        return audit_image_layers.audit_stream(
            io.BytesIO(archive),
            temp_parent=temp_path,
            limits=limits or audit_image_layers.DEFAULT_LIMITS,
        )
    finally:
        assert list(temp_path.iterdir()) == []


def assert_audit_error(
    archive: bytes,
    temp_path: Path,
    code: str,
    *,
    limits: object | None = None,
) -> audit_image_layers.AuditError:
    with pytest.raises(audit_image_layers.AuditError) as captured:
        audit_bytes(archive, temp_path, limits=limits)
    assert captured.value.code == code
    return captured.value


def test_legacy_layer_tar_reference_is_accepted(tmp_path: Path):
    layer = build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])

    assert audit_bytes(build_docker_archive([layer]), tmp_path) == (1, 1)


def test_content_addressed_layer_digest_is_validated_and_audited(tmp_path: Path):
    layer = build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])
    layer_name = content_addressed_name(layer)

    assert audit_bytes(
        build_docker_archive([layer], layout="content-addressed"),
        tmp_path,
    ) == (1, 1)
    assert layer_name.startswith("blobs/sha256/")
    assert len(layer_name.removeprefix("blobs/sha256/")) == 64


def test_safe_opaque_layer_reference_without_extension_is_accepted(tmp_path: Path):
    layer = build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])
    layer_name = "layers/runtime/current"

    assert audit_bytes(
        build_docker_archive([layer], layer_names=[layer_name]),
        tmp_path,
    ) == (1, 1)


def test_content_addressed_compressed_layer_is_detected_by_content(tmp_path: Path):
    layer = build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])
    compressed_layer = gzip.compress(layer, mtime=0)

    assert audit_bytes(
        build_docker_archive([compressed_layer], layout="content-addressed"),
        tmp_path,
    ) == (1, 1)


def test_content_addressed_digest_mismatch_fails_before_tar_parsing(tmp_path: Path):
    private_content = b"PRIVATE-CONTENT-NOT-A-TAR"
    incorrect_name = f"blobs/sha256/{'0' * 64}"

    error = assert_audit_error(
        build_docker_archive(
            [private_content],
            layout="content-addressed",
            layer_names=[incorrect_name],
        ),
        tmp_path,
        "IMAGE_LAYER_DIGEST_MISMATCH",
    )
    assert error.layer_index == 1
    assert error.path == incorrect_name
    assert private_content.decode() not in error.render()


def test_valid_layer_with_incorrect_content_addressed_digest_is_rejected(
    tmp_path: Path,
):
    layer = build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])
    incorrect_name = f"blobs/sha256/{'0' * 64}"

    assert_audit_error(
        build_docker_archive(
            [layer],
            layout="content-addressed",
            layer_names=[incorrect_name],
        ),
        tmp_path,
        "IMAGE_LAYER_DIGEST_MISMATCH",
    )


def test_unreferenced_outer_blob_is_not_audited_as_a_layer(tmp_path: Path):
    referenced_layer = build_layer(
        [{"name": "app/backend/app/main.py", "data": b"runtime"}]
    )
    unreferenced_layer = build_layer(
        [{"name": "app/backend/tests/test_hidden.py", "data": b"private"}]
    )
    referenced_name = content_addressed_name(referenced_layer)

    archive = build_docker_archive(
        [referenced_layer],
        layout="content-addressed",
        layer_members=[
            {"name": referenced_name, "data": referenced_layer},
            {"name": content_addressed_name(unreferenced_layer), "data": unreferenced_layer},
        ],
    )
    assert audit_bytes(archive, tmp_path) == (1, 1)


@pytest.mark.parametrize("layout", ["legacy", "content-addressed"])
def test_realistic_docker_archive_allows_base_tests_and_precompressed_assets(
    tmp_path: Path,
    layout: str,
):
    base_layer = build_layer(
        [
            {"name": ".", "member_type": tarfile.DIRTYPE},
            {"name": "usr/local/lib/python3.12/tests", "member_type": tarfile.DIRTYPE},
            {"name": "usr/local/lib/python3.12/tests/test_os.py", "data": b"base"},
        ]
    )
    application_layer = build_layer(
        [
            {"name": "app/backend/app/main.py", "data": b"runtime"},
            {"name": "app/skills/default/SKILL.md", "data": b"runtime skill"},
            {"name": "usr/share/nginx/html/assets/app.js", "data": b"asset"},
            {"name": "usr/share/nginx/html/assets/app.js.gz", "data": b"compressed"},
            {"name": "usr/share/nginx/html/assets/app.css.br", "data": b"compressed"},
        ]
    )

    assert audit_bytes(
        build_docker_archive([base_layer, application_layer], layout=layout),
        tmp_path,
    ) == (2, 8)


@pytest.mark.parametrize("layout", ["legacy", "content-addressed"])
def test_old_application_tests_fail_even_when_a_later_layer_whiteouts_them(
    tmp_path: Path,
    layout: str,
):
    old_layer = build_layer(
        [{"name": "app/backend/tests/test_hidden.py", "data": b"never print me"}]
    )
    whiteout_layer = build_layer(
        [{"name": "app/backend/.wh.tests", "data": b""}]
    )

    error = assert_audit_error(
        build_docker_archive([old_layer, whiteout_layer], layout=layout),
        tmp_path,
        "forbidden_directory",
    )
    rendered = error.render()
    assert "layer=1" in rendered
    assert "app/backend/tests/test_hidden.py" in rendered
    assert "never print me" not in rendered


def test_forbidden_application_tests_inside_content_addressed_blob_fail(
    tmp_path: Path,
):
    layer = build_layer(
        [{"name": "app/backend/tests/test_private.py", "data": b"never print me"}]
    )

    error = assert_audit_error(
        build_docker_archive([layer], layout="content-addressed"),
        tmp_path,
        "forbidden_directory",
    )
    assert error.layer_index == 1
    assert error.path == "app/backend/tests/test_private.py"
    assert "never print me" not in error.render()


@pytest.mark.parametrize(
    ("member_type", "linkname", "expected_code"),
    [
        (tarfile.SYMTYPE, "../../../etc/passwd", "link_target_escape"),
        (tarfile.LNKTYPE, "../../etc/passwd", "link_target_escape"),
        (tarfile.SYMTYPE, "../../etc/passwd", "controlled_link_escape"),
        (tarfile.SYMTYPE, "/etc/passwd", "controlled_link_escape"),
        (
            tarfile.SYMTYPE,
            "/app/backend/tests/test_hidden.py",
            "forbidden_directory",
        ),
        (tarfile.LNKTYPE, "/app/backend/app/main.py", "absolute_path"),
        (tarfile.LNKTYPE, r"..\..\etc\passwd", "invalid_path_characters"),
    ],
)
def test_links_cannot_escape_the_image_or_controlled_root(
    tmp_path: Path,
    member_type: bytes,
    linkname: str,
    expected_code: str,
):
    layer = build_layer(
        [
            {
                "name": "app/backend/current",
                "member_type": member_type,
                "linkname": linkname,
            }
        ]
    )
    error = assert_audit_error(
        build_docker_archive([layer]),
        tmp_path,
        expected_code,
    )
    assert error.path == "app/backend/current"


def test_absolute_symlink_target_inside_image_and_controlled_root_is_allowed(
    tmp_path: Path,
):
    layer = build_layer(
        [
            {
                "name": "app/backend/current",
                "member_type": tarfile.SYMTYPE,
                "linkname": "/app/backend/app/main.py",
            },
            {"name": "app/backend/app/main.py", "data": b"runtime"},
        ]
    )

    assert audit_bytes(build_docker_archive([layer]), tmp_path) == (1, 2)


@pytest.mark.parametrize(
    "linkname",
    [
        "../../../host/etc/passwd",
        "/../../host/etc/passwd",
        "/usr/../../host/etc/passwd",
    ],
)
def test_symlink_target_cannot_escape_image_root(tmp_path: Path, linkname: str):
    layer = build_layer(
        [
            {
                "name": "etc/alternatives/awk",
                "member_type": tarfile.SYMTYPE,
                "linkname": linkname,
            }
        ]
    )

    error = assert_audit_error(
        build_docker_archive([layer]),
        tmp_path,
        "link_target_escape",
    )
    assert error.path == "etc/alternatives/awk"


def test_debian_alternatives_absolute_symlinks_are_all_audited(tmp_path: Path):
    alternatives = {
        "awk": "/usr/bin/mawk",
        "editor": "/bin/nano",
        "nawk": "/usr/bin/mawk",
        "pager": "/bin/more",
        "pico": "/bin/nano",
        "vi": "/usr/bin/vim.basic",
        "www-browser": "/usr/bin/www-browser",
    }
    layer = build_layer(
        [
            {
                "name": f"etc/alternatives/{name}",
                "member_type": tarfile.SYMTYPE,
                "linkname": target,
            }
            for name, target in alternatives.items()
        ]
    )

    assert audit_bytes(build_docker_archive([layer]), tmp_path) == (
        1,
        len(alternatives),
    )


def test_absolute_symlink_member_path_remains_rejected(tmp_path: Path):
    layer = build_layer(
        [
            {
                "name": "/etc/alternatives/awk",
                "member_type": tarfile.SYMTYPE,
                "linkname": "/usr/bin/mawk",
            }
        ]
    )

    assert_audit_error(
        build_docker_archive([layer]),
        tmp_path,
        "absolute_path",
    )


@pytest.mark.parametrize(
    ("name", "expected_code"),
    [
        ("/app/backend/absolute.py", "absolute_path"),
        ("app/backend/../escape.py", "path_traversal"),
        (r"app\backend\ambiguous.py", "invalid_path_characters"),
    ],
)
def test_invalid_member_paths_fail_closed(
    tmp_path: Path,
    name: str,
    expected_code: str,
):
    layer = build_layer([{"name": name, "data": b"content must remain private"}])
    error = assert_audit_error(
        build_docker_archive([layer]),
        tmp_path,
        expected_code,
    )
    assert "content%20must%20remain%20private" not in error.render()


@pytest.mark.parametrize(
    ("layer_reference", "expected_code"),
    [
        ("/layers/runtime", "absolute_path"),
        ("layers/../runtime", "path_traversal"),
        (r"layers\runtime", "invalid_path_characters"),
        ("layers//runtime", "ambiguous_path"),
        ("layers/runtime/", "ambiguous_path"),
    ],
)
def test_invalid_manifest_layer_references_fail_closed(
    tmp_path: Path,
    layer_reference: str,
    expected_code: str,
):
    layer = build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])

    error = assert_audit_error(
        build_docker_archive(
            [layer],
            layer_names=[layer_reference],
            layer_members=[],
        ),
        tmp_path,
        expected_code,
    )
    assert error.path == layer_reference


@pytest.mark.parametrize("layer_reference", ["", None])
def test_manifest_layer_reference_must_be_a_non_empty_string(
    tmp_path: Path,
    layer_reference: object,
):
    archive = build_docker_archive(
        [build_layer([])],
        layer_names=[layer_reference],  # type: ignore[list-item]
        layer_members=[],
    )

    assert_audit_error(archive, tmp_path, "invalid_manifest")


def test_missing_repeated_and_duplicate_layer_references_fail_closed(tmp_path: Path):
    layer = build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])
    layer_name = "layers/runtime"

    missing = build_docker_archive(
        [layer],
        layer_names=[layer_name],
        layer_members=[],
    )
    assert_audit_error(missing, tmp_path, "missing_layer")

    repeated_reference = build_docker_archive(
        [layer],
        layer_names=[layer_name, layer_name],
        layer_members=[{"name": layer_name, "data": layer}],
    )
    assert_audit_error(repeated_reference, tmp_path, "invalid_layer_reference")

    duplicate_member = build_docker_archive(
        [layer],
        layer_names=[layer_name],
        layer_members=[
            {"name": layer_name, "data": layer},
            {"name": layer_name, "data": layer},
        ],
    )
    assert_audit_error(duplicate_member, tmp_path, "duplicate_outer_member")


@pytest.mark.parametrize(
    ("member_type", "expected_code"),
    [
        (tarfile.DIRTYPE, "invalid_layer"),
        (tarfile.SYMTYPE, "invalid_outer_member"),
        (tarfile.LNKTYPE, "invalid_outer_member"),
        (tarfile.CHRTYPE, "invalid_outer_member"),
        (tarfile.FIFOTYPE, "invalid_outer_member"),
        (b"Z", "invalid_outer_member"),
    ],
)
def test_referenced_outer_layer_must_be_a_regular_file(
    tmp_path: Path,
    member_type: bytes,
    expected_code: str,
):
    layer_name = "layers/runtime"
    archive = build_docker_archive(
        [build_layer([])],
        layer_names=[layer_name],
        layer_members=[
            {
                "name": layer_name,
                "member_type": member_type,
                "linkname": "layers/target",
            }
        ],
    )

    assert_audit_error(archive, tmp_path, expected_code)


def test_nul_and_overlong_paths_are_rejected_when_representable():
    with pytest.raises(audit_image_layers.AuditError, match="invalid_path_characters"):
        audit_image_layers.normalize_member_name("app/backend/bad\x00name")

    limits = replace(audit_image_layers.DEFAULT_LIMITS, max_name_bytes=16)
    with pytest.raises(audit_image_layers.AuditError, match="invalid_path_length"):
        audit_image_layers.normalize_member_name(
            "app/backend/too-long.py",
            limits=limits,
        )

    with pytest.raises(audit_image_layers.AuditError, match="invalid_path_characters"):
        audit_image_layers.resolve_link_target(
            "app/backend/current",
            "target\x00name",
            is_symlink=True,
        )


def test_special_member_type_in_controlled_root_is_rejected(tmp_path: Path):
    layer = build_layer(
        [{"name": "app/backend/device", "member_type": tarfile.CHRTYPE}]
    )
    assert_audit_error(
        build_docker_archive([layer]),
        tmp_path,
        "unexpected_member_type",
    )


def test_control_characters_are_sanitized_and_never_create_multiline_output(
    tmp_path: Path,
):
    layer = build_layer(
        [{"name": "app/backend/bad\nname.py", "data": b"PRIVATE-CONTENT"}]
    )
    error = assert_audit_error(
        build_docker_archive([layer]),
        tmp_path,
        "invalid_path_characters",
    )
    rendered = error.render()
    assert "\n" not in rendered
    assert "%0A" in rendered
    assert "PRIVATE-CONTENT" not in rendered


@pytest.mark.parametrize(
    "path",
    [
        "app/backend/review-package.zip",
        "app/backend/export.dump",
        "app/backend/backup.sql",
        "app/backend/local.sqlite3",
        "var/lib/seo/debug.log",
        "app/static/backup.sql.gz",
    ],
)
def test_precise_forbidden_artifacts_fail_without_printing_content(
    tmp_path: Path,
    path: str,
):
    secret_content = b"PRIVATE-CONTENT-MUST-NOT-APPEAR"
    layer = build_layer([{"name": path, "data": secret_content}])
    error = assert_audit_error(build_docker_archive([layer]), tmp_path, error_code(path))
    rendered = error.render()
    assert path in rendered
    assert secret_content.decode() not in rendered


def error_code(path: str) -> str:
    if path.endswith(".gz"):
        return "unexpected_compressed_artifact"
    return "forbidden_artifact"


def test_truncated_empty_missing_manifest_and_missing_layers_fail_closed(tmp_path: Path):
    valid = build_docker_archive([build_layer([])])
    last_content_byte = len(valid.rstrip(b"\x00"))
    one_end_block = (
        valid[: ((last_content_byte + 511) // 512) * 512] + b"\x00" * 512
    )

    assert_audit_error(one_end_block, tmp_path, "truncated_archive")
    assert_audit_error(b"", tmp_path, "empty_archive")
    assert_audit_error(
        build_docker_archive([build_layer([])], include_manifest=False),
        tmp_path,
        "missing_manifest",
    )

    no_layers = build_docker_archive([build_layer([])])
    manifest = json.dumps(
        [{"Config": f"{'c' * 64}.json", "RepoTags": [], "Layers": []}]
    ).encode()
    no_layers = replace_outer_member(no_layers, "manifest.json", manifest)
    assert_audit_error(no_layers, tmp_path, "missing_layers")


def replace_outer_member(archive_bytes: bytes, name: str, replacement: bytes) -> bytes:
    source = io.BytesIO(archive_bytes)
    output = io.BytesIO()
    with tarfile.open(fileobj=source, mode="r:") as source_archive:
        with tarfile.open(fileobj=output, mode="w") as destination:
            for member in source_archive:
                if not member.isfile():
                    destination.addfile(member)
                    continue
                data_file = source_archive.extractfile(member)
                assert data_file is not None
                data = replacement if member.name == name else data_file.read()
                add_tar_member(destination, member.name, data=data)
    return output.getvalue()


def test_limits_for_archive_layers_members_and_layer_size_cleanup_temp_files(
    tmp_path: Path,
):
    two_member_layer = build_layer(
        [
            {"name": "app/backend/app/one.py", "data": b"1"},
            {"name": "app/backend/app/two.py", "data": b"2"},
        ]
    )
    archive = build_docker_archive([two_member_layer])

    assert_audit_error(
        archive,
        tmp_path,
        "archive_size_limit",
        limits=replace(audit_image_layers.DEFAULT_LIMITS, max_archive_bytes=len(archive) - 1),
    )
    assert_audit_error(
        build_docker_archive([build_layer([]), build_layer([])]),
        tmp_path,
        "layer_count_limit",
        limits=replace(audit_image_layers.DEFAULT_LIMITS, max_layers=1),
    )
    assert_audit_error(
        archive,
        tmp_path,
        "layer_member_limit",
        limits=replace(audit_image_layers.DEFAULT_LIMITS, max_layer_members=1),
    )
    assert_audit_error(
        archive,
        tmp_path,
        "invalid_layer",
        limits=replace(audit_image_layers.DEFAULT_LIMITS, max_layer_bytes=1024),
    )
    assert_audit_error(
        archive,
        tmp_path,
        "outer_member_limit",
        limits=replace(audit_image_layers.DEFAULT_LIMITS, max_outer_members=2),
    )
    assert_audit_error(
        archive,
        tmp_path,
        "manifest_size_limit",
        limits=replace(audit_image_layers.DEFAULT_LIMITS, max_manifest_bytes=16),
    )
    assert_audit_error(
        archive,
        tmp_path,
        "config_size_limit",
        limits=replace(audit_image_layers.DEFAULT_LIMITS, max_config_bytes=1),
    )


def test_malformed_layer_fails_closed(tmp_path: Path):
    malformed_layer = b"not-a-valid-tar".ljust(512, b"x") + b"\x00" * 1024
    assert_audit_error(
        build_docker_archive([malformed_layer]),
        tmp_path,
        "invalid_layer",
    )


def test_truncated_content_addressed_layer_fails_closed(tmp_path: Path):
    valid_layer = build_layer(
        [{"name": "app/backend/app/main.py", "data": b"runtime"}]
    )
    last_content_byte = len(valid_layer.rstrip(b"\x00"))
    truncated_layer = (
        valid_layer[: ((last_content_byte + 511) // 512) * 512] + b"\x00" * 512
    )

    assert_audit_error(
        build_docker_archive([truncated_layer], layout="content-addressed"),
        tmp_path,
        "truncated_layer",
    )


def test_truncated_compressed_content_addressed_layer_fails_closed(tmp_path: Path):
    valid_layer = build_layer(
        [{"name": "app/backend/app/main.py", "data": b"runtime"}]
    )
    truncated_compressed_layer = gzip.compress(valid_layer, mtime=0)[:-8]

    assert_audit_error(
        build_docker_archive(
            [truncated_compressed_layer],
            layout="content-addressed",
        ),
        tmp_path,
        "invalid_layer",
    )


def test_cli_reports_only_bounded_metadata_and_never_file_content():
    clean_archive = build_docker_archive(
        [build_layer([{"name": "app/backend/app/main.py", "data": b"runtime"}])]
    )
    clean = subprocess.run(
        [sys.executable, str(HELPER_PATH)],
        input=clean_archive,
        capture_output=True,
        check=False,
    )
    assert clean.returncode == 0
    assert clean.stderr == b""
    assert clean.stdout.startswith(b"IMAGE_LAYER_AUDIT: passed layers=1 members=1")

    private_content = b"PRIVATE-CONTENT-MUST-NOT-APPEAR"
    forbidden_archive = build_docker_archive(
        [
            build_layer(
                [
                    {
                        "name": "app/backend/tests/test_private.py",
                        "data": private_content,
                    }
                ]
            )
        ]
    )
    forbidden = subprocess.run(
        [sys.executable, str(HELPER_PATH)],
        input=forbidden_archive,
        capture_output=True,
        check=False,
    )
    assert forbidden.returncode == 1
    assert forbidden.stdout == b""
    assert b"code=forbidden_directory layer=1" in forbidden.stderr
    assert b"path=app/backend/tests/test_private.py" in forbidden.stderr
    assert private_content not in forbidden.stderr

    digest_private_content = b"PRIVATE-DIGEST-CONTENT-NOT-A-TAR"
    incorrect_name = f"blobs/sha256/{'0' * 64}"
    digest_mismatch_archive = build_docker_archive(
        [digest_private_content],
        layout="content-addressed",
        layer_names=[incorrect_name],
    )
    digest_mismatch = subprocess.run(
        [sys.executable, str(HELPER_PATH)],
        input=digest_mismatch_archive,
        capture_output=True,
        check=False,
    )
    assert digest_mismatch.returncode == 1
    assert digest_mismatch.stdout == b""
    assert b"code=IMAGE_LAYER_DIGEST_MISMATCH layer=1" in digest_mismatch.stderr
    assert f"path={incorrect_name}".encode() in digest_mismatch.stderr
    assert digest_private_content not in digest_mismatch.stderr
    assert digest_mismatch.stderr.count(b"\n") == 1
