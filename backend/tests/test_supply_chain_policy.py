from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ci_scans_dependencies_secrets_and_the_exact_image():
    workflow = yaml.safe_load(_read(".github/workflows/docker-build.yml"))
    jobs = workflow["jobs"]
    security = jobs["dependency-security"]
    production = jobs["production-image"]
    security_text = str(security)
    production_text = str(production)

    assert "pip-audit" in security_text
    assert "npm audit --omit=dev" in security_text
    assert "gitleaks/gitleaks-action@v2" in security_text
    assert "aquasecurity/trivy-action@0.28.0" in production_text
    assert "anchore/sbom-action@v0" in production_text
    assert "HIGH,CRITICAL" in production_text
    assert production_text.count("docker/build-push-action@v6") == 1


def test_dependabot_covers_all_dependency_ecosystems():
    config = yaml.safe_load(_read(".github/dependabot.yml"))
    ecosystems = {item["package-ecosystem"] for item in config["updates"]}
    assert ecosystems == {"pip", "npm", "github-actions", "docker"}


def test_runtime_and_development_python_dependencies_are_separated():
    runtime = _read("backend/requirements-runtime.txt")
    development = _read("backend/requirements-dev.txt")
    compatibility = _read("backend/requirements.txt")
    dockerfile = _read("Dockerfile")

    assert "pytest==" not in runtime
    assert "ruff==" not in runtime
    assert "pytest==" in development
    assert "ruff==" in development
    assert "-r requirements-runtime.txt" in development
    assert "-r requirements-dev.txt" in compatibility
    assert "backend/requirements-runtime.txt" in dockerfile
    assert "sed -E" not in dockerfile
