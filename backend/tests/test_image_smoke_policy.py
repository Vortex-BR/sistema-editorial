from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
CANDIDATE_EXPRESSION = "${{ github.event.pull_request.head.sha || github.sha }}"


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def load_workflow() -> dict:
    return yaml.safe_load(read(".github/workflows/docker-build.yml"))


def action_step(job: dict, action: str) -> dict:
    return next(step for step in job["steps"] if step.get("uses") == action)


def named_step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_production_image_uses_one_candidate_for_build_smoke_scan_and_publish():
    workflow = load_workflow()
    workflow_env = workflow["env"]

    assert workflow_env["CANDIDATE_SHA"] == CANDIDATE_EXPRESSION
    assert workflow_env["CI_MERGE_SHA"] == "${{ github.sha }}"
    assert workflow_env["PGVECTOR_IMAGE"] == "pgvector/pgvector:0.8.5-pg17"

    job = workflow["jobs"]["production-image"]
    checkout = action_step(job, "actions/checkout@v4")
    metadata = named_step(job, "Resolve immutable candidate metadata")
    build = action_step(job, "docker/build-push-action@v6")
    trivy = action_step(job, "aquasecurity/trivy-action@0.28.0")
    smoke = named_step(job, "Run production image smoke test")
    cleanup = named_step(job, "Remove scoped smoke containers but preserve the tested image")
    publish = named_step(job, "Publish the exact tested image without rebuilding")

    assert checkout["with"]["ref"] == "${{ env.CANDIDATE_SHA }}"
    assert "git rev-parse HEAD" in metadata["run"]
    assert "resolve_alembic_head.py" in metadata["run"]
    assert build["with"]["load"] is True
    assert build["with"]["push"] is False
    assert build["with"]["tags"] == "${{ steps.metadata.outputs.image_tag }}"
    assert trivy["with"]["image-ref"] == "${{ steps.metadata.outputs.image_tag }}"
    assert smoke["env"]["IMAGE_TAG"] == "${{ steps.metadata.outputs.image_tag }}"
    assert smoke["env"]["EXPECTED_ALEMBIC_HEAD"] == (
        "${{ steps.metadata.outputs.alembic_head }}"
    )
    assert cleanup["if"] == "always()"
    assert job["env"]["PRESERVE_IMAGE"] == "1"
    assert 'docker tag "${IMAGE_TAG}" "${target}"' in publish["run"]
    assert 'push_output="$(docker push "${target}")"' in publish["run"]
    assert "digest: (sha256:[0-9a-f]{64})" in publish["run"]
    assert "docker image inspect --format '{{index .RepoDigests 0}}'" not in publish["run"]
    assert publish["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )
    assert sum(
        step.get("uses") == "docker/build-push-action@v6" for step in job["steps"]
    ) == 1


def test_ci_derives_alembic_head_instead_of_hardcoding_an_old_revision():
    workflow = read(".github/workflows/docker-build.yml")
    resolver = read("scripts/ci/resolve_alembic_head.py")

    assert 'EXPECTED_ALEMBIC_HEAD: "0032"' not in workflow
    assert "resolve_alembic_head.py" in workflow
    assert "expected exactly one Alembic head" in resolver
    assert "ast.parse" in resolver


def test_production_dockerfile_uses_runtime_allowlist_and_oci_labels():
    dockerfile = read("Dockerfile")
    normalized = "\n".join(line.strip() for line in dockerfile.splitlines())

    assert "COPY --chown=root:seo backend/ ./" not in normalized
    assert "COPY --chown=root:seo backend/app/ ./app/" in normalized
    assert "COPY --chown=root:seo backend/alembic/ ./alembic/" in normalized
    assert "COPY --chown=root:seo backend/alembic.ini ./alembic.ini" in normalized
    assert "FROM python:3.12-slim AS backend-dependencies" in normalized
    assert "--prefix=/install -r requirements-runtime.txt" in normalized
    assert "COPY --from=backend-dependencies /install/ /usr/local/" in normalized
    assert "COPY backend/requirements-runtime.txt ./requirements-runtime.txt" in normalized
    assert "sed -E" not in normalized
    assert "org.opencontainers.image.revision=${APP_COMMIT_SHA}" in normalized
    assert "org.opencontainers.image.version=${APP_BUILD_VERSION}" in normalized
    assert "org.opencontainers.image.source=${APP_IMAGE_SOURCE}" in normalized
    assert "ARG GIT_SHA=" in normalized
    assert "APP_COMMIT_SHA=${APP_COMMIT_SHA:-${GIT_SHA}}" in normalized
    assert "APP_BUILD_VERSION=${APP_BUILD_VERSION:-easypanel-${GIT_SHA}}" in normalized
    assert "APP_SOURCE_DIGEST=${APP_SOURCE_DIGEST:-${GIT_SHA}}" in normalized
    assert "Production image build arguments are invalid:" in normalized
    assert "re.fullmatch(r'[0-9a-f]{40}'" in normalized
    assert "rm -rf /app/backend/tests" not in normalized.lower()


def test_easypanel_deploy_uses_the_ci_validated_immutable_image():
    readme = read("README.md")
    easypanel_readme = read("deploy/easypanel/README.md")
    easypanel_environment = read(".env.easypanel.example")

    assert "selecione a fonte **Docker Image**" in readme
    assert "ghcr.io/Vortex-BR/seo-docker:sha-SHA_COMPLETO" in readme
    assert "Não configure a branch" in readme
    assert "Deixe também vazios os campos **Command** e **Arguments**" in readme
    assert "não reconstrua continuamente a branch `main`" in easypanel_readme
    assert "**Command** e **Arguments** vazios" in easypanel_readme
    for name in ("APP_COMMIT_SHA", "APP_BUILD_VERSION", "APP_SOURCE_DIGEST"):
        assert f"\n{name}=" not in easypanel_environment


def test_production_build_context_excludes_development_residue():
    patterns = {
        line.strip()
        for line in read(".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    required_patterns = {
        ".git",
        ".env",
        "**/.env.*",
        "**/tests/**",
        "**/fixtures/**",
        "**/__pycache__",
        "**/.pytest_cache",
        "**/*.pyc",
        "**/node_modules",
        "**/*.zip",
        "**/*.db",
        "**/*.dump",
    }
    assert required_patterns <= patterns


def test_image_smoke_covers_provenance_runtime_security_and_scoped_cleanup():
    workflow = read(".github/workflows/docker-build.yml")
    script = read("scripts/ci/image-smoke.sh")
    layer_audit = read("scripts/ci/audit_image_layers.py")

    required_evidence = (
        "CANDIDATE_SHA:",
        "CI_MERGE_SHA:",
        "CHECKOUT_SHA:",
        "APP_COMMIT_SHA_OBSERVED:",
        "OCI_REVISION:",
        "OCI_VERSION:",
        "OCI_SOURCE:",
        "IMAGE_LAYER_POLICY:",
        "DATABASE_INITIAL_STATE: empty",
        "INVALID_CONFIGURATION: failed closed",
        "default_openai_route_configuration",
        "ALEMBIC_HEAD:",
        "/api/v1/health",
        "/api/v1/readiness",
        "/api/v1/projects",
        "/api/openapi.json",
        "pid_one_uid",
        "RUNTIME_PERMISSIONS",
        "beat_count",
        "RUNTIME_SECRET_SCAN",
        'Path("/app/backend/tests")',
        '"fixtures"',
        '"__pycache__"',
        '".pyc"',
        "unnecessary runtime development dependency",
        "IMAGE_AUDIT",
        "IMAGE_SMOKE_RESULT",
        "=== CONTAINERS ===",
        "OOMKilled=",
        "ConfiguredEnvironmentNames=",
        "docker logs --timestamps --tail 500",
    )
    for evidence in required_evidence:
        assert evidence in script

    assert 'docker image save "${IMAGE_TAG}"' in script
    assert "python3 scripts/ci/audit_image_layers.py" in script
    assert "IMAGE_LAYER_POLICY: passed" in script
    assert "docker history" not in script
    assert "TemporaryDirectory" in layer_audit
    assert "RUNNER_TEMP" in layer_audit
    assert "GITHUB_WORKSPACE" in layer_audit
    assert "max_archive_bytes: int = 4 * 1024**3" in layer_audit
    assert "max_layers: int = 256" in layer_audit
    assert "max_layer_members: int = 250_000" in layer_audit
    assert "app/backend" in layer_audit
    assert "usr/share/nginx/html" in layer_audit
    assert "extractall(" not in layer_audit
    assert ".extract(" not in layer_audit

    forbidden_operations = (
        "docker system " + "prune",
        "docker image " + "prune",
        "docker volume " + "prune",
        "docker network " + "prune",
        "docker builder " + "prune",
    )
    for operation in forbidden_operations:
        assert operation not in workflow
        assert operation not in script

    assert workflow.count("docker/build-push-action@v6") == 1
    assert "Publish the exact tested image without rebuilding" in workflow
    assert 'PRESERVE_IMAGE: "1"' in workflow
    assert 'docker container rm --force "${container}"' in script
    assert 'docker network rm "${NETWORK_NAME}"' in script
    assert 'docker image rm "${IMAGE_TAG}"' in script
